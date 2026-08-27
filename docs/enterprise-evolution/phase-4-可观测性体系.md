# Phase 4：可观测性体系

> 状态：进行中
> 前置阶段：Phase 1；Redis 观测依赖 Phase 2，队列观测依赖 Phase 3  
> 建议周期：1～2 个迭代  
> 阶段负责人：SRE；后端、测试与业务负责人协作

## 1. 目标与边界

建立可关联、可告警、可执行处置的日志、指标和链路体系，使请求能从 API 追踪到 PG/Redis/RabbitMQ/Worker/外部供应商。

| 当前实现 | 阶段目标 |
|---|---|
| Phase 0 只有 Request ID 与 JSON 日志 | Request ID、Trace ID、Job ID 可关联 |
| 无统一指标和链路后端 | OTel Collector、Prometheus、Tempo、Loki、Grafana |
| `/health` 不能区分存活/就绪 | live/ready/startup 语义和依赖矩阵 |
| 无 SLO、告警和处置手册 | 指标→告警→Dashboard→Runbook 闭环 |

非目标：不把用户 ID、会话 ID、文件名作为指标标签；不记录 Prompt/简历/音频正文；不让外部供应商故障触发 liveness 重启风暴；不以 Trace 代替业务审计。

## 2. 输入、输出与锁定决策

采用 OpenTelemetry Collector + Prometheus + Tempo + Loki + Grafana。服务统一通过 OTLP/gRPC 上报；本地开发允许 console exporter，但生产禁止。生产环境由 SDK 全量上报，Collector 尾部采样保留错误/高延迟链路 100%、普通链路 10%；指标不采样。Prometheus/Loki 保留 30 天，Tempo 保留 7 天。metric label 只使用 route template、method、status class、provider、operation、job_type、outcome 等固定枚举。

初始 SLO：API 月可用性≥99.9%；普通 API p95<500ms；实时首 token p95<2s；任务成功率≥99%；Outbox p95<10s；录音任务 p95<20min。上线 30 天后只能通过评审修订，不在实现提交中静默改变。

## 3. 任务清单

### P4-T01 建立 OTel SDK 与 Collector

- **实施状态**：已完成；SDK/Collector 契约、生命周期与后端全量覆盖率门禁通过。
- **依赖/并行**：Phase 1；最先执行。**规模/角色**：M，后端/SRE。
- **预计文件**：`rag_llm_server/pyproject.toml`、`rag_llm_server/uv.lock`、`rag_llm_server/main.py`、`rag_llm_server/observability/telemetry.py`、`observability/otel-collector.yaml`、`rag_llm_server/tests/test_telemetry_config.py`。
- **契约与步骤**：配置 service.name/version/environment、OTLP endpoint、采样器和资源属性；lifespan 初始化/flush/shutdown；Collector 分离 traces/metrics/logs pipeline。
- **失败处理**：遥测后端不可用不阻断核心请求，但本地有界队列满后丢弃并计数；生产配置缺 service/environment 时 fail fast。
- **验证/验收**：SDK 生命周期、采样规则、敏感资源属性和 Collector 配置检查通过；应用关闭能 flush 有界时间。
- **回滚/提交**：关闭 exporter 并回退依赖；`feat: 建立 OpenTelemetry 采集基础`。

### P4-T02 关联 Request ID、Trace ID 与 Job ID

