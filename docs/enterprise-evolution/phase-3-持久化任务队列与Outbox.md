# Phase 3：持久化任务队列与 Outbox

> 状态：进行中
> 前置阶段：Phase 2 退出门槛全部通过  
> 建议周期：2～3 个迭代  
> 阶段负责人：后端负责人；SRE、测试与前端协作

## 1. 目标与边界

把进程内冷路径和录音任务迁移为可持久、可查询、可重试、可审计的任务；用 Transactional Outbox 消除“业务提交成功但消息未发送”的窗口。

| 当前实现 | 阶段目标 |
|---|---|
| `_cold_tasks` + `asyncio.create_task` | RabbitMQ durable queue + Celery cold worker |
| `_running_tasks` 追踪录音 | recording worker + PostgreSQL 任务状态 |
| API 进程退出可能丢任务 | Outbox 与业务数据同事务提交，dispatcher 重投 |
| 失败结果分散在录音/报告行 | `background_job` 统一状态机、错误码和审计 |

非目标：实时 SSE/RTC Interviewer 不进入队列；不实现“恰好一次消息”；不把 Celery result backend 作为业务事实；不提供无授权的公网任务重放 API。

## 2. 输入、输出与锁定契约

输入：PostgreSQL、Redis 幂等组件、托管 RabbitMQ 测试实例。  
输出：Celery worker、`background_job/outbox_event`、异步 API、DLQ/重试/重放和 kill 验收证据。

任务状态只允许：`pending → running → succeeded|failed|cancelled`；重试从 `running` 回 `pending` 并增加 attempt。消息含 `schema_version=1`、`job_id`、`job_type`，不携带录音二进制或敏感正文。broker 持久消息、publisher confirm、late ack、worker lost 重入队、prefetch=1。默认最多 5 次，退避为 5 秒、30 秒、2 分钟、10 分钟、30 分钟；参数/权限错误不可重试，网络/限流/供应商 5xx 可重试。

公共接口冻结为：

- `POST /api/interview/finish`：返回 `202 {job_id,session_id,status:"pending"}`；同一 session 重复 finish 返回同一 job。
- `GET /api/jobs/{job_id}`：仅 owner 可见，返回 `job_id,type,status,attempt,created_at,started_at,finished_at,result_ref,error_code`；不返回内部堆栈。
- 录音上传保留 `recording_id/status`，新增 `job_id`；旧客户端可忽略新字段。
- 至少一次投递，依靠 `job_id`、业务唯一约束、幂等键和状态机实现业务效果仅一次。

## 3. 任务清单

### P3-T01 建立 Celery 与 RabbitMQ 基础

- **实施状态**：已完成；本地 RabbitMQ 4.3.5 持久发布/消费验收通过（18/18）。
- **依赖/并行**：Phase 2；最先执行。**规模/角色**：M，后端/SRE。
- **预计文件**：`rag_llm_server/pyproject.toml`、`rag_llm_server/uv.lock`、`rag_llm_server/config.py`、`rag_llm_server/tasks/celery_app.py`、`rag_llm_server/tests/test_celery_config.py`。
- **契约与步骤**：配置 TLS broker URL、confirm、durable queue、late ack、reject_on_worker_lost、prefetch=1；定义 cold/recording/outbox 路由和独立并发上限。
- **失败处理**：生产禁止 memory/Redis broker；URL/证书错误 fail fast；日志脱敏凭据；连接重试有总时限。
- **验证/验收**：配置矩阵、路由、序列化白名单和生产保护测试通过；测试 broker 能持久发布/消费样本消息。
- **回滚/提交**：业务接入前移除 Celery 配置；`chore: 增加 RabbitMQ 持久任务基础`。

### P3-T02 建立任务与 Outbox 数据模型

