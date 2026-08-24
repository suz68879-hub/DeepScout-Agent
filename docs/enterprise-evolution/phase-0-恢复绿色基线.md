# Phase 0：恢复绿色基线

> 状态：已阻塞（本地门槛全绿；GitHub 网络不可达，无法推送并验证 required checks）  
> 前置阶段：无  
> 建议周期：1 个迭代  
> 阶段负责人：技术负责人；前端、后端与 CI 负责人协作

## 1. 目标与边界

目标是先建立可信基线：修复已知红灯和资源泄漏，加入最小可用 Request ID、JSON 日志与覆盖率门禁，并把 CI 主阵地迁移到 GitHub。完成前不得开始数据库或基础设施迁移。

| 当前实现 | 阶段目标 |
|---|---|
| Vitest 现有基线为 89/90，`src/App.test.tsx` 路由用例失败 | 不删断言、不加任意等待，恢复全绿 |
| pytest 通过但有 `aiosqlite` event loop 关闭警告 | 所有异步资源在 lifespan/fixture 中确定性关闭 |
| 普通文本日志，缺少统一关联字段 | 每个 HTTP 请求都有 Request ID 和 JSON 日志 |
| CI 无覆盖率阈值 | 记录现状、禁止下降；新增代码行覆盖率不低于 90% |
| 本地 remote 指向 Gitee | GitHub `main` 成为主线，Gitee 只读保留 |

非目标：不引入 PostgreSQL、Redis、RabbitMQ、OpenTelemetry 或 Docker；不修改业务流程；不以降低测试质量换取绿色状态。

## 2. 输入、输出与锁定决策

输入：当前代码和测试、`.github/workflows/ci.yml`、Git 历史、GitHub 目标仓库权限。  
输出：全绿基线、覆盖率报告、Request ID/JSON 日志规范、可运行的 GitHub required checks。

锁定决策：

- `X-Request-ID` 仅接受 1～128 个可打印 ASCII 字符；非法或缺失时生成 UUID v4，并始终写回响应头。
- 日志至少包含 `timestamp`、`level`、`service`、`environment`、`event`、`request_id`、`trace_id`、`error_code`；缺失的 trace/error 字段写 `null`。
- 密码、Cookie、Authorization、token、手机号、邮箱、简历正文、录音地址不进入日志。
- 覆盖率先记录真实基线并向下取整为初始阈值；阈值只能通过独立评审提交提高。前后端新增/修改行覆盖率目标为 90%。
- GitHub 迁移不改写提交历史；`master` 推送为 `main`，标签原样保留。

## 3. 任务清单

### P0-T01 固化测试、构建和告警基线

- **依赖/并行**：无；必须最先执行。**规模/角色**：S，测试负责人。
- **预计文件**：`docs/enterprise-evolution/evidence/phase-0-baseline.md`（新建，记录命令、版本、结果摘要，不提交大体积日志）。
- **契约与步骤**：使用 Node 20、Python 3.13 和锁文件安装；依次执行通用验证命令，记录失败用例、warning 类别、耗时和当前覆盖率，不修改测试。
- **失败处理**：环境安装失败与产品测试失败分开记录；不得把环境错误归类为测试缺陷。
- **验证/验收**：基线证据包含前端、后端、E2E、构建五类结果，可复现 `App.test.tsx` 失败和后端线程 warning。
- **回滚/提交**：删除证据文件即可回滚；`docs: 记录企业级演进测试基线`。

### P0-T02 修复历史记录路由测试

