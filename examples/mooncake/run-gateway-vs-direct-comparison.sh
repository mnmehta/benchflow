#!/usr/bin/env bash
set -euo pipefail

#
# Mooncake Benchmark: Gateway vs Direct Service Comparison
#
# This script runs the Mooncake benchmark in two phases:
# 1. Phase 1: Deploy gpt-oss-120b and benchmark via intelligent gateway (with cleanup)
# 2. Phase 2: Deploy fresh gpt-oss-120b instance and benchmark via direct K8s service
#
# Phase 2 uses target.force_deploy=true to deploy the model AND benchmark via a direct
# service (bypassing the gateway) in a single run, ensuring complete artifact collection.
#
# This allows comparing the performance impact of llm-d's intelligent scheduling/routing
# while ensuring fair comparison with clean KV cache state for the direct benchmark.
#

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BENCHFLOW_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Configuration - Two KUBECONFIGs required
BFLOW_KUBECONFIG="${BFLOW_KUBECONFIG:-/home/michey/kubeconfigs/kubeconfig.alberto_bflow}"
TARGET_KUBECONFIG="${TARGET_KUBECONFIG:-/home/michey/kubeconfigs/kubeconfig.llmd.fra}"
CLUSTER_NAME="${CLUSTER_NAME:-psap-h200-fra-rhaiis}"
NAMESPACE="${NAMESPACE:-benchflow}"
REPO_REF="${REPO_REF:-v0.6.0}"

# Skip phase 1 (gateway) if SKIP_GATEWAY=1
SKIP_GATEWAY="${SKIP_GATEWAY:-0}"

#echo Temporarily using fire athena =================================================
#TARGET_KUBECONFIG="${TARGET_KUBECONFIG:-/home/michey/kubeconfigs/kubeconfig.fire-athena}"
#CLUSTER_NAME="${CLUSTER_NAME:-psap-h200-fire-athena}"

# Experiment files
GATEWAY_EXPERIMENT="${BENCHFLOW_ROOT}/experiments/llm-d/gpt-oss-120b-release.yaml"
DIRECT_EXPERIMENT="${SCRIPT_DIR}/gpt-oss-120b-release-direct-combined.yaml"
DIRECT_SERVICE_YAML="${SCRIPT_DIR}/gpt-oss-120b-direct-service.yaml"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${BLUE}[INFO]${NC} $*"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $*"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $*"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $*"
}

# Optional: Override BenchFlow image (for testing custom builds)
BENCHFLOW_IMAGE="${BENCHFLOW_IMAGE:-}"
BENCHFLOW_IMAGE_FLAG=""
if [ -n "$BENCHFLOW_IMAGE" ]; then
    BENCHFLOW_IMAGE_FLAG="--benchflow-image ${BENCHFLOW_IMAGE}"
    log_info "Using custom BenchFlow image: ${BENCHFLOW_IMAGE}"
fi

wait_for_pipelinerun() {
    local run_name="$1"
    local timeout="${2:-7200}"  # Default 2 hours

    log_info "Waiting for PipelineRun '${run_name}' to complete (timeout: ${timeout}s)..."

    local elapsed=0
    local check_interval=30

    while [ $elapsed -lt $timeout ]; do
        # Get PipelineRun status (use BenchFlow kubeconfig)
        local status=$(KUBECONFIG="${BFLOW_KUBECONFIG}" kubectl get pipelinerun "${run_name}" -n "${NAMESPACE}" \
            -o jsonpath='{.status.conditions[?(@.type=="Succeeded")].status}' 2>/dev/null || echo "NotFound")

        local reason=$(KUBECONFIG="${BFLOW_KUBECONFIG}" kubectl get pipelinerun "${run_name}" -n "${NAMESPACE}" \
            -o jsonpath='{.status.conditions[?(@.type=="Succeeded")].reason}' 2>/dev/null || echo "Unknown")

        if [ "$status" = "True" ]; then
            log_success "PipelineRun '${run_name}' completed successfully!"
            return 0
        elif [ "$status" = "False" ]; then
            log_error "PipelineRun '${run_name}' failed with reason: ${reason}"
            log_error "Check logs with: kubectl logs -n ${NAMESPACE} -l tekton.dev/pipelineRun=${run_name} --all-containers"
            return 1
        elif [ "$status" = "NotFound" ]; then
            log_warn "PipelineRun '${run_name}' not found yet, waiting..."
        else
            # Still running
            log_info "PipelineRun status: ${reason} (elapsed: ${elapsed}s / ${timeout}s)"
        fi

        sleep $check_interval
        elapsed=$((elapsed + check_interval))
    done

    log_error "Timeout waiting for PipelineRun '${run_name}' after ${timeout}s"
    return 1
}

