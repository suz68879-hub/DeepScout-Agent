# Phase 0 验收证据

> 验收日期：2026-08-24  
> 最终验收 HEAD：`48f10a4`
> 状态：已完成

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

## GitHub 门禁验收

- `main` 已推送至 `48f10a439ece59e989d230d13c88e803f7909373`，远端与本地 SHA 一致。
- 仓库管理员确认 `frontend-quality`、`frontend-coverage`、`backend-coverage`、`e2e` 均已配置为 `main` required checks，最终运行全绿。
- [验证 PR #1](https://github.com/suz68879-hub/DeepScout-Agent/pull/1) 使用提交 `8b190c9` 主动令 `frontend-quality` 失败；GitHub 将该检查标记为必需并禁用合并按钮。
- 验证 PR 已关闭，远端与本地临时分支 `codex/phase0-required-check-verification` 均已删除；未向 `main` 合入预期失败提交。

Phase 0 本地与 GitHub 退出门槛全部通过，可以进入 Phase 1。
