# Phase 1：PostgreSQL 与数据迁移

> 状态：已完成
> 前置阶段：Phase 0 退出门槛全部通过  
> 建议周期：2～3 个迭代  
> 阶段负责人：后端负责人；DBA、测试负责人协作

## 1. 目标与边界

将本地 SQLite 业务数据和 LangGraph checkpointer 迁移到 PostgreSQL，建立独立 Alembic 迁移和一次维护窗口切换能力。

| 当前实现 | 阶段目标 |
|---|---|
| `SQLiteStorage` 在启动时建表/补列 | Alembic 独立管理 PostgreSQL Schema |
| `BaseStorage` 由 SQLite 单实现承载 | SQLite/PostgreSQL 通过同一 Repository 契约测试 |
| `AsyncSqliteSaver` 绑定本地文件 | PostgreSQL checkpointer，生命周期由应用管理 |
| SQLite Text-to-SQL 方言与连接 | PostgreSQL 只读账号、超时、行数和 SQL Guard |
| 列表存在无界查询 | cursor/limit 分页、稳定排序和匹配索引 |

非目标：不长期双写，不引入 Redis/队列，不执行破坏性删列，不把 ORM 对象直接作为 API 响应。

## 2. 输入、输出与锁定决策

输入：Phase 0 全绿 SHA、SQLite Schema 与脱敏样本、PostgreSQL 16+ 测试实例、维护窗口。  
输出：PostgreSQL Repository、Alembic revisions、迁移/校验工具、切换和回退证据。

锁定决策：SQLAlchemy Async + psycopg + Alembic；每个请求/任务独立 `AsyncSession`；migration、应用和 Analytics 账号分离；默认一次维护窗口“停写→迁移→校验→切换”；表和索引采用 `expand-contract`；所有时间写 UTC，API 保持现有字符串格式兼容一个发布窗口。

## 3. 任务清单

### P1-T01 增加 PostgreSQL 依赖与配置

- **依赖/并行**：Phase 0；最先执行。**规模/角色**：S，后端。
- **预计文件**：`rag_llm_server/pyproject.toml`、`rag_llm_server/uv.lock`、`rag_llm_server/config.py`、`rag_llm_server/.env.example`。
- **契约与步骤**：增加 SQLAlchemy、psycopg、Alembic 和 PostgreSQL checkpointer；新增 `DATABASE_URL`、pool size/overflow/timeout/recycle，生产缺失 URL 时 fail fast；`DATABASE_PATH` 只在 SQLite 模式读取。
- **失败处理**：URL 非 PostgreSQL、池参数非法或生产使用默认口令时拒绝启动；日志只显示主机和库名，不显示凭据。
- **验证/验收**：配置测试覆盖开发/测试/生产、非法值和脱敏；`uv lock --check`、`uv run pytest tests/test_config.py -q` 通过。
- **回滚/提交**：回退依赖与配置，SQLite 路径仍可运行；`chore: 增加 PostgreSQL 数据访问依赖`。

### P1-T02 建立异步 Engine 与事务边界

- **依赖/并行**：P1-T01。**规模/角色**：M，后端。
- **预计文件**：`rag_llm_server/db/__init__.py`、`rag_llm_server/db/engine.py`、`rag_llm_server/main.py`、`rag_llm_server/tests/test_db_engine.py`。
- **契约与步骤**：lifespan 创建/释放 Engine；提供 `session_scope()`，成功 commit、异常 rollback；开启 pool_pre_ping；禁止全局共享 Session。
- **失败处理**：首次连接失败使 readiness 失败并阻止生产启动；事务异常保留原异常类型，关闭失败写结构化日志。
- **验证/验收**：测试提交、回滚、并发 Session 隔离、连接释放；应用关闭后 pool 无借出连接。
- **回滚/提交**：移除 Engine 初始化，不切换 Storage；`feat: 建立 PostgreSQL 异步事务基础`。

### P1-T03 建立 Alembic 与初始 Schema

