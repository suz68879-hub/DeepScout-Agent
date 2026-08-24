# Phase 5：火山引擎部署与发布

> 状态：未开始  
> 路径：Phase 5A ECS 过渡上线 → Phase 5B VKE 企业级部署  
> 前置阶段：5A 依赖 Phase 1/2/3；5B 依赖 Phase 2/3/4 和 5A 镜像契约  
> 阶段负责人：SRE/平台负责人；后端、前端、安全和发布负责人协作

## 1. 目标与边界

先用 ECS + Docker Compose 以较低运维门槛上线，再复用相同镜像和配置迁移到 VKE，实现多副本、自动扩缩容、故障摘流和 Argo Rollouts 灰度。

| 形态 | 定位 | 验收边界 |
|---|---|---|
| ECS | 可上线过渡形态 | 托管数据服务、TLS、不可变镜像、双槽更新、备份与回滚 |
| VKE | 最终企业级形态 | 2+ 副本、Helm、HPA/PDB/NetworkPolicy、Argo 自动灰度 |

非目标：ECS 不承诺单机故障无感；不在 Compose 中自建生产 PG/Redis/RabbitMQ；不维护 ECS/VKE 两套应用代码；不使用 `latest`；不在仓库保存 Secret；本阶段任务文档不授权实际采购或生产发布。

## 2. 输入、输出与锁定决策

输入：已通过扫描的镜像源码、Phase 4 probes/metrics、火山引擎账号与配额、域名/证书、VPC/子网参数、托管服务连接信息。  
输出：Docker/Compose、Terraform 资源清单、Helm Chart、发布流水线、Argo Rollouts、回滚证据。

锁定决策：默认 `VOLC_REGION=cn-beijing`；Terraform 管理可重建云资源，provider lock file 入库；ECS 与 VKE 连接同一托管 PostgreSQL/Redis/RabbitMQ/TOS；`web/cold-worker/recording-worker/outbox-dispatcher` 共用后端镜像；镜像按 digest 部署；配置使用环境变量，Secret 由环境外部注入；VKE canary 为 `10%→25%→50%→100%`，每阶段至少观察 10 分钟且满足 100 个请求，低流量 30 分钟仍不足时需发布负责人审批。

## 3. Phase 5A：ECS 过渡部署

### P5A-T01 建立多阶段 Docker 镜像

- **依赖/并行**：Phase 3；最先执行。**规模/角色**：M，前后端/SRE。
- **预计文件**：`Dockerfile.frontend`、`.dockerignore`、`rag_llm_server/Dockerfile`、`rag_llm_server/docker-entrypoint.sh`、镜像测试文件。
- **契约与步骤**：前端 Node 20 build→非 root Nginx；后端 Python 3.13 + uv sync frozen→非 root 运行时；web/worker 用同一镜像不同 command；写 OCI source/revision/version labels。
- **失败处理**：禁止复制 `.env`、`.git`、SQLite、测试缓存和开发依赖；启动脚本不执行 Alembic；只读根文件系统仅开放 `/tmp`。
- **验证/验收**：BuildKit 构建成功；Trivy 无可修复 High/Critical；容器以非 root 运行，镜像内无凭据/数据库/Git 历史。
- **回滚/提交**：保留上一 digest；`chore: 增加前后端生产容器镜像`。

### P5A-T02 建立 ECS Compose 清单

- **依赖/并行**：P5A-T01。**规模/角色**：M，SRE。
- **预计文件**：`deploy/compose/compose.ecs.yaml`、`deploy/compose/compose.observability.yaml`、`deploy/compose/.env.example`、`deploy/compose/README.md`。
- **契约与步骤**：定义 frontend/api/cold-worker/recording-worker/outbox-dispatcher/OTel/Grafana 栈；应用不容器化 PG/Redis/Rabbit；健康检查、restart policy、只读文件系统、日志轮转和资源限制齐全。
- **失败处理**：必需变量 `${VAR:?required}`；服务不在默认网段暴露数据库/metrics；Secret 只用 ECS 外部 env_file/挂载。
- **验证/验收**：`docker compose config` 通过；缺 Secret 明确失败；本地连接测试依赖时完整业务冒烟通过。
- **回滚/提交**：恢复上一 compose 版本与 digest；`chore: 增加 ECS Compose 部署清单`。

