# 上下文与企业记忆

更新时间：2026-11-15
状态：业务上下文预算、记忆作用域和引用 Trace 已实现；Runtime 私有压缩未接管

## 架构图

~~~mermaid
flowchart LR
  Agent[员工版本] --> Assembler[BusinessContextAssembler]
  Skill[Skill 引用] --> Assembler
  Message[最近消息] --> Assembler
  Memory[EnterpriseMemory] --> Assembler
  Tool[Tool 结果引用] --> Assembler
  Assembler --> Snapshot[ContextSnapshot]
  Snapshot --> Runtime[RuntimeAdapter]
~~~

ContextSnapshot 记录来源引用、哈希、估算 Token、纳入/排除原因和 Runtime handoff metadata。超过预算时按 policy 裁剪；不解析 Runtime 私有历史。

## 记忆作用域

Conversation、User、Project、Tenant、Agent 记忆都必须带 platform/tenant 边界；只有显式 MemoryStore.write 才写入长期记忆。普通聊天不会自动升级为长期记忆。

## 验证

~~~bash
cd backend
python -m pytest tests/test_context_memory.py -q
~~~

未验证：向量检索、真实摘要模型和跨进程 MemoryStore。
