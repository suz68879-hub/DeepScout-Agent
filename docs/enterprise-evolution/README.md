# 企业级架构演进任务索引

> 状态：执行基线  
> 上位设计：[企业级架构演进设计](../企业级架构演进设计.md)  
> 适用角色：后端、前端、测试、SRE、安全与发布负责人

## 1. 使用方式

本目录是后续开发的唯一阶段任务清单。开始一个任务前，执行人必须确认其依赖已完成，只实施该任务列出的范围；任务完成后更新任务状态、验证证据和提交号。涉及生产资源、外部仓库、数据库切换、任务重放或恢复的步骤仍需在执行时获得授权。

任务状态只使用：`未开始`、`进行中`、`已阻塞`、`已完成`。同一阶段最多一个共享基础任务处于“进行中”；可以并行的任务必须使用独立分支，并在共享契约冻结后开始。

## 2. 阶段导航

| 阶段 | 文档 | 主要产出 | 前置阶段 | 状态 |
|---|---|---|---|---|
| Phase 0 | [恢复绿色基线](phase-0-恢复绿色基线.md) | 全绿测试、Request ID、JSON 日志、GitHub 基线 | 无 | 已完成 |
| Phase 1 | [PostgreSQL 与数据迁移](phase-1-PostgreSQL与数据迁移.md) | PostgreSQL Repository、Alembic、迁移工具 | Phase 0 | 未开始 |
| Phase 2 | [Redis 与无状态 API](phase-2-Redis与无状态API.md) | Redis 会话、限流、锁、幂等 | Phase 1 | 未开始 |
| Phase 3 | [持久化任务队列与 Outbox](phase-3-持久化任务队列与Outbox.md) | Celery、RabbitMQ、任务状态机、Outbox | Phase 2 | 未开始 |
| Phase 4 | [可观测性体系](phase-4-可观测性体系.md) | OTel、Prometheus、Tempo、Loki、Grafana | Phase 1；部分任务依赖 2/3 | 未开始 |
| Phase 5 | [火山引擎部署与发布](phase-5-火山引擎部署与发布.md) | ECS 过渡部署、VKE、Helm、Argo Rollouts | ECS：1/2/3；VKE：2/3/4 | 未开始 |
| Phase 6 | [供应链安全与灾备](phase-6-供应链安全与灾备.md) | 安全门禁、SBOM、PITR、恢复演练 | 安全部分：0；灾备：1/3/5 | 未开始 |

```text
Phase 0 → Phase 1 → Phase 2 → Phase 3 → Phase 5B(VKE) → Phase 6 灾备
                    └────────→ Phase 4 ──────────────┘
Phase 1/2/3 完成后可先执行 Phase 5A(ECS)
Phase 6 的供应链安全任务可在 Phase 0 后并行
```

## 3. 已锁定的跨阶段决策

- 保持模块化单体，拆分 `web`、`cold-worker`、`recording-worker`、`outbox-dispatcher` 运行角色，不拆微服务。
- PostgreSQL 是生产事实数据库；SQLite 仅用于迁移期兼容和测试。
- Redis 保存可重建的短期共享状态；安全相关能力失败时不得回退进程内内存。
- RabbitMQ 采用至少一次投递；PostgreSQL 任务状态机、唯一约束和幂等键保证业务效果仅一次。
- ECS 是火山引擎过渡上线形态；VKE 才是多副本、自动扩缩容和 Argo Rollouts 的最终验收形态。
- ECS 与 VKE 使用同一镜像、环境变量和托管 PostgreSQL/Redis/RabbitMQ/TOS，不维护两套应用实现。
- 默认地域为 `cn-beijing`，通过单一部署变量覆盖；仓库内不保存云账号、域名证书或生产 Secret。
- GitHub 目标仓库为 `https://github.com/suz68879-hub/DeepScout-Agent.git`，默认分支为 `main`；原 Gitee remote 在迁移期只读保留。

## 4. 单任务完成标准

每个任务只有在以下条件全部满足时才能标记“已完成”：

- 仅修改任务列出的文件；发现额外范围时先拆出新任务。
- 单元、组件或集成测试覆盖成功路径、边界和规定的失败路径。
- 相关测试、类型检查、Lint 和构建通过，没有新增 warning。
- 数据库/API/配置变化与阶段文档一致；如必须改变契约，先修订上位设计并评审。
- 不包含真实密钥、调试输出、临时兼容分支或无到期条件的安全例外。
- 回滚步骤已验证，或明确证明该任务只包含可直接回退的兼容性变更。
- 提交遵循中文 Conventional Commit，并且一个提交只承担一个任务职责。

## 5. 通用验证命令

在仓库根目录执行：

```powershell
npm run lint
npm run typecheck
npm run test
npm run build
npm run e2e

Set-Location rag_llm_server
uv sync --dev
uv run pytest -q
```

涉及覆盖率、数据库或安全门禁时，阶段文档会给出附加命令。生产凭据不得用于本地或 PR 验证。

每张任务卡的“预计文件”已经列出该任务的目标测试文件；执行时按以下固定规则组成定向验证命令，不需要重新选择测试范围：

```powershell
# 前端任务：把卡片中的 *.test.ts / *.test.tsx 路径依次追加到命令末尾
npm run test -- <测试文件路径>

# 后端任务：在后端目录执行卡片中的 test_*.py
Set-Location rag_llm_server
uv run pytest -q <测试文件路径>

# 部署与策略文件
docker compose -f <卡片中的 Compose 文件> config
terraform -chdir=deploy/terraform fmt -check
terraform -chdir=deploy/terraform validate
helm lint deploy/helm/interview-coach -f <卡片中的 values 文件>
helm template deepscout deploy/helm/interview-coach -f <卡片中的 values 文件>
promtool check rules <卡片中的规则文件>
trivy fs --scanners vuln,secret,misconfig .
```

任务卡列出多个测试文件时在同一命令中全部追加；任务定向验证通过后，还必须执行本节的通用全量命令才能到达检查点。尖括号表示直接代入该卡片已经列出的路径，不允许执行人自行缩小范围。

## 6. 变更与授权边界

| 行为 | 执行要求 |
|---|---|
| 新增依赖、表、公共 API | 按对应阶段任务实施并完成评审 |
| 推送 GitHub、切换默认分支 | 执行时确认仓库权限与授权 |
| 创建或变更火山引擎资源 | 执行时确认账号、地域、费用和变更窗口 |
| 生产 Alembic migration | 预检、备份、审批、维护窗口齐全后执行 |
| 任务重放、PITR、对象恢复 | 仅在隔离环境演练；生产操作单独审批 |
| 降低覆盖率或安全阈值 | 禁止通过实现提交直接降低，必须先修订架构决策 |

## 7. 文档维护

- 阶段任务实施中只补充事实证据、命令输出摘要和提交号，不改变已锁定契约。
- 决策发生变化时，先更新上位设计或新增 ADR，再同步本目录中的任务与依赖。
- 旧决策不删除；使用“已替代”状态并链接新决策，保证审计链完整。
- 每个阶段结束时核对下一阶段的输入、配置样例、迁移版本和运行手册是否齐全。
