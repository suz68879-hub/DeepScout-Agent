# Phase 4 SLO 基线

## 生效范围与责任

- 生效环境：生产环境；预发使用同一规则但路由到演练通知渠道。
- API/Redis：`backend-oncall`；任务/RabbitMQ/Outbox：`worker-oncall`；
  PostgreSQL：`database-oncall`；AI/ASR/RTC：`ai-oncall`；遥测管道：`sre-oncall`。
- 本基线自 Phase 4 上线起冻结 30 天。30 天后只能通过评审修改，禁止在实现
  提交中静默调整阈值。

## SLI 与目标

| SLI | Prometheus recording rule | 初始目标 |
|---|---|---|
| API 月可用性 | `deepscout:http_availability:ratio30d` | ≥99.9% |
| 普通 API 延迟 | `deepscout:http_duration:p95_10m` | p95 <500ms |
| 实时首 token | `deepscout:first_token:p95_10m` | p95 <2s |
| 后台任务成功率 | `deepscout:job_success:ratio30m` | ≥99% |
| Outbox 投递 | `outbox_oldest_age_seconds` | p95 目标代理值 <10s |
| 录音任务耗时 | `deepscout:job_duration:p95_30m{job_type="recording.process"}` | p95 <20min |

Outbox 当前使用最老未发布事件年龄作为保守代理：它比单事件 p95 更敏感，任何
持续超过 10 秒的积压都会触发告警。上线后如引入事件年龄 Histogram，可在评审后
替换为真实 p95，不改变本阶段目标。

## 告警策略

- 可用性使用 99.9% SLO 的 error budget burn rate；5 分钟与 1 小时窗口同时
  超过 14.4 才触发 critical，并要求 5 分钟至少 100 个请求。
- 延迟、任务成功率、队列年龄、Outbox、连接池和供应商异常连续 10 分钟才告警，
  避免瞬时抖动；DLQ 和遥测丢弃连续 5 分钟告警。
- 每条规则必须带 `severity`、`owner`、`dashboard_url`、`runbook_url`。
- 没有样本时保持 No data，不用零值填充掩盖采集故障。

## 验证记录

- 规则静态结构和元数据由 Phase 4 验收脚本校验。
- `observability/tests/test_rules.yml` 包含 API 快速燃烧触发与健康恢复序列。
- 使用官方 Prometheus `promtool 3.13.2` 执行 `check rules` 与 `test rules`，
  12 条 recording rule、12 条告警及触发/恢复序列均通过。合入/发布流水线
  必须重复执行相同检查，任何失败阻止发布。
