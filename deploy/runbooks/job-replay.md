# 后台任务受审计重放 Runbook

## 适用范围

仅用于重放已进入 `failed` 终态的 `interview.finish` 或
`recording.process` 任务。命令创建一个新的 pending Job 与 Outbox，原 Job
保持不变；新 Job 的 `status/result_ref/error_code` 是本次重放结果。

禁止把本工具暴露为公网 API，禁止重放非 failed 任务，禁止绕过业务结果
唯一性保护，禁止把数据库 DSN、任务 payload、录音 key 或用户数据写入工单。

## 权限与审批

- 本地或测试环境：执行人必须拥有受控主机权限，并提供企业身份 `operator`
  和不少于 10 个字符的 `reason`。
- 生产环境：工单必须由第二人审批；`operator` 与 `approved-by` 必须不同，
  且分别对应当前 IAM/堡垒机登录身份和审批人身份。
- 生产命令额外要求 `--confirm-production`。该参数只表示执行人确认当前窗口，
  不替代 IAM、工单或双人审批。
- 使用 application 数据库角色执行；不得使用 migration 管理员账号。

## 前置检查

1. 确认目标环境、维护窗口、工单号和两名责任人。
2. 确认 API、Outbox dispatcher、对应 worker 与 RabbitMQ 健康。
3. 确认 Alembic head 为 `20260826_0007`。
4. 确认原 Job 为 failed，且没有已成功的报告或录音结果。
5. 确认该原 Job 没有直接重放子 Job；失败的重放子 Job 应作为下一次源 Job，
   形成连续审计链。

## Dry-run

先在仓库后端目录执行只读校验：

```powershell
Set-Location rag_llm_server
uv run python scripts/replay_job.py `
  --job-id "<failed-job-uuid>" `
  --operator "<corporate-identity>" `
  --reason "<ticket-id and replay reason>" `
  --dry-run
```

生产 dry-run 同样需要第二审批人与显式环境确认：

```powershell
uv run python scripts/replay_job.py `
  --job-id "<failed-job-uuid>" `
  --operator "<corporate-identity>" `
  --approved-by "<approver-corporate-identity>" `
  --reason "<ticket-id and replay reason>" `
  --dry-run `
  --confirm-production
```

成功输出的 `status` 为 `validated`、`replay_job_id` 为 `null`；数据库 Job、
Outbox 和录音状态均不得变化。

## 执行重放

复核 dry-run 和审批后，移除 `--dry-run`。命令只输出源 Job ID、新 Job ID、
状态和 dry-run 标记，不输出 payload 或内部异常。记录新 Job ID 到工单。

```powershell
uv run python scripts/replay_job.py `
  --job-id "<failed-job-uuid>" `
  --operator "<corporate-identity>" `
  --approved-by "<approver-corporate-identity>" `
  --reason "<ticket-id and replay reason>" `
  --confirm-production
```

## 验证与审计

- 原 Job 必须继续保持 failed，且其时间与错误码未被覆盖。
- 新 Job 必须为 pending/running/终态之一，`replay_of` 指向原 Job。
- `replay_operator/replay_approved_by/replay_reason/replayed_at` 必须完整。
- 新 Job 必须恰有一个 `job.created` Outbox；重复命令必须返回
  `ALREADY_REPLAYED`，不得创建第二个直接子 Job。
- 最终只允许一个业务报告/录音结果。成功后记录新 Job 的 status；失败时记录
  公共 `error_code`，不得复制内部堆栈或 payload 到工单。

## 失败与回滚

- 校验失败：不产生写入，根据安全错误码修正工单或停止操作。
- CLI 在提交前失败：事务整体回滚，不会留下半个 Job 或 Outbox。
- 新 Job 已提交：它是普通持久任务，不能通过删除审计字段“撤销”。立即停止
  后续重放；如需阻止尚未执行的任务，按事件流程暂停对应 dispatcher/worker，
  再通过受控应用操作取消任务。
- 回退应用版本前先禁用本 CLI 并排空或冻结新 Job。Schema revision 可回退到
  `20260826_0006`，但只有在确认不再需要重放审计字段且完成审批后执行。

任何跨租户、重复业务结果、审计字段缺失或无法解释的终态都必须升级为安全
事件，并阻止继续重放。