- **依赖/并行**：P1-T02。**规模/角色**：M，后端/DBA。
- **预计文件**：`rag_llm_server/alembic.ini`、`rag_llm_server/db/models.py`、`rag_llm_server/db/migrations/env.py`、`rag_llm_server/db/migrations/script.py.mako`、首个 revision。
- **契约与步骤**：映射现有用户、会话摘要、简历、面试会话、消息、报告、录音；补齐 PK/FK/唯一约束和 owner 索引；revision 只做兼容性 create/expand。
- **失败处理**：禁止在应用启动时 `create_all`；revision 失败即停止，不自动重跑部分 DDL；敏感生产 URL 不写入 alembic.ini。
- **验证/验收**：空库 `upgrade head` 成功，`downgrade base` 仅在测试库成功，二次 upgrade 幂等；`uv run alembic check` 无漂移。
- **回滚/提交**：测试环境 downgrade；生产仅删除尚未切流的新表；`feat: 建立 PostgreSQL 初始数据库迁移`。

#### 检查点 A：P1-T01～P1-T03

- [ ] 空 PostgreSQL 可通过 Alembic 完整创建和验证。
- [ ] Engine 生命周期和事务回滚有自动化测试。
- [ ] 应用尚未切换，SQLite 基线仍全绿。

### P1-T04 实现用户与鉴权 Repository

- **依赖/并行**：检查点 A。**规模/角色**：M，后端。
- **预计文件**：`rag_llm_server/services/storage/postgres.py`、`rag_llm_server/services/storage/base.py`、`rag_llm_server/tests/storage_contract/test_auth_contract.py`、`rag_llm_server/tests/conftest.py`。
- **契约与步骤**：实现用户创建/查询、密码摘要和 session digest 操作；数据库唯一约束处理并发注册；所有查询强制 user/tenant 边界。
- **失败处理**：唯一冲突转换为既有业务错误；数据库异常不返回 SQL/DSN；事务失败必须 rollback。
- **验证/验收**：同一 contract suite 对 SQLite/PostgreSQL 均通过；并发同名注册只成功一次；跨用户读取为空。
- **回滚/提交**：Storage 选择仍保持 SQLite；`feat: 实现 PostgreSQL 鉴权数据仓储`。

### P1-T05 实现简历、面试会话与消息 Repository

- **依赖/并行**：检查点 A；可与 P1-T04 并行。**规模/角色**：M，后端。
- **预计文件**：`rag_llm_server/services/storage/postgres.py`、`rag_llm_server/services/storage/base.py`、`rag_llm_server/tests/storage_contract/test_interview_contract.py`、`rag_llm_server/tests/storage_contract/test_resume_contract.py`。
- **契约与步骤**：实现 owner-scoped CRUD、会话状态更新、消息序号与稳定排序；同一 session 的消息序号建立唯一约束。
- **失败处理**：跨租户资源统一按不存在处理；并发更新用条件更新/版本字段防止旧状态覆盖新状态。
- **验证/验收**：双实现 contract 测试覆盖 CRUD、顺序、并发状态更新和租户隔离。
- **回滚/提交**：不切换生产 Storage；`feat: 实现 PostgreSQL 面试数据仓储`。

### P1-T06 实现报告与录音 Repository

- **依赖/并行**：检查点 A；可与 P1-T04/P1-T05 并行。**规模/角色**：M，后端。
- **预计文件**：`rag_llm_server/services/storage/postgres.py`、`rag_llm_server/services/storage/base.py`、`rag_llm_server/tests/storage_contract/test_report_contract.py`、`rag_llm_server/tests/storage_contract/test_recording_contract.py`。
- **契约与步骤**：实现报告/录音创建、状态更新、内部恢复查询和 owner 校验；为 Phase 3 保留独立任务 Repository 接口位置，但本阶段不创建任务表。
- **失败处理**：报告和会话、录音和报告 owner 不一致时事务拒绝；状态更新使用允许字段白名单。
- **验证/验收**：双实现 contract 测试覆盖关联一致性、重复报告约束、processing 列表顺序和跨租户隔离。
- **回滚/提交**：不切换生产 Storage；`feat: 实现 PostgreSQL 报告录音仓储`。

#### 检查点 B：P1-T04～P1-T06

- [ ] 现有业务实体 contract suite 在两种数据库上结果一致。
- [ ] 所有 owner-scoped 查询有跨租户负向测试。
- [ ] 并发唯一约束没有依赖进程锁。

### P1-T07 建立 Storage 选择器与集成测试

