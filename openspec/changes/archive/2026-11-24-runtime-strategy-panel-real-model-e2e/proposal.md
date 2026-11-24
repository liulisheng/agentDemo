# Proposal

## Why

当前运行配置弹框把引擎、模型和回答策略压在一个窄表单中，用户难以判断“当前生效值、即将覆盖的值和新 Session 的影响范围”。平台虽然已经保存 `RuntimeOptions`，但还缺少从策略面板进入新对话、使用真实 OpenAI-compatible ModelRoute 执行一轮并回显快照的可验证闭环。

## What Changes

- 将运行配置抽为独立的 `RuntimeStrategyPanel`，由默认运行环境、Deployment 弹框和工作台新 Session 入口共用。
- 将配置面板改为浅色管理台双栏布局：左侧编辑策略，右侧显示当前生效值、来源、引擎能力、覆盖差异和“仅影响新 Session”说明。
- 支持思考模式、推理强度、答复详细程度、推理预算，并显示能力不支持、继承、清除和只读状态。
- 保存成功后提供“使用此配置新建对话”入口；新 Session 固定策略快照，旧 Conversation 不静默切换。
- 为真实 OpenAI-compatible 本地/内网 ModelRoute 增加明确的验收路径：通过现有 `local-model-key` SecretRef 和 `http://host.docker.internal:63072/v1` 约定，发送一轮受限真实请求，回显策略、模型、阶段和 Usage。
- 将真实模型验收与 Fake Runtime、确定性 Mock 分开记录；不把本地 Mock 结果标记为真实模型结果。
- 保持平台统一 `RuntimeOptions`，由 Harness/Codex/其他 Adapter 做 capability 校验和私有参数映射，不把供应商字段暴露给前端。
- 更新中文需求、架构图、流程图、验收记录和独立 HTML 原型，明确真实模型凭据只来自未提交环境变量。

## Capabilities

### New Capabilities

无。本变更扩展现有运行配置、执行引擎、工作台和观测能力。

### Modified Capabilities

- `model-management`：运行策略改为独立面板，提供作用域默认、Deployment 覆盖、能力提示和真实 ModelRoute 验收入口。
- `platform-api`：增加策略面板所需的有效值/来源/能力摘要和新 Session 启动参数，保持租户与角色边界。
- `execution-engines`：统一策略在 OpenAI-compatible、DeepSeek Harness、Codex 和 Fake 之间的能力校验、映射和真实请求标识。
- `conversations-and-channels`：从策略面板新建 Conversation/Session，并在工作台展示固定策略与阶段结果。
- `execution-observability`：记录真实模型请求的非敏感 Provider 标识、策略摘要、阶段、耗时和 Usage，不保存凭据或原始思考链。
- `documentation-and-design`：新增独立策略面板原型、真实模型流程、状态说明和验收边界。

## Impact

- **前端：** 新增可复用策略面板、Deployment/默认环境/工作台入口和组件状态测试；调整弹框宽度、层级、间距和响应式布局。
- **后端：** 新增策略面板读取/保存/启动 Session 的控制面组合，复用现有 `RuntimeOptions`、Session/RunSnapshot 和 ModelRoute 授权。
- **Runtime：** 增加 OpenAI-compatible 真实请求验收适配与请求引用脱敏；不改变 Fake 的离线契约。
- **部署：** Compose 通过 Worker-only SecretRef 注入真实模型凭据；API/Web 容器不得读取凭据。
- **数据库：** 本期复用现有策略 JSON、Session/RunSnapshot 和 ModelRoute 表，不新增表；若发现需要结构变更，必须另建带中文 COMMENT 的 forward migration。
- **明确不做：** 不展示原始思考链；不把 API Key 写入前端、日志、Trace 或文档；不自动切换模型；不把确定性 Mock 当作真实模型验收；不在本期实现 Codex Tool/MCP 或生产身份中心。

## 分阶段交付边界

### 本期

- 独立策略面板和四项配置完整交互。
- 配置保存、能力提示、新 Session 入口和旧 Session 快照保持。
- 使用已授权的真实 OpenAI-compatible ModelRoute 完成一轮真实模型调用，并在工作台展示阶段、秒级时间和 Usage。

### 后续

- 多 Provider 真实 reasoning 字段的长期契约验证。
- Codex app-server 真实 Turn、长时恢复和取消验证。
- 成本预算、上下文压缩、策略模板和生产 Secret Manager 接入。

## 验收目标

- 运行配置不再是拥挤的单栏表单，双栏策略面板与现有浅色原型一致且窄屏可读。
- 管理员保存策略后能从同一面板新建对话；真实 OpenAI-compatible 模型返回可见答复。
- Session/Run 快照中的策略、模型路由、配置修订和来源与面板显示一致。
- 工作台能看到“思考中/调用工具/生成答复”阶段和 `YYYY-MM-DD HH:mm:ss` 时间。
- 真实模型调用失败、超时、缺少 SecretRef 和能力不支持均显示稳定错误，不切换 Fake。
