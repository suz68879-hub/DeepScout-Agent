# Redis 故障 Runbook

## 影响与检测

关联告警：`RedisErrorRateHigh`。Redis 承载登录态缓存、限流、幂等和分布式锁；故障时
安全相关能力必须 fail closed，禁止回退到进程内状态。liveness 不受影响，ready 应
报告 `redis=unavailable|timeout`。

- Dashboard：`/d/deepscout-dependencies`。
- 责任人：`backend-oncall`，基础设施协同人为 `sre-oncall`。

## 权限与安全边界

只读诊断使用受控 Redis observer 凭据。生产 failover、扩容、配置修改或 key 操作是
**审批点**。禁止 `FLUSH*`、批量 `DEL`、关闭认证/TLS、扫描或导出业务 value；工单
不得出现 URL、密码、session token、幂等 key 或锁 token。

## 诊断顺序

1. 查看 ready、Redis error rate 的 `operation/error_type` 和故障开始时间。
2. 在托管控制台检查节点健康、连接、内存、eviction、复制和网络事件。
3. 使用受控别名执行只读命令：

   ```powershell
   redis-cli -h <managed-redis-alias> --tls PING
   redis-cli -h <managed-redis-alias> --tls INFO server
   redis-cli -h <managed-redis-alias> --tls INFO clients
   redis-cli -h <managed-redis-alias> --tls INFO memory
   redis-cli -h <managed-redis-alias> --tls INFO replication
   ```

4. 比较 API 与 worker 实例错误，区分单实例连接池问题和集群级故障。
5. 若主要是 `timeout`，检查事件循环/网络；若是 `connection`，检查证书、DNS 和节点。
6. 只使用 key 数量和 TTL 聚合统计，不查看 value，不把原始 key 作为 metric label。

## 安全缓解与审批点

- 保持限流、幂等、锁和登录态 fail closed；可向用户返回稳定的 503/重试提示。
- 暂停依赖分布式锁的新 RTC Start 操作，已运行会话按既有租约处理。
- 连接风暴时先降低应用并发和重连频率；**审批后**再扩容或执行托管 failover。
- 故障节点恢复后逐步恢复流量，避免所有实例同时重建缓存。

禁止为了恢复可用性关闭 TLS、扩大无界重试、切换本地内存锁或删除幂等记录。

## 升级与恢复确认

立即升级条件：疑似锁/幂等失效、跨租户缓存、数据丢失、failover 失败或 15 分钟未
恢复。通知 SRE、后端负责人；疑似凭据泄漏通知安全负责人并轮换凭据。

恢复标准：ready 连续 10 分钟为 200；错误率归零或回归基线；连接/内存稳定；同一
RTC session 不重复 Start；重复请求仍返回相同幂等结果。记录故障窗口、审批、恢复
验证和是否发生缓存重建流量峰值。

## 演练记录

预发阻断 Redis 连接并恢复：记录告警、ready 降级/恢复时间，确认 live=200、没有
进程内安全回退、没有重复 RTC 或重复任务。
