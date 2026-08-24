# Phase 0 验收证据

> 验收日期：2026-08-24  
> 本地验收 HEAD：`e47c827`  
> 状态：已阻塞（仅剩 GitHub 外部门禁）

## 本地验收

| 检查 | 结果 |
|---|---|
| `npm run lint` | 通过 |
| `npm run typecheck` | 通过 |
| `npm run test:coverage` | 90/90 通过；statements/lines 45.98%、branches 74.16%、functions 45.83%，阈值生效 |
| `npm run build` | 通过；保留既有 Vite CJS 与大 chunk warning |
| `npm run e2e` | Chromium 3/3 通过 |
| `uv run pytest --cov=. --cov-report=term-missing --cov-report=xml:coverage.xml -q` | 279/279 通过；产品代码覆盖率 81.90%，阈值 81% |

后端验收不存在 `PytestUnhandledThreadExceptionWarning` 或未关闭数据库连接告警。剩余 warning 为第三方依赖弃用提示，以及受限工作区无法写 `.pytest_cache` 的环境提示。

## 已完成能力

- Request ID 覆盖透传、生成、非法输入、异常响应和 20 并发请求隔离。
- JSON 日志包含规定关联字段，密码、Authorization、token、邮箱、手机号和录音 URL 样本均被脱敏。
- CI 已拆分为 `frontend-quality`、`frontend-coverage`、`backend-coverage`、`e2e`，覆盖率报告作为 artifact 上传。
- GitHub 为 `origin`，Gitee 为 `gitee`；本地 `main` 未改写历史、未 force push。

## 外部阻塞

两次执行 `git push origin main` 均在连接 GitHub 前失败：

1. `Recv failure: Connection was reset`
2. `Failed to connect to github.com port 443`

因此以下门槛尚未完成：

- [ ] 推送本地 Phase 0 提交到 GitHub `main`。
- [ ] 核验四个 Actions checks 全绿。
- [ ] 将四个 checks 配置为 `main` required checks，并验证失败检查可阻止合并。

根据阶段索引，阻塞解除并补齐远端运行链接、commit SHA 前，不开始 Phase 1。
