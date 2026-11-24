# Tasks

## 1. 原型和需求基线

- [x] 1.1 将 `docs/原型/员工Deployment运行配置.html`、`DeepSeekHarness默认引擎配置.html`、`海豚调度员工工作台.html` 和 `当前方案总览.html` 的回答策略、思考阶段、审批、只读/错误/空状态和秒级时间统一为当前 React 工作台的浅色视觉令牌，并同步“已接入/真实 Provider 未验证”状态；用 Python HTML parser 和关键文案检查验证四个原型可解析且字段齐全。
- [x] 1.2 更新 `docs/思考模式与回答策略方案.md`、`docs/当前方案需求与设计总览.md`、`docs/前端交互设计.md`，建立原型字段到页面、API、Session/RunSnapshot 和验收项的追踪矩阵；运行 `python -m pytest -q backend/tests/test_documentation_completeness.py` 验证文档完整性。

## 2. 统一 RuntimeOptions 和持久化快照

- [x] 2.1 在平台领域和 Runtime 端口定义类型化 RuntimeOptions，校验思考模式、推理强度、预算、答复详细程度、输出/工具/超时范围；添加正常、非法字段、越界和未知字段测试。
- [x] 2.2 让 Deployment 默认/覆盖解析把 RuntimeOptions 与 EngineProfile/ModelRoute 分开合并，并在 Session/RunSnapshot 保存有效策略、来源和配置版本；验证旧 Session 不随新配置变化、重启可恢复和同幂等键不重复执行。
- [x] 2.3 扩展 API schema、serializer 和 capability 错误，区分 `INVALID_ARGUMENT`、`ENGINE_CAPABILITY_UNSUPPORTED`、`ENGINE_MODEL_INCOMPATIBLE` 和 `RUNTIME_CONFIG_REVISION_CONFLICT`；验证成员只读、管理员写入、跨租户拒绝和过期修订冲突。
- [x] 2.4 确认现有 `runtime_options_json`/Session metadata/RunSnapshot JSON 足够承载本期字段；若不新增 MySQL 表/列，补充设计说明和 schema 注释验证；若新增字段，创建带中文 COMMENT 的 forward migration，并通过 `information_schema.TABLES`/`COLUMNS` 测试。
- [x] 2.5 为用户/租户默认运行环境增加 `runtime_strategy_bindings` 持久化、API、作用域解析和中文 COMMENT migration；验证管理员/成员权限、跨租户拒绝、清除默认和重启恢复。

## 3. EngineProvider 和 Adapter 映射

- [x] 3.1 扩展 EngineProvider capability 摘要，声明 reasoning、reasoning_effort、reasoning_budget 和 max_output_tokens；验证未支持能力在 Runtime 出站前被拒绝且不切换 Fake/模型。
- [x] 3.2 扩展 `RuntimeRequest`/`RuntimeEvent`，让 DeepSeek Harness、Codex App Server 和 Fake 通过同一平台契约接收策略并返回标准化思考/工具/答复阶段；为每个 Adapter 添加契约测试。
- [x] 3.3 用确定性 Mock 验证 DeepSeek/Codex 私有字段映射、Session resume、超时、Provider 不返回 reasoning token 和 Tool/MCP capability 拒绝；真实供应商字段未验证时在文档中明确标注，不伪造支持。

## 4. 实际工作台实现

- [x] 4.1 将 `DeploymentRuntimeConfigurationDialog` 和默认运行环境页面按原型增加回答策略字段、能力提示、继承/覆盖和只读状态；组件测试验证表单提交、成员限制、兼容性错误和旧 Session 提示。
- [x] 4.2 将 `ConversationWorkbench`/`ConversationWorkbenchPage` 按原型展示策略 chip、思考中/调用工具/生成答复阶段、内部事件折叠、审批卡和 Run 快照；组件测试验证成功、等待审批、失败、UNKNOWN、加载、空和错误状态。
- [x] 4.3 将工作台消息、工具、审批和 Run 状态的时间格式化到 `YYYY-MM-DD HH:mm:ss`，同秒按服务端 sequence 排序；添加时间精度和多轮消息顺序回归测试。
- [x] 4.4 完成视觉回归检查：当前 React 工作台与四个 HTML 原型使用同一浅色管理台令牌、布局层级、状态颜色、按钮文案、窄屏断点和键盘焦点；运行前端全量测试和 `npm run build`。

## 5. Trace、Usage 和安全边界

- [x] 5.1 在 Trace/Usage 中记录策略摘要、阶段、秒级时间、耗时和 reasoning token 的 exact/estimated/unavailable 状态；测试禁止原始思考链、隐藏 Prompt、凭据和完整工具正文进入消息、Trace、Usage 或日志。
- [x] 5.2 验证租户、Session、Run、Approval 和对象存储读取边界；跨租户、非参与者、非提交者和成员只读场景均返回明确拒绝或资源不存在语义。

## 6. 集成验收

- [x] 6.1 运行后端全量测试、Ruff、compileall、前端全量测试、前端生产构建、OpenSpec strict 和文档完整性测试；记录命令、结果、环境和未验证项。
- [x] 6.2 使用 Docker Compose 验证 bootstrap、backend、web、Run Worker 和 Approval Worker 健康状态；用静态原型和本地工作台逐项核对主流程、策略展示、秒级时间和审批状态。
- [x] 6.3 在真实 Provider 仍未授权时保持 DeepSeek/Codex reasoning 字段为 capability-gated；记录真实 Provider、长时恢复、生产身份和生产 Secret Manager 的未验证边界。

## Workflow follow-up

- 在用户评审并批准本 Change 的全部 planning artifacts 后，使用 `openspec-apply-change` 开始实现。
- 实现和验证完成后，再使用 `openspec-archive-change` 归档，并同步主规格。
