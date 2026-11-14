# 多租户不可信任务沙箱设计

状态：接口与状态规则已冻结；当前实施范围为 Docker/Kubernetes 容器后端；microVM 延后评估；Docker 单 worker 和临时 kind Kubernetes smoke test 已通过，目标生产 Linux/Kubernetes 集群仍待验收；2026-11-14。

## 1. 场景与边界

用户已确认目标是服务器执行多个用户的不可信任务。应假定任务、上传文件、依赖包、工具代码及模型生成命令都可能恶意，甚至在客户机内取得 root。目标是保护宿主机、控制面及其他租户，而不是仅防止误操作。

本设计是本地 SDK/CLI 基础版的后续服务器扩展；基础版不能直接作为不可信多租户生产服务。当前先建设 Docker 和 Kubernetes 后端，不部署 microVM。

## 2. 隔离路线

当前实施基线为容器后端：本地开发使用 Docker，服务器使用 Kubernetes Pod/Job。Kubernetes 不等于 Docker 容器本身，实际运行时通常是 containerd；安全边界由运行时、节点隔离、Pod 安全策略、网络策略和资源限制共同组成。

普通 Docker 容器默认只适合开发、内部可信任务或低风险试运行。对于用户可提交任意代码的生产任务，必须使用 rootless、只读根文件系统、禁止特权和设备透传、丢弃 Linux capabilities、seccomp/AppArmor、专用节点和默认拒绝网络策略。没有满足这些条件时，调度器只能拒绝不可信任务。

Firecracker/microVM 不在本阶段实施范围，后续若容器隔离强度不足，再通过同一 `SandboxProvider` 接口增加后端，不修改 Harness 业务协议。

每个活动会话分配独立容器或 Kubernetes Pod，内部运行 Python worker、官方 SDK、Harness、MCP 工具及全部子进程。不同租户不得共享运行实例、可写卷、Harness home 或含用户数据的快照。同一会话串行执行；关闭实例后持久数据独立保存，恢复时创建新实例并恢复原生会话。

Kubernetes namespace 只能做资源和命名隔离，不能单独当作不可信代码的安全边界。Pod 不能使用 `privileged`、`hostNetwork`、`hostPID`、`hostPath`、宿主 Docker socket 或任意设备。更高敏感等级需要专用节点、节点 taint/toleration 和独立凭据代理。

## 3. 控制面与执行面

请求路径：认证入口 → 授权与配额 → 调度器 → Docker/Kubernetes 沙箱管理器 → 容器内 worker → Harness/MCP。

控制面保存租户、会话、运行与沙箱映射，负责审计、取消、配额和回收。用户输入不得控制宿主命令、镜像路径、挂载参数或 VM 管理接口。租户身份从认证上下文生成，不接受请求体自报的 tenant_id 作为授权依据。

引擎 SDK 在客户机内启动本地 Harness，避免假定官方 SDK 支持远程启动。宿主与 worker 使用本项目版本化的任务/事件协议，通过节点代理及受控通信通道传递；这是应用协议，不是复制官方 JSON-RPC 协议。节点代理仅允许任务提交、事件读取、取消及有限产物操作，不提供任意宿主执行能力。

worker 属于可被攻陷的执行域。其输出、路径、事件和产物都必须当作不可信数据校验；不能凭 worker 报告“完成”就认定 VM 已停止或资源已回收。

## 4. 抽象与生命周期

新增 SandboxProvider：create、start、inspect、stop、destroy；新增 SandboxSpec：受信镜像摘要、资源限额、网络策略、运行时类别和数据引用；SandboxHandle 提供运行状态与受控通信句柄。实现 `DockerSandboxProvider` 和 `KubernetesSandboxProvider`，引擎协议保持业务语义，不混入 Docker 或 Kubernetes 类型。

调度状态为 queued → provisioning → running → stopping → stopped → destroyed；失败单独记录。取消先请求 Harness 停止，超过清理期限后由 Docker/Kubernetes 管理器终止容器或删除 Pod。控制面撤销凭据、解除网络授权并回收卷及进程，不依赖任务主动清理。

使用运行租约、心跳及 fencing token 防止失联节点恢复后执行过期任务。状态变更与调度操作应幂等；未知执行结果不自动重放有副作用任务。后台回收器处理启动中断、孤儿容器/Pod、遗留卷与失联节点。

### 调度器冻结接口

调度器只接受经过认证和授权的 `SubmitRunCommand`，不接受调用方自报的节点、镜像、挂载或 `tenant_id`。核心接口如下：

```python
class SandboxScheduler(Protocol):
    def submit(self, command: SubmitRunCommand) -> RunRef: ...
    def cancel(self, tenant: TenantContext, run_id: str) -> None: ...
    def inspect(self, tenant: TenantContext, run_id: str) -> RunSnapshot: ...
    def recover_expired(self, now: datetime) -> int: ...
```

`submit` 先在 MySQL 中以 `(platform_id, tenant_id, idempotency_key)` 幂等写入 `queued` 运行，再由调度器领取。领取事务锁定一行并写入 `worker_id`、`fencing_token` 和租约到期时间；只有持有当前 token 的节点可以推进状态。沙箱后端返回的 VM 标识只作为不可信引用保存，不能由租户输入。

