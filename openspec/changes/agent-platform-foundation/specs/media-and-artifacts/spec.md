# Spec Delta

## Purpose

为图片、文件、音频、视频和 Runtime 产物提供租户隔离、可处理、可审计的对象存储能力，并允许平台通过配置选择本地或外部对象存储后端。

## ADDED Requirements

### Requirement: Object storage backend is configurable
系统 SHALL 支持 local、s3、minio、oss 和 cos 存储提供方，并通过 StorageProfile 显式选择当前 platform、tenant 或 project 的后端。

#### Scenario: Local development storage
- **WHEN** 环境配置 provider 为 local
- **THEN** 上传内容写入配置的本地存储根目录，并通过受控上传/下载接口访问

#### Scenario: External storage selection
- **WHEN** 租户绑定 minio、oss、cos 或 s3 StorageProfile
- **THEN** 新上传使用该后端生成的受控地址，且不写入本地存储

### Requirement: Storage selection does not silently fallback
系统 SHALL 在选定存储后端不可用、配置无效或凭据缺失时返回明确错误，不自动切换到其他后端。

#### Scenario: External storage unavailable
- **WHEN** OSS 凭据无效或对象存储不可访问
- **THEN** 上传或读取失败并保留可重试状态，不产生本地副本

### Requirement: Media upload uses an intent and tenant-safe object reference
系统 SHALL 在上传前创建租户范围 upload intent，并由服务端生成不可猜测的 object_ref；客户端不能提交宿主路径或任意对象键。

#### Scenario: Upload completion
- **WHEN** 客户端完成文件上传并提交 upload_id
- **THEN** 系统校验租户、哈希、大小和 intent 状态后发布 MediaAsset

### Requirement: Media lifecycle is observable
系统 SHALL 支持 INIT、UPLOADING、UPLOADED、SCANNING、PROCESSING、READY、FAILED、REJECTED 和 DELETED 状态，并记录处理错误和重试信息。

#### Scenario: Processing failure
- **WHEN** OCR、ASR、转码或安全扫描失败
- **THEN** MediaAsset 标记 FAILED 或 REJECTED，记录错误类别并允许符合策略的重试

### Requirement: Object references are tenant scoped
系统 SHALL 对 MediaAsset、派生文件、Runtime 产物和删除任务执行 platform_id + tenant_id 授权，读取不能跨租户。

#### Scenario: Cross-tenant object read
- **WHEN** 用户使用其他租户的 asset_id 或 object_ref 读取对象
- **THEN** 系统按不存在处理并拒绝底层对象读取

### Requirement: Deletion and garbage collection are retryable
系统 SHALL 先撤销对象读取，再将原始文件和派生文件引用加入可重试 GC 队列，清理失败不得阻塞数据库删除事务。

#### Scenario: Retry object deletion
- **WHEN** 外部对象存储暂时拒绝删除
- **THEN** GC 任务保存稳定错误码、重试次数和下一次执行时间，并最终完成清理

### Requirement: Platform storage is the canonical source for conversation attachments
系统 SHALL 将需要持久化、审计、复用或跨 Runtime 使用的对话附件保存为平台 MediaAsset；Runtime Workspace 或供应商文件引用只能作为执行副本或 opaque 引用。

#### Scenario: Attach file to a message
- **WHEN** 用户向 Codex、DeepSeek 或其他 Runtime 对话上传文件
- **THEN** 平台创建 MediaAsset 和 MessageAttachment，并把 RuntimeAttachmentRef 交给 RuntimeAdapter

#### Scenario: Runtime workspace cleanup
- **WHEN** Runtime 完成 Run 并清理 Workspace
- **THEN** 平台 MediaAsset 仍按保留策略可读取，除非用户或策略已请求删除

### Requirement: Attachment retention is explicit
系统 SHALL 支持 EPHEMERAL、SESSION、CONVERSATION 和 KNOWLEDGE_BASE 保留策略，并在创建附件时记录策略。

#### Scenario: Ephemeral code attachment
- **WHEN** 代码 Agent 使用 EPHEMERAL 文件完成 Run
- **THEN** 系统在宽限期后清理执行副本和不再引用的对象，但保留必要的审计元数据

#### Scenario: Conversation attachment reuse
- **WHEN** 后续 Run 引用 CONVERSATION 附件
- **THEN** 系统重新校验租户授权后从平台 ObjectStore 生成新的 RuntimeAttachmentRef

### Requirement: Runtime attachment conversion is engine-specific
系统 SHALL 由 RuntimeAdapter 将平台 MediaAsset 转换为目标引擎支持的图片、文件、音频、视频或派生文本输入，不要求不同引擎共享私有文件格式。

#### Scenario: Unsupported video input
- **WHEN** 目标 Runtime 不支持原始视频输入
- **THEN** 系统使用已授权的 ASR、关键帧或摘要派生内容，并记录转换结果和限制
