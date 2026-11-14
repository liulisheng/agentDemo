# 用户中心到 Harness 认证协议

状态：协议冻结草案，日期：2026-11-09。

## 责任边界

用户中心负责用户登录、密码/MFA、OIDC Provider、服务端浏览器会话、用户和租户状态、角色目录以及 Token 签发。Harness 控制面负责验证 Token、解析授权上下文、检查当前租户和用户状态、签发短期任务能力令牌，并把 `TenantContext` 传给仓储和调度器。

Harness 不接收浏览器提交的 `user_id`、`platform_id`、`tenant_id`、角色或权限作为可信身份。浏览器只携带用户中心颁发的 HttpOnly 会话 cookie；服务端到服务端使用 OIDC access token 或受信的内部身份解析结果。

## OIDC 客户端约束

Harness 注册为用户中心的固定 OIDC client。issuer、audience、JWKS URI、redirect URI、scope 和平台/租户绑定由服务端配置管理，不能由浏览器传入。回调地址必须精确匹配白名单；未注册客户端、租户不匹配、issuer 不匹配或 callback 不匹配均 fail closed。

Harness 验证：签名算法和 JWKS、`iss`、`aud`、`exp`、`nbf`、`iat`、`sub`、`platform_id`、`tenant_id`，以及必须存在的角色/权限声明。Token 通过后仍查询用户中心目录或缓存的短 TTL 状态，确认用户和租户为 active，且用户对目标租户具有 active 访问关系。

## TenantContext

```python
@dataclass(frozen=True)
class TenantContext:
    platform_id: str
    tenant_id: str
    user_id: str
    roles: frozenset[str]
    auth_source: Literal["oidc", "internal"]
    auth_time: datetime
    token_id: str | None
```

上下文只在请求生命周期内传递。仓储、会话服务和调度器都必须接收它；不得依赖线程全局变量或普通 `X-User-Id`、`X-Tenant-Id` 请求头。若网关传递上下文，必须使用内部 mTLS 或签名的短期内部 Token，并在服务边界重新验证。

## 长任务能力令牌

提交任务时，控制面为指定 `platform_id + tenant_id + user_id + run_id` 签发短期能力令牌。令牌包含 `run_id`、允许的动作、策略版本、过期时间、jti 和预算范围，不包含 API Key。worker 只能使用该令牌访问任务所需的事件、取消、模型网关和产物接口。

用户被禁用、租户被暂停、权限被撤销、运行取消或租约过期时，控制面停止续租并撤销能力令牌。长任务不依赖初始登录 Token 一直有效；每次心跳和敏感动作都检查 run、租约和令牌状态。

## 失败行为

缺失 Token、签名无效、过期、issuer/audience 不匹配、租户不存在、用户被禁用、租户暂停、角色不足、平台/租户不一致、内部上下文签名无效，统一拒绝请求并写入脱敏审计事件。不得自动选择默认租户、默认用户或默认平台。

## 验收场景

- 用户 A 的 Token 不能访问用户 B 的租户会话。
- 同一个用户切换租户时必须重新确认 membership 和角色。
- 禁用用户后新任务被拒绝，已有长任务在下一次续租时停止。
- 修改 OIDC client 的回调地址不会接受旧的未注册地址。
- 伪造普通租户请求头不能改变 `TenantContext`。
- 能力令牌只能访问绑定的 `run_id`，不能读取同租户其他运行。
