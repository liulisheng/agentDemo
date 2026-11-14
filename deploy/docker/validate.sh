#!/usr/bin/env bash
set -euo pipefail

IMAGE="${IMAGE:-agentdemo/harness-worker:sdk-task7}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DOCKERFILE="${DOCKERFILE:-${ROOT_DIR}/deploy/docker/Dockerfile}"

if [[ "${SKIP_BUILD:-0}" != "1" ]]; then
  docker build --pull=false -f "${DOCKERFILE}" \
    -t "${IMAGE}" "${ROOT_DIR}"
fi

digest="$(docker image inspect "${IMAGE}" --format '{{index .RepoDigests 0}}')"
echo "IMAGE=${digest}"

mcp_args=()
if [[ -n "${MCP_TOOL:-}" ]]; then
  mcp_args+=(--mcp-tool "${MCP_TOOL}")
fi

docker run --rm \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=128m \
  --network none \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --memory 1g \
  --cpus 1 \
  --user 65532:65532 \
  "${IMAGE}" --engine fake docker-sandbox-ok
echo "FAKE_STATUS=PASSED"

if [[ "${LIFECYCLE:-0}" == "1" ]]; then
  docker run --rm \
    --read-only \
    --tmpfs /tmp:rw,noexec,nosuid,size=128m \
    --network none \
    --cap-drop ALL \
    --security-opt no-new-privileges \
    --memory 1g \
    --cpus 1 \
    --user 65532:65532 \
    --entrypoint python \
    "${IMAGE}" /opt/harness/lifecycle_smoke.py
  echo "LIFECYCLE_STATUS=PASSED"
fi

set +e
sdk_output="$(docker run --rm \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=128m \
  --network none \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --memory 1g \
  --cpus 1 \
  --user 65532:65532 \
  "${IMAGE}" --engine deepseek --dsh-home /tmp/dsh-home --startup-only "${mcp_args[@]}" docker-startup 2>&1)"
sdk_status=$?
set -e
printf '%s\n' "${sdk_output}"
if [[ "${sdk_status}" -eq 0 ]]; then
  echo "SDK_STATUS=PASSED"
else
  echo "SDK_STATUS=UNVERIFIED"
  exit 3
fi

if [[ "${REAL_PROMPT:-0}" == "1" ]]; then
  : "${LOCAL_CONFIG:?Set LOCAL_CONFIG to a read-only local provider JSON with host.docker.internal as the base URL}"
  docker run --rm \
    --read-only \
    --tmpfs /tmp:rw,noexec,nosuid,size=128m \
    --add-host host.docker.internal:host-gateway \
    --network bridge \
    --cap-drop ALL \
    --security-opt no-new-privileges \
    --memory 1g \
    --cpus 1 \
    --user 65532:65532 \
    -v "${LOCAL_CONFIG}:/opt/harness/.artifacts/local-openai.json:ro" \
    "${IMAGE}" \
    --engine deepseek \
    --dsh-home /tmp/dsh-home \
    --local-config /opt/harness/.artifacts/local-openai.json \
    --max-tokens 32 \
    "${mcp_args[@]}" \
    "${REAL_PROMPT_TEXT:-Reply with exactly: docker-npm-real-ok}"
  echo "REAL_MODEL_STATUS=PASSED"
fi
