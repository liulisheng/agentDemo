# Spec Delta

## ADDED Requirements

### Requirement: Task outputs become tenant-scoped execution artifacts

系统 SHALL 将团队 Task 或单员工 Run 产生的文本、结构化结果、文件、图片、SQL、补丁和报告登记为带有 Conversation、Task、Run、Attempt、EngineProvider 和 ModelRoute 来源的 ExecutionArtifact。

#### Scenario: Register a file output
- **WHEN** Worker 为 Task 生成 `sales-summary.csv`
- **THEN** 系统将文件写入授权 ObjectStore，创建 MediaAsset 和 ExecutionArtifact，并记录 SHA-256、类型、大小、来源任务和引擎

#### Scenario: Runtime returns only text
- **WHEN** Runtime 只返回文本结果而没有文件
- **THEN** 系统保存 TaskOutput 摘要，并允许汇总 Agent 或用户在 Conversation 中查看，不强制创建空文件 Artifact

### Requirement: Artifact lifecycle is visible and retryable

系统 SHALL 为 ExecutionArtifact 提供 `CREATED`、`UPLOADING`、`SCANNING`、`READY`、`DELIVERING`、`DELIVERED`、`FAILED` 和 `EXPIRED` 状态，并记录处理错误和重试信息。

#### Scenario: Artifact scanning is pending
- **WHEN** 文件已上传但安全扫描尚未完成
- **THEN** 工作台显示扫描中，文件不能进入外部交付包，但任务状态和其他已就绪 Artifact 不受影响

#### Scenario: Artifact processing fails
- **WHEN** 文件扫描、打包或预览处理失败
- **THEN** Artifact 标记 FAILED，记录稳定错误类别，并提供符合策略的重试入口

### Requirement: Artifact delivery uses a manifest and idempotency

系统 SHALL 通过 Delivery Manifest 描述最终答复、任务结果、产出物来源和交付范围，并以租户、Artifact、渠道和幂等键防止重复交付。

#### Scenario: Confirm web delivery
- **WHEN** 用户确认交付两个 READY Artifact
- **THEN** 系统创建带幂等键的 ArtifactDelivery，更新交付状态并在 Conversation 中展示待发送或已发送结果

#### Scenario: Repeat delivery request
- **WHEN** 客户端使用同一幂等键重复发起相同交付
- **THEN** 系统返回原交付结果，不创建重复的外部消息或交付记录

### Requirement: Partial orchestration failure preserves completed artifacts

系统 SHALL 在部分任务失败时保留已完成 Artifact，最终交付清单必须区分已交付、可交付、扫描中、失败和未生成的产出物。

#### Scenario: Deliver completed outputs after a task failure
- **WHEN** T2 成功生成 CSV、T3 因 Worker 故障失败且 T4 尚未执行
- **THEN** 工作台显示 `PARTIAL_FAILED`，允许用户预览或交付 T2 的 CSV，并显示 T3 的失败原因

#### Scenario: Cancelled task output
- **WHEN** 用户取消正在生成的 Artifact 对应 Task
- **THEN** 未完成 Artifact 不进入 READY 或外部交付，已完成的 Artifact 按保留策略继续可读

### Requirement: Artifact preview and download enforce visibility

系统 SHALL 按 platform、tenant、Conversation 参与关系、Artifact visibility 和用户角色授权预览、下载、加入交付包和外部投递。

#### Scenario: Viewer reads an artifact
- **WHEN** Viewer 请求团队 Artifact 的预览或下载
- **THEN** 系统只在该 Artifact 对 Viewer 可见时返回脱敏内容或短期授权 URL，不提供修改、删除或交付操作

#### Scenario: Cross-tenant artifact request
- **WHEN** 用户使用其他租户的 artifact_id 或 object_ref 请求下载
- **THEN** 系统按资源不存在处理，拒绝底层对象读取并不泄露文件存在性

### Requirement: Artifact bundle preserves provenance

系统 SHALL 生成 ZIP、报告包或其他组合交付物时保留每个成员 Artifact 的来源 Task、Run、Attempt、EngineProvider、时间和内容摘要。

#### Scenario: Create a delivery bundle
- **WHEN** 用户选择多个 READY Artifact 创建交付包
- **THEN** 平台生成组合 Artifact 和 Delivery Manifest，并可追溯包内每个文件的来源和 SHA-256
