#!/usr/bin/env bash
set -euo pipefail

# Benchmark Pod with Persistent Volume for Results

export KUBECONFIG=/home/michey/kubeconfigs/kubeconfig.llmd.fra
NAMESPACE="${NAMESPACE:-benchflow}"
RELEASE_NAME="${RELEASE_NAME:-qwen3-32b}"
MODEL_NAME="${MODEL_NAME:-Qwen/Qwen3-32B}"
BENCHMARK_IMAGE="ghcr.io/llm-d/llm-d-benchmark:v0.6.0"

# Endpoint selection: gateway (default) or direct service
# Set USE_DIRECT_SERVICE=true to bypass gateway and hit model service directly
USE_DIRECT_SERVICE="${USE_DIRECT_SERVICE:-false}"

if [ "${USE_DIRECT_SERVICE}" = "true" ]; then
    # Direct service endpoint (bypasses gateway/router)
    # For llm-d v0.6.0+, use the model service directly
    DIRECT_SVC="${DIRECT_SVC:-ms-${RELEASE_NAME}}"
    BASE_URL="http://${DIRECT_SVC}.${NAMESPACE}.svc.cluster.local:8000"
    echo "Using DIRECT SERVICE endpoint: ${BASE_URL}"
else
    # Gateway endpoint (default) - routes through llm-d gateway/scheduler
    GATEWAY_SVC="infra-${RELEASE_NAME}-inference-gateway-istio"
    BASE_URL="http://${GATEWAY_SVC}.${NAMESPACE}.svc.cluster.local:80"
    echo "Using GATEWAY endpoint: ${BASE_URL}"
fi

echo "Using image: ${BENCHMARK_IMAGE}"

# Workload parameters
SYSTEM_PROMPT_LEN=6000
QUESTION_LEN=1200
USERS_PER_GROUP=5
OUTPUT_LEN=1000
NUM_GROUPS=150

# Use existing PVC for results
PVC_NAME="benchmark-results"
echo "Using existing PVC: ${PVC_NAME}"

# Check if PVC exists
if ! kubectl get pvc "${PVC_NAME}" -n "${NAMESPACE}" &>/dev/null; then
    echo "PVC does not exist, creating it..."
    kubectl apply -f - << EOF
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: ${PVC_NAME}
  namespace: ${NAMESPACE}
spec:
  accessModes:
    - ReadWriteMany
  resources:
    requests:
      storage: 20Gi
  storageClassName: nfs
EOF
else
    echo "PVC already exists, reusing it"
fi

# Create workload ConfigMap
WORKLOAD_CONFIG="capacity-73-workload"
echo "Creating workload ConfigMap: ${WORKLOAD_CONFIG}"

kubectl create configmap "${WORKLOAD_CONFIG}" -n "${NAMESPACE}" --dry-run=client -o yaml \
    --from-literal=workload.yaml="$(cat << EOFCONFIG
load:
  type: poisson
  interval: 30.0
  stages:
  # Warmup — seat residents (~1×S)
  - rate: 15
    duration: 50
  # Main ladder
  - rate: 3
    duration: 20
  - rate: 10
    duration: 20
  - rate: 15
    duration: 20
  - rate: 20
    duration: 38
  - rate: 22
    duration: 34
  - rate: 25
    duration: 30
  - rate: 30
    duration: 25
  - rate: 35
    duration: 21
  - rate: 40
    duration: 38
  - rate: 43
    duration: 36
  - rate: 46
    duration: 33
  - rate: 49
    duration: 30
  - rate: 52
    duration: 29
  - rate: 55
    duration: 27
  - rate: 57
    duration: 26
  - rate: 60
    duration: 25
  worker_max_tcp_connections: 3000

api:
  type: completion
  streaming: true

server:
  type: vllm
  model_name: ${MODEL_NAME}
  base_url: ${BASE_URL}
  ignore_eos: true

tokenizer:
  pretrained_model_name_or_path: ${MODEL_NAME}

data:
  type: shared_prefix
  shared_prefix:
    num_groups: ${NUM_GROUPS}
    num_prompts_per_group: ${USERS_PER_GROUP}
    system_prompt_len: ${SYSTEM_PROMPT_LEN}
    question_len: ${QUESTION_LEN}
    output_len: ${OUTPUT_LEN}

report:
  request_lifecycle:
    summary: true
    per_stage: true
    per_request: true

storage:
  local_storage:
    path: /results
EOFCONFIG
)" | kubectl apply -f -

# Create benchmark pod
POD_NAME="benchmark-capacity-73-${RELEASE_NAME}"
echo "Creating benchmark pod: ${POD_NAME}"

kubectl apply -f - << EOF
apiVersion: v1
kind: Pod
metadata:
  name: ${POD_NAME}
  namespace: ${NAMESPACE}
  labels:
    app: llm-d-benchmark
    workload: capacity-73