- **实施状态**：已完成；空库迁移往返、旧 head 兼容、数据库约束/权限及 Alembic 无漂移验证通过。
- **依赖/并行**：P3-T01。**规模/角色**：M，后端/DBA。
- **预计文件**：`rag_llm_server/db/models.py`、新增 Alembic revision、`rag_llm_server/tests/test_job_schema.py`。
- **契约与步骤**：`background_job` 含 owner/type/status/payload_ref/result_ref/attempt/max_attempts/timestamps/error；`outbox_event` 含 aggregate/event/payload/published_at/attempt/next_attempt_at；建立状态、队列扫描和业务唯一索引。
- **失败处理**：payload 使用 JSONB 白名单且不存二进制/凭据；非法状态由 CHECK 拒绝；revision 只 expand。
- **验证/验收**：空库升级、旧版本兼容启动、约束/索引和 downgrade 测试通过；Alembic 无漂移。
- **回滚/提交**：未切流时可测试库 downgrade；生产保留新表；`feat: 增加后台任务与 Outbox 数据模型`。

### P3-T03 实现任务状态机 Repository

- **实施状态**：已完成；状态矩阵、并发 claim、终态保护、owner 隔离与租约恢复验证通过。
- **依赖/并行**：P3-T02。**规模/角色**：M，后端。
- **预计文件**：`rag_llm_server/services/jobs/repository.py`、`rag_llm_server/services/jobs/types.py`、`rag_llm_server/tests/test_job_repository.py`、`rag_llm_server/tests/test_job_state_machine.py`。
- **契约与步骤**：提供幂等 create/get/claim/succeed/fail/requeue/cancel；状态转换用条件 UPDATE；owner 查询强制租户隔离；公开错误只保存枚举 error_code。
- **失败处理**：非法/过期转换返回冲突，不覆盖终态；worker 崩溃后由租约扫描把超时 running 安全 requeue。
- **验证/验收**：全状态矩阵、并发 claim、终态不可逆、owner 隔离和租约恢复测试通过。
- **回滚/提交**：未接 API 前移除模块；`feat: 实现持久任务状态机`。

#### 检查点 A：P3-T01～P3-T03

- [x] RabbitMQ 消息配置满足持久化与 late ack。
- [x] Schema 只有 expand 变化且旧版本仍能运行。
- [x] 并发 worker 只能 claim 同一 job 一次。

### P3-T04 建立统一任务分发接口

- **依赖/并行**：检查点 A。**规模/角色**：M，后端。
- **预计文件**：`rag_llm_server/services/jobs/dispatcher.py`、`rag_llm_server/services/jobs/handlers.py`、`rag_llm_server/tests/test_job_dispatcher.py`、`rag_llm_server/tests/test_job_handlers.py`。
- **契约与步骤**：定义 `enqueue(job_type, owner_id, payload_ref, idempotency_key)`；生产实现只创建 Job+Outbox；测试提供显式 inline adapter，默认仍验证状态机。
- **失败处理**：未知类型、缺 owner、payload 超限拒绝；inline adapter 不允许在 production 配置。
- **验证/验收**：相同幂等键返回同 job；不同 handler 路由正确；生产保护和异常映射测试通过。
- **回滚/提交**：未迁移业务任务前移除；`feat: 建立统一后台任务分发接口`。

### P3-T05 实现 Transactional Outbox 与投递器

- **依赖/并行**：P3-T04。**规模/角色**：M，后端/SRE。
- **预计文件**：`rag_llm_server/services/jobs/outbox.py`、`rag_llm_server/tasks/outbox_dispatcher.py`、`rag_llm_server/tests/test_outbox.py`、`rag_llm_server/tests/integration/test_outbox_rabbitmq.py`。
- **契约与步骤**：业务/Job/Outbox 同事务；dispatcher 用 `FOR UPDATE SKIP LOCKED` 批量 claim，publisher confirm 后标记 published；重复发布沿用 job_id 幂等。
- **失败处理**：发布失败增加 attempt/next_attempt_at；数据库标记失败时允许重发；超过阈值进入 outbox 告警但不丢记录。
- **验证/验收**：提交前 kill、confirm 后标记前 kill、双 dispatcher、RabbitMQ 短断场景最终均被消费且业务仅一次。
- **回滚/提交**：停止 dispatcher，未发布记录保留；`feat: 实现事务 Outbox 可靠投递`。

