# Phase 6：供应链安全与灾备

> 状态：未开始  
> 前置阶段：供应链任务依赖 Phase 0；PITR 依赖 Phase 1；任务重建依赖 Phase 3；最终演练依赖 Phase 5B  
> 建议周期：1～2 个迭代，之后按月/季度持续执行  
> 阶段负责人：安全与 SRE 负责人；仓库管理员、DBA、后端和发布负责人协作

## 1. 目标与边界

建立从 PR 到发布制品的可验证供应链，以及 PostgreSQL、TOS 和 RabbitMQ 的实际恢复能力。

| 当前实现 | 阶段目标 |
|---|---|
| CI 仅有测试/构建 | required checks 覆盖 SAST、依赖、Secret、IaC、镜像 |
| Actions 使用可变版本标签 | 第三方 Action 固定完整 commit SHA |
| 镜像缺 SBOM/来源证明 | 每个 digest 有 SPDX/CycloneDX SBOM 和 attestation |
| 有备份设计但无恢复证据 | PITR、对象抽样恢复、任务重建按 RPO/RTO 演练 |

非目标：不承诺跨地域双活；不把扫描器零告警当作绝对安全；不自动执行生产恢复/重放；不接受无负责人、无到期日的永久例外。

## 2. 输入、输出与锁定决策

输入：GitHub `main`、Phase 5 release workflow/镜像/Helm/Terraform、托管服务备份策略。  
输出：required checks、安全报告、SBOM/attestation、恢复 Runbook 和季度演练证据。

阻断规则：新增或有修复版本的 High/Critical 漏洞阻断；任何真实 Secret 阻断；IaC High/Critical 阻断；例外必须有风险、负责人、补偿措施、到期日（最长 30 天）。PR 默认 `contents:read`；只有 release job 获得 `packages:write/id-token:write/attestations:write`。PostgreSQL RPO≤5 分钟、RTO≤60 分钟；TOS 误删恢复 RTO≤4 小时；RabbitMQ 整集群丢失后从 PG 重建未完成任务，不能产生重复业务效果。

## 3. 任务清单

### P6-T01 配置 GitHub 分支和环境保护

- **依赖/并行**：Phase 0；最先执行。**规模/角色**：S，仓库管理员。
- **预计范围**：GitHub `main` branch ruleset、staging/production Environments、`docs/enterprise-evolution/evidence/github-protection.md`。
- **契约与步骤**：禁止直接 push/force/delete；至少 1 reviewer；dismiss stale review；要求 conversation resolved、线性历史和所有阶段 checks；production 要求独立审批和分支限制。
- **失败处理**：GitHub 套餐/权限不支持某项时阶段标记阻塞，不通过降低规则绕过；紧急 bypass 仅仓库管理员且保留审计。
- **验证/验收**：测试 PR 证明失败 check、缺 review、未解决对话均不可合并；生产 workflow 未审批不能读取 Secret。
- **回滚/提交**：恢复上一 ruleset 导出；外部配置任务不产生代码提交。

### P6-T02 接入 CodeQL SAST

- **依赖/并行**：P6-T01。**规模/角色**：S，安全/CI。
- **预计文件**：`.github/workflows/codeql.yml`、`.github/codeql/codeql-config.yml`。
- **契约与步骤**：扫描 Python、JavaScript/TypeScript；PR、main push 和每周定时；使用 security-extended 查询；生成 SARIF；新增 High/Critical 阻断。
- **失败处理**：分析失败本身视为 required check 失败；排除仅限 build/vendor/generated 目录并在配置注释理由。
- **验证/验收**：两种语言分析成功；受控测试规则可生成告警；SARIF 权限最小化。
- **回滚/提交**：回退 workflow，外部历史告警保留；`ci: 接入 CodeQL 静态安全扫描`。

### P6-T03 接入依赖更新与漏洞审查

- **依赖/并行**：P6-T01；可与 P6-T02 并行。**规模/角色**：M，安全/CI。
- **预计文件**：`.github/dependabot.yml`、`.github/workflows/dependency-review.yml`、`.github/workflows/ci.yml`、`docs/security-exceptions.yml`。
- **契约与步骤**：npm、uv/pip、GitHub Actions 每周分组更新；PR dependency review；npm/Python 锁文件扫描；只阻断新增/有修复版本 High/Critical；例外 schema 强制到期≤30天。
- **失败处理**：扫描服务故障不得默认为通过；过期例外使 CI 失败；自动更新不直接合并。
- **验证/验收**：受控漏洞 fixture 被阻断；合法例外到期前有告警、到期后失败；锁文件和 manifest 均覆盖。
- **回滚/提交**：回退 workflows，不删除历史 alerts；`ci: 增加依赖漏洞与更新门禁`。

#### 检查点 A：P6-T01～P6-T03

- [ ] `main` 不能绕过 required checks 和 review。
- [ ] Python/TypeScript SAST 与依赖审查均生成可追踪结果。
- [ ] 安全例外有结构化负责人和到期约束。

