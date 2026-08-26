# Phase 3 验收证据

> 验收日期：2026-08-26
> 分支：`codex/phase3-persistent-jobs`
> 结论：通过

## 1. 验收边界

- PostgreSQL 只允许数据库名以 `_test` 结尾；本次使用隔离测试库，不读取或迁移现有 `interview.db`。
- RabbitMQ 为每次验收创建随机前缀的 durable 测试 exchange/queue 和独占 DLQ observer，结束后精确删除，不清空共享 broker。
- 每个场景使用唯一用户、会话、Job 和幂等键；测试结束通过 owner 外键级联清理本次数据。
- 不调用真实 LLM、RTC、TOS 或 ASR，不执行生产重放、迁移或切换，不记录 DSN、凭据、payload 或个人数据。

## 2. 任务提交

| 任务 | 提交 | 结果 |
|---|---|---|
| P3-T01 RabbitMQ 基础 | `22e9ce1` | 通过 |
| P3-T02 Job/Outbox Schema | `c6481f3` | 通过 |
| P3-T03 状态机 Repository | `129f21d` | 通过 |
| P3-T04 统一分发接口 | `15922b6` | 通过 |
| P3-T05 Transactional Outbox | `635dcf2` | 通过 |
| P3-T06 面试冷路径 | `b5012f5` | 通过 |
| P3-T07 录音任务 | `5727a14` | 通过 |
| P3-T08 异步任务 API | `8cc31c8` | 通过 |
| P3-T09 重试与 DLQ | `69cf27a` | 通过 |
| P3-T10 受审计重放 | `3bbb760` | 通过 |
| P3-T11 韧性验收 | 本提交 | 通过 |

## 3. 故障注入结果

所有注入点各执行 10 次：

| 注入点 | Job 最终状态 | Outbox/DLQ | 业务效果 |
|---|---|---|---|
| 事务提交前终止 | 未创建（10/10） | 无 Outbox | 0 |
| 事务提交后、dispatcher 前终止 | succeeded（10/10） | 10/10 published | 每 session 1 份报告 |
| publish confirm 后、标记前终止 | succeeded（10/10） | 每条确认发布 2 次后标记 | 每 session 1 份报告 |
| worker 业务提交后终止 | succeeded（10/10） | 过期 lease 恢复 | 每 session 1 份报告 |
| 重复 worker 消息 | succeeded（10/10） | 重复执行返回同一终态 | 每 session 1 份报告 |
| RabbitMQ 短断后恢复 | succeeded（10/10） | 每条失败 1 次后全部 published | 每 session 1 份报告 |
| 不可重试模型错误 | failed（10/10） | 10/10 明确进入 DLQ | 0，DLQ 仅含白名单字段 |

50 个已提交的成功任务均生成且只生成 1 份报告；10 个不可重试任务均具有明确 `error_code` 和 DLQ 消息；没有永久 running、静默丢失或不可解释终态。

## 4. 自动化入口与质量门禁

执行入口：

```powershell
$env:CELERY_BROKER_TEST_URL = '<isolated-test-broker-url>'
.\scripts\verify_job_resilience.ps1
```

结果：

- 韧性验收：`7 passed in 19.53s`，脚本报告 `7 scenarios x 10 injections`。
- 后端全量：`623 passed, 15 skipped`，覆盖率 `82.96%`，门槛 `81%`。
- Alembic head：`20260826_0007`，P3-T10 已完成升级/降级往返并验证无漂移。
- 前端未被 P3-T11 修改；最近门禁为 `97 passed`、lint/typecheck/build 通过、E2E `3 passed`。

## 5. Phase 3 退出门槛

- [x] 提交边界、dispatcher 重启和 worker 租约恢复均不会静默丢失任务。
- [x] 重复发布、重复消费、有限重试和任务重放均由 Job 状态、幂等键及业务唯一约束保护。
- [x] Job 查询保持 owner-scoped；成功任务可查询，失败任务具有安全错误码和明确 DLQ，重放具有审计链。
- [x] 实时 Interviewer 仍走同步 API/RTC 路径，未接入 Celery；P3-T11 未修改生产路径，前端面试 E2E 通过。定量首 token p95 继续由 Phase 4 指标验收。