- **实施状态**：已完成；HTTP→Outbox→RabbitMQ→Worker 链路、日志关联、非法上下文重建与 baggage 隔离测试通过。
- **依赖/并行**：P4-T01、Phase 0 P0-T05/P0-T06。**规模/角色**：M，后端。
- **预计文件**：`rag_llm_server/middleware/request_context.py`、`rag_llm_server/logging_config.py`、`rag_llm_server/services/jobs/dispatcher.py`、`rag_llm_server/services/jobs/outbox.py`、`rag_llm_server/tasks/celery_app.py`、`rag_llm_server/tasks/interview_tasks.py`、`rag_llm_server/tasks/recording_tasks.py`、`rag_llm_server/tests/test_trace_correlation.py`、`rag_llm_server/tests/test_outbox.py`。
- **契约与步骤**：HTTP 自动生成 server span；日志 Filter 注入 trace/span；Outbox message 注入 W3C trace context，worker 建 consumer span 并关联 job_id。
- **失败处理**：非法 traceparent 生成新 trace 并记录安全事件；跨异步任务不得复用可变 Context；job payload 不保存完整 baggage。
- **验证/验收**：API→Outbox→Rabbit→Worker 的测试 spans 共用 trace 或显式 link；日志可用 request_id/trace_id/job_id 关联。
- **回滚/提交**：保留 Phase 0 Request ID/JSON 日志；`feat: 打通请求与后台任务链路上下文`。

### P4-T03 接入 PG、Redis 与 RabbitMQ 追踪

- **实施状态**：已完成；SQLAlchemy、Redis 与 RabbitMQ 成功/失败链路、安全属性和重复注册测试通过。
- **依赖/并行**：P4-T01、Phase 2、Phase 3。**规模/角色**：M，后端/SRE。
- **预计文件**：`rag_llm_server/observability/instrumentation.py`、`rag_llm_server/db/engine.py`、`rag_llm_server/services/redis_client.py`、`rag_llm_server/services/jobs/outbox.py`、`rag_llm_server/middleware/request_context.py`、`rag_llm_server/tasks/celery_app.py`、`rag_llm_server/tests/test_dependency_tracing.py`。
- **契约与步骤**：启用 SQLAlchemy/Redis/Celery instrumentation；数据库 span 只保留规范化 operation/table，不记录 bind values；broker span 不记录 message body。
- **失败处理**：instrumentation 初始化失败输出单条安全错误并禁用该插件，不重复注册；追踪异常不改变业务结果。
- **验证/验收**：每类依赖有成功、超时、错误 span；测试断言无 DSN、SQL 参数、Redis key 原始输入和消息正文。
- **回滚/提交**：逐插件关闭 instrumentation；`feat: 增加核心基础设施链路追踪`。

#### 检查点 A：P4-T01～P4-T03

- [x] Collector 可接收并路由三类遥测。
- [x] HTTP、数据库、缓存、消息 span 可关联。
- [x] spans/logs 不包含凭据和业务正文。

### P4-T04 为外部供应商建立脱敏 Span

- **实施状态**：已完成；ARK 主调用链、旧流式客户端、ASR 与 RTC 已统一记录脱敏结果，并覆盖成功、超时、限流、取消与敏感样本测试。
- **依赖/并行**：检查点 A。**规模/角色**：M，后端/安全。
- **预计文件**：`rag_llm_server/observability/external_span.py`、`rag_llm_server/services/agent_llm.py`、`rag_llm_server/services/llm_service.py`、`rag_llm_server/services/asr_client.py`、`rag_llm_server/services/rtc_service.py`、`rag_llm_server/tests/test_external_tracing.py`。
- **契约与步骤**：统一 provider/operation/model/outcome/http_status/duration/retry_count；仅记录输入输出 token 数、字节数和枚举结果，不记录 Prompt、转写或完整 URL query。
- **失败处理**：供应商错误映射稳定 error.type；取消和超时分开；span 装饰器异常不得触发第二次供应商调用。
- **验证/验收**：ARK/ASR/RTC 成功、超时、限流、取消测试通过；敏感样本不出现在 exporter 数据。
- **回滚/提交**：移除 wrapper 不改变服务接口；`feat: 增加外部 AI 与 RTC 脱敏追踪`。

### P4-T05 增加核心指标

