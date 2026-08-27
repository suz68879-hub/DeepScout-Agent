# Phase 4 可观测性验收证据

## 验收范围

本次覆盖 SDK/Collector、日志与 Trace 关联、PG/Redis/RabbitMQ、ARK/ASR/RTC、
Prometheus 指标、分层探针、Grafana、SLO/告警、Runbook、脱敏和标签基数。

## 自动化结果

| 检查 | 结果 |
|---|---|
| 后端完整 pytest + coverage | 666 passed、20 skipped；83.32% ≥ 81% |
| `tests/observability` | 3 passed；敏感扫描、标签基数、完整 trace 流均通过 |
| Dashboard/config verifier | 4 dashboards、12 recording rules、12 alerts、5 linked Runbooks |
| `promtool 3.13.2 check rules` | SUCCESS；两个文件各 12 rules |
| `promtool 3.13.2 test rules` | SUCCESS；快速燃烧触发与健康恢复序列通过 |
| `git diff --check` | 通过 |

官方 promtool 从 Prometheus GitHub Release 下载到工作区临时目录，仅用于本次检查，
验证后已删除。仓库不携带该二进制。

## 脱敏与基数

- 注入密码、私有音频 URL、邮箱、手机号和 Prompt 样本，扫描 JSON log、finished
  spans 和 Prometheus exposition，零命中。
- SQL span 不保留 statement/bind values/DSN；Redis 不保留 key/value/URL；RabbitMQ
  不保留 message body；外部 span 不保留 Prompt/转写/签名 query。
- metric label 仅包含 `method/route/status_class/provider/operation/outcome/job_type/queue/
  error_type/collector/le`；UUID 和文件名被映射到固定 `other/unknown`。
- 一组覆盖全部指标类型的合成采样产生的唯一序列不超过 120。

## 链路证据

合成 API 请求在同一个 trace 中产生并校验以下父子链：

`POST /enqueue` server → `deepscout.cold publish` producer →
`interview.finish process` consumer → `ark chat` client。

W3C `traceparent` 只通过 Outbox allowlist 和 RabbitMQ header 传递；consumer 带安全
`job.id`，外部 span 的 outcome 为固定枚举。非法父上下文另有回归测试确保重建 trace。

## 探针与告警

- live 不调用外部组件；PG/Redis/MQ 故障时仍为 200。
- ready 对 configuration/migrations/postgresql/redis/rabbitmq 分别执行 1 秒有界检查，
  覆盖断开、timeout、migration outdated 和恢复。
- startup 在连接池/Prompt 完成前返回 503，完成后 200，shutdown 后恢复 503。
- API 99.9% SLO 使用 5m/1h burn rate，且有最小请求量保护；其余告警有持续时间，
  12 条告警全部具备 severity、owner、dashboard_url 和 runbook_url。

## 最终门禁

执行命令：

```powershell
Set-Location rag_llm_server
uv run pytest --cov=. --cov-report=term-missing -q
```

结果：`666 passed, 20 skipped`，总覆盖率 `83.32%`，通过固定 `81%` 门槛。

## 部署后人工验收

以下项目依赖 Phase 5 提供真实 Prometheus/Grafana/Tempo/Loki 与预发通知渠道，当前
不能在本地伪造为已完成：

1. 非 Runbook 作者按 5 份手册各完成一次预发处置，记录告警、定位和恢复耗时。
2. 从真实告警链接进入 Dashboard，再以 trace_id 下钻到 Loki/Tempo。
3. 验证生产网络策略确实阻断公网 `/metrics`，并验证 30/7 天保留策略。

这些是部署验收项，不影响仓库内规则、链接、合成序列和安全门禁的通过状态；未留档
前不得宣称 Phase 4 的人工演练门槛完成。
