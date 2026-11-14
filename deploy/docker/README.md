# Docker worker smoke test

这是阶段 5 的本地容器基线，不代表生产不可信代码隔离已经通过。

## 构建

```bash
docker build --pull=false \
  -f deploy/docker/Dockerfile \
  -t agentdemo/harness-worker:task7 .
docker image inspect agentdemo/harness-worker:task7 \
  --format 'agentdemo/harness-worker@{{index .RepoDigests 0}}'
```

Dockerfile 固定了 `python:3.13-slim` 的基础镜像 digest，并且构建上下文不会复制本地 `.artifacts` 或 API key。

## 受限 smoke test

```bash
docker run --rm \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  --network none \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --memory 256m \
  --cpus 0.5 \
  --user 65532:65532 \
  agentdemo/harness-worker:task7 \
  --engine fake 'sandbox-ok'
```

本地最终验证结果：含固定 SDK/runtime 依赖的 arm64 镜像 digest 为 `sha256:72d3aa46909990a8a1dd773c1da24c72958a6e2aa9f1e24283c34b0338d89216`，返回 `fake: docker-sandbox-ok`，容器身份为 `65532:65532`，根文件系统只读，网络模式为 `none`，capability 全部清空，启用 `no-new-privileges`，内存上限为 1 GiB。

这条 smoke test 只证明容器运行时基线和 Fake worker 启动；真实 Harness SDK、MCP、网络代理、凭据撤销、Kubernetes Pod Security、节点隔离和孤儿回收必须在目标 Linux/Kubernetes 环境继续验证。

## 固定 SDK/runtime 容器检查

可重复执行 Docker-only 验收：

```bash
deploy/docker/validate.sh
```

脚本先运行受限 Fake worker，再运行项目 CLI 的 SDK startup-only。Fake 通过时打印 `FAKE_STATUS=PASSED`；SDK native binding 缺失时打印 `SDK_STATUS=UNVERIFIED` 并以退出码 `3` 结束。已构建镜像可用 `SKIP_BUILD=1` 跳过 npm 依赖重下载。

真实本地中转站 prompt 验收：

```bash
REAL_PROMPT=1 \
LOCAL_CONFIG=/tmp/agentdemo-local-openai-docker.json \
SKIP_BUILD=1 \
DOCKERFILE=deploy/docker/Dockerfile.npm-runtime \
IMAGE=agentdemo/harness-worker:npm-runtime \
  deploy/docker/validate.sh
```

`LOCAL_CONFIG` 需要是只读 JSON，并把 `base_url` 设置为容器可访问的 `http://host.docker.internal:50811/v1`。脚本输出 `REAL_MODEL_STATUS=PASSED` 才表示真实模型链路通过。

生命周期 smoke test：设置 `LIFECYCLE=1` 后，脚本会在同一受限镜像中验证 Fake session 创建、执行、恢复和取消确认，并输出 `LIFECYCLE_STATUS=PASSED`。

MCP 验收：

```bash
MCP_TOOL=echo \
REAL_PROMPT=1 \
REAL_PROMPT_TEXT='Call the echo tool with text docker-mcp-ok and return exactly its result.' \
LOCAL_CONFIG=/tmp/agentdemo-local-openai-docker.json \
SKIP_BUILD=1 \
DOCKERFILE=deploy/docker/Dockerfile.npm-runtime \
IMAGE=agentdemo/harness-worker:npm-runtime \
  deploy/docker/validate.sh
```

本次结果：容器内 `echo` 工具发现通过，真实 prompt 返回 `{"text": "docker-mcp-ok"}`，`REAL_MODEL_STATUS=PASSED`。

MySQL persistence demo 使用同一 npm 镜像连接 Docker network 内的 MySQL 8.4.13，依次执行 V001/V002 后验证：

```text
schema_version=2
idempotent_submission=true
persisted_run_state=SUCCEEDED
result_text=fake: docker-persistence-ok
```

## npm `dsh` CLI 路线

保留 Python wheel runtime 的默认 Dockerfile，同时提供 npm CLI 变体：

```bash
DOCKERFILE=deploy/docker/Dockerfile.npm-runtime \
IMAGE=agentdemo/harness-worker:npm-runtime \
  deploy/docker/validate.sh
```

这个镜像通过 `DSH_BIN=/opt/harness/bin/dsh` 让 Python SDK 调用 npm 安装的 `@deepseek-ai/dsh@0.2.2-rc.3`。本次已构建并验收通过：镜像 digest 为 `sha256:fa57537bb3bf80d18b8f21b75a52d610aafce35d466d351e9ecc83bcaa27cd1b`，Fake、SDK startup、真实 prompt、MCP、生命周期和 MySQL persistence 均通过。npm 依赖下载需要可用的 npm registry 网络；默认 Dockerfile 仍保留 Python wheel runtime 作为回退路径。

将 `requirements-deepseek-check.txt` 加入镜像后，`deepseek-harness-sdk==0.2.2rc2` 和匹配 runtime 在 arm64 与 amd64 Linux 容器中都能安装，但 `--startup-only` 失败于：

```text
No usable native binding found for node-addon-require-builtin-linux-arm64-gnu (auto)
No usable native binding found for node-addon-require-builtin-linux-x64-gnu (auto)
```

因此当前镜像只把 Fake worker smoke test 作为通过证据；真实 Harness/MCP 容器链路必须等上游 runtime 提供可用的 Linux native binding 后重新验证。
