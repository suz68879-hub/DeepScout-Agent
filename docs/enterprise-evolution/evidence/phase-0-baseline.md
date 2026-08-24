# Phase 0 基线证据

> 记录日期：2026-08-24  
> 分支：`main`  
> 基线提交：`29e5163`

## 环境

- Node.js：本地 v24.14.1；CI 锁定 20
- Python：3.13（`uv` 管理）
- PostgreSQL：本机 `postgresql-x64-16` 服务运行中，Phase 0 不使用
- Git 远端：GitHub 为 `origin`，Gitee 为 `gitee`

## 实测结果

| 检查 | 结果 | 摘要 |
|---|---|---|
| `npm run lint` | 通过 | 无 ESLint error |
| `npm run typecheck` | 通过 | `tsc --noEmit` 成功 |
| `npm run build` | 通过，有既有 warning | Vite 构建成功；存在 CJS API deprecated 与大 chunk warning |
| `npm run test` | 失败 | 25 个测试文件中 24 个通过；89/90，通过率 98.9%；`src/App.test.tsx` 的面试路由懒加载用例停留在 fallback |
| `uv run pytest -q` | 通过，有资源告警 | 272/272 通过；出现 `PytestUnhandledThreadExceptionWarning`，根因为 `aiosqlite` 工作线程在 event loop 关闭后回调 |
| `npm run e2e` | 环境失败 | 3 个用例均在浏览器启动前因 `browserType.launch: spawn EPERM` 失败，不属于产品断言失败 |

## 已确认的修复入口

- 前端红灯：路由冒烟测试应在页面模块边界隔离重型 RTC 页面，仍验证 `/interview/:sessionId` 可达，不增加固定等待。
- 后端告警：测试 fixture 创建的 `SqliteStorage` 未统一回收；应用 graph/checkpointer 关闭顺序还需覆盖启动失败路径。
- 覆盖率：前端尚未安装 Vitest coverage provider；后端已有 `pytest-cov`，但未固化阈值或报告。
- 可观测性：Request ID、JSON 日志和脱敏模块尚不存在。

## 基线约束

- 不通过 warning filter、跳过测试、删除断言或降低质量阈值恢复绿色。
- Playwright 必须在允许启动 Chromium 的本地环境或 GitHub runner 重新验证。
- Phase 0 退出门槛完成前不切换业务数据库。