### P6-T04 接入 Secret 扫描与泄漏处置

- **依赖/并行**：P6-T01。**规模/角色**：M，安全/CI。
- **预计文件**：`.github/workflows/secret-scan.yml`、`.trivyignore`、`deploy/runbooks/secret-incident.md`、测试用假凭据文件。
- **契约与步骤**：启用 GitHub secret scanning/push protection（仓库能力允许时），CI 使用 Trivy secret 扫描全历史增量；只允许明确假值规则；Runbook 包含立即吊销、轮换、历史评估、通知和复盘。
- **失败处理**：发现真实 Secret 先轮换再清历史，不能只加入 ignore；扫描服务失败阻断 PR。
- **验证/验收**：受控假 token 触发 CI；`.env.example` 占位符不误报；Runbook 演练在隔离凭据完成。
- **回滚/提交**：回退 workflow 不关闭平台 push protection；`ci: 增加密钥扫描与泄漏处置门禁`。

### P6-T05 使用 Trivy 扫描仓库、IaC 与镜像

- **依赖/并行**：Phase 5 清单；可与 P6-T04 并行。**规模/角色**：M，安全/CI。
- **预计文件**：`.github/workflows/security.yml`、`trivy.yaml`、`.trivyignore`、`deploy/tests/security-policy.rego`。
- **契约与步骤**：PR 扫 fs/config/secret，release 扫每个镜像 digest；上传 SARIF/JSON；有修复版本 High/Critical 和 IaC High/Critical 阻断；ignore 必须关联例外 ID。
- **失败处理**：数据库更新失败不使用过期缓存静默通过；digest 未扫描不允许发布；license 告警先审计不自动阻断。
- **验证/验收**：已知脆弱依赖、错误安全组、root container、嵌入 secret fixture 均被识别；干净镜像通过。
- **回滚/提交**：回退 workflow 需安全负责人审批；`ci: 增加代码基础设施与镜像安全扫描`。

### P6-T06 生成 SBOM、签名与来源证明

- **依赖/并行**：P6-T05、Phase 5 release。**规模/角色**：M，CI/安全。
- **预计文件**：`.github/workflows/release.yml`、`deploy/scripts/verify-artifact.ps1`、`deploy/runbooks/artifact-verification.md`、制品元数据 schema。
- **契约与步骤**：每个 frontend/backend digest 生成 SPDX JSON 和 CycloneDX；使用 GitHub OIDC artifact attestation，签名绑定 digest/SHA/workflow；SBOM/报告作为不可变 release artifact 保留。
- **失败处理**：SBOM、attestation 或验证任一失败不推生产；禁止基于 tag 验证；fork PR 无签名权限。
- **验证/验收**：脚本能按 digest 验证 provenance、仓库、commit 和 workflow；篡改 SBOM/digest 必须失败。
- **回滚/提交**：旧 digest 与其证明继续保留；`ci: 为发布镜像生成 SBOM 与来源证明`。

### P6-T07 固定 Actions 并收紧流水线权限

- **依赖/并行**：P6-T02～P6-T06。**规模/角色**：M，安全/CI。
- **预计文件**：`.github/workflows/ci.yml`、`codeql.yml`、`security.yml`、`release.yml`、`.github/dependabot.yml`。
- **契约与步骤**：所有第三方 Action 固定完整 SHA并注释对应版本；顶层 permissions `{}`，job 逐项授权；脚本输入不直接拼 shell；Dependabot 管理 Action 更新。
- **失败处理**：未知发布者、过宽 `write-all`、PR target 执行不可信代码并持有 Secret 均阻断；升级先在 PR 验证。
- **验证/验收**：actionlint、CodeQL workflow 查询和权限审查通过；fork PR 无 Secret/写权限，release job 才有 OIDC/attestation 权限。
- **回滚/提交**：恢复上一已审查 SHA，不恢复浮动 tag；`ci: 固定 Actions 版本并收紧权限`。

#### 检查点 B：P6-T04～P6-T07

- [ ] 源码、依赖、Secret、IaC、镜像形成连续门禁。
- [ ] 每个发布 digest 都有可验证 SBOM 和 provenance。
- [ ] PR/Fork 不接触生产 Secret 或写权限。

### P6-T08 建立 PostgreSQL PITR 与恢复验证

- **依赖/并行**：Phase 1、Phase 5 托管 PG。**规模/角色**：M，DBA/SRE。
- **预计文件**：`deploy/runbooks/database-restore.md`、`scripts/verify-postgres-restore.ps1`、`docs/enterprise-evolution/evidence/pitr-template.md`。
- **契约与步骤**：托管快照+WAL/PITR，目标 RPO≤5min/RTO≤60min；恢复到隔离 VPC/新实例；核对 Alembic head、行数/哈希/关联和抽样业务；应用只读冒烟后销毁按审批执行。
- **失败处理**：不覆盖原生产实例；目标时间不可恢复、凭据混用或校验失败均判定演练失败；恢复数据按生产敏感级保护。
- **验证/验收**：在随机目标时间完成恢复，测得 RPO/RTO 达标；校验器 100% 通过；证据含时间线、资源 ID、SHA 和缺陷关闭项。
- **回滚/提交**：恢复演练不变更生产；`docs: 建立 PostgreSQL PITR 恢复流程`。

