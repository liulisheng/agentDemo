# 黑盒证据、限制和未知项

## Evidence

| 场景 | REA Evidence ID | 结果 |
|---|---|---|
| 登录和初始化 | `ev_f715d5450c9af4601f615f26518eeb70bccfc6bc5410c5d7c90d6b4969eb36a6` | 登录成功，加载租户资源和首次引导 |
| Gallery | `ev_7a4ef0f44dae3e24cfe219d9a83e7743f960fb99ecc2bad0b735fcc757352d29` | 观察平台导航、资源广场和员工卡片 |
| Agent/Session/Run | `ev_ff7207eb9f95f684c80336ff468908dc647b3b489a377f44e12f888ccd8ff246` | 观察员工详情、工作台、对话和 Runtime 调用 |
| Skill 页面 | `ev_ae682d83982503b15fb530e920d9859bcda0a1e754eb882ebf7aaeaeac13e8d6` | 观察 Skill 列表、状态和能力范围 |

临时完整捕获文件位于：

```text
/tmp/staffdeck-login-run-capture.json
/tmp/staffdeck-gallery-final-capture.json
/tmp/staffdeck-agent-chat-capture.json
/tmp/staffdeck-skill-page-capture.json
```

## 已观察事实

- `/api/auth/login` 返回 200。
- 租户范围为 `tenant_demo`。
- 员工广场显示 5 个可聊天数字员工。
- 员工卡片显示资料、Skill、SOP 数量。
- 员工工作台加载知识库、通用 Skill、Skill、Tool、Session、反馈、定时任务和工作记录。
- Runtime composition snapshot 保存执行引擎、安全配置、Skill/SOP 列表和能力绑定。
- 首次发送消息创建 Session，状态为 `running`。
- 对话请求进入 `/api/chat/stream`。
- 消息、事件和 Trace 有独立查询 API。
- Session 记录 `agent_id`、`tenant_id`、`user_id`、状态、摘要、活动 Skill/Step 和团队关联。

## 未知项

- `/api/chat/stream` 的完整请求体和最终响应格式。
- Runtime 最终完成、失败、取消和超时状态的完整转换。
- Tool Call 的实际请求和响应内容。
- Skill/SOP 每一步的执行事件和状态转换。
- 知识库导入、检索和引用的完整流程。
- 记忆写入规则和删除规则。
- 团队、多 Agent、人工接管和渠道接入的完整交互。

## 证据限制

- 场景使用 REA 创建的隔离 Chromium，不代表用户现有浏览器中的历史状态。
- 网络事件是浏览器接收顺序，不足以证明精确因果关系。
- 部分响应体被脱敏或在捕获截止时仍未完成。
- `/api/chat/stream` 在本次 3 秒观察窗口内未完成，最终助手消息不能据此推断。
- 未复制 StaffDeck 源代码、静态资源、样式或前端 Bundle。