- **实施状态**：已完成；HTTP、首 token、外部请求、任务、队列/Outbox、数据库池与 Redis 指标及内网 `/metrics` 测试通过。
- **依赖/并行**：P4-T03、P4-T04。**规模/角色**：M，后端/SRE。
- **预计文件**：`rag_llm_server/observability/metrics.py`、`rag_llm_server/observability/external_span.py`、`rag_llm_server/observability/instrumentation.py`、`rag_llm_server/main.py`、`rag_llm_server/middleware/request_context.py`、`rag_llm_server/agents/interviewer.py`、`rag_llm_server/services/jobs/repository.py`、`rag_llm_server/services/jobs/outbox.py`、`rag_llm_server/tasks/outbox_dispatcher.py`、`rag_llm_server/tests/test_metrics.py`。
- **契约与步骤**：实现设计文档中的 HTTP、首 token、外部请求、job、queue depth、outbox、DB pool、Redis error 指标；histogram bucket 与 SLO 对齐；`/metrics` 仅内网访问。
- **失败处理**：未知 route 使用模板 `unknown`；禁止动态 label；采集队列深度失败记录 error counter，不让 scrape 失败。
- **验证/验收**：指标类型、bucket、固定 labels 和并发计数测试通过；高基数扫描确认无 ID/文件名 label。
- **回滚/提交**：关闭 metrics endpoint/observer；`feat: 增加核心服务与任务指标`。

### P4-T06 实现健康与就绪探针

- **实施状态**：已完成；live/ready/startup/兼容探针、组件超时、迁移落后、依赖恢复和安全响应测试通过。
- **依赖/并行**：Phase 1/2/3；可与 P4-T04/P4-T05 并行。**规模/角色**：M，后端/SRE。
- **预计文件**：`rag_llm_server/api/health.py`、`rag_llm_server/main.py`、`rag_llm_server/tests/test_health_api.py`、`rag_llm_server/tests/test_lifecycle.py`。
- **契约与步骤**：`/health/live` 只检查 event loop；`/health/ready` 检查配置、Alembic head、PG、Redis 和任务提交能力；`/health/startup` 检查 Prompt/连接池初始化；旧 `/health` 一个发布窗口映射 ready 并带 deprecation header。
- **失败处理**：外部 LLM/ASR/RTC 不参与 live/ready；每项检查有 1 秒超时并返回组件枚举，不暴露 host/异常；HTTP 200/503 明确。
- **验证/验收**：健康、PG/Redis/MQ 断开、迁移落后、启动未完成和恢复测试通过；live 在依赖故障时仍为 200。
- **回滚/提交**：兼容窗口内恢复旧 `/health`；`feat: 增加分层健康与就绪探针`。

#### 检查点 B：P4-T04～P4-T06

- [x] 核心 SLI 可由现有指标计算。
- [x] 外部调用 spans 完成脱敏验证。
- [x] live/ready/startup 的故障语义互不混淆。

### P4-T07 建立 Grafana 仪表盘

- **实施状态**：已完成；4 个 dashboard、23 个面板和文件 provisioning 已通过 JSON/YAML 结构与低基数查询校验。
- **依赖/并行**：检查点 B。**规模/角色**：M，SRE。
- **预计文件**：`observability/grafana/dashboards/api.json`、`worker.json`、`dependencies.json`、`business.json`、`provisioning/dashboards.yaml`。
- **契约与步骤**：API 面板覆盖 RED；worker 覆盖 job rate/duration/error、queue age/depth/DLQ；依赖覆盖 PG pool/Redis/Rabbit/provider；业务面板只使用聚合指标。
- **失败处理**：空数据明确显示 No data，不显示 0；变量仅固定环境/service/job_type；查询不得依赖高基数日志字段。
- **验证/验收**：JSON 可 provisioning；所有 SLO 有对应 panel；从 service/route/job_type 可下钻到日志/trace 链接。
- **回滚/提交**：回退 dashboard provisioning；`feat: 增加企业级服务观测仪表盘`。

### P4-T08 定义 SLO 与告警

