#!/usr/bin/env bash
set -euo pipefail

: "${WORKER_IMAGE:?Set WORKER_IMAGE to a registry image pinned by @sha256 digest}"
: "${KUBECONFIG:?Set KUBECONFIG to the target cluster configuration}"

if [[ "${WORKER_IMAGE}" != *@sha256:* ]]; then
  echo "WORKER_IMAGE must use an immutable @sha256 digest" >&2
  exit 2
fi

kubectl config current-context
kubectl cluster-info
kubectl get nodes -o wide
kubectl get namespace harness-runs >/dev/null 2>&1 || kubectl create namespace harness-runs
kubectl label namespace harness-runs \
  pod-security.kubernetes.io/enforce=restricted \
  pod-security.kubernetes.io/audit=restricted \
  pod-security.kubernetes.io/warn=restricted --overwrite

manifest_file="$(mktemp)"
trap 'rm -f "$manifest_file"' EXIT
sed \
  -e "s#agentdemo/harness-worker:sdk-task7#${WORKER_IMAGE}#" \
  -e 's/imagePullPolicy: Never/imagePullPolicy: IfNotPresent/' \
  deploy/kubernetes/kind-smoke-job.yaml >"$manifest_file"

kubectl apply -f "$manifest_file"
kubectl wait --for=condition=complete job/harness-worker-smoke \
  -n harness-runs --timeout=180s
kubectl logs job/harness-worker-smoke -n harness-runs
kubectl get job,pod,networkpolicy -n harness-runs -o yaml

kubectl get pod -n harness-runs -l app=harness-worker-smoke \
  -o jsonpath='user={.items[0].spec.containers[0].securityContext.runAsUser}{"\n"}readonly={.items[0].spec.containers[0].securityContext.readOnlyRootFilesystem}{"\n"}token={.items[0].spec.automountServiceAccountToken}{"\n"}seccomp={.items[0].spec.containers[0].securityContext.seccompProfile.type}{"\n"}'
