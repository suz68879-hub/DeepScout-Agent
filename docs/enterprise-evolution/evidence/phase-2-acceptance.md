# Phase 2 验收证据

> 验收日期：2026-08-25
> 分支：`codex/phase2-redis-stateless`
> 结论：通过

## 1. 验收边界

- 两个独立 Uvicorn API 进程共享测试 PostgreSQL 与本地 Redis 7.2.3。
- Redis 使用专用非 0 DB（DB 15）；测试仅删除本次运行可推导的精确 key，不执行 `FLUSHDB`。
- PostgreSQL 仅创建唯一前缀测试用户；结束后删除该用户及对应 LangGraph checkpoint。
- RTC 外部供应商调用在验收进程中替换为 Redis 原子计数桩，验证分布式锁与 fencing，不访问真实供应商。
- 未读取、复制或迁移现有 `rag_llm_server/data/interview.db`，未执行生产切换。

## 2. 任务提交

| 任务 | 提交 | 结果 |
|---|---|---|
| P2-T01 Redis 生命周期 | `025dba2`、`96a809d` | 通过 |
| P2-T02 Key/TTL/序列化 | `9f813e4` | 通过 |
| P2-T03 认证会话缓存 | `93e24fc` | 通过 |
| P2-T04 认证限流 | `975ebf1` | 通过 |
| P2-T05 分布式租约锁 | `b18769b` | 通过 |
| P2-T06 RTC 房间锁 | `7d33116` | 通过 |
| P2-T07 关键写幂等 | `b8868a0` | 通过 |
| P2-T08 Redis 故障边界 | `5662654` | 通过 |
| P2-T09 双副本一致性验收 | 本提交 | 通过 |

## 3. 双副本验收结果

执行入口：

```powershell
.\scripts\verify_stateless_api.ps1 -Rounds 100 -RedisUrl redis://127.0.0.1:6379/15
```

连续执行两次，结果分别为：

| 演练 | pytest | 耗时 | 结果 |
|---|---:|---:|---|
| 第一次 | 7 passed | 13.39s | 通过 |
| 第二次 | 7 passed | 12.34s | 通过 |

每次演练均验证：

- 注册后在另一副本读取登录态；跨副本登出立即失效；重新登录后另一副本可恢复会话。
- 两副本并发累计 6 次错误登录恰好得到 5 次 `401` 和 1 次 `429`，限流绕过数为 0。
- 100 个并发、同 key 同 body 的关键写请求只创建 1 条 interview session，重放结果一致。
- 两副本同时执行 RTC start，供应商计数始终为 1；fencing 状态落入 PostgreSQL。
- 随机终止一个副本后，存活副本继续读取登录态、重放幂等响应并返回 RTC 已启动状态。
- 任一断言或清理失败均使脚本非零退出；脚本使用唯一 repo-local `--basetemp` 并确定性回收子进程。

## 4. 故障与质量门禁

- Redis 连接、超时、损坏分类测试通过；连续 3 次失败使 readiness false，连续 2 次成功恢复。
- liveness 不访问 Redis；认证、限流、锁和幂等 Redis 故障均 fail closed 为脱敏 `503`。
- Phase 2 最终后端门禁：`449 passed, 1 skipped`，覆盖率 `84.57%`（阈值 `81%`）；跳过项为已由独立 PowerShell 入口连续验证两次的双进程测试。
- 生产代码扫描未发现认证字典、RTC `asyncio.Lock` 或共享状态内存 fallback。

## 5. Phase 2 退出门槛

- [x] 任意 API 副本终止后不丢失共享会话和幂等结果。
- [x] 两个副本下无认证限流绕过、重复 RTC 启动或重复关键写入。
- [x] Redis 故障时安全能力 fail closed，恢复后 readiness 自动恢复。
- [x] 生产代码不再使用进程内会话、限流或 RTC 锁。