- **依赖/并行**：依赖 P0-T01；可与 P0-T03 并行。**规模/角色**：S，前端。
- **预计文件**：`src/App.test.tsx`、`src/App.tsx`、`src/pages/History/index.tsx`（只修改根因涉及的文件）。
- **契约与步骤**：核对现有页面标题和导航文案；产品页面以“面试历史”为标题、“历史记录”为导航项；用 Testing Library 等待真实路由渲染，不增加固定 sleep。
- **失败处理**：如果路由未挂载则修路由；如果仅测试期望过时则只修期望，不同时改产品文案。
- **验证/验收**：`npm run test -- src/App.test.tsx` 通过；点击导航后 URL 与标题均正确；全量 `npm run test` 不回归。
- **回滚/提交**：回退该单一提交；`fix: 修复历史记录路由测试`。

### P0-T03 修复 aiosqlite 生命周期告警

- **依赖/并行**：依赖 P0-T01；可与 P0-T02 并行。**规模/角色**：M，后端。
- **预计文件**：`rag_llm_server/agents/graph.py`、`rag_llm_server/main.py`、`rag_llm_server/tests/conftest.py`、`rag_llm_server/tests/test_lifecycle.py`。
- **契约与步骤**：定位全局 `_checkpoint_conn` 与 fixture 的创建/关闭顺序；由 FastAPI lifespan 持有连接，关闭任务后再关闭 checkpointer；测试 fixture 使用相同顺序并等待线程退出。
- **失败处理**：禁止用 warning filter 隐藏 `PytestUnhandledThreadExceptionWarning`；关闭失败必须让测试失败并输出资源类型。
- **验证/验收**：连续运行 `uv run pytest -q` 三次均通过且无 event loop closed/unhandled thread warning；lifespan 测试覆盖正常关闭和启动失败清理。
- **回滚/提交**：恢复旧生命周期实现；`fix: 确保 SQLite 检查点连接正确关闭`。

#### 检查点 A：P0-T01～P0-T03

- [ ] 已知前后端红灯均有根因证据。
- [ ] 前端全量单测通过。
- [ ] 后端全量测试连续三次无资源线程 warning。

### P0-T04 建立覆盖率基线与门禁

- **依赖/并行**：依赖检查点 A。**规模/角色**：M，测试/CI。
- **预计文件**：`package.json`、`vite.config.ts`、`rag_llm_server/pyproject.toml`、`.github/workflows/ci.yml`。
- **契约与步骤**：前端启用 Vitest v8 coverage，后端使用 pytest-cov；首次结果向下取整写入配置；CI 上传 Cobertura/LCOV 报告并拒绝低于基线的提交。
- **失败处理**：依赖生成代码、类型声明和第三方 vendor 可排除，其余排除必须在配置中附理由；不得因失败直接降低阈值。
- **验证/验收**：`npm run test -- --coverage` 与 `uv run pytest --cov=. --cov-report=term-missing` 通过；人为提高阈值可证明 CI 会失败。
- **回滚/提交**：回退覆盖率配置与依赖；`test: 建立前后端覆盖率基线`。

### P0-T05 实现 Request ID

- **依赖/并行**：依赖检查点 A；可与 P0-T04 并行。**规模/角色**：M，后端。
- **预计文件**：`rag_llm_server/main.py`、`rag_llm_server/middleware/request_context.py`、`rag_llm_server/tests/test_request_context.py`。
- **契约与步骤**：使用 `contextvars` 保存请求上下文；按锁定规则校验/生成 `X-Request-ID`；异常响应也必须返回相同 ID；请求结束清理上下文。
- **失败处理**：非法上游 ID 不回显、不报 5xx，生成新 ID；上下文清理在 `finally` 中执行，防止异步请求串号。
- **验证/验收**：测试覆盖透传、生成、非法输入、异常响应和 20 个并发请求隔离。
- **回滚/提交**：移除中间件注册与新模块；`feat: 增加请求标识透传中间件`。

### P0-T06 引入结构化日志和脱敏