- **依赖/并行**：检查点 B。**规模/角色**：M，后端/测试。
- **预计文件**：`rag_llm_server/services/storage/__init__.py`、`rag_llm_server/config.py`、`rag_llm_server/tests/conftest.py`、`rag_llm_server/tests/test_storage_backend.py`。
- **契约与步骤**：`STORAGE_BACKEND=sqlite|postgres`；生产仅允许 postgres；测试参数化运行两个 backend；SQLite 完成迁移后改为只读兼容。
- **失败处理**：未知 backend 拒绝启动；测试 PostgreSQL 不可用时 integration job 失败而非跳过。
- **验证/验收**：两种 backend 启动测试通过；production+sqlite 配置明确失败；全量后端测试在 PostgreSQL job 中通过。
- **回滚/提交**：默认保持 sqlite 直至切换任务；`feat: 增加可验证的数据仓储选择器`。

### P1-T08 迁移 LangGraph Checkpointer

- **依赖/并行**：P1-T07。**规模/角色**：M，后端。
- **预计文件**：`rag_llm_server/agents/graph.py`、`rag_llm_server/db/checkpointer.py`、`rag_llm_server/main.py`、`rag_llm_server/tests/test_graph.py`、`rag_llm_server/tests/test_lifecycle.py`。
- **契约与步骤**：PostgreSQL checkpointer 使用独立连接池和 lifespan；thread_id 继续使用 session_id；首次部署的 setup 由 migration/一次性初始化执行，不由每个副本竞争执行。
- **失败处理**：checkpointer 不可用使 readiness 失败；禁止静默回退 SQLite；关闭顺序为业务任务→graph/checkpointer→数据库池。
- **验证/验收**：两个应用实例读写同一 checkpoint；重启后状态恢复；并发 session 不串写；关闭无资源 warning。
- **回滚/提交**：在未切生产前将 backend 切回 sqlite；`feat: 将 LangGraph 检查点迁移到 PostgreSQL`。

### P1-T09 迁移 Text-to-SQL 到 PostgreSQL 只读连接

- **依赖/并行**：P1-T07；可与 P1-T08 并行。**规模/角色**：M，后端/安全。
- **预计文件**：`rag_llm_server/mcp/sql_server.py`、`rag_llm_server/mcp/sqlite_server.py`、`rag_llm_server/config.py`、`rag_llm_server/tests/test_sql_guard.py`、`rag_llm_server/tests/test_text2sql.py`。
- **契约与步骤**：使用独立只读 DSN；仅允许单条 SELECT/CTE；事务设为 read only，statement timeout 5 秒，最大返回 500 行；展示 schema 白名单。
- **失败处理**：解析不确定、注释绕过、多语句、DML/DDL、系统表、超时均拒绝并记录低敏事件；不返回数据库内部错误。
- **验证/验收**：PostgreSQL 方言用例和绕过用例通过；只读账号实际无法写入；500 行截断有显式元数据。
- **回滚/提交**：保留旧模块仅用于 SQLite 测试，生产配置回退前需恢复旧数据库；`feat: 建立 PostgreSQL 只读分析查询`。

#### 检查点 C：P1-T07～P1-T09

- [ ] PostgreSQL 模式下应用、Graph、Analytics 均可启动。
- [ ] 不存在生产 SQLite 静默回退。
- [ ] Text-to-SQL 只读与超时由数据库权限和应用双重保证。

### P1-T10 为列表 API 增加分页和索引

- **依赖/并行**：检查点 B。**规模/角色**：M，后端/前端。
- **预计文件**：`rag_llm_server/api/reports.py`、`rag_llm_server/api/resume.py`、`rag_llm_server/api/analytics.py`、`rag_llm_server/services/storage/base.py`、对应 Alembic revision。
- **契约与步骤**：列表统一接受 `limit`（默认 20，范围 1～100）和 opaque `cursor`；响应为 `items,next_cursor`；按 `created_at DESC,id DESC` 稳定排序并建立组合索引；旧的裸数组响应只保留一个发布窗口的兼容适配。
- **失败处理**：非法 cursor 返回 400；未知/过期 cursor 不回退首页；分页期间新增数据不得产生重复项。
- **验证/验收**：边界、稳定排序、跨页无重/无漏、索引查询计划测试通过；前端调用契约测试同步通过。
- **回滚/提交**：兼容窗口内可恢复旧响应；索引在后续 contract migration 删除；`feat: 为业务列表增加游标分页`。

