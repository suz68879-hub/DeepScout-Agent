# Phase 2：Redis 与无状态 API

> 状态：未开始  
> 前置阶段：Phase 1 退出门槛全部通过  
> 建议周期：1～2 个迭代  
> 阶段负责人：后端负责人；SRE、安全与测试协作

## 1. 目标与边界

移除生产 API 的进程内共享状态，使任意两个副本对会话、限流、RTC 并发和幂等请求得到一致结果。

| 当前实现 | 阶段目标 |
|---|---|
| `api/auth.py` 使用 `_login_failures`、`_register_attempts` | Redis 原子限流，所有副本共享 |
| `rtc_service.py` 使用 `dict[str, asyncio.Lock]` | 带租约、续租、所有权和 fencing token 的分布式锁 |
| 会话验证依赖数据库直查/本地上下文 | PostgreSQL 为事实来源，Redis 为可失效会话缓存 |
| 关键写请求只靠业务局部守卫 | 统一 24 小时幂等记录 |

非目标：Redis 不保存永久业务事实，不作为 Celery 生产 broker，不实现跨地域锁，不在 Redis 故障时退化为本地字典。

## 2. 输入、输出与锁定决策

输入：PostgreSQL 生产契约、现有限流参数、托管 Redis 7+ 测试实例。  
输出：Redis client、Key/TTL 规范、会话缓存、原子限流、锁、幂等组件和双副本证据。

统一 Key 使用 `deepscout:{APP_ENV}:<domain>:<purpose>:<hashed-id>`；用户输入先 SHA-256，不把用户名、IP、session token 明文写入 key。JSON payload 包含 `schema_version:1`。会话 TTL 不超过 Cookie 剩余寿命；限流窗口沿用当前产品值；RTC 锁租约 30 秒、每 10 秒续租；幂等记录 24 小时。Redis 连接或 Lua 执行失败时，鉴权、锁和幂等操作返回可重试 503 并让 readiness 失败。

## 3. 任务清单

### P2-T01 建立 Redis 连接与生命周期

- **依赖/并行**：Phase 1；最先执行。**规模/角色**：M，后端。
- **预计文件**：`rag_llm_server/pyproject.toml`、`rag_llm_server/uv.lock`、`rag_llm_server/config.py`、`rag_llm_server/services/redis_client.py`、`rag_llm_server/tests/test_redis_client.py`。
- **契约与步骤**：增加 async Redis client；lifespan 创建连接池、PING 和关闭；配置 URL、pool、socket/connect timeout、TLS；日志脱敏 URL。
- **失败处理**：生产 URL 缺失/非法时 fail fast；运行期连接错误转换为统一 `SharedStateUnavailable`，不创建本地 fallback。
- **验证/验收**：连接、超时、池释放、TLS 配置和脱敏测试通过；Redis 停止后返回规定错误。
- **回滚/提交**：未接业务前移除 client；`chore: 增加 Redis 共享状态依赖`。

### P2-T02 固化 Key、TTL 与序列化规范

- **依赖/并行**：P2-T01。**规模/角色**：S，后端/安全。
- **预计文件**：`rag_llm_server/services/redis_keys.py`、`rag_llm_server/tests/test_redis_keys.py`、`rag_llm_server/.env.example`。
- **契约与步骤**：集中生成 auth session、rate limit、RTC lock/fence、idempotency key；所有 key 带环境前缀和哈希；JSON 仅允许白名单字段和版本。
- **失败处理**：TTL≤0、未知 schema 或超长输入拒绝；解析失败删除损坏缓存并按该能力的失败策略处理。
- **验证/验收**：固定向量、环境隔离、无 PII、TTL 边界和 schema 版本测试通过。
- **回滚/提交**：新模块尚未接业务可直接移除；`feat: 定义 Redis 键与过期规范`。

### P2-T03 迁移会话缓存

