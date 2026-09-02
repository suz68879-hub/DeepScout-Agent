# AI 面试陪练（AI Interview Coach）

基于火山引擎 AIGC-RTC 实时对话 Demo（v1.6.0，BSD-3-Clause）二次开发的 **AI 数字人模拟面试**应用：上传简历、选择目标岗位，与 AI 数字人进行真实音视频面试，结束后生成四维评分报告与个性化改进建议，并支持真实面试录音的上传分析。

## 功能特性

- **简历解析**：上传 PDF/文本简历，由 resume_parser Agent 提取技能、经历、岗位画像
- **数字人视频面试**：基于火山 RTC 实时音视频 + 数字人交互，interviewer Agent 按岗位追问、多轮对话（支持 3A 处理、AI 代理实时对话）
- **四维评分报告**：evaluator + reporter Agent 从技术/项目/表达/临场四维打分，雷达图 + 逐轮评语 + 个性化改进建议
- **历史记录与数据分析**：全部面试留档；Analytics 页支持自然语言查询（text2sql Agent → SQLite）
- **录音上传分析**：上传真实面试录音（wav/mp3/ogg），ASR 说话人分离 → 角色判定 → 四维评分，报告与在线面试同构
- **RAG 知识库**：简历/岗位知识检索（llama-index 本地向量库或火山知识库），支撑面试官追问

## 技术栈

| 层 | 技术 |
|---|---|
| 前端 | React 18 + TypeScript + Vite、Arco Design、Redux Toolkit、ECharts 6、@volcengine/rtc |
| 后端 | Python + FastAPI + uvicorn、LangGraph（6 Agent 编排）、SQLite、llama-index |
| 大模型 | 火山方舟（ARK，OpenAI 兼容端点）、豆包 Seed-ASR（录音识别）、火山 TOS 对象存储 |
| 测试 | Vitest（前端）、Playwright（E2E）、pytest（后端） |

## 目录结构

```
├── src/                     # 前端（React + Vite + TS）
│   ├── pages/               # Home / Interview / Report / History / Analytics
│   ├── components/          # AiAvatarCard、ChartPanel、ScoreOverlay 等
│   ├── rtc/                 # 火山 RTC 封装与 codec 语义消息解析
│   ├── api/  domain/  store/  styles/
├── rag_llm_server/          # 后端（FastAPI + LangGraph）
│   ├── main.py              # 应用装配与生命周期（端口 3001）
│   ├── api/                 # 业务、RTC 与可选 debug 路由
│   ├── agents/              # 6 Agent + graph.py（LangGraph 状态机）+ prompts/
│   ├── services/            # interview_service、report_service、rag_service、asr_client、storage(TOS) 等
│   ├── rag/                 # llama-index 本地向量库 / 火山知识库 provider
│   └── data/                # SQLite（interview.db，gitignore）
├── docs/                    # 学习文档与计划（见下方「项目文档」）
└── scripts/                 # 工具脚本（如 eval_consistency.py）
```

## 快速开始

