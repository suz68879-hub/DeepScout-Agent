# PostgreSQL 故障 Runbook

## 影响与检测

关联告警：`DatabasePoolSaturation`。典型影响是 API ready 失败、请求超时、任务状态
无法提交或完成；liveness 仍应为 200。不得把外部供应商超时误判为数据库故障。

- Dashboard：`/d/deepscout-dependencies`，同时查看 `/d/deepscout-api`。
- 探针：`GET /health/ready` 中 `postgresql` 或 `migrations` 非 `ready`。
- 责任人：`database-oncall`，应用协同人为 `backend-oncall`。

## 权限与安全边界

只读检查使用受控 application-observer 角色。生产扩容、参数修改、连接终止、切换
主库或执行 migration 都是 **审批点**，必须有事件工单和数据库负责人批准。禁止清表、
删除任务、跳过 Alembic revision、关闭租户过滤或把 DSN/查询参数复制到工单。

## 诊断顺序

1. 确认影响环境和开始时间，检查 ready；不得因 ready=503 自动重启所有实例。
2. 在 Dependencies 面板确认 `db_pool_in_use / db_pool_capacity`、API p95 和错误率。
3. 使用托管数据库控制台检查实例状态、CPU、存储、连接数、复制延迟和维护事件。
4. 通过受控数据库别名执行只读查询：

   ```powershell
   psql --dbname=<managed-service-alias> -c "select now(), current_setting('server_version');"
   psql --dbname=<managed-service-alias> -c "select state, count(*) from pg_stat_activity group by state;"
   psql --dbname=<managed-service-alias> -c "select version_num from alembic_version;"
   ```

5. 在慢查询视图中只记录 query fingerprint、耗时和调用组件；不得复制 bind values。
6. 如果 `migrations=outdated`，对照发布版本和预期 Alembic head，停止继续放量。

## 安全缓解与审批点

- 单个应用实例异常：先从负载均衡摘除该实例，保留现场日志与 trace。
- 查询突增：限流非关键入口或暂停新批处理；不得取消已提交任务的幂等保护。
- 连接池饱和：先降低并发或扩应用间隔；**审批后**才可调整 pool/数据库连接上限。
- migration 落后：冻结发布，按数据库变更流程补执行；不得手工修改 `alembic_version`。
- 托管 PG 故障：按云厂商事件流程切换；切换与回切均需数据库负责人批准。

这里不提供强制终止会话、主从切换或 schema 回退的可复制命令。此类操作必须在
托管控制台受审计执行，并先确认事务、任务租约和 Outbox 一致性。

## 升级与恢复确认

立即升级条件：数据一致性疑似受损、跨租户结果、复制中断、恢复超过 15 分钟或需
主库切换。通知 SRE、数据库负责人、业务负责人；疑似泄漏同时通知安全负责人。

恢复必须同时满足：ready 连续 10 分钟为 200；migration 为 head；池使用率低于
70%；API p95/错误率恢复；pending Job 与 Outbox 持续下降；抽样 trace 可完成
API→PG→MQ。记录时间线、根因、缓解审批人和后续行动。

## 演练记录

非作者在预发断开数据库网络 2 分钟：记录告警时间、定位耗时、恢复耗时和误操作；
确认 live 始终 200、ready 为 503 且恢复后自动回到 200。