### P5A-T03 建立网络与 ECS Terraform

- **依赖/并行**：P5A-T01；可与 P5A-T02 并行。**规模/角色**：M，平台/SRE。
- **预计文件**：`deploy/terraform/versions.tf`、`variables.tf`、`network.tf`、`ecs.tf`、`outputs.tf`。
- **契约与步骤**：定义 VPC、公私子网、安全组、ECS、弹性公网入口和标签；仅 80/443 对公网，22 仅堡垒/运维 CIDR；出站按服务依赖收敛；terraform state 使用受控远端后端。
- **失败处理**：默认不创建资源；`plan` 人工审查后才 apply；禁止 `0.0.0.0/0:22`、明文密码和公共数据库地址。
- **验证/验收**：`terraform fmt -check/validate` 与 Trivy IaC 通过；plan 无公网数据库/Redis/RabbitMQ；outputs 不标记 Secret。
- **回滚/提交**：生产资源不自动 destroy，按变更单恢复安全组/实例；`chore: 增加火山引擎 ECS 网络清单`。

#### 检查点 5A-A：P5A-T01～P5A-T03

- [ ] 相同后端镜像可分别启动 web 和三类 worker/dispatcher。
- [ ] Compose 不包含生产数据库、缓存或 broker 容器。
- [ ] Terraform plan 通过网络最小暴露审查。

### P5A-T04 对接托管数据与对象服务

- **依赖/并行**：检查点 5A-A。**规模/角色**：M，平台/DBA/SRE。
- **预计文件**：`deploy/terraform/data-services.tf`、`deploy/terraform/data-service-outputs.tf`、`deploy/volcengine/managed-services.md`、`rag_llm_server/.env.example`。
- **契约与步骤**：定义 PostgreSQL HA、Redis HA、RabbitMQ 和 TOS 参数；仅 VPC 访问；业务/migration/analytics 账号分离；连接地址通过 Secret 系统注入，Terraform 只输出非敏感资源 ID。
- **失败处理**：公网访问、单节点生产规格、自动备份/PITR 未启用均阻止 apply；状态文件按敏感资产保护。
- **验证/验收**：ECS 私网能连四类服务；公网探测失败；Alembic、Redis/Rabbit TLS、TOS 上传下载冒烟通过。
- **回滚/提交**：切换前可删除空资源；有数据后按备份/审批流程，不直接 destroy；`chore: 增加火山引擎托管数据服务清单`。

### P5A-T05 建立 Nginx、TLS 与 Secret 注入

- **依赖/并行**：P5A-T02、P5A-T04。**规模/角色**：M，SRE/安全。
- **预计文件**：`deploy/nginx/deepscout.conf`、`deploy/nginx/security-headers.conf`、`deploy/compose/compose.ecs.yaml`、`deploy/runbooks/ecs-secret-rotation.md`。
- **契约与步骤**：同站点 `/api` 反代、WebSocket/SSE 超时、上传大小限制；HTTP→HTTPS、HSTS、安全头；证书挂载到 `/etc/deepscout/tls`；env_file 权限 600，支持新旧 Secret 短期轮换。
- **失败处理**：证书缺失/过期或生产 Cookie 非 Secure 时不启动；日志不记录 Authorization/Cookie/query token。
- **验证/验收**：`nginx -t`、TLS 扫描、SSE/RTC 回调/200MB 流式上传边界、Secret 轮换演练通过。
- **回滚/提交**：切回上一 Nginx 配置和证书版本；`chore: 增加 ECS TLS 网关与密钥轮换`。