- **依赖/并行**：P2-T02。**规模/角色**：M，后端。
- **预计文件**：`rag_llm_server/api/auth.py`、`rag_llm_server/services/auth_service.py`、`rag_llm_server/services/session_cache.py`、`rag_llm_server/tests/test_auth_api.py`、`rag_llm_server/tests/test_session_cache.py`。
- **契约与步骤**：PostgreSQL session digest 仍为事实来源；登录成功写最小用户摘要，认证先读 Redis、miss 回源 PG 并按剩余寿命回填；登出同时撤销 PG session 并删除缓存。
- **失败处理**：Redis 不可用时认证请求返回 503，不把缓存失败误报 401；损坏缓存删除后仅允许一次 PG 回源。
- **验证/验收**：hit/miss/过期/登出/密码变更和两个 client 共享测试通过；缓存 payload 不含原始 token/密码摘要。
- **回滚/提交**：feature flag 在迁移窗口内允许回到 PG 直查，禁止本地缓存；`feat: 将认证会话缓存迁移到 Redis`。

#### 检查点 A：P2-T01～P2-T03

- [ ] Redis 生命周期无泄漏，错误类型稳定。
- [ ] key 中无明文 PII/token。
- [ ] 两个应用 client 能共享登录/登出状态。

### P2-T04 迁移登录与注册限流

- **依赖/并行**：检查点 A。**规模/角色**：M，后端/安全。
- **预计文件**：`rag_llm_server/api/auth.py`、`rag_llm_server/services/rate_limit.py`、`rag_llm_server/tests/test_auth_api.py`、`rag_llm_server/tests/test_rate_limit.py`。
- **契约与步骤**：用 Lua 原子执行滑动窗口/计数与过期；登录按 hashed(IP+username)，注册按 hashed IP；成功登录清理该身份失败桶；响应继续使用 429 和 `Retry-After`。
- **失败处理**：Redis 错误返回 503，不放行；脚本版本由 SHA 加载，NOSCRIPT 时只重载一次。
- **验证/验收**：窗口边界、并发突发、成功清零、不同身份隔离和双 client 共享测试通过；删除 `_login_failures/_register_attempts`。
- **回滚/提交**：仅可回退到“Redis 实现的上一版本”，不得恢复进程字典用于生产；`feat: 将认证限流迁移到 Redis`。

### P2-T05 建立分布式锁组件

- **依赖/并行**：P2-T02；可与 P2-T04 并行。**规模/角色**：M，后端。
- **预计文件**：`rag_llm_server/services/distributed_lock.py`、`rag_llm_server/tests/test_distributed_lock.py`、`rag_llm_server/tests/integration/test_redis_lock.py`。
- **契约与步骤**：`SET NX PX` 获取随机 owner token，Lua compare-and-delete 释放、compare-and-pexpire 续租；每个资源使用 `INCR` fencing token；上下文管理器默认 30/10 秒。
- **失败处理**：未获得锁返回明确 busy；失去租约中止后续写入；非 owner 不能续租/释放；时钟使用 Redis TTL，不依赖本机绝对时间。
- **验证/验收**：互斥、续租、过期接管、错误 owner、client 崩溃和 fencing 单调性测试通过。
- **回滚/提交**：业务接入前直接移除；`feat: 建立 Redis 分布式租约锁`。

### P2-T06 迁移 RTC 房间锁

- **依赖/并行**：P2-T05。**规模/角色**：M，后端/RTC。
- **预计文件**：`rag_llm_server/services/rtc_service.py`、`rag_llm_server/api/rtc.py`、`rag_llm_server/tests/test_rtc_api.py`、`rag_llm_server/tests/test_rtc_multitenant.py`、`rag_llm_server/tests/integration/test_rtc_distributed_lock.py`。
- **契约与步骤**：锁资源使用 session_id 哈希；持锁后再次读取 PostgreSQL 状态并写 fencing token；重复 start 返回现有 session 状态，不启动第二个 RTC Agent。
- **失败处理**：获取超时返回 409/可重试错误；续租失败停止启动流程；旧 fencing token 的状态写入被数据库条件更新拒绝。
- **验证/验收**：两个 API 实例同时 start 只调用一次供应商；跨租户不能竞争/读取他人状态；删除 `_session_locks`。
- **回滚/提交**：回退 API 版本前保持 Redis 锁 key 到自然过期；`feat: 使用分布式锁保护 RTC 会话启动`。

#### 检查点 B：P2-T04～P2-T06

