# 外部适配器分阶段验证清单

更新时间：2026-11-15
状态：离线契约已验证，真实外部服务按表格分阶段验证

| 适配器 | 契约 | 离线 | 真实验证 | 开关 |
|---|---|---|---|---|
| Fake Runtime | RuntimeAdapter | 已通过 | 不适用 | 默认开启 |
| DeepSeek Harness | RuntimeAdapter | 边界已定义 | 未验证 | 可配置关闭 |
| Codex app-server/exec | RuntimeAdapter | 边界已定义 | 未验证 | 可配置关闭 |
| new-api | ModelProvider | Fake transport 已通过 | 未验证 | 可配置关闭 |
| Dadi Model Gateway | ModelProvider | Fake transport 已通过 | 未验证 | 可配置关闭 |
| Dadi User Center | IdentityProvider | Fake 字段已通过 | 未验证 | 可配置关闭 |
| IM Channel | ChannelAdapter | FakeChannel 已通过 | 未验证 | 可配置关闭 |
| ObjectStore | ObjectStore | Local 已通过 | S3/MinIO/OSS/COS 未验证 | provider 显式选择 |
| Kubernetes Sandbox | SandboxProvider | 契约已定义 | 未验证 | 可配置关闭 |

任何未验证适配器不得因配置缺失静默回退到生产本地执行。

验证命令：cd backend && python -m pytest tests/test_runtime_registry.py tests/test_model_routes.py -q。
