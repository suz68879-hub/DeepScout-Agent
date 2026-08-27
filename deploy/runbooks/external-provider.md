# ARK、ASR 与 RTC 外部供应商故障 Runbook

## 影响与检测

关联告警：`FirstTokenLatencyHigh`、`ExternalProviderErrorRateHigh`。影响可能是首 token
变慢、录音转写失败、RTC 启停失败或对话降级；PostgreSQL/Redis/RabbitMQ 可能健康。

- Dashboard：`/d/deepscout-dependencies` 和 `/d/deepscout-api`。
- 责任人：`ai-oncall`，RTC/网络协同人为 `backend-oncall`。

## 权限与安全边界

供应商控制台使用企业 SSO 只读权限。配额、模型端点、地域、超时、降级与凭据轮换均
是 **审批点**。禁止复制 Prompt、转写、录音 URL、签名 query、AK/SK/API key 或完整
响应到工单；仅使用 provider/operation/model/outcome/http_status 和 trace_id。

## 诊断顺序

1. 按 provider/operation/outcome 区分 `timeout`、`rate_limited`、`connection` 和
   `provider`，确认单模型、单操作还是全供应商故障。
2. 查看首 token p95、外部 p95、请求量和错误比例，低流量时不凭单样本升级。
3. 从脱敏 trace 检查 ARK/ASR/RTC span 与上游 API/worker；不得开启正文采集。
4. 在供应商控制台检查服务状态、配额、限流、模型发布和证书，不查看用户内容。
5. 比较不同应用实例和网络出口，排除本地 DNS/TLS/代理故障。
6. ASR 轮询故障时核对公共状态码分布；RTC 检查 Start/Stop 幂等状态和锁，不重发。

## 安全缓解与审批点

- 限流：保留有界退避，限制新冷任务/录音流量，不提高无界重试。
- ARK：使用既有安全错误话术；**审批后**才可切换已验证的备用 endpoint/model。
- ASR：保持 task_id 幂等轮询；不得重新上传同一录音制造重复任务。
- RTC：保持 session fence 和分布式锁；不得重复 StartVoiceChat 或绕过签名验证。
- 网络故障：按出口/证书变更流程处理，禁止关闭 TLS 校验。

## 升级与恢复确认

立即升级条件：供应商全局故障、配额耗尽、疑似凭据泄漏、签名异常、跨租户内容或
15 分钟未恢复。通知 AI/后端/SRE；泄漏事件通知安全并启动凭据轮换。

恢复标准：外部错误率和 p95 回归基线 10 分钟；首 token p95 <2s；积压下降；同一
ASR/RTC task 未重复创建；敏感样本扫描零命中。记录 provider 状态、审批、备用端点
使用情况和恢复时间，不记录输入输出正文。

## 演练记录

预发将供应商桩切为 429、timeout、恢复：记录告警与恢复耗时，确认稳定 error.type、
有界重试、单次调用和零正文遥测。