### P5A-T06 建立 systemd 托管与恢复

- **依赖/并行**：P5A-T02、P5A-T05。**规模/角色**：S，SRE。
- **预计文件**：`deploy/systemd/deepscout.service`、`deploy/scripts/ecs-start.ps1`、`deploy/scripts/ecs-healthcheck.ps1`、`deploy/runbooks/ecs-operations.md`。
- **契约与步骤**：systemd 调用固定 compose project；启动前拉取 digest、校验 env/磁盘/依赖；启动顺序 dispatcher/worker/API/frontend；异常重启有速率限制。
- **失败处理**：healthcheck 失败恢复上一 digest；迁移不在 systemd 自动执行；磁盘不足禁止拉新镜像。
- **验证/验收**：实例重启后自动恢复；连续崩溃不会无限重启；日志轮转与磁盘阈值有效。
- **回滚/提交**：systemd 指回上一 release 目录；`chore: 增加 ECS 服务托管与恢复脚本`。

#### 检查点 5A-B：P5A-T04～P5A-T06

- [ ] ECS 仅通过私网连接托管依赖。
- [ ] HTTPS、Cookie、SSE 和上传边界通过验证。
- [ ] 实例重启可自动恢复到固定镜像 digest。

### P5A-T07 建立 GitHub 构建与 ECS 双槽发布

- **依赖/并行**：检查点 5A-B、Phase 6 的镜像扫描任务可后续增强。**规模/角色**：M，CI/SRE。
- **预计文件**：`.github/workflows/release.yml`、`deploy/scripts/deploy-ecs.ps1`、`deploy/nginx/upstreams.conf`、`deploy/runbooks/rollback.md`。
- **契约与步骤**：tag 构建并推送 CR，记录 digest；GitHub production Environment 审批后部署 idle blue/green slot；执行 smoke 后原子切换 Nginx；旧槽保留 30 分钟。
- **失败处理**：扫描、签名、环境审批、健康检查任一失败不切流；禁止 SSH 密码和长期云密钥，凭据权限限于目标环境。
- **验证/验收**：同一 release 可重复部署；故意部署失败镜像不影响 active slot；切换与回滚均记录 actor/SHA/digest。
- **回滚/提交**：原子切回旧 upstream；`ci: 增加 ECS 双槽发布与回滚`。

### P5A-T08 执行 ECS 上线验收

- **依赖/并行**：P5A-T07；5A 最后执行。**规模/角色**：M，发布/测试/SRE。
- **预计文件**：`e2e/tests/deployment-smoke.spec.ts`、`deploy/scripts/verify-ecs.ps1`、`docs/enterprise-evolution/evidence/phase-5a-acceptance.md`。
- **契约与步骤**：在 staging ECS 执行登录→简历→面试→任务→报告、录音流程；重启 API/worker/实例；校验备份状态、遥测和旧 digest 回滚。
- **失败处理**：数据依赖使用隔离租户；任何手工修容器才能恢复、未加密流量或状态丢失均阻止上线。
- **验证/验收**：新 ECS 可按 Terraform+Runbook 重建；冒烟全绿；应用回滚在 15 分钟内；实例故障的已知影响写入运行手册。
- **回滚/提交**：切回旧槽；`test: 增加 ECS 部署与回滚验收`。

## 4. Phase 5B：VKE 企业级部署

### P5B-T01 建立 VKE 与容器镜像仓库

- **依赖/并行**：P5A-T01、Phase 2/3/4。**规模/角色**：M，平台/SRE。
- **预计文件**：`deploy/terraform/vke.tf`、`deploy/terraform/cr.tf`、`deploy/terraform/vke-outputs.tf`、`deploy/volcengine/vke-cluster.md`。
- **契约与步骤**：私网 VKE、多可用区节点池、CR、staging/production namespace；控制面/节点访问最小化；镜像 digest 与保留策略；工作负载和运维身份分离。
- **失败处理**：单可用区、节点公网暴露、默认管理员凭据或可变标签部署阻止 apply。
- **验证/验收**：Terraform validate/plan 和 IaC 扫描通过；节点可私网拉取 CR，业务 namespace 默认隔离。
- **回滚/提交**：空集群按审批销毁；有工作负载先迁出；`chore: 增加火山引擎 VKE 集群清单`。

