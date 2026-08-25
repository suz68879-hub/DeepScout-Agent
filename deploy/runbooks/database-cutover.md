# SQLite → PostgreSQL 切换与回退手册

## 适用范围与职责

本手册用于已审批环境的单次数据库切换。Phase 1 仅允许在生成的无 PII 基准库和专用测试目标库演练；生产执行必须另行审批。

| 角色 | 职责 |
|---|---|
| 发布负责人 | 宣布停写、记录时间、执行继续/回退决策 |
| 应用负责人 | 停止后台任务和 API 写入，执行 canary 冒烟 |
| DBA | 备份、确认空目标库、迁移、校验和数据库回退 |
| 验证负责人 | 核对校验报告、required checks 和验收证据 |

所有 DSN 和密码只能来自未跟踪的环境变量。命令必须使用 `--target-env`，不得把 DSN 展开到命令行、日志或证据文件。

## 前置门槛

发布负责人逐项确认后才能开始：

- 当前提交 SHA、Alembic head 和冻结脚本 SHA 已记录。
- `MIGRATION_DATABASE_URL` 指向 migration 角色，目标是本次专用空库。
- `DATABASE_URL` 指向 application 角色，`ANALYTICS_DATABASE_URL` 指向 analytics-readonly 角色。
- 维护窗口、负责人、备份位置和回退截止时间已经确认。
- PostgreSQL 已完成 `upgrade head`，目标业务表 count 全为 0。
- 后台任务、Graph/checkpointer 和 API 的停止顺序已经演练。

任一项不满足即停止，不开始迁移。

## 切换步骤

1. 发布负责人宣布停写并记录 `T0`；应用负责人依次停止后台业务任务、Graph/checkpointer、API 写入入口。
2. DBA 确认 SQLite 无活动写连接，创建只读备份并记录文件大小及 SHA-256。原文件不得移动、修改或作为演练源。
3. DBA 对备份执行 dry-run：

   ```text
   uv run python scripts/migrate_sqlite_to_postgres.py --source <sqlite-backup> --target-env MIGRATION_DATABASE_URL --batch-size 500 --dry-run
   ```

4. dry-run 计数经验证负责人确认后，DBA 执行迁移。每批成功后记录安全的 `next_resume_from`；中断时使用最后一个已提交 token 续跑：

   ```text
   uv run python scripts/migrate_sqlite_to_postgres.py --source <sqlite-backup> --target-env MIGRATION_DATABASE_URL --batch-size 500
   uv run python scripts/migrate_sqlite_to_postgres.py --source <sqlite-backup> --target-env MIGRATION_DATABASE_URL --batch-size 500 --resume-from <token>
   ```

5. DBA 执行全量校验；退出码非 0 或任一表 `ok=false` 均阻止切换：

   ```text
   uv run python scripts/verify_data_migration.py --source <sqlite-backup> --target-env MIGRATION_DATABASE_URL
   ```

6. 验证负责人确认 count、PK、规范化行、对象 key、FK 和 owner 全部一致。发布负责人记录 `T1` 并决定是否进入 canary。
7. 应用负责人仅启动一个 PostgreSQL canary 实例，依次验证健康检查、登录、简历读取、面试恢复、报告分页、录音元数据和只读 Analytics；敏感内容不得写入证据。
8. canary 全绿后，发布负责人批准逐步恢复流量和后台任务，并记录 `T2`。先启动 API，再启动 Graph/checkpointer，最后启动后台业务任务。

## 失败与回退决策

- `T1` 前失败：保持停写，清空本次专用目标库后修复工具；SQLite 原库和备份保持不变。
- 校验不一致：禁止 canary，不得人工修目标行；修复冻结脚本后从空目标库重演。
- canary 失败且 PostgreSQL 尚无新写入：停止 canary，将配置恢复到 SQLite，按 API → Graph/checkpointer → 后台任务顺序恢复服务。
- PostgreSQL 已产生新写入：不得直接回退。先保持停写，由 DBA 导出并处置新增数据；发布负责人和数据负责人共同批准后才能回退，否则优先前向修复。
- 超过维护窗口：发布负责人执行上述适用的回退分支，不得跳过校验强行切换。

## 完成证据

只保留提交 SHA、Alembic head、开始/结束时间、各表计数与哈希、校验退出码、canary 结果、切换/回退耗时和决策人。不得保存 DSN、密码、token、用户名、简历、消息、报告或对象正文。
