# OpenTelemetry、Prometheus、Tempo、Loki 与 Grafana 故障 Runbook

## 影响与检测

关联告警：`ApiAvailabilityFastBurn`、`ApiLatencyHigh`、`TelemetryExportDrops`。
遥测故障可能导致 Dashboard No data、Trace/日志断链或告警失明，但不得改变业务请求
结果。先区分真实业务故障与仅观测管道故障。

- Dashboard：`/d/deepscout-api`、`/d/deepscout-dependencies`。
- 责任人：`sre-oncall`；真实 API 故障同时通知 `backend-oncall`。

## 权限与安全边界

只读访问 Grafana/Prometheus/Tempo/Loki 和 Collector 指标。修改采样、保留期、告警、
数据源、队列或重启组件是 **审批点**。禁止关闭脱敏、扩大敏感属性、把普通采样率
提高到 100%、删除遥测数据掩盖事件，或把日志/Trace 公开分享。

## 诊断顺序

1. 检查应用 `/health/live` 与业务请求；若业务健康但 Dashboard No data，按管道处理。
2. 检查应用 `/metrics`（仅内网）与 Collector 自身 metrics，比较接收、队列、丢弃、
   export failure；不得把 `/metrics` 暴露到公网。
3. 检查 Collector traces/metrics/logs 三条 pipeline 是否独立，确认 memory limiter、
   batch、tail sampling 和 exporter 状态。
4. 在 Prometheus targets/rules 查看 scrape 与规则评估；空数据不得用零值填充。
5. 在 Tempo 按 trace_id 查 API→MQ→Worker→external；在 Loki 按同 trace_id 查安全日志。
6. 检查 Grafana provisioning 与 datasource 健康，避免直接在线编辑受管 dashboard。

## 安全缓解与审批点

- 单 exporter 故障：保留其他 pipeline；等待有界队列，避免同步阻塞应用。
- Collector 压力：**审批后**扩容或降低普通 trace 保留采样；错误/高延迟仍保留 100%。
- Prometheus/Tempo/Loki 故障：恢复存储或 datasource；不修改 SLO 来消除告警。
- 规则异常：回退规则 Git 版本并保留事件；不得临时删除 critical 告警。
- Grafana 异常：从 Git provisioning 恢复，不在生产 UI 创建不可审计副本。

## 升级与恢复确认

立即升级条件：告警完全失明、三类遥测同时丢弃、敏感数据疑似进入后端、保留数据
损坏或 15 分钟未恢复。通知 SRE/后端；数据泄漏立即通知安全并限制访问。

恢复标准：Collector export failure 为 0；Prometheus targets/rules healthy；Dashboard
有真实数据；合成 trace 可跨 API/MQ/Worker/外部依赖；日志与 span 敏感扫描零命中；
测试告警触发后可自动恢复。记录丢失窗口、估算数据量、审批和后续容量行动。

## 演练记录

预发阻断一个 Collector exporter 5 分钟再恢复：记录告警时间、定位和恢复耗时，确认
业务不受影响、其他 pipeline 正常、队列有界且恢复后告警自动关闭。