#### 检查点 B：P3-T04～P3-T05

- [ ] API 事务提交后必有可恢复 Outbox 记录。
- [ ] 双 dispatcher 不会永久漏发，重复发布无重复业务效果。
- [ ] 测试 inline adapter 不可能在生产启用。

### P3-T06 迁移面试冷路径任务

- **依赖/并行**：检查点 B。**规模/角色**：M，后端/Agent。
- **预计文件**：`rag_llm_server/services/interview_service.py`、`rag_llm_server/tasks/interview_tasks.py`、`rag_llm_server/agents/graph.py`、`rag_llm_server/tests/test_interview_service.py`、`rag_llm_server/tests/test_interview_tasks.py`。
- **契约与步骤**：Evaluator/Planner/Reporter 冷路径以 session_id/job_id 执行；handler 每步读取最新 PG/checkpoint，写入用状态/唯一约束；删除 `_cold_tasks` 和 shutdown gather。
- **失败处理**：实时 Interviewer 仍同步；同一 session 冷任务按业务唯一键串行；不可重试模型输出错误进入 failed。
- **验证/验收**：重复投递、worker 重启、前序 job 未完成和报告唯一性测试通过；API 进程不再创建冷路径 task。
- **回滚/提交**：停止新 worker 并等待 in-flight 后再回退 API；`feat: 将面试冷路径迁移到持久任务`。

### P3-T07 迁移录音处理任务

- **依赖/并行**：检查点 B；可与 P3-T06 并行。**规模/角色**：M，后端/Agent。
- **预计文件**：`rag_llm_server/services/recording_service.py`、`rag_llm_server/tasks/recording_tasks.py`、`rag_llm_server/tests/test_recording_service.py`、`rag_llm_server/tests/test_recording_tasks.py`、`rag_llm_server/tests/test_recording_report_service.py`。
- **契约与步骤**：上传完成后创建 Job+Outbox；worker 只传 recording_id/TOS key，继续使用 asr_task_id 幂等锚点；删除 `_running_tasks`、`resume_pending` 和 create_task。
- **失败处理**：ASR pending 使用 Celery retry，不在 worker 内长 sleep；报告唯一约束阻止重复；TOS/ASR 暂态按重试表处理。
- **验证/验收**：上传、ASR 轮询、超时、重复、worker kill 和报告唯一性测试通过；API 重启不影响处理。
- **回滚/提交**：先停止新录音接入、排空队列，再回退 API/worker；`feat: 将录音分析迁移到持久任务`。

### P3-T08 发布异步任务 API 与前端轮询

- **依赖/并行**：P3-T06、P3-T07。**规模/角色**：M，前后端。
- **预计文件**：`rag_llm_server/api/interview.py`、`rag_llm_server/api/recording.py`、`rag_llm_server/api/jobs.py`、`src/api/rest.ts`、`src/domain/interview/types.ts`。
- **契约与步骤**：实现冻结的 202/job 查询响应；job 查询 owner-scoped；前端以 2 秒起步、最大 10 秒退避轮询，终态停止；页面刷新后用 job_id 恢复。
- **失败处理**：未知/他人 job 返回 404；内部错误只映射 error_code 和安全文案；网络失败保留 job_id 并允许重试查询。
- **验证/验收**：API contract、前端类型、pending→终态、刷新恢复、失败文案和旧录音客户端兼容测试通过。
- **回滚/提交**：旧同步 finish 只在一个兼容版本内由 feature flag 保留；`feat: 发布可查询的异步任务接口`。

#### 检查点 C：P3-T06～P3-T08

- [ ] 生产 API 无 `_cold_tasks`、`_running_tasks` 或后台 create_task。
- [ ] 面试结束与录音任务均返回持久 job_id。
- [ ] 页面刷新和 API/worker 重启后仍能查询终态。

