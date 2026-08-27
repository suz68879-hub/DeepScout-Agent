# RabbitMQ、后台队列与 Outbox 积压 Runbook

## 影响与检测

关联告警：`BackgroundJobSuccessLow`、`RecordingJobLatencyHigh`、
`QueueBacklogAgeHigh`、`DeadLetterQueueNotEmpty`、`OutboxPublicationDelayed`。
影响包括报告/录音延迟、任务失败或 API 提交后长期 pending；API 同步请求可能仍正常。

- Dashboard：`/d/deepscout-worker`，依赖信息见 `/d/deepscout-dependencies`。
- 责任人：`worker-oncall`，RabbitMQ 协同人为 `sre-oncall`。

## 权限与安全边界

只读检查使用 RabbitMQ monitoring 权限和数据库 observer 角色。生产扩 worker、暂停
consumer、重放或移动消息是 **审批点**。禁止 purge/直接 ack DLQ、编辑消息正文、
跳过 job_id 幂等、删除 Job/Outbox 或把 payload/录音 key 写入工单。

## 诊断顺序

1. 按 `queue/job_type/outcome` 确认范围，区分 cold、recording、outbox 和 DLQ。
2. 检查最老年龄与 depth：只增不减通常是 consumer/供应商问题；Outbox 年龄先升通常
   是 dispatcher 或 RabbitMQ 发布问题。
3. 使用 RabbitMQ 只读命令：

   ```powershell
   rabbitmqctl list_queues name messages_ready messages_unacknowledged consumers durable
   rabbitmqctl list_connections user peer_host state channels
   ```

4. 使用数据库 observer 查询聚合状态，不读取 payload：

   ```sql
   select job_type, status, count(*) from background_job group by job_type, status;
   select count(*), min(created_at) from outbox_event where published_at is null;
   ```

5. 从一个 job_id 的安全日志进入 trace，确认 Outbox publish、consumer 和外部 span。
6. 检查 worker 心跳、并发、prefetch、重试率和供应商限流；不要先重放。

## 安全缓解与审批点

- 下游限流：暂停增加并发，保留退避；必要时限制新录音提交。
- worker 容量不足：**审批后**按角色单独扩 cold/recording worker，观察 10 分钟。
- dispatcher 故障：恢复单实例 dispatcher，依赖 Outbox 重新投递，不手工拼消息。
- DLQ：先按 `job-replay.md` dry-run 验证；生产重放需双人审批并创建新 Job。
- RabbitMQ 故障：保持 Outbox 未发布记录，恢复 broker 后让 dispatcher 自动追平。

禁止清队列、批量改 Job 状态、把 failed 改回 pending、绕过最大尝试或关闭幂等约束。

## 升级与恢复确认

立即升级条件：DLQ 增长、持久消息疑似丢失、重复业务结果、跨租户任务、积压超过
30 分钟或恢复需要 broker 管理操作。通知 worker/SRE/业务负责人；数据异常通知安全。

恢复标准：各 queue depth 和 oldest age 持续下降；Outbox oldest <10s；DLQ 不再增长；
任务成功率 ≥99%；抽样 Job 只有一个业务结果，trace 完整。记录是否重放、审批人与
新旧 job_id 关系，不记录 payload。

## 演练记录

预发暂停 recording worker 5 分钟后恢复：记录告警时间、定位和清空耗时，验证消息
未丢失、未重复处理、告警自动恢复。
