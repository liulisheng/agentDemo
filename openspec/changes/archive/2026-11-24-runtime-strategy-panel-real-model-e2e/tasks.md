# Tasks

## 1. 设计与原型基线

- [x] 1.1 新增 `docs/原型/运行策略独立面板.html`，覆盖双栏配置、当前生效值、来源、覆盖差异、能力不支持、管理员/成员、加载/空/错误/保存成功和真实 Run 结果状态；用 HTML parser 和 `node --check` 验证字段、状态文案及原型脚本。
- [x] 1.2 更新 `docs/思考模式与回答策略方案.md`、`docs/当前方案需求与设计总览.md`、`docs/前端交互设计.md` 和 `docs/验证记录.md`，补充独立面板、真实 OpenAI-compatible Endpoint、SecretRef 边界和验收证据；文档完整性测试通过。

## 2. 独立策略面板

- [x] 2.1 新增可复用 `RuntimeStrategyPanel` 组件，严格按 `docs/原型/运行策略独立面板.html` 实现双栏布局、策略字段、继承/清除、当前生效值、来源和差异摘要；组件测试覆盖管理员、成员只读、窄屏和键盘焦点，并用原型关键文案/状态检查证明实现没有未记录差异。
- [x] 2.2 将默认运行环境页接入策略面板，保留 EngineProfile/ModelRoute 独立选择和作用域权限；测试保存个人/租户策略、加载、空和错误状态。
- [x] 2.3 将 Deployment 运行配置弹框替换为策略面板宿主，移除拥挤的嵌套表单，增加能力提示、冲突重试和“保存并新建对话”；测试修订冲突和旧 Session 提示。
- [x] 2.4 将工作台接入“新建对话使用此配置”入口，保存成功后显示修订和策略快照；测试不重复创建 Conversation/Session。

## 3. 控制面与 Runtime 链路

- [x] 3.1 增加面板组合读取/保存响应，返回有效策略、字段来源、EngineProvider capability、ModelRoute 摘要和新 Session 上下文；测试跨租户、成员只读、过期修订和能力拒绝。
- [x] 3.2 校验保存并新建流程只使用服务端解析的 Deployment/User/Tenant 策略和固定 ModelRoute；测试旧 Conversation 不切换，新 Session 使用新修订。
- [x] 3.3 为真实 OpenAI-compatible ModelRoute 增加 NATIVE 出站验收适配，使用现有 `local-model-key` SecretRef、模型 ID 和 endpoint；测试请求不经过 Fake、Secret 不进入日志、Usage/request reference 可归一化。
- [x] 3.4 补齐真实模型失败边界：401/403、模型不存在、协议错误、超时 UNKNOWN、能力不支持和幂等重试；运行时测试不得发送第二次不确定请求。
- [x] 3.5 更新 RuntimeAdapter capability 摘要和 Provider-specific mapping 文档；Codex 未验证预算字段保持拒绝，真实 OpenAI-compatible 参数只发送白名单字段。

## 4. 工作台与观测

- [x] 4.1 将真实 NATIVE Run 的策略、引擎、模型、配置修订、脱敏 request reference、阶段、秒级时间和 Usage 显示到工作台；测试刷新后从服务端恢复。
- [x] 4.2 确认原始思考链、隐藏 Prompt、凭据和完整工具正文不进入 Message、Trace、Usage、日志或前端；运行安全边界测试。

## 5. 集成验收与发布

- [x] 5.1 使用真实 OpenAI-compatible Endpoint 和当前未提交 SecretRef 完成一次“保存策略 → 新建 Session → 提交短请求 → 真实答复 → 刷新恢复”的 NATIVE Run；记录 Run/Trace/Usage 证据和费用/配额影响。
- [x] 5.2 运行后端全量测试、Harness 测试、Ruff、compileall、前端全量测试、生产构建、HTML parser、文档完整性和 OpenSpec strict；记录已验证与未验证项。
- [x] 5.3 Docker Compose 重新构建发布，验证 bootstrap、backend、web、Run Worker、Approval Worker 健康和 `/api/health`；确认 API/Web 不拥有真实模型密钥。

## Workflow follow-up

- 用户评审并批准 proposal、design、spec 和 tasks 后，使用 `openspec-apply-change` 开始实现。
- 实现和真实模型验收完成后，归档 OpenSpec change 并同步主规格。