### P3-T09 增加重试与死信队列

- **依赖/并行**：检查点 C。**规模/角色**：M，后端/SRE。
- **预计文件**：`rag_llm_server/tasks/retry_policy.py`、`rag_llm_server/tasks/celery_app.py`、`rag_llm_server/services/jobs/repository.py`、`rag_llm_server/tests/test_retry_policy.py`、`rag_llm_server/tests/integration/test_dead_letter.py`。
- **契约与步骤**：按锁定退避表分类异常；超过 5 次写 failed 并路由 DLQ；DLQ 消息保留 job_id/error_code/original_queue，不含 PII。
- **失败处理**：参数/权限/安全错误直接终态；未知异常默认不可无限重试；重试次数以 PG 为准。
- **验证/验收**：每类异常、退避、最大次数、DLQ 和 worker lost 测试通过；相同 job 不产生多个终态。
- **回滚/提交**：恢复旧 worker 前排空/冻结相关队列；`feat: 增加任务重试与死信策略`。

### P3-T10 增加受审计任务重放

- **依赖/并行**：P3-T09。**规模/角色**：M，后端/安全。
- **预计文件**：`rag_llm_server/scripts/replay_job.py`、`rag_llm_server/services/jobs/replay.py`、新增审计字段 revision、`rag_llm_server/tests/test_job_replay.py`、`deploy/runbooks/job-replay.md`。
- **契约与步骤**：仅 CLI/受控作业执行；要求 operator、reason、原 job_id；创建新 job 并关联 replay_of，不复活原终态；记录时间和结果。
- **失败处理**：非 failed/DLQ、缺理由、payload schema 不兼容或业务结果已存在时拒绝；生产执行需双人审批。
- **验证/验收**：授权、重复重放、审计链、已成功业务保护和 dry-run 测试通过。
- **回滚/提交**：停止重放入口，已创建任务按正常状态机处理；`feat: 增加可审计的任务重放能力`。

### P3-T11 执行队列韧性验收

- **依赖/并行**：P3-T10；最后执行。**规模/角色**：M，测试/SRE。
- **预计文件**：`rag_llm_server/tests/resilience/test_job_delivery.py`、`scripts/verify_job_resilience.ps1`、`docs/enterprise-evolution/evidence/phase-3-acceptance.md`。
- **契约与步骤**：覆盖事务提交前后、publish confirm 前后、worker 执行中 kill、重复消息、RabbitMQ 短断和恢复；记录 job/outbox/DLQ 最终状态。
- **失败处理**：任何静默丢失、永久 running、重复报告或不可解释终态均阻止完成；测试仅运行隔离命名空间。
- **验证/验收**：每个注入点至少重复 10 次；所有任务最终成功或明确 DLQ；业务效果计数始终为 1。
- **回滚/提交**：脚本和测试可直接回退；`test: 增加持久任务故障注入验收`。

## 4. 阶段退出门槛

- [ ] API/Worker/dispatcher 任一重启不静默丢任务。
- [ ] 重复消息、重试和任务重放不产生重复报告或录音结果。
- [ ] 所有任务 owner-scoped、可查询、可审计，最终成功或进入明确 DLQ。
- [ ] 实时 Interviewer 路径延迟未因队列化回归。

## 5. 风险与交接

| 风险 | 缓解 |
|---|---|
| Celery retry 与 PG attempt 不一致 | PG 状态机为唯一事实，任务开头原子 claim |
| confirm 后进程退出导致重复消息 | job_id 幂等、唯一约束和状态条件更新 |
| 供应商长期故障造成积压 | 独立队列、有限重试、DLQ、队列年龄告警 |
| 新旧 Worker 消息不兼容 | schema_version，至少一个发布窗口向后兼容 |

交给 Phase 4/5：运行角色与命令、队列名、任务指标、health 依赖、DLQ/重放 Runbook、kill 验收脚本。