### P6-T09 建立 TOS 对象恢复与校验

- **依赖/并行**：Phase 5 TOS；可与 P6-T08 并行。**规模/角色**：M，SRE/后端。
- **预计文件**：`deploy/runbooks/tos-restore.md`、`scripts/verify-tos-restore.ps1`、`rag_llm_server/tests/test_tos_restore_manifest.py`、`docs/enterprise-evolution/evidence/tos-restore-template.md`。
- **契约与步骤**：启用版本控制/生命周期；每月按 manifest 随机抽取录音、简历、报告对象，校验 key/version/size/SHA-256；误删恢复 RTO≤4h；访问使用只读短期凭据。
- **失败处理**：不得在生产 key 原位试删；对象缺版本、哈希不符或权限过宽即失败；证据不记录用户路径明文。
- **验证/验收**：隔离 prefix 完成删除版本恢复和跨应用版本读取；抽样哈希 100% 一致，实际 RTO 达标。
- **回滚/提交**：演练对象按保留策略清理；`docs: 建立 TOS 对象恢复与校验流程`。

### P6-T10 建立 RabbitMQ 丢失后的任务重建

- **依赖/并行**：Phase 3、P6-T08。**规模/角色**：M，后端/SRE。
- **预计文件**：`rag_llm_server/scripts/rebuild_pending_jobs.py`、`rag_llm_server/services/jobs/recovery.py`、`rag_llm_server/tests/test_job_recovery.py`、`deploy/runbooks/rabbitmq-recovery.md`。
- **契约与步骤**：从 PG 查未终态 job 和未发布 outbox；running 超租约转 pending；按 job_id 重建 outbox，不直接拼 Celery 消息；支持 dry-run、批次、时间范围和 operator/reason 审计。
- **失败处理**：succeeded/cancelled 不重建；payload schema 不兼容进入人工清单；执行生产重建需双人审批并先暂停 dispatcher。
- **验证/验收**：清空隔离 RabbitMQ 后所有未终态任务恢复；重复运行不增加业务效果；审计和计数完整。
- **回滚/提交**：停止恢复工具，已建 outbox 由状态机幂等处理；`feat: 增加 RabbitMQ 灾难任务重建工具`。

### P6-T11 执行季度灾备与最终验收

- **依赖/并行**：P6-T08～P6-T10、Phase 5B；最后执行。**规模/角色**：M，SRE/DBA/安全/业务。
- **预计文件**：`deploy/runbooks/disaster-recovery-drill.md`、`scripts/verify-disaster-recovery.ps1`、`docs/enterprise-evolution/evidence/phase-6-acceptance.md`。
- **契约与步骤**：隔离环境串联 PG PITR、TOS 恢复、Rabbit 重建和 VKE 应用启动；记录 RPO/RTO、数据校验、业务冒烟、责任人和缺陷关闭日期；每季度重复。
- **失败处理**：任一 RPO/RTO/完整性不达标则架构状态不能标记 Implemented；不得用未验证备份替代演练。
- **验证/验收**：PG≤5min/60min、TOS≤4h、任务无静默丢失/重复；恢复后登录→面试→报告与录音流程全绿。
- **回滚/提交**：演练环境按审批销毁，证据长期保留；`docs: 记录企业级灾备恢复演练`。

## 4. 阶段退出门槛

- [ ] 测试、覆盖率、CodeQL、依赖、Secret、IaC、镜像扫描均为 required checks。
- [ ] 无未批准 High/Critical 或过期安全例外。
- [ ] 每个生产镜像 digest 有 SBOM、签名/attestation 和可验证来源。
- [ ] PostgreSQL、TOS、RabbitMQ 恢复演练满足规定 RPO/RTO，业务 E2E 通过。
- [ ] 所有安全与恢复操作有 actor、时间、输入版本和结果证据。

## 5. 风险与持续运行

| 风险 | 缓解 |
|---|---|
| 初期告警过多导致绕过 | 只阻断新增/可修复高风险，例外最长 30 天 |
| Action 供应链被替换 | 固定完整 SHA、最小权限、受控 Dependabot PR |
| 有备份但不能恢复 | 月度对象抽样、季度 PITR/队列重建，记录实际 RTO |
| 恢复脚本造成重复任务 | PG 状态机/幂等约束、dry-run、批次和双人审批 |

阶段完成后：每周处理依赖 PR；每月 TOS 抽样和安全例外到期审查；每季度执行完整灾备；每次重大 Schema/队列 payload/云架构变化后追加专项演练。