spec:
  restartPolicy: Never
  containers:
  - name: benchmark
    image: ${BENCHMARK_IMAGE}
    command: ["/bin/bash", "-c"]
    args:
    - |
      set -ex

      # Set writable cache directories
      export HF_HOME=/tmp/.cache/huggingface
      export TRANSFORMERS_CACHE=/tmp/.cache/huggingface/transformers
      mkdir -p \$HF_HOME \$TRANSFORMERS_CACHE

      echo ""
      echo "=========================================="
      echo "llm-d-benchmark Image"
      echo "=========================================="
      cat /workspace/repos.txt || echo "No repos.txt found"
      echo ""

      mkdir -p /results
      cd /results

      echo "=========================================="
      echo "Starting 73-capacity benchmark"
      echo "Time: \$(date)"
      echo "Endpoint: ${BASE_URL}"
      echo "Model: ${MODEL_NAME}"
      echo "Results will be persisted to PVC: ${PVC_NAME}"
      echo "=========================================="
      echo ""

      # Run inference-perf
      inference-perf --config_file /workload/workload.yaml 2>&1 | tee benchmark.log

      echo ""
      echo "Benchmark complete. Running analysis..."
      sleep 10

      # Use the llm-d-benchmark analysis script
      export LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR=/results
      /usr/local/bin/inference-perf-analyze_results.sh 2>&1 | tee analysis.log

      echo ""
      echo "=========================================="
      echo "Results Summary:"
      echo "=========================================="
      ls -lah /results/

      if [ -d /results/analysis ]; then
        echo ""
        echo "Analysis outputs:"
        ls -lah /results/analysis/
      fi

      if [ -f /results/summary.json ]; then
        echo ""
        echo "Performance Summary:"
        cat /results/summary.json
      fi

      echo ""
      echo "=========================================="
      echo "Benchmark finished at \$(date)"
      echo "Results are persisted in PVC: ${PVC_NAME}"
      echo "=========================================="
    volumeMounts:
    - name: workload-config
      mountPath: /workload
    - name: results
      mountPath: /results
    resources:
      requests:
        memory: "8Gi"
        cpu: "4"
      limits:
        memory: "24Gi"
        cpu: "8"
  volumes:
  - name: workload-config
    configMap:
      name: ${WORKLOAD_CONFIG}
  - name: results
    persistentVolumeClaim:
      claimName: ${PVC_NAME}
EOF

echo ""
echo "=========================================="
echo "Benchmark pod deployment initiated"
echo "=========================================="
echo "Pod name: ${POD_NAME}"
echo "Namespace: ${NAMESPACE}"
echo "Image: ${BENCHMARK_IMAGE}"
echo "Endpoint: ${BASE_URL}"
echo "Results PVC: ${PVC_NAME}"
echo ""
echo "Monitor progress:"
echo "  export KUBECONFIG=/home/michey/kubeconfigs/kubeconfig.llmd.fra"
echo "  kubectl logs -f ${POD_NAME} -n ${NAMESPACE}"
echo ""
echo "Access results after completion:"
echo "  # Create a pod to access the PVC"
echo "  kubectl run results-viewer --image=busybox -n ${NAMESPACE} --restart=Never --rm -it --overrides='{\"spec\":{\"containers\":[{\"name\":\"results-viewer\",\"image\":\"busybox\",\"command\":[\"sh\"],\"volumeMounts\":[{\"name\":\"results\",\"mountPath\":\"/results\"}]}],\"volumes\":[{\"name\":\"results\",\"persistentVolumeClaim\":{\"claimName\":\"${PVC_NAME}\"}}]}}' -- sh"
echo ""
echo "Or copy results to local:"
echo "  kubectl run copy-pod --image=busybox -n ${NAMESPACE} --restart=Never --overrides='{\"spec\":{\"containers\":[{\"name\":\"copy\",\"image\":\"busybox\",\"command\":[\"sleep\",\"3600\"],\"volumeMounts\":[{\"name\":\"results\",\"mountPath\":\"/results\"}]}],\"volumes\":[{\"name\":\"results\",\"persistentVolumeClaim\":{\"claimName\":\"${PVC_NAME}\"}}]}}'"
echo "  kubectl wait --for=condition=Ready pod/copy-pod -n ${NAMESPACE}"
echo "  kubectl cp ${NAMESPACE}/copy-pod:/results ~/benchflow/capacity_73_results"
echo "  kubectl delete pod copy-pod -n ${NAMESPACE}"
echo ""
echo "Delete resources when done:"
echo "  kubectl delete pod ${POD_NAME} -n ${NAMESPACE}"
echo "  kubectl delete configmap ${WORKLOAD_CONFIG} -n ${NAMESPACE}"
echo "  kubectl delete pvc ${PVC_NAME} -n ${NAMESPACE}"
echo "=========================================="
