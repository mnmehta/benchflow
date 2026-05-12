#!/usr/bin/env bash
set -euo pipefail

# Copy Benchmark Results from PVC

export KUBECONFIG=/home/michey/kubeconfigs/kubeconfig.llmd.fra
NAMESPACE="benchflow"
PVC_NAME="benchmark-results"
LOCAL_DIR="${HOME}/benchflow/capacity_73_results"
COPY_POD_NAME="copy-pod-$$"  # Use PID to make unique

echo "=========================================="
echo "Copying Benchmark Results from PVC"
echo "=========================================="
echo "PVC: ${PVC_NAME}"
echo "Namespace: ${NAMESPACE}"
echo "Local destination: ${LOCAL_DIR}"
echo ""

# Create temporary pod to access the PVC
echo "Creating temporary copy pod: ${COPY_POD_NAME}"
kubectl run "${COPY_POD_NAME}" --image=busybox -n "${NAMESPACE}" --restart=Never \
  --overrides="{\"spec\":{\"containers\":[{\"name\":\"copy\",\"image\":\"busybox\",\"command\":[\"sleep\",\"3600\"],\"volumeMounts\":[{\"name\":\"results\",\"mountPath\":\"/results\"}]}],\"volumes\":[{\"name\":\"results\",\"persistentVolumeClaim\":{\"claimName\":\"${PVC_NAME}\"}}]}}"

echo "Waiting for pod to be ready..."
if kubectl wait --for=condition=Ready pod/"${COPY_POD_NAME}" -n "${NAMESPACE}" --timeout=120s; then
    echo "✓ Pod is ready"
else
    echo "✗ Pod failed to become ready"
    kubectl describe pod "${COPY_POD_NAME}" -n "${NAMESPACE}"
    kubectl delete pod "${COPY_POD_NAME}" -n "${NAMESPACE}" --force --grace-period=0 2>/dev/null || true
    exit 1
fi

echo ""
echo "Listing files in PVC..."
kubectl exec "${COPY_POD_NAME}" -n "${NAMESPACE}" -- ls -lah /results/ || true

echo ""
echo "Copying results to ${LOCAL_DIR}..."
rm -rf "${LOCAL_DIR}"
mkdir -p "${LOCAL_DIR}"

if kubectl cp "${NAMESPACE}/${COPY_POD_NAME}:/results" "${LOCAL_DIR}"; then
    echo "✓ Results copied successfully"
else
    echo "✗ Failed to copy results"
    kubectl delete pod "${COPY_POD_NAME}" -n "${NAMESPACE}" --force --grace-period=0 2>/dev/null || true
    exit 1
fi

echo ""
echo "Cleaning up copy pod..."
kubectl delete pod "${COPY_POD_NAME}" -n "${NAMESPACE}"

echo ""
echo "=========================================="
echo "Results Summary"
echo "=========================================="
echo "Location: ${LOCAL_DIR}"
echo ""
echo "Contents:"
ls -lh "${LOCAL_DIR}/"
echo ""

if [ -d "${LOCAL_DIR}/analysis" ]; then
    echo "Analysis directory:"
    ls -lh "${LOCAL_DIR}/analysis/"
    echo ""
fi

if [ -f "${LOCAL_DIR}/summary.json" ]; then
    echo "Performance Summary (summary.json):"
    cat "${LOCAL_DIR}/summary.json"
    echo ""
fi

echo "=========================================="
echo "Results successfully retrieved!"
echo "=========================================="