前置要求：Node 20+、Python 3.13+、Redis 7+、[uv](https://docs.astral.sh/uv/)。

### 1. 后端（端口 3001）

```bash
cd rag_llm_server
uv sync            # 安装依赖（uv.lock）
cp .env.example .env
uv run uvicorn main:app --host 0.0.0.0 --port 3001
```

也可执行 `uv run python main.py`；两种方式都由 FastAPI lifespan 初始化并关闭存储、LangGraph checkpointer 和进程内冷任务，默认不启用 reload。

### 2. 前端（端口 3000）

```bash
npm install
npm run dev
```

前端通过 `src/config/index.ts` 的 `AIGC_PROXY_HOST`（默认 `http://localhost:3001`）访问后端。

### 3. 环境变量（`.env` 置于 `rag_llm_server/` 下）

| 变量 | 必填 | 说明 |
|---|---|---|
| `VOLC_ACCESS_KEY` / `VOLC_SECRET_KEY` | 是 | 火山引擎 OpenAPI 凭证 |
| `RTC_APP_ID` / `RTC_APP_KEY` | 是 | 火山 RTC 应用（数字人面试） |
| `ASR_APP_ID` / `TTS_APP_ID` | 是 | 实时 ASR / TTS |
| `ARK_API_KEY` | 是 | 方舟 API Key |
| `ARK_ENDPOINT_ID` | 是 | 方舟推理接入点（绑定思考模型时以 `extra_body={'thinking': {'type': 'disabled'}}` 关闭思考） |
| `ARK_INTERVIEWER_ENDPOINT_ID` 等 | 否 | 各 Agent 独立端点（interviewer/planner/evaluator/reporter/resume_parser/text2sql/recording_analyzer），未配置时回落 `ARK_ENDPOINT_ID` |
| `ARK_EMBEDDING_ENDPOINT_ID` | 否 | 方舟 embedding 接入点 |
| `EMBEDDING_API_BASE` / `EMBEDDING_API_KEY` / `EMBEDDING_MODEL` | 否 | 其他厂商 embedding（如阿里百炼 text-embedding-v4），未配置时回落方舟 |
| `REDIS_URL` | 是 | Redis 7+ 连接串（登录限流、RTC 会话锁、幂等键）。开发与生产均必填，缺失时拒绝启动；不会回退进程内内存。生产使用 `rediss://` |
| `SERVER_URL` | 使用 RTC 时是 | 公网可达的本服务地址；缺失时应用可启动，但 StartVoiceChat 返回 503 |
| `CORS_ORIGINS` | 否 | 允许的浏览器来源，逗号分隔；默认仅允许本机 3000 端口 |
| `ENABLE_DEBUG_ROUTES` | 否 | 默认 `false`；设为 `true` 后注册 `/debug/chat`、`/debug/rag` |
| `RTC_CALLBACK_SECRET` | 是 | RTC 回调验签密钥；缺失时拒绝启动 |
| `AUTH_COOKIE_SECURE` | 否 | 默认 `false`；HTTPS 生产环境必须设为 `true` |
| `BOOTSTRAP_ADMIN_USERNAME` / `BOOTSTRAP_ADMIN_PASSWORD` | 仅历史迁移时 | 旧库存在无归属业务数据时创建启动管理员并接收全部历史数据；重复启动不会重置密码 |
| `TOS_*`（ACCESS_KEY/SECRET_KEY/ENDPOINT/REGION/BUCKET） | 否 | 火山 TOS 对象存储：配置后录音 + 简历原文件持久化入 TOS；未配置时录音接口 fail fast、报告文件回落本地 |
| `ASR_FILE_API_KEY` | 否 | 录音文件识别（Seed-ASR 大模型版）API Key |
| `RAG_PROVIDER` | 否 | `llamaindex`（默认，本地向量库）/ `volc_kb`（火山知识库） |

> 密钥只进 `.env`，绝不进入提交。如需恢复检索索引：配置 embedding 后运行 `uv run python scripts/build_index.py`（若有）。

## 测试

```bash
# 前端
npm run typecheck     # tsc --noEmit
npm run lint
npm test              # vitest run
npm run build
npm run e2e           # Playwright 自行构建并启动 3100 端口

# 后端
cd rag_llm_server
uv run pytest -q
```

调试接口默认返回 404；仅在本地需要排查模型或检索链路时设置
`ENABLE_DEBUG_ROUTES=true` 后重启服务，调试接口仍要求登录。

当前架构是“单进程、单 worker、SQLite 的多用户隔离”：用户通过用户名密码注册登录，服务端使用 7 天有效的 HttpOnly Cookie 会话；简历、面试、消息、报告、录音和 Analytics 均按用户过滤，每场面试使用独立 RTC Room/User/Task/Callback 标识。生产部署需保持前后端同站点范围、HTTPS 下开启 `AUTH_COOKIE_SECURE=true`，并固定 `uvicorn --workers 1`。

开放注册不包含邮箱验证、找回密码、账号删除或管理员后台。登录限流、RTC 会话锁和写请求幂等依赖 Redis，故障时 fail closed（503），不会退回进程内字典。SQLite 只适合当前单实例开发规模。生产部署需保持前后端同站点范围、HTTPS 下开启 `AUTH_COOKIE_SECURE=true`，并使用 PostgreSQL、Redis 与任务队列。

旧数据库首次升级时，如存在无 `user_id` 的历史数据，必须设置 `BOOTSTRAP_ADMIN_USERNAME` 和 `BOOTSTRAP_ADMIN_PASSWORD`。迁移会在事务内把全部历史数据归属该管理员；缺少配置、归属为空或关联审计失败时应用拒绝启动。新上传对象使用 `users/{user_id}/...`，历史对象 key 不搬迁。

## 项目文档

- [LEARNING_GUIDE.md](./LEARNING_GUIDE.md) — 项目学习文档（面试准备用）
- [docs/README.md](./docs/README.md) — 3 天冲刺通关文档（00-03 四份：环境搭建 / 架构总览 / 模块精讲 / 进阶）
- [docs/superpowers/specs/2026-08-16-interview-coach-design.md](./docs/superpowers/specs/2026-08-16-interview-coach-design.md) — 产品规格（§1–§12，含录音分析）
- [docs/superpowers/plans/](./docs/superpowers/plans/) — 各阶段实施计划（P1–P6）

## 许可证

BSD-3-Clause（继承自火山引擎 AIGC-RTC 实时对话 Demo，原作者：北京火山引擎科技有限公司）。
