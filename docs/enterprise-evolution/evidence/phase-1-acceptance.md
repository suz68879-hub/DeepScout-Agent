# Phase 1 验收证据

> 日期：2026-08-25
> 状态：已完成
> 分支：`codex/phase1-postgresql`
> Alembic head：`20260825_0003`

## 功能交付

- PostgreSQL AsyncEngine、独立 AsyncSession 和 Service 事务边界；生产配置缺失或非法时 fail fast。
- 七张业务表的 UUID/timestamptz/JSONB/FK/唯一约束、租户组合索引和乐观并发版本字段。
- SQLite/PostgreSQL 同一 Repository contract，storage selector 在生产拒绝 SQLite 与未知 backend。
- 独立 PostgreSQL LangGraph checkpointer 池和 owner 验证；独立 analytics-readonly 连接、租户视图和 SQLGlot PostgreSQL 只读 Guard。
- 报告列表 `limit=20`、1～100、opaque cursor、稳定 keyset 排序、非法/未知 cursor 400，以及一个发布窗口的 `legacy=true` 适配。
- 可重复迁移器、全量校验器、无 PII 基准生成器和切换/回退 Runbook。
- GitHub `backend-coverage` 使用 runner PostgreSQL、运行时随机且 mask 的三角色密码，并以 PostgreSQL 模式执行迁移和全量覆盖率。

## 本地验证

| 门槛 | 结果 |
|---|---|
| Alembic `current/check/downgrade base/upgrade head/check` | 通过；head `20260825_0003`，无 Schema 漂移 |
| 后端全量 + coverage | 353 passed；82.39%，阈值 81% |
| 后端全量（`APP_ENV=test`、`STORAGE_BACKEND=postgres`） | 353 passed |
| 迁移连接清理（`ResourceWarning=error`） | 8 passed |
| 前端 lint / typecheck / build | 全部通过 |
| 前端 coverage | 91 passed；coverage thresholds 通过 |
| Playwright Chromium E2E | 3 passed |
| SQLite/PostgreSQL contract | 全量后端测试内通过 |
| Text-to-SQL 安全与 analytics 实际写拒绝 | 全量后端测试内通过 |
| 两次无 PII 迁移与回退 | 850/850，100% 全量校验，详见演练证据 |

首次 E2E 在沙箱内因浏览器进程 `spawn EPERM` 未进入页面断言；同一命令获准在沙箱外运行后 3/3 通过。首次并行执行 Vite build 与 coverage 时 Windows 返回一次 `EBADF realpath`；按 CI 串行方式独立复跑后 91/91 通过，不涉及代码修改。

## 提交链

| SHA | 任务 |
|---|---|
| `62a26dd` | P1-T01 PostgreSQL 依赖与配置 |
| `143e024` | P1-T02 异步事务基础 |
| `609c69c` | P1-T03 初始 Schema |
| `3dcd03b` | P1-T04 鉴权 Repository |
| `9106537` | P1-T05 面试 Repository |
| `a019050` | P1-T06 报告录音 Repository |
| `968ad91` | P1-T07 Storage selector |
| `43a737b` | P1-T08 PostgreSQL checkpointer |
| `df475ec` | P1-T09 PostgreSQL 只读分析 |
| `234fe56` | P1-T10 游标分页 |
| `f3e832b` | P1-T11 迁移工具 |
| `e9320fc` | P1-T12 校验器与 Runbook |
| `c7351fd` | P1-T13 无 PII 基准 |
| `f17fd2f` | 演练连接清理修复 |
| `ba350e0` | PostgreSQL CI 门槛 |
| `da1a225` | CI LangGraph checkpointer 初始化修复与回归测试 |

## GitHub 验证

- [PR #3](https://github.com/suz68879-hub/DeepScout-Agent/pull/3) 已创建，目标分支为 `main`。
- 提交 `da1a225` 的 [GitHub Actions run 32819359805](https://github.com/suz68879-hub/DeepScout-Agent/actions/runs/32819359805) 中，`frontend-quality`、`frontend-coverage`、`backend-coverage`、`e2e` 四项 required checks 全绿。
- 首次 `backend-coverage` 暴露全新 CI 数据库未初始化 LangGraph `checkpoint_*` 表；修复后增加 CI 初始化顺序回归测试，本地 PostgreSQL CI 等价命令为 353 passed、覆盖率 82.39%。
- PR 合并到 `main` 前不启动 Phase 2 开发。
