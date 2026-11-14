# Agent 执行示例

更新时间：2026-11-15

## 日志分析 Agent

- Engine：Fake 或 DeepSeek RuntimeAdapter
- Skill：instruction + tool-bundle（只读日志查询）
- Tool：LogQueryCapability
- Workspace：NO_FILES
- Context：最近消息、Tenant memory、日志引用，预算由 BusinessContextPolicy 控制
- Approval：读取允许，重启/删除动作不在能力集合中

## 代码开发 Agent

- Engine：Fake 或 Codex RuntimeAdapter
- Skill：workflow（checkout、修改、测试、diff）
- Tool：Git checkout 和受控测试工具
- Workspace：EPHEMERAL/PERSISTENT，绑定 repository、commit 和 project
- Context：员工版本、Skill、Memory、工具结果引用
- Approval：写入主分支和生产操作需要审批

两个例子都先解析 ExecutionProfile、Workspace/Sandbox、ContextSnapshot 和 Skill/Tool policy，再创建 Run；Runtime 内部 shell、编辑器和私有记忆不由平台重复实现。