### P5B-T02 建立 Helm Chart 与环境配置

- **依赖/并行**：P5B-T01。**规模/角色**：M，SRE。
- **预计文件**：`deploy/helm/interview-coach/Chart.yaml`、`values.yaml`、`values-staging.yaml`、`values-production.yaml`、`templates/_helpers.tpl`。
- **契约与步骤**：只在 values 放非敏感配置和 Secret 名称；image repository/tag/digest 分离且生产强制 digest；命名/labels/annotations 统一；`helm lint/template` 可离线渲染。
- **失败处理**：production 缺 digest、Secret ref、resources 或环境标识时模板 fail；禁止 values 中出现凭据格式。
- **验证/验收**：两环境 lint/template、schema 和 secret scan 通过；相同镜像/环境变量契约与 ECS 一致。
- **回滚/提交**：回退 Chart 版本；`chore: 建立 VKE Helm 部署骨架`。

### P5B-T03 配置工作负载、Service 与 Ingress

- **依赖/并行**：P5B-T02。**规模/角色**：M，SRE/后端。
- **预计文件**：Helm `templates/api-deployment.yaml`、`worker-deployments.yaml`、`frontend-deployment.yaml`、`services.yaml`、`ingress.yaml`。
- **契约与步骤**：API/frontend/cold/recording/outbox 独立 Deployment；仅 frontend/API 有 Service；Ingress 同站点 HTTPS、SSE/上传参数与 ECS 一致；terminationGracePeriod 覆盖最长安全确认时间。
- **失败处理**：worker 不暴露 Service；依赖地址只来自 Secret；preStop 后停止取新任务、完成或安全 requeue。
- **验证/验收**：kubeconform/helm test 通过；staging 业务冒烟、优雅终止和无公网内部端口通过。
- **回滚/提交**：Helm rollback 到上一 Chart+digest；`chore: 增加 VKE 应用工作负载清单`。

#### 检查点 5B-A：P5B-T01～P5B-T03

- [ ] VKE 能按 digest 私网拉镜像并启动所有运行角色。
- [ ] Helm 两环境渲染无 Secret 和可变标签。
- [ ] 对公网仅暴露统一 HTTPS 入口。

### P5B-T04 配置探针、HPA、PDB 与反亲和

- **依赖/并行**：P5B-T03、Phase 4 P4-T06。**规模/角色**：M，SRE。
- **预计文件**：Helm `templates/api-deployment.yaml`、`worker-deployments.yaml`、`hpa.yaml`、`pdb.yaml`、`values-production.yaml`。
- **契约与步骤**：API startup/live/ready 接对应 endpoint；API minReplicas=2；PDB minAvailable=1；跨 hostname/zone 优先反亲和；worker HPA 使用队列深度/最老年龄，未接自定义指标前 min=2 固定副本。
- **失败处理**：外部供应商不进 liveness；探针连续失败仅摘流/重启符合 Phase 4 语义；资源限制避免 OOM 重启循环。
- **验证/验收**：kill pod/node、依赖断开、扩缩容和 drain 测试通过；维护期间至少一个 API ready。
- **回滚/提交**：恢复上一 values 和副本数；`chore: 增加 VKE 高可用与扩缩容策略`。

### P5B-T05 配置 NetworkPolicy 与最小权限