调度器必须有显式的后端能力状态：`available`、`draining`、`unavailable`。后端为 `unavailable` 时提交可以保留为排队或直接返回容量错误，具体由配额策略决定，但绝不能落到宿主机执行。节点心跳失效后，回收器先 fencing 旧 worker，再核验 VM 状态和凭据撤销结果；无法确认时标记 `unknown`，等待人工或安全策略处理。

SDK 最小验证已经证明 Python 可以在本机启动和关闭打包 Harness runtime。阶段 5 已在 Docker 中验证固定 digest、非 root、只读根文件系统、无网络、capability 清空、no-new-privileges、资源限制和 Fake worker 启动；同一 worker 协议在 Kubernetes Pod 内的启动、事件转发、取消和回收仍需目标集群验证。调度器不直接依赖官方 SDK 类型。

服务器元数据当前选用 MySQL/InnoDB，以 platform_id + tenant_id + session_id 复合唯一约束保证标识边界；所有环境均使用 MySQL，不设 SQLite 开发或测试替代实现。每个查询都由应用层依据认证上下文授权并显式带 platform_id、tenant_id 条件，使用复合索引、外键和专项跨租户越权测试保护租户边界。并发调度用 InnoDB 事务、租约及 fencing token，不依赖 OS 文件锁。该选择暂定，后续若要求数据库原生行级隔离，再重新评估数据库。

## 5. 网络、凭据与存储

- 默认拒绝出站和横向访问；必要流量经受控代理放行。阻断控制面、其他租户、内网管理端口、云元数据及 link-local 地址，并覆盖 IPv4/IPv6、DNS 重绑定和重定向绕过。
- 模型 API Key 保留在外部模型网关，客户机仅获得限时、限租户、限模型、限预算的任务令牌。客户端可窃取自身任务令牌，应以令牌权限和预算限制损害，不能假定环境变量对任务保密。
- 业务凭据通过专用能力代理使用；无必要不向客户机注入长期密钥。到期、取消和销毁时撤销任务授权。
- 根镜像固定摘要、只读且经审查；工作卷按租户会话隔离，禁止宿主任意路径挂载、设备透传及 Docker/KVM 管理接口暴露。
- 持久文件与会话归档按租户鉴权、加密存储。恢复只允许相同租户读取对应会话数据，不能共享带秘密的内存快照。
- 应用对话 transcript 与 Harness 原生历史分开存储：消息索引在 MySQL，正文和流式 checkpoint 在租户隔离的加密对象存储；恢复/导出时先按认证 TenantContext 校验消息、session 和对象引用。具体消息格式、断线续播、部分结果、删除队列和推理数据边界见[对话正文持久化设计](superpowers/specs/2026-11-13-conversation-content-persistence-design.md)。
- 产物导出限制文件数、总量和单文件大小，拒绝路径穿越、逃逸符号链接及设备文件。解压和恶意文件检查应在独立受限环境中进行；下载仍视为不可信内容。

## 6. 配额与运维

强制设置 CPU、内存、进程数、磁盘容量/IO、网络流量、日志量、执行时限、租户并发和模型费用上限。超限由外部控制组件终止或拒绝，不仅靠客户机内自律。

审计记录主体、租户、会话、运行、镜像摘要、策略版本、审批、网络拒绝、超限和回收结果；默认不记录敏感正文。审批不能突破平台硬性禁止策略。故障节点隔离、镜像撤回和安全更新应有明确运维流程。

## 7. 实施门槛与验收

实施前确认服务器 Linux 发行版、CPU 架构、Docker/Kubernetes 版本、容器运行时、节点隔离能力、峰值并发、单任务资源和网络需求。当前不锁定 Firecracker 或其他 microVM 版本。

分阶段验证：单 Docker worker 与 SDK 通信 → Kubernetes Pod/Job 调度 → MCP/会话恢复兼容 → 网络和凭据代理 → 多租户调度与配额 → 对抗测试及回收演练。

必须测试跨租户读取/取消/下载越权、元数据访问、DNS/重定向绕过、fork bomb、磁盘及日志耗尽、任务令牌撤销、导出路径逃逸、容器/Pod 启动失败、节点重启、孤儿回收和重复调度。还需检查控制面从不加载租户插件代码。

上线门槛：策略不能被任务修改，失败不回退宿主执行，资源回收可证明，真实 SDK/工具链在 Docker/Kubernetes 隔离环境内通过集成测试，并完成独立安全审查。不能以一次功能测试通过代替安全认证。

## 8. 依据

- [Firecracker 生产宿主配置](https://github.com/firecracker-microvm/firecracker/blob/main/docs/prod-host-setup.md)：官方讨论多租户任意代码执行环境及生产约束。
- [Firecracker jailer](https://github.com/firecracker-microvm/firecracker/blob/main/docs/jailer.md)：VMM 进程隔离机制。
- [gVisor 安全模型](https://gvisor.dev/docs/architecture_guide/security/)：候选方案的边界与限制。

关联：[基础设计](design.md)、[实施计划](implementation-plan.md)、[技术决策](decisions.md)。
