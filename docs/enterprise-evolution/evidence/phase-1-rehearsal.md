# Phase 1 PostgreSQL 数据迁移演练证据

> 日期：2026-08-25
> 环境：本机 PostgreSQL 16，专用数据库 `deepscout_test`
> 数据：确定性生成的无 PII SQLite 基准；未读取、复制或迁移 `rag_llm_server/data/interview.db`
> 结果：两次全量迁移与回退演练通过

## 冻结版本与数据规模

- 迁移器：`f3e832b`（SQLite→PostgreSQL，批事务、upsert、dry-run、resume-from）。
- 校验器与 Runbook：`e9320fc`。
- 两次演练使用的基准生成器：`c7351fd`；两份源文件 SHA-256 均为 `043c7dfe947390a80bdde7960f2db8e3787935fa40cf637f20a0e00d90c80b78`，大小均为 278528 bytes。
- 每次共 850 行：用户 50、鉴权会话 50、简历 50、面试会话 100、消息 400、报告 100、录音 100。
- 每次演练前均在同一连接内断言数据库为 `deepscout_test`、角色为 `deepscout_migration`，并确认七张业务表为空。

## 演练结果

| 项目 | 第一次 | 第二次 |
|---|---:|---:|
| 生成基准 | 1349 ms | 1362 ms |
| dry-run | 1227 ms | 1274 ms |
| dry-run 源文件哈希不变 | 是 | 是 |
| 首次迁移 | 2025 ms | 2171 ms |
| 全量校验 | 1773 ms | 1744 ms |
| count / PK / 规范化行哈希 | 100% | 100% |
| 报告与录音对象 key 哈希 | 100% | 100% |
| 源/目标 FK 错误 | 0 / 0 | 0 / 0 |
| 源/目标 owner 错误 | 0 / 0 | 0 / 0 |
| 回退清空专用目标 | 230 ms | 180 ms |
| 人工修库 | 无 | 无 |

第一次演练另行重复执行 migration（1913 ms）并再次全量校验（1707 ms），各表计数和哈希保持一致，证明 upsert 重跑不生成重复记录。中断续跑由自动化契约以每批 1 行提交后恢复验证；dry-run、首次、重复、续跑与源库只读共 3 项迁移测试通过。

## 演练发现与处置

正式迁移前的工具验证发现 Windows psycopg 不能运行在 Proactor event loop，且脚本直接执行时缺少包根路径；已在 `f3e832b` 固定 Selector CLI 入口并验证直接执行。阶段全量覆盖率审计随后发现基准 fixture 的 SQLite 事务上下文不会自动关闭连接；`f17fd2f` 增加显式关闭，并以 `ResourceWarning=error` 复跑 8 项迁移相关测试全部通过。迁移器与校验器在两次演练之间没有修改。

## 回退结论

两次均在 canary 前回退：migration 角色执行专用业务表清空与 identity reset，随后逐表 count 为 0。没有生产切换、生产写入或现有 SQLite 数据操作。若 canary 后 PostgreSQL 已产生新写入，Runbook 明确禁止直接回退，必须先处置新增数据或选择前向修复。