- **依赖/并行**：P5B-T03；可与 P5B-T04 并行。**规模/角色**：M，SRE/安全。
- **预计文件**：Helm `templates/network-policies.yaml`、`service-accounts.yaml`、`security-context.yaml`、`values-production.yaml`、`deploy/volcengine/secret-injection.md`。
- **契约与步骤**：namespace default deny；按 API/worker/collector 放行 DNS、PG/Redis/Rabbit/TOS/供应商；独立 ServiceAccount，无 token automount（无 API 需求）；非 root、seccomp、drop capabilities、只读 rootfs。
- **失败处理**：网络需求新增必须更新 allowlist 和测试；Secret 仅外部创建并以 name/key 引用，Helm 不生成值。
- **验证/验收**：允许依赖连通、禁止的跨 workload/公网端口不通；Kubernetes/Trivy policy 扫描无 High/Critical。
- **回滚/提交**：先恢复上一已审查 policy，不临时 default allow；`chore: 收紧 VKE 网络与运行权限`。

### P5B-T06 建立 Alembic Migration Job

- **依赖/并行**：P5B-T02、Phase 1。**规模/角色**：M，SRE/DBA。
- **预计文件**：Helm `templates/migration-job.yaml`、`templates/migration-rbac.yaml`、`values.yaml`、`.github/workflows/release.yml`、`deploy/runbooks/database-cutover.md`。
- **契约与步骤**：Job 名含 release revision，`backoffLimit=0`，使用 migration 专用 Secret；release 先 `alembic check/current/upgrade head`，成功才部署；同版本用数据库 advisory lock 防重复。
- **失败处理**：Job 失败立即停止放量；不自动 downgrade；旧应用必须兼容 expand Schema；日志不含 DSN。
- **验证/验收**：两个并发 Job 只有一个执行；失败 revision 阻止发布；成功后 ready 检查 Alembic head。
- **回滚/提交**：代码/镜像回滚，Schema 前向修复；`ci: 增加发布前数据库迁移作业`。

#### 检查点 5B-B：P5B-T04～P5B-T06

- [ ] 双副本跨节点运行，drain/kill 不中断核心流量。
- [ ] 默认拒绝网络与最小权限验证通过。
- [ ] Migration Job 不会被多副本重复执行。

### P5B-T07 部署 Argo Rollouts 金丝雀发布

- **依赖/并行**：检查点 5B-B。**规模/角色**：M，SRE/CI。
- **预计文件**：Helm `templates/api-rollout.yaml`、`templates/preview-services.yaml`、`templates/analysis-template.yaml`、`values-production.yaml`、`.github/workflows/release.yml`。
- **契约与步骤**：API 从 Deployment 转 Rollout；权重 10/25/50/100；每阶段最少 10 分钟和 100 请求；worker 使用先新后旧、兼容 payload 的受控滚动；GitHub 只提交 digest/Chart 版本并等待 Rollout。
- **失败处理**：Analysis failed/error、readiness、迁移、安全扫描任一失败自动 abort；生产 promote/abort 需审计 actor。
- **验证/验收**：成功逐级晋级；故意错误版本在 25% 前 abort；旧 ReplicaSet/digest 保留 24 小时。
- **回滚/提交**：`kubectl argo rollouts undo`/Helm 恢复上一 digest；`ci: 增加 VKE 金丝雀发布流程`。

### P5B-T08 建立自动晋级与回滚阈值

- **依赖/并行**：P5B-T07、Phase 4。**规模/角色**：M，SRE/业务负责人。
- **预计文件**：Helm `templates/analysis-template.yaml`、`observability/prometheus/recording-rules.yaml`、`deploy/runbooks/rollback.md`、`deploy/tests/test_rollout_analysis.yaml`。
- **契约与步骤**：canary 5xx/业务失败率不得高于基线+0.5 个百分点，p95 不得劣化>20%，ready pod 必须全程满足目标，队列/outbox 不持续增长；无有效指标视为失败而非通过。
- **失败处理**：Critical/High 新告警直接 abort；低流量按锁定 30 分钟审批规则；回滚顺序为新 worker→API→frontend。
- **验证/验收**：合成 Prometheus 序列覆盖通过/失败/无数据；abort 后 15 分钟内恢复旧版本 SLO。
- **回滚/提交**：恢复上一 AnalysisTemplate；`feat: 增加灰度自动验收与回滚门槛`。