get_latest_pipelinerun() {
    local label_selector="$1"

    # Use BenchFlow kubeconfig to get PipelineRuns
    KUBECONFIG="${BFLOW_KUBECONFIG}" kubectl get pipelinerun -n "${NAMESPACE}" \
        -l "${label_selector}" \
        --sort-by=.metadata.creationTimestamp \
        -o jsonpath='{.items[-1:].metadata.name}' 2>/dev/null || echo ""
}

check_prerequisites() {
    log_info "Checking prerequisites..."

    # Check BenchFlow KUBECONFIG
    if [ ! -f "${BFLOW_KUBECONFIG}" ]; then
        log_error "BenchFlow KUBECONFIG not found: ${BFLOW_KUBECONFIG}"
        exit 1
    fi
    log_success "BenchFlow KUBECONFIG: ${BFLOW_KUBECONFIG}"

    # Check Target KUBECONFIG
    if [ ! -f "${TARGET_KUBECONFIG}" ]; then
        log_error "Target KUBECONFIG not found: ${TARGET_KUBECONFIG}"
        exit 1
    fi
    log_success "Target KUBECONFIG: ${TARGET_KUBECONFIG}"

    # Check bflow command
    if ! command -v bflow &> /dev/null; then
        log_error "bflow command not found. Is BenchFlow installed?"
        exit 1
    fi
    log_success "bflow command: $(which bflow)"

    # Check kubectl/oc
    if ! command -v kubectl &> /dev/null && ! command -v oc &> /dev/null; then
        log_error "kubectl or oc command not found"
        exit 1
    fi
    log_success "kubectl/oc: $(command -v kubectl || command -v oc)"

    # Check experiment files
    if [ ! -f "${GATEWAY_EXPERIMENT}" ]; then
        log_error "Gateway experiment not found: ${GATEWAY_EXPERIMENT}"
        exit 1
    fi
    if [ ! -f "${DIRECT_EXPERIMENT}" ]; then
        log_error "Direct experiment not found: ${DIRECT_EXPERIMENT}"
        exit 1
    fi
    if [ ! -f "${DIRECT_SERVICE_YAML}" ]; then
        log_error "Direct service YAML not found: ${DIRECT_SERVICE_YAML}"
        exit 1
    fi
    log_success "All experiment files found"

    # Check BenchFlow cluster connectivity
    if ! KUBECONFIG="${BFLOW_KUBECONFIG}" kubectl get namespace "${NAMESPACE}" &> /dev/null; then
        log_error "Cannot access namespace '${NAMESPACE}' on BenchFlow cluster"
        exit 1
    fi
    log_success "BenchFlow cluster connectivity verified"

    # Check target cluster connectivity
    if ! KUBECONFIG="${TARGET_KUBECONFIG}" kubectl get namespace "${NAMESPACE}" &> /dev/null; then
        log_error "Cannot access namespace '${NAMESPACE}' on target cluster"
        exit 1
    fi
    log_success "Target cluster connectivity verified"
}

# =============================================================================
# MAIN EXECUTION
# =============================================================================

