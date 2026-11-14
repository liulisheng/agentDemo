# 独立领域模型参考

以下模型来自黑盒行为和响应字段观察，名称和实现属于本项目自己的设计。

## DigitalEmployee

```text
DigitalEmployee
- id
- platform_id
- tenant_id
- name
- description
- role_name
- owner_user_id
- status
- published_to_gallery
- directory_access
- directory_statistics
- resource_counts
```

`resource_counts` 至少包含：

```text
knowledge_base
general_skill
skill
tool
sop
```

## CapabilityBinding

数字员工通过能力绑定组合知识库、Skill、SOP 和 Tool：

```text
CapabilityBinding
- binding_id
- staff_id
- slot
- module_id
- module_version
- config_revision
- resource_ref
```

独立实现中建议使用：

```text
DigitalEmployeeVersion
    +-- KnowledgeBinding[]
    +-- SkillBinding[]
    +-- SopBinding[]
    +-- ToolBinding[]
    +-- ExecutionProfile
```

## CompositionSnapshot

Runtime 启动前生成不可变组成快照：

```text
CompositionSnapshot
- snapshot_id
- digital_employee_version_id
- execution_engine
- security_profile
- skill_refs
- sop_refs
- knowledge_refs
- tool_refs
- trace_names
- created_at
```

黑盒观察中快照还包含 Skill 名称、步骤名称和工具名称之间的映射。

## Session、Message、Turn、Trace

```text
Conversation
    |
    v
Session
    |
    +-- Message
    +-- Turn
    +-- Event
    +-- Trace
    +-- RuntimeSessionRef
```

Session 观察字段：

```text
id
tenant_id
user_id
agent_id
agent_name
status
title
summary
active_skill_id
active_step_id
is_scheduled
team_id
team_name
created_at
updated_at
```

Message 观察字段：

```text
id
tenant_id
session_id
role
content
metadata.client_turn_id
metadata.model_config_id
turn_id
created_at
feedback_rating
```

Trace 观察字段：

```text
turn_id
user_message_id
started_at
completed_at
lines[]
```

## ModelConfig

黑盒观察到模型配置包含：

```text
ModelConfig
- id
- tenant_id
- name
- provider
- api_protocol
- base_url
- model
- temperature
- max_output_tokens
- protocol_options
- trust_status
- verification_attempt_status
- config_revision
- security_revision
- is_default
- enabled
```

本项目使用 `ModelRoute` 对应这些行为，并将密钥保存为 Secret 引用。
