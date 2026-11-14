# Kubernetes 沙箱部署基线

Kubernetes 负责调度和回收 worker Pod；Harness 仍在 Pod 内由 worker 启动。不要在控制面 Pod 中执行租户代码，也不要把 Docker socket、`hostPath` 或宿主设备挂进 worker。

## 最小受限 Pod 模板

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  generateName: harness-worker-
  namespace: harness-runs
spec:
  backoffLimit: 0
  ttlSecondsAfterFinished: 3600
  template:
    spec:
      automountServiceAccountToken: false
      restartPolicy: Never
      containers:
        - name: worker
          image: registry.example.com/harness-worker@sha256:<digest>
          securityContext:
            runAsNonRoot: true
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop: ["ALL"]
            seccompProfile:
              type: RuntimeDefault
          resources:
            requests:
              cpu: "500m"
              memory: "512Mi"
            limits:
              cpu: "2"
              memory: "2Gi"
          volumeMounts:
            - name: work
              mountPath: /work
      volumes:
        - name: work
          emptyDir: {}
```

生产环境还必须配合 Pod Security Admission、NetworkPolicy 默认拒绝、专用节点 taint/toleration、镜像签名/摘要校验、日志和卷配额、对象存储下载鉴权以及任务级凭据代理。Namespace 隔离本身不够。

调度器使用专用 ServiceAccount，只授予目标 namespace 内创建、读取、停止和删除 Job/Pod 的最小 RBAC 权限；禁止使用 `cluster-admin`，禁止让 worker 读取 Kubernetes API。Admission Policy 应拒绝特权容器、宿主命名空间、hostPath、未固定镜像和超出资源上限的 Pod。

## Docker 与 Kubernetes 的关系

- Docker Compose：本地 MySQL 和单 worker 开发验证。
- Kubernetes Job/Pod：服务器调度、资源限制、重试关闭、完成后回收。
- 两者使用同一 worker 镜像和同一 `SandboxProvider` 接口。
- 不在 Kubernetes 内运行 Docker-in-Docker，也不向任务暴露 Docker socket。

## 当前验证状态

本地 Docker 已完成单 worker smoke test，使用固定镜像 digest、非 root、只读根文件系统、默认无网络、全部 capability 清空、`no-new-privileges` 和 256 MiB 内存限制。本机还用临时 kind v1.32.2 集群验证了受限 Job、Pod Security restricted 标签、default-deny NetworkPolicy、资源上限、无 ServiceAccount token 和强制删除回收；该 kind 集群已销毁，不能替代目标生产集群。目标 Linux 节点配置 kubeconfig、NetworkPolicy 实际阻断和节点隔离通过前，不得把 `SandboxProvider` 标为 `available`。

kind 复现命令：

```bash
kind create cluster --name agentdemo-stage5 --image kindest/node:v1.32.2
kind load docker-image agentdemo/harness-worker:sdk-task7 --name agentdemo-stage5
kind export kubeconfig --name agentdemo-stage5 --kubeconfig /tmp/agentdemo-stage5-kubeconfig
KUBECONFIG=/tmp/agentdemo-stage5-kubeconfig kubectl apply -f deploy/kubernetes/kind-smoke-job.yaml
KUBECONFIG=/tmp/agentdemo-stage5-kubeconfig kubectl wait --for=condition=complete job/harness-worker-smoke -n harness-runs --timeout=120s
KUBECONFIG=/tmp/agentdemo-stage5-kubeconfig kubectl logs job/harness-worker-smoke -n harness-runs
kind delete cluster --name agentdemo-stage5
```

目标集群验收使用固定 registry digest，不使用本地 tag：

```bash
KUBECONFIG=/path/to/target-kubeconfig \
WORKER_IMAGE=registry.example.com/harness-worker@sha256:<digest> \
  deploy/kubernetes/validate-target.sh
```

当前阶段不引入 microVM runtime；如果以后需要更强隔离，只新增 Provider，不改变 Harness、MCP、租约和持久化接口。
