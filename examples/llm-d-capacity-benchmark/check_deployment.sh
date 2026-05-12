#!/usr/bin/env bash
set -euo pipefail

# Deployment Verification Script
# Use this to check your deployment and get the correct endpoint details

NAMESPACE="${NAMESPACE:-benchflow}"
RELEASE_NAME="${RELEASE_NAME:-qwen3-32b}"

echo "=========================================="
echo "Checking Deployment: ${RELEASE_NAME}"
echo "Namespace: ${NAMESPACE}"
echo "=========================================="
echo ""

# Check pods
echo "Pods:"
kubectl get pods -n "${NAMESPACE}" -l "app.kubernetes.io/instance=${RELEASE_NAME}" 2>/dev/null || \
    kubectl get pods -n "${NAMESPACE}" | grep "${RELEASE_NAME}" || \
    echo "  No pods found for release: ${RELEASE_NAME}"
echo ""

# Check gateways
echo "Gateways:"
kubectl get gateway -n "${NAMESPACE}" 2>/dev/null | grep -i "${RELEASE_NAME}\|llm-d" || \
    echo "  No gateways found"
echo ""

# Check services
echo "Services:"
kubectl get svc -n "${NAMESPACE}" 2>/dev/null | grep -E "${RELEASE_NAME}|epp|gateway" || \
    echo "  No services found"
echo ""

# Check deployments
echo "Deployments:"
kubectl get deployment -n "${NAMESPACE}" 2>/dev/null | grep "${RELEASE_NAME}" || \
    echo "  No deployments found"
echo ""

# Try to find the endpoint
echo "=========================================="
echo "Attempting to detect endpoint..."
echo "=========================================="

# Method 1: Try infra-{release}-gateway-istio (llm-d v0.6.0+)
GATEWAY_SVC="infra-${RELEASE_NAME}-inference-gateway-istio"
if kubectl get svc "${GATEWAY_SVC}" -n "${NAMESPACE}" &>/dev/null; then
    echo "✓ Found gateway service: ${GATEWAY_SVC}"
    ENDPOINT="http://${GATEWAY_SVC}.${NAMESPACE}.svc.cluster.local:80"
    echo "  Endpoint: ${ENDPOINT}/v1"
    exit 0
fi

# Method 2: Try gaie-{release}-epp (llm-d v0.6.0+ recipe layout)
EPP_SVC="gaie-${RELEASE_NAME}-epp"
if kubectl get svc "${EPP_SVC}" -n "${NAMESPACE}" &>/dev/null; then
    echo "✓ Found EPP service: ${EPP_SVC}"
    ENDPOINT="http://${EPP_SVC}.${NAMESPACE}.svc.cluster.local:80"
    echo "  Endpoint: ${ENDPOINT}/v1"
    exit 0
fi

# Method 3: Try ms-{release} (llm-d v0.4.0)
MS_SVC="ms-${RELEASE_NAME}"
if kubectl get svc "${MS_SVC}" -n "${NAMESPACE}" &>/dev/null; then
    echo "✓ Found ModelService: ${MS_SVC}"
    ENDPOINT="http://${MS_SVC}.${NAMESPACE}.svc.cluster.local:8000"
    echo "  Endpoint: ${ENDPOINT}/v1"
    exit 0
fi

# Method 4: Try llm-d-inference-gateway (shared gateway)
SHARED_GATEWAY="llm-d-inference-gateway"
if kubectl get gateway "${SHARED_GATEWAY}" -n "${NAMESPACE}" &>/dev/null; then
    echo "✓ Found shared gateway: ${SHARED_GATEWAY}"
    echo "  Note: This is a shared gateway. Get the endpoint from the gateway status."
    kubectl get gateway "${SHARED_GATEWAY}" -n "${NAMESPACE}" -o jsonpath='{.status.addresses[0].value}' || true
    exit 0
fi

echo "✗ Could not automatically detect endpoint"
echo ""
echo "Please check your deployment manually:"
echo "  kubectl get all -n ${NAMESPACE}"
echo "  kubectl get gateway -n ${NAMESPACE}"
echo ""
echo "Then update the scripts with the correct service name and endpoint."