- **实施状态**：已完成；SLO 基线、12 条 recording rule、12 条分级告警及 promtool 3.13.2 触发/恢复测试通过。
- **依赖/并行**：P4-T07。**规模/角色**：M，SRE/业务负责人。
- **预计文件**：`observability/prometheus/recording-rules.yaml`、`observability/prometheus/alerts.yaml`、`observability/tests/test_rules.yml`、`docs/enterprise-evolution/evidence/slo-baseline.md`。
- **契约与步骤**：固化本阶段初始 SLO；可用性采用 5m/1h 多窗口 error budget burn；延迟连续 10 分钟、DLQ/队列年龄、outbox、连接池和遥测丢弃建立分级告警。
- **失败处理**：每条告警必须有 severity、owner、dashboard_url、runbook_url；缺失任一字段规则测试失败；低流量使用最小请求量保护。
- **验证/验收**：`promtool check rules` 和规则单测通过；注入合成序列可触发/恢复告警且无持续抖动。
- **回滚/提交**：回退规则版本并保留事件记录；`feat: 建立 SLO 与分级告警规则`。

### P4-T09 编写故障 Runbook

- **依赖/并行**：P4-T08。**规模/角色**：M，SRE/后端。
- **预计文件**：`deploy/runbooks/database-incident.md`、`redis-incident.md`、`queue-backlog.md`、`external-provider.md`、`observability-pipeline.md`。
- **契约与步骤**：每份包含影响、检测、权限、仪表盘、诊断顺序、安全缓解、升级和恢复确认；命令使用只读检查优先；生产变更步骤标记审批点。
- **失败处理**：禁止把清库、跳过幂等或关闭安全门禁作为缓解；不可逆命令不直接提供可复制形式。
- **验证/验收**：每条告警链接存在；非作者按 Runbook 在演练环境完成一次处置并记录耗时。
- **回滚/提交**：文档版本回退，旧版本保留 Git 历史；`docs: 增加可观测性故障运行手册`。

### P4-T10 执行脱敏、基数与链路验收

- **依赖/并行**：P4-T09；最后执行。**规模/角色**：M，测试/SRE/安全。
- **预计文件**：`rag_llm_server/tests/observability/test_redaction.py`、`test_cardinality.py`、`test_trace_flow.py`、`scripts/verify_observability.ps1`、`docs/enterprise-evolution/evidence/phase-4-acceptance.md`。
- **契约与步骤**：运行完整业务合成流；扫描 log/span/metric；计算 time series 数量；触发一条告警并按 trace_id 定位到 worker/外部依赖。
- **失败处理**：任何真实内容/高基数 ID 泄漏、trace 断链或无 Runbook 告警均阻止完成并清理测试遥测。
- **验证/验收**：敏感样本零命中；metric labels 在允许清单；API→MQ→Worker 链路完整；告警恢复后自动关闭。
- **回滚/提交**：验收测试可直接回退；`test: 增加可观测性安全与链路验收`。

## 4. 阶段退出门槛

- [ ] Request ID、Trace ID、Job ID 可跨日志/Trace/任务关联。
- [ ] 核心 SLO 有指标、Dashboard、告警和演练过的 Runbook。
- [ ] live/ready/startup 经故障注入验证且没有重启风暴。
- [ ] 日志/Trace 无敏感正文，Metrics 无高基数 ID 标签。

## 5. 风险与交接

| 风险 | 缓解 |
|---|---|
| 高基数导致成本失控 | 固定 label allowlist，ID 仅进入脱敏日志/Trace |
| 采集后端故障影响业务 | 异步有界导出，遥测失败不改变请求结果 |
| 告警过多被忽视 | SLO 驱动、低流量保护、owner 与 Runbook 强制字段 |
| Trace 泄漏 Prompt/转写 | 统一外部 span wrapper 与敏感样本扫描 |

交给 Phase 5：OTLP/metrics 端口、probe 路径、Dashboard/alert provisioning、存储保留期和资源预算。