### P5B-T09 将 Grafana Stack 迁移到 VKE

- **依赖/并行**：P5B-T05、Phase 4。**规模/角色**：M，SRE。
- **预计文件**：`deploy/helm/observability/Chart.yaml`、`values-production.yaml`、`templates/storage.yaml`、`templates/network-policy.yaml`、`deploy/runbooks/observability-restore.md`。
- **契约与步骤**：部署 Collector/Prometheus/Tempo/Loki/Grafana；metrics/log 30 天、trace 7 天；持久卷/对象存储按组件能力配置；Grafana 身份和数据源凭据外部注入。
- **失败处理**：生产不使用 emptyDir 保存事实遥测；采集后端故障不影响应用；容量达到 80% 告警，保留策略自动清理。
- **验证/验收**：重建 Pod 不丢保留窗口内数据；Dashboard/alerts provisioning；ECS 与 VKE 短期双写核对后停止 ECS 栈。
- **回滚/提交**：迁移观察期内恢复 ECS endpoint；`chore: 将可观测性栈迁移到 VKE`。

### P5B-T10 执行 VKE 企业级验收

- **依赖/并行**：P5B-T08、P5B-T09；最后执行。**规模/角色**：M，发布/测试/SRE。
- **预计文件**：`e2e/tests/deployment-smoke.spec.ts`、`scripts/verify-vke-ha.ps1`、`docs/enterprise-evolution/evidence/phase-5b-acceptance.md`。
- **契约与步骤**：执行双副本、Pod/节点 kill、drain、扩缩容、migration 冲突、canary abort/undo、遥测重启和完整 E2E；记录实际 RTO。
- **失败处理**：任何单 Pod/单节点故障导致核心业务不可用、重复 migration/任务、丢失已提交任务或无法回滚均阻止完成。
- **验证/验收**：API/worker≥2 副本；故障不丢任务；canary 自动回滚≤15 分钟；新环境可由 Terraform+Helm+外部 Secret 清单重建。
- **回滚/提交**：恢复上一 Chart/digest，数据服务不回退；`test: 增加 VKE 高可用发布验收`。

## 5. 阶段退出门槛

- [ ] ECS 过渡形态具备 TLS、托管数据、双槽更新和 15 分钟应用回滚。
- [ ] VKE API/worker 至少两个副本，跨节点调度，单 Pod/节点故障不影响核心链路。
- [ ] Argo Rollouts 自动灰度/停止/回滚和 Migration Job 经故障演练。
- [ ] 所有部署使用镜像 digest；Secret 不在代码、镜像、Terraform output 或 Helm values 中。
- [ ] Terraform、Compose、Helm 和 Runbook 能重建新环境。

## 6. 风险、交接与参考

| 风险 | 缓解 |
|---|---|
| ECS 单机故障 | 明确为过渡形态、托管数据外置、快速重建和 VKE 迁移 |
| ECS/VKE 产生配置漂移 | 同一镜像/环境变量，契约测试和共享 values 映射 |
| canary 低流量误判 | 最小请求数+最长观察时间+人工审批，不以无数据通过 |
| migration 与回滚不兼容 | expand-contract、advisory lock、发布前 Job |
| 云资源误暴露/误删除 | Terraform plan 审批、VPC only、生产不自动 destroy |

交给 Phase 6：镜像/Chart/digest、release workflow、Terraform/Helm 清单、环境保护名称、恢复 Runbook 和实际 RTO。

官方参考：[ECS 云服务器](https://www.volcengine.com/docs/6396?lang=zh)、[VKE 容器服务](https://www.volcengine.com/docs/6460?lang=zh)、[RabbitMQ 版](https://www.volcengine.com/docs/6451?lang=zh)。实施时以锁定日期的官方产品能力和配额为准。
