# 真实链路验证执行顺序

这份 runbook 用来把“设计完成”推进到“证据完成”。每一步都要记录版本、命令、日志位置和结论；失败时停在当前阶段，不用 Fake 结果替代真实结果。

## 1. 先完成 MySQL 8.4

Docker Desktop Linux Engine 已在 2026-11-10 启动，并用于一次性 MySQL 8.4.13 验证。可按以下步骤启动项目的本地开发实例：

```powershell
cd deploy/mysql
Copy-Item .env.example .env
docker compose up -d
docker compose ps
```

V001+V002、`tests/test_mysql_integration.py` 和 Task 5 删除/GC 集成已在一次性 MySQL 8.4.13 实例通过；只读 doctor 报告 schema version 2，且未修改数据库。当前证据为 MySQL 集成 14 passed。详见[真实验证矩阵](verification-matrix.md)。继续按[迁移验收说明](migrations/README.md)检查生产部署需要的备份恢复、权限边界和运维流程。Docker/Kubernetes 沙箱链路仍待目标 Linux/Kubernetes 环境验证。

## 2. 实现并验证对话正文持久化

V002、只读 doctor、本地加密 ContentStore、MySQL 上传 intent/消息仓储及可选的非流式消息服务已在离线和一次性 MySQL 8.4.13 验证。实现保存完整用户输入后再发布 run；成功后原子保存助手最终文本、消息状态和 `runs.result_ref`。详见[对话正文持久化设计](superpowers/specs/2026-11-13-conversation-content-persistence-design.md)和[真实验证矩阵](verification-matrix.md)。后续验证按以下顺序推进：

1. 已在一次性 MySQL 8.4.13 执行 V001、V002 并通过 doctor；代表性 V002 schema 和消息仓储集成用例通过。生产迁移仍需按前文完成备份恢复、权限边界与运维验收。
2. 目前提供本地加密 ContentStore；配置生产对象存储、租户隔离、密钥管理/轮换及备份策略后，重新验证 MySQL 仅保存 opaque ref、哈希、大小、顺序和状态。
3. 非流式 Fake 引擎路径已验证 user prompt 在提交前保存、assistant 最终回复在成功后保存、同幂等键复用消息且不重复写入。真实 provider 的对话正文接入和跨进程 transcript 读取仍需目标环境验收。
4. 待验证真实 SDK live delta 是否在 `run()` 返回前交付；只有证明后才实现按 32 KiB 或 2 秒（先到者）生成 checkpoint。浏览器断线后用 `last_chunk_sequence` 持续查询/long-poll 同一 durable chunk log；在重连空查询与通知等待之间提交一个 chunk，确认下次查询仍能读到且不跳序。
5. 终止 worker 后只恢复最后已提交分段并标记 `PARTIAL`/`UNKNOWN`；不得自动重发模型请求。
6. 已在离线和 MySQL 8.4.13 集成中验证跨租户读取/删除拒绝、fencing 旧 worker 写入拒绝、对象/数据库部分失败补偿、intent 过期与延迟发布互斥、tenant-scoped 24 小时孤儿扫描和显式删除重试；覆盖 PUT 在首次清理后才完成，`DELETED` 队列行不会屏蔽对象重现，扫描会重新入队。
7. 确认孤儿扫描保护已有 `runs.result_ref`，并且在启用清理前已排空不创建 upload intent 的旧版 writer。
8. 确认应用管理的所有持久介质均不保存原始内部推理或隐藏 reasoning token，包括 MySQL、正文对象、chunk、manifest、日志、审计 payload 和导出；Harness 原生历史需单独验证 SDK 是否会保留该内容，不能排除此行为时不得宣称满足推理不持久化要求。prompt、凭据、完整工具参数/输出也不得写入审计 payload。
9. 默认不按时间自动删除消息或会话级审计；显式删除时清除正文与元数据，并将关联 `UPLOADING` intent 引用一并排队，备份副本按部署备份生命周期到期清理。

