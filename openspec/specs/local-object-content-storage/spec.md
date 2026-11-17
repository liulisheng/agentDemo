# local-object-content-storage Specification

## Purpose

在当前本地部署中安全持久化消息正文、运行结果和策略允许的 payload，并为后续替换 MinIO、S3、OSS 或 COS 保持稳定的对象引用行为。

## Requirements

### Requirement: Local object keys are generated within tenant scope
系统 SHALL 在服务端生成包含 platform 和 tenant 边界的对象引用，不向客户端暴露可任意选择的宿主机路径。

#### Scenario: Create an upload intent
- **WHEN** 已认证租户为受支持的内容类型申请上传
- **THEN** 系统生成不可由客户端指定的租户范围对象引用和有期限的上传 intent

### Requirement: Object upload is bounded and verified
系统 SHALL 在完成上传时校验 intent 状态、租户范围、大小限制和内容摘要，并仅在内容与 intent 一致时发布对象引用。

#### Scenario: Upload content matching its intent
- **WHEN** 客户端提交大小符合 intent 的内容
- **THEN** 系统保存对象并返回含哈希、大小和状态的 MediaAsset 引用

#### Scenario: Upload exceeds the declared bound
- **WHEN** 上传内容超出配置大小或与 intent 声明不符
- **THEN** 系统拒绝发布对象引用且内容不可通过平台读取

### Requirement: Object reads and deletes are tenant authorized
系统 SHALL 在每次对象读取或删除时校验 platform、tenant 和对象归属，且删除请求提交后立即撤销应用读取权限。

#### Scenario: Cross-tenant object read
- **WHEN** 其他租户使用对象引用读取内容
- **THEN** 系统按对象不存在或无权访问处理且不返回正文

### Requirement: Object cleanup is retryable and race safe
系统 SHALL 通过可重试的 GC 队列清理孤儿对象，并避免过期 intent、迟到上传或有效业务引用导致已发布内容丢失。

#### Scenario: Delete fails and is retried
- **WHEN** 对象删除操作暂时失败
- **THEN** 系统保留清理任务和重试状态，后续重试可以完成删除

#### Scenario: Late upload completes after cleanup scan
- **WHEN** 上传在一次孤儿扫描之后才完成且其 intent 已失效
- **THEN** 系统不得发布可读业务引用，并在后续扫描中重新发现并排队清理该对象

### Requirement: Raw payload storage follows retention policy
系统 SHALL 按租户或项目策略决定是否保存完整 prompt、工具 payload 和 Runtime 结果，普通 Trace 仅记录安全元数据及对象引用。

#### Scenario: Raw payload retention is disabled
- **WHEN** 当前策略关闭原始 payload 保存
- **THEN** 系统不创建正文对象且 Trace 不包含原始内容

### Requirement: Storage provider selection is explicit
系统 SHALL 通过 ObjectStore provider 配置选择存储后端，未安装的 provider 必须明确失败且不得静默回退到本地磁盘。

#### Scenario: External provider is selected but unavailable
- **WHEN** 配置选择尚未安装的 MinIO、S3、OSS 或 COS adapter
- **THEN** 系统返回明确的 provider unavailable 错误且不写入其他后端