### P1-T11 编写可重复执行的数据迁移工具

- **依赖/并行**：P1-T07、P1-T08。**规模/角色**：M，后端/DBA。
- **预计文件**：`rag_llm_server/scripts/migrate_sqlite_to_postgres.py`、`rag_llm_server/scripts/migration_common.py`、`rag_llm_server/tests/test_data_migration.py`。
- **契约与步骤**：按 FK 顺序批量读取；使用原 ID、UTC 时间和 upsert；参数支持 source、target、batch-size、dry-run、resume-from；每批独立事务并输出不含 PII 的计数。
- **失败处理**：单批失败 rollback 并记录表/主键哈希；不跳过坏行；重跑不生成重复记录。
- **验证/验收**：脱敏 fixture 首次、重复和中断续跑结果一致；dry-run 不写目标库；源库保持只读。
- **回滚/提交**：切流前清空专用测试目标库；生产回退依靠未修改的 SQLite；`feat: 增加 SQLite 到 PostgreSQL 迁移工具`。

### P1-T12 建立迁移校验与回退流程

- **依赖/并行**：P1-T11。**规模/角色**：M，后端/DBA。
- **预计文件**：`rag_llm_server/scripts/verify_data_migration.py`、`rag_llm_server/tests/test_migration_verifier.py`、`deploy/runbooks/database-cutover.md`。
- **契约与步骤**：逐表校验 count、PK 集合、关联完整性、关键字段规范化哈希和对象 key；报告只含统计与哈希；定义停写、备份、迁移、校验、切换、冒烟、回退步骤。
- **失败处理**：任一 count/哈希/关联不一致即阻止切换；切换后出现写入则优先前向修复，回退前必须处理 PostgreSQL 新数据。
- **验证/验收**：故意删除/篡改目标行时校验器非零退出；完整样本 100% 通过；Runbook 有明确负责人和决策点。
- **回滚/提交**：工具本身可直接回退；`feat: 增加数据库迁移校验与切换手册`。

### P1-T13 执行两次脱敏演练与阶段验收

- **依赖/并行**：P1-T09～P1-T12；最后执行。**规模/角色**：M，DBA/测试/发布。
- **预计文件**：`docs/enterprise-evolution/evidence/phase-1-rehearsal.md`、`docs/enterprise-evolution/evidence/phase-1-acceptance.md`。
- **契约与步骤**：第一次演练发现问题，第二次使用冻结工具和 Runbook；记录数据规模、耗时、校验结果、切换/回退时长和版本 SHA；生产执行另行审批。
- **失败处理**：两次结果不一致或超过维护窗口则阶段不通过；不得以抽样替代结构化数据全量校验。
- **验证/验收**：两次脱敏迁移均 100% 校验，第二次无人工修库；Repository contract、API、Graph、Text-to-SQL 测试全绿。
- **回滚/提交**：演练库销毁按环境流程执行，证据保留；`docs: 记录 PostgreSQL 迁移演练结果`。

## 4. 阶段退出门槛

- [x] SQLite/PostgreSQL Repository contract suite 全部通过。
- [x] Alembic 可从空库升级，Schema 无漂移，应用启动不执行 DDL。
- [x] 两次脱敏迁移 100% 校验且回退演练通过。
- [x] PostgreSQL 模式下全量后端/E2E 通过。
- [x] 生产目标配置不依赖本地 SQLite，Analytics 使用独立只读账号。

## 5. 风险与交接

| 风险 | 缓解 |
|---|---|
| SQLite 历史脏数据 | 迁移前 fail-fast 校验，禁止静默修值 |
| ORM 语义与 SQLite 不同 | 同一 contract suite 参数化验证 |
| 迁移后代码回滚不兼容 | expand-contract，至少保留一个稳定版本 |
| Text-to-SQL 获得写权限 | 独立只读账号、只读事务和 Guard 三层保护 |

交给 Phase 2：`DATABASE_URL`/连接预算、Repository 契约、迁移 head revision、分页契约、PostgreSQL 集成测试环境。
