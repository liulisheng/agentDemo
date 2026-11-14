# 独立实现的交互规格

本文档描述本项目可以独立实现的行为，不规定 StaffDeck 的页面复制。

## 员工广场

### 员工卡片

系统 SHALL 展示数字员工名称、岗位、状态、描述和能力统计。

WHEN 用户点击卡片，系统 SHALL 打开员工详情而不是立即创建 Session。

### 使用员工

WHEN 用户在详情中点击“使用员工”，系统 SHALL 创建或解析一个面向该数字员工的工作台上下文。

系统 SHALL 在进入对话前加载该员工的 CompositionSnapshot 所需资源引用。

## 员工工作台

工作台 SHALL 至少提供以下分区：

```text
基本资料
工作记录
定时任务
记忆
对话日志
知识库
技能
SOP
工具
反馈自进化
```

反馈自进化候选 SHALL 先进入待审核状态，管理员批准后才能生成新的 Skill/SOP 版本。

## 对话工作区

WHEN 用户第一次发送消息，系统 SHALL：

1. 解析当前 DigitalEmployeeDeployment。
2. 创建或恢复 Session。
3. 保存 User Message。
4. 创建 Turn/Run。
5. 生成 CompositionSnapshot。
6. 调用 RuntimeAdapter。
7. 发布流式事件和可见消息。

系统 SHALL 提供：

- Session 列表。
- 新建对话。
- 消息历史。
- 当前员工信息。
- 当前模型配置。
- 执行记录入口。
- 停止生成。
- 发送队列。

## Skill 和能力展示

数字员工详情 SHALL 显示资料、Skill、SOP 和 Tool 的数量。

能力详情 SHALL 能追溯到：

```text
数字员工版本
    -> CapabilityBinding
        -> resource_ref
            -> Knowledge / Skill / SOP / Tool
```

## 页面和领域边界

页面展示可以参考 Gallery、Dashboard、Skill List 和 Chat Workspace 的信息架构，但实现必须使用本项目的：

- DigitalEmployeeVersion
- CapabilityBinding
- CompositionSnapshot
- RuntimeAdapter
- Session
- Run
- Trace