main() {
    cat << 'EOF'
╔════════════════════════════════════════════════════════════════════════════╗
║                                                                            ║
║          Mooncake Benchmark: Gateway vs Direct Comparison                 ║
║                                                                            ║
║  This benchmark compares llm-d intelligent gateway routing against        ║
║  direct service access to measure the impact of KV-cache-aware scheduling ║
║                                                                            ║
╚════════════════════════════════════════════════════════════════════════════╝
EOF

    echo ""
    log_info "Configuration:"
    log_info "  BenchFlow KUBECONFIG: ${BFLOW_KUBECONFIG}"
    log_info "  Target KUBECONFIG: ${TARGET_KUBECONFIG}"
    log_info "  Cluster: ${CLUSTER_NAME}"
    log_info "  Namespace: ${NAMESPACE}"
    log_info "  llm-d version: ${REPO_REF}"
    echo ""

    check_prerequisites

    # =========================================================================
    # PHASE 1: Deploy and benchmark via intelligent gateway
    # =========================================================================

    if [ "${SKIP_GATEWAY}" = "1" ]; then
        log_info "SKIP_GATEWAY=1, skipping Phase 1 (gateway benchmark)"
        GATEWAY_RUN="skipped"
    else
        echo ""
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        log_info "PHASE 1: Deploy gpt-oss-120b and run Mooncake benchmark via intelligent gateway"
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo ""

        log_info "Submitting gateway benchmark experiment..."
        GATEWAY_OUTPUT=$(KUBECONFIG="${BFLOW_KUBECONFIG}" bflow experiment run "${GATEWAY_EXPERIMENT}" \
            --cluster-name "${CLUSTER_NAME}" \
            --llmd-repo-ref "${REPO_REF}" \
            --no-download \
            ${BENCHFLOW_IMAGE_FLAG} 2>&1)

        echo "${GATEWAY_OUTPUT}"

        # Extract PipelineRun name from last line of output
        GATEWAY_RUN=$(echo "${GATEWAY_OUTPUT}" | tail -1 | tr -d '[:space:]')

        if [ -z "${GATEWAY_RUN}" ]; then
            log_error "Could not extract PipelineRun name from bflow output"
            exit 1
        fi

        log_info "PipelineRun created: ${GATEWAY_RUN}"
        log_info "Monitor with: kubectl logs -n ${NAMESPACE} -l tekton.dev/pipelineRun=${GATEWAY_RUN} -f --all-containers"
        echo ""

        # Wait for completion
        if ! wait_for_pipelinerun "${GATEWAY_RUN}" 7200; then
            log_error "Gateway benchmark failed. Aborting."
            exit 1
        fi

        log_success "Gateway benchmark completed!"
        echo ""
    fi

    # =========================================================================
    # PHASE 2: Deploy direct instance and benchmark via service (force_deploy)
    # =========================================================================

    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    log_info "PHASE 2: Deploy fresh direct instance and benchmark via direct service"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""

    log_info "Creating direct service on target cluster..."
    KUBECONFIG="${TARGET_KUBECONFIG}" kubectl apply -f "${DIRECT_SERVICE_YAML}" -n "${NAMESPACE}"

    # Verify service was created
    sleep 2
    if KUBECONFIG="${TARGET_KUBECONFIG}" kubectl get service gpt-oss-120b-direct -n "${NAMESPACE}" &> /dev/null; then
        log_success "Direct service 'gpt-oss-120b-direct' created"
        log_info "Service will get endpoints once deployment creates pods"
    else
        log_error "Failed to create direct service"
        exit 1
    fi
    echo ""

    log_info "Submitting direct experiment (deploy + benchmark via service, force_deploy enabled)..."
    DIRECT_OUTPUT=$(KUBECONFIG="${BFLOW_KUBECONFIG}" bflow experiment run "${DIRECT_EXPERIMENT}" \
        --cluster-name "${CLUSTER_NAME}" \
        --llmd-repo-ref "${REPO_REF}" \
        --no-download \
        --no-cleanup \
        ${BENCHFLOW_IMAGE_FLAG} 2>&1)

    echo "${DIRECT_OUTPUT}"

    # Extract PipelineRun name from last line of output
    DIRECT_RUN=$(echo "${DIRECT_OUTPUT}" | tail -1 | tr -d '[:space:]')

    if [ -z "${DIRECT_RUN}" ]; then
        log_error "Could not extract PipelineRun name from bflow output"
        exit 1
    fi

    # Verify it's different from gateway run
    if [ "${DIRECT_RUN}" = "${GATEWAY_RUN}" ]; then
        log_error "Direct and gateway PipelineRuns are the same! This should not happen."
        log_error "Gateway: ${GATEWAY_RUN}"
        log_error "Direct: ${DIRECT_RUN}"
        exit 1
    fi

    log_info "PipelineRun created: ${DIRECT_RUN}"
    log_info "Monitor with: kubectl logs -n ${NAMESPACE} -l tekton.dev/pipelineRun=${DIRECT_RUN} -f --all-containers"
    echo ""

    # Wait for completion
    if ! wait_for_pipelinerun "${DIRECT_RUN}" 7200; then
        log_error "Direct experiment failed. Aborting."
        exit 1
    fi

    log_success "Direct experiment completed!"
    echo ""

    # =========================================================================
    # SUMMARY
    # =========================================================================

    echo ""
    echo "╔════════════════════════════════════════════════════════════════════════════╗"
    echo "║                         BENCHMARK COMPARISON COMPLETE                      ║"
    echo "╚════════════════════════════════════════════════════════════════════════════╝"
    echo ""
    log_success "Both phases completed successfully!"
    echo ""
    log_info "Results Summary:"
    if [ "${GATEWAY_RUN}" != "skipped" ]; then
        log_info "  Phase 1 - Gateway (with scheduling):        ${GATEWAY_RUN}"
    fi
    log_info "  Phase 2 - Direct (via service, no sched):   ${DIRECT_RUN}"
    echo ""
    log_info "Compare results in MLflow:"
    log_info "  Experiment: michey-gpt-oss-120b-mooncake"
    log_info "  URL: https://mlflow.apps.aperdomo-lab.ibm.rhperfscale.org/#/experiments/43"
    echo ""
    if [ "${GATEWAY_RUN}" != "skipped" ]; then
        log_info "Key Differences:"
        log_info "  Gateway (${GATEWAY_RUN}):"
        log_info "    - KV-cache-aware routing (prefix-cache-scorer)"
        log_info "    - Load-balanced pod selection"
        log_info "    - Optimized for prefix matching"
        log_info ""
        log_info "  Direct (${DIRECT_RUN}):"
        log_info "    - Round-robin service routing"
        log_info "    - No cache-aware optimization"
        log_info "    - Fresh deployment (no KV cache pollution)"
        log_info "    - Complete artifact collection (force_deploy enabled)"
        echo ""
    else
        log_info "Note: Gateway phase was skipped (SKIP_GATEWAY=1)"
        echo ""
    fi
}

main "$@"