DeepSeek 固定版本适配器现声明 `streaming=False`，因为公开 SDK 通知回调不提供运行中的可见文本 delta。通用 `VisibleTextDelta` 的 checkpoint、cursor replay 和 worker-loss 恢复已在 Fake Engine 与 MySQL 8.4 集成中验证；只有 DeepSeek 上游提供可验证的实时 delta 接口后，才执行并通过 DeepSeek 流式项。

## 3. 锁定 SDK 兼容范围

- 将 `deepseek-harness-sdk` 和匹配的 runtime 版本写入 lock 文件或构建清单。
- 只在 `engines/deepseek` 适配器内引用官方 SDK 类型。
- 用真实 API Key 做一次最小 prompt，保存请求、事件顺序、最终响应和 finish reason。
- 版本升级必须重新跑完整矩阵；在通过前不得宣称生产兼容。

预发布版本可以用于开发验证，但发布策略应是“适配器隔离 + 固定版本 + canary + 可回滚”，而不是把预发布 API 暴露给业务层。

## 4. 完成 Harness 真实功能链路

按以下顺序执行：

1. 真实 prompt：确认 start、事件、final response、finish reason。
2. MCP：发现工具、非法参数拒绝、成功返回、工具异常传播。
3. 审批：无 responder 拒绝高风险动作；合法 responder 只允许白名单决策。
4. 恢复：结束进程后使用同一 Harness home 恢复；历史缺失必须明确报错。
5. 取消与超时：区分已确认取消、超时、断连和未知结果，不自动重放副作用任务。

## 5. 准备 Docker/Kubernetes 执行环境

本阶段使用 Docker 或 Kubernetes，不准备 Firecracker/microVM。Docker 适合本地开发和低风险试运行；Kubernetes 负责服务器上的调度、资源限制、节点隔离和回收。

Kubernetes 验证必须确认 Pod Security、运行时用户、只读根文件系统、禁止特权、禁止 hostPath/hostNetwork/hostPID、网络默认拒绝、资源上限、节点 taint/toleration、镜像摘要和日志/卷回收。namespace 本身不能当作安全边界。

本机 Docker Engine 已验证可用；本机默认 kubeconfig 仍不可用（`~/.kube/config` 是目录），但已用独立 `/tmp` kubeconfig 创建临时 kind v1.32.2 集群并完成受限 Job/强制删除 smoke test。该结果只证明本地控制面和 manifest 基线，不能替代目标 Linux 集群验收。目标 Linux 节点上应先执行：

```bash
kubectl config get-contexts
kubectl cluster-info
kubectl get nodes -o wide
kubectl get namespace harness-runs >/dev/null 2>&1 || kubectl create namespace harness-runs
kubectl label namespace harness-runs pod-security.kubernetes.io/enforce=restricted --overwrite

WORKER_IMAGE=registry.example.com/harness-worker@sha256:<digest> \
  deploy/kubernetes/validate-target.sh
```

## 6. 单容器和单 Pod 验证

先只跑一个租户、一个 worker、一个容器或 Pod，依次验证 Harness、MCP、恢复、产物、网络默认拒绝、凭据撤销、租约过期、旧 worker fencing、强制终止和孤儿回收。Docker 本地只能先验证镜像启动、Fake worker 生命周期、只读根文件系统、非 root、能力清空和资源限制；真实 Harness SDK、MCP、网络策略和 Kubernetes 回收必须在目标节点执行。全部通过后才进入多租户调度和配额验证。

Kubernetes Job 证据最少应包含：`kubectl get job,pod -n harness-runs -o yaml`、Pod 事件、容器退出码、NetworkPolicy、资源配置、强制删除后的回收结果，以及旧 worker fencing 和对象 GC 的控制面日志。

## 7. 通过门槛

只有 V001/V002、对话内容存储、Harness SDK、MCP/审批/会话恢复和 Docker/Kubernetes 容器链路都有日志证据，才能把对应的 `SandboxProvider` 从 `unavailable` 改为 `available`。当前 Docker 已具备本地 Engine，Kubernetes 因无可用 context 仍为 `UNAVAILABLE`；生产 API 必须拒绝调度不可信任务。V002/对话存储证据要单独标注，不能从 V001 run 状态验证推导出来。