- **依赖/并行**：依赖 P0-T05。**规模/角色**：M，后端。
- **预计文件**：`rag_llm_server/logging_config.py`、`rag_llm_server/main.py`、`rag_llm_server/tests/test_logging.py`、`rag_llm_server/.env.example`。
- **契约与步骤**：生产/测试输出单行 JSON，本地可通过明确配置使用可读格式；Filter 注入 Request ID；统一异常事件名和 error_code；实现键名与值模式双重脱敏。
- **失败处理**：序列化失败仅记录安全的错误类型，不回退打印原对象；日志配置失败应在启动时终止。
- **验证/验收**：日志可被逐行 JSON 解析；并发请求 ID 正确；构造密码、Cookie、token、邮箱样本后日志中不存在原值。
- **回滚/提交**：恢复标准 logging 配置；`feat: 增加结构化日志与敏感字段脱敏`。

#### 检查点 B：P0-T04～P0-T06

- [ ] 覆盖率报告可重复生成且阈值生效。
- [ ] 请求、异常响应和日志使用同一个 Request ID。
- [ ] 脱敏测试没有泄漏样本值。

### P0-T07 迁移代码历史到 GitHub

- **依赖/并行**：依赖检查点 B；外部写操作需单独授权。**规模/角色**：S，仓库管理员。
- **预计范围**：本地 `.git/config`、GitHub `suz68879-hub/DeepScout-Agent`、原 Gitee remote；不修改业务文件。
- **契约与步骤**：确认 GitHub 仓库可写且不会覆盖他人历史；把 Gitee `origin` 重命名为 `gitee`，添加 GitHub 为 `origin`；推送 `master:main` 和全部标签；核对源/目标 commit SHA 后再设置默认分支。
- **失败处理**：目标仓库存在不相干提交时立即停止；拒绝 force push；任何 SHA 不一致时保留两个 remote 并撤销默认分支切换。
- **验证/验收**：GitHub `main` HEAD 与本地 `master` SHA 一致，所有标签数量和 SHA 一致，Gitee remote 仍可读取。
- **回滚/提交**：恢复 remote 命名和 GitHub 默认分支；该任务不产生代码提交。

### P0-T08 完成 GitHub Actions 基线验收

- **依赖/并行**：依赖 P0-T07。**规模/角色**：M，CI/仓库管理员。
- **预计文件**：`.github/workflows/ci.yml`、`docs/enterprise-evolution/evidence/phase-0-acceptance.md`。
- **契约与步骤**：工作流只监听 `main` 与 PR；前端、后端、E2E、覆盖率全部成为独立 required checks；PR 权限保持 `contents: read`，不注入生产 Secret。
- **失败处理**：E2E 环境不稳定时修复环境或测试隔离，不改为允许失败；GitHub 功能或权限不足时记录阻塞并保留 Gitee 主线。
- **验证/验收**：GitHub `main` push 和 PR 均成功运行；人为制造一个测试失败可阻止合并；验收证据包含运行链接和 commit SHA。
- **回滚/提交**：回退 workflow 变更，不删除历史运行记录；`ci: 建立 GitHub 绿色基线门禁`。

## 4. 阶段退出门槛

- [ ] `npm run lint/typecheck/test/build/e2e` 全部通过。
- [ ] `uv run pytest -q` 全部通过且无未处理线程异常。
- [ ] 覆盖率基线已固化，低于基线会阻止 CI。
- [ ] Request ID、JSON 日志和脱敏契约有自动化测试。
- [ ] GitHub CI 成为主线 required checks；迁移证据可审计。

## 5. 风险与交接

| 风险 | 缓解 |
|---|---|
| 当前工作区存在未提交改动 | 每项任务从干净分支开始，只提交自身范围 |
| 生命周期修复掩盖真实连接所有权问题 | 连续运行和启动失败测试，不使用 warning filter |
| GitHub 目标仓库已有他人内容 | 只读检查后再授权推送，禁止 force push |
| 一次性覆盖率阈值过高 | 现状基线起步，只升不降，新增代码使用更高标准 |

交给 Phase 1：全绿 commit SHA、覆盖率阈值、GitHub required checks 名称、Request ID/日志字段规范。