- [ ] 双 client 不能绕过认证限流。
- [ ] 锁 owner、续租与 fencing 场景通过故障测试。
- [ ] 生产代码不再含认证字典和 RTC asyncio.Lock。

### P2-T07 增加关键写请求幂等

- **依赖/并行**：P2-T02、P2-T06。**规模/角色**：M，后端。
- **预计文件**：`rag_llm_server/middleware/idempotency.py`、`rag_llm_server/main.py`、`rag_llm_server/api/interview.py`、`rag_llm_server/api/recording.py`、`rag_llm_server/tests/test_idempotency.py`。
- **契约与步骤**：`Idempotency-Key` 为 16～128 ASCII；作用域=user+method+route+body hash；首次占位、成功缓存状态码和安全响应 24h；并发重复等待短窗口后返回已完成结果或 409 processing。
- **失败处理**：同 key 不同 body 返回 409；5xx 不缓存成功结果但释放/短期标记失败；Redis 错误返回 503。
- **验证/验收**：重复、并发、不同用户、body 冲突、过期和异常路径通过；响应带 `Idempotency-Replayed`。
- **回滚/提交**：仅在未依赖该契约的版本回退；`feat: 增加关键写请求幂等保护`。

### P2-T08 固化 Redis 故障与就绪策略

- **依赖/并行**：P2-T03、P2-T04、P2-T06、P2-T07。**规模/角色**：S，后端/SRE。
- **预计文件**：`rag_llm_server/services/redis_client.py`、`rag_llm_server/main.py`、`rag_llm_server/tests/test_app.py`、`rag_llm_server/tests/test_redis_failure_policy.py`。
- **契约与步骤**：区分连接、超时、数据损坏；认证/锁/幂等 fail closed；只读非安全缓存可回源 PG；连续三次健康失败使 readiness false，恢复两次后 ready。
- **失败处理**：不得把 Redis 错误转换为 401/404；响应不暴露 host；liveness 不检查 Redis。
- **验证/验收**：断开、延迟、恢复测试验证 503/readiness 和自动恢复；代码搜索不存在生产内存 fallback。
- **回滚/提交**：回退健康策略不回退共享状态实现；`feat: 固化 Redis 故障降级边界`。

### P2-T09 执行双副本一致性验收

- **依赖/并行**：P2-T08；最后执行。**规模/角色**：M，测试/SRE。
- **预计文件**：`rag_llm_server/tests/integration/test_multi_replica.py`、`scripts/verify_stateless_api.ps1`、`docs/enterprise-evolution/evidence/phase-2-acceptance.md`。
- **契约与步骤**：启动两个 API 进程共享 PG/Redis，通过反向轮询分别发送登录、登出、限流、RTC start 和幂等请求；随机终止一个进程后继续。
- **失败处理**：任何重复 RTC、限流绕过、状态丢失或本地 fallback 均阻止阶段完成；测试资源使用独立环境前缀并清理。
- **验证/验收**：脚本可重复运行；100 轮并发场景无重复业务效果；终止任意副本后登录态和幂等结果可恢复。
- **回滚/提交**：验收脚本可直接回退；`test: 增加双副本无状态一致性验收`。

## 4. 阶段退出门槛

- [ ] 任意 API 副本重启不丢失共享会话和幂等结果。
- [ ] 两个副本下无认证限流绕过、重复 RTC 启动或重复关键写入。
- [ ] Redis 故障时安全能力 fail closed，恢复后 readiness 自动恢复。
- [ ] 生产代码不再使用进程内会话、限流或 RTC 锁。

## 5. 风险与交接

| 风险 | 缓解 |
|---|---|
| 锁租约小于实际操作耗时 | 自动续租、fencing token、数据库条件更新 |
| Redis key 泄漏 PII | 环境前缀与 SHA-256，日志禁止输出原 key 输入 |
| fail closed 扩大 Redis 故障影响 | 托管 HA、短超时、readiness 摘流和明确告警 |
| Redis 数据损坏导致错误授权 | PG 仍为事实来源，schema 校验和撤销机制 |

交给 Phase 3：Redis client/Key 规范、幂等中间件、双副本测试方式、PG 事务和 fencing 约束。
