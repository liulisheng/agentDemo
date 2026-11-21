# Proposal

## Why

平台已有 ModelRoute 数据对象和员工版本选择入口，但管理员无法在平台内管理模型连接、一个连接下的多个模型或检查模型是否可用；Worker 也没有按 Run 选中的路由配置实际模型调用。需要先打通租户自管的本地 OpenAI 兼容模型流程，同时为以后从模型网关获取模型目录和授权结果留出边界。

## What Changes

- 增加租户级模型连接管理，一个连接保存协议、Base URL、SecretRef 和目录来源，可关联多个模型。
- 增加模型目录同步/手工录入、模型路由管理和平台侧租户权限控制；员工版本仍绑定稳定的 ModelRoute。
- 增加连接测试和模型测试。连接测试检查认证与目录可达性；模型测试由 Worker 发起一次无工具、受限输出的真实推理请求，并记录脱敏结果和用量。
- 扩展 RunSnapshot 与 Runtime/Harness 调用契约，使 Worker 按提交 Run 时固定的连接版本、模型 ID、参数和 SecretRef 配置本地 OpenAI 兼容接口。
- 通过可替换的 ModelCatalogProvider 和 ModelAuthorizationProvider 边界，保留后续接入 Dadi Model Gateway、new-api 目录与授权模型的能力；本次不实现其服务部署、适配器或网关授权。
- 更新员工版本模型选择交互，显示当前租户可用的已启用路由；参考原型见 `docs/原型/模型管理.html`。

## Capabilities

### New Capabilities

- `model-management`: 租户级模型连接、多模型目录、连接/模型测试、平台侧路由可用性和授权管理。

### Modified Capabilities

- `model-routes`: ModelRoute 绑定租户连接中的具体模型，并在 Run 创建时固定完整的非敏感路由快照，Worker 据此选择实际模型 Provider。

## Impact

- 后端领域、Application API、MySQL 仓储与 Worker/Harness Runtime 适配。
- 新增租户模型连接、目录和测试结果持久化；可执行 SQL migration 放在 `docs/migrations/`，并为新增/变更表字段添加中文 COMMENT。
- 前端增加模型管理页面与员工版本模型路由选择状态。
- 本地模型 Base URL 为 OpenAI 兼容 `/v1`；Worker 在 Docker Desktop 中通过 `host.docker.internal` 访问宿主机。凭据由 SecretRef/SecretProvider 解析，API Key 不进入数据库快照、日志或用户响应。
- 本地模型测试会发送一次真实推理请求；只有用户显式触发模型测试时才产生模型调用。Dadi/new-api 网关授权和上游凭据管理留待后续独立变更。
