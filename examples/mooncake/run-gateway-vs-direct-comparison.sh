#!/usr/bin/env bash
set -euo pipefail

#
# Mooncake Benchmark: Gateway vs Direct Service Comparison
#
# This script runs the Mooncake benchmark in three phases:
# 1. Phase 1: Deploy gpt-oss-120b and benchmark via intelligent gateway (with cleanup)
# 2. Phase 2A: Deploy fresh gpt-oss-120b instance (clean KV cache, no pollution from Phase 1)
# 3. Phase 2B: Benchmark via direct K8s service (bypasses gateway, round-robin routing)
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

# Experiment files
GATEWAY_EXPERIMENT="${BENCHFLOW_ROOT}/experiments/llm-d/gpt-oss-120b-release.yaml"
DIRECT_DEPLOY_EXPERIMENT="${SCRIPT_DIR}/gpt-oss-120b-release-direct-deploy.yaml"
DIRECT_BENCHMARK_EXPERIMENT="${SCRIPT_DIR}/gpt-oss-120b-release-direct-benchmark.yaml"
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
    if [ ! -f "${DIRECT_DEPLOY_EXPERIMENT}" ]; then
        log_error "Direct deploy experiment not found: ${DIRECT_DEPLOY_EXPERIMENT}"
        exit 1
    fi
    if [ ! -f "${DIRECT_BENCHMARK_EXPERIMENT}" ]; then
        log_error "Direct benchmark experiment not found: ${DIRECT_BENCHMARK_EXPERIMENT}"
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

    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    log_info "PHASE 1: Deploy gpt-oss-120b and run Mooncake benchmark via intelligent gateway"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""

    log_info "Submitting gateway benchmark experiment..."
    GATEWAY_OUTPUT=$(KUBECONFIG="${BFLOW_KUBECONFIG}" bflow experiment run "${GATEWAY_EXPERIMENT}" \
        --cluster-name "${CLUSTER_NAME}" \
        --llmd-repo-ref "${REPO_REF}" \
        --no-download 2>&1)

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

    # =========================================================================
    # PHASE 2A: Deploy fresh direct instance (clean KV cache)
    # =========================================================================

    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    log_info "PHASE 2A: Deploy fresh direct instance (no KV cache pollution)"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""

    log_info "Submitting direct deployment experiment (deploy-only, no benchmark)..."
    DIRECT_DEPLOY_OUTPUT=$(KUBECONFIG="${BFLOW_KUBECONFIG}" bflow experiment run "${DIRECT_DEPLOY_EXPERIMENT}" \
        --cluster-name "${CLUSTER_NAME}" \
        --llmd-repo-ref "${REPO_REF}" \
        --no-download \
        --no-benchmark \
        --no-cleanup 2>&1)

    echo "${DIRECT_DEPLOY_OUTPUT}"

    # Extract PipelineRun name from last line of output
    DIRECT_DEPLOY_RUN=$(echo "${DIRECT_DEPLOY_OUTPUT}" | tail -1 | tr -d '[:space:]')

    if [ -z "${DIRECT_DEPLOY_RUN}" ]; then
        log_error "Could not extract PipelineRun name from bflow output"
        exit 1
    fi

    log_info "PipelineRun created: ${DIRECT_DEPLOY_RUN}"
    log_info "Monitor with: kubectl logs -n ${NAMESPACE} -l tekton.dev/pipelineRun=${DIRECT_DEPLOY_RUN} -f --all-containers"
    echo ""

    # Wait for deployment to complete
    if ! wait_for_pipelinerun "${DIRECT_DEPLOY_RUN}" 7200; then
        log_error "Direct deployment failed. Aborting."
        exit 1
    fi

    log_success "Direct deployment completed!"
    echo ""

    # =========================================================================
    # PHASE 2B: Create direct service and benchmark
    # =========================================================================

    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    log_info "PHASE 2B: Create direct service and benchmark (bypass gateway)"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""

    log_info "Creating direct service on target cluster..."
    KUBECONFIG="${TARGET_KUBECONFIG}" kubectl apply -f "${DIRECT_SERVICE_YAML}" -n "${NAMESPACE}"

    # Verify service was created and has endpoints
    sleep 5
    if KUBECONFIG="${TARGET_KUBECONFIG}" kubectl get service gpt-oss-120b-direct -n "${NAMESPACE}" &> /dev/null; then
        log_success "Direct service 'gpt-oss-120b-direct' created"

        # Check for endpoints
        ENDPOINT_COUNT=$(KUBECONFIG="${TARGET_KUBECONFIG}" kubectl get endpoints gpt-oss-120b-direct -n "${NAMESPACE}" \
            -o jsonpath='{.subsets[*].addresses[*].ip}' 2>/dev/null | wc -w || echo "0")

        if [ "${ENDPOINT_COUNT}" -gt 0 ]; then
            log_success "Service has ${ENDPOINT_COUNT} endpoint(s) ready"
        else
            log_warn "Service created but no endpoints found yet (may still be initializing)"
        fi
    else
        log_error "Failed to create direct service"
        exit 1
    fi
    echo ""

    log_info "Submitting direct benchmark experiment (benchmark-only, via service)..."
    DIRECT_BENCH_OUTPUT=$(KUBECONFIG="${BFLOW_KUBECONFIG}" bflow experiment run "${DIRECT_BENCHMARK_EXPERIMENT}" \
        --cluster-name "${CLUSTER_NAME}" \
        --llmd-repo-ref "${REPO_REF}" \
        --no-download \
        --no-deploy \
        --no-cleanup 2>&1)

    echo "${DIRECT_BENCH_OUTPUT}"

    # Extract PipelineRun name from last line of output
    DIRECT_BENCH_RUN=$(echo "${DIRECT_BENCH_OUTPUT}" | tail -1 | tr -d '[:space:]')

    if [ -z "${DIRECT_BENCH_RUN}" ]; then
        log_error "Could not extract PipelineRun name from bflow output"
        exit 1
    fi

    # Verify it's different from other runs
    if [ "${DIRECT_BENCH_RUN}" = "${GATEWAY_RUN}" ] || [ "${DIRECT_BENCH_RUN}" = "${DIRECT_DEPLOY_RUN}" ]; then
        log_error "PipelineRun names conflict!"
        log_error "Gateway: ${GATEWAY_RUN}"
        log_error "Direct Deploy: ${DIRECT_DEPLOY_RUN}"
        log_error "Direct Bench: ${DIRECT_BENCH_RUN}"
        exit 1
    fi

    log_info "PipelineRun created: ${DIRECT_BENCH_RUN}"
    log_info "Monitor with: kubectl logs -n ${NAMESPACE} -l tekton.dev/pipelineRun=${DIRECT_BENCH_RUN} -f --all-containers"
    echo ""

    # Wait for completion
    if ! wait_for_pipelinerun "${DIRECT_BENCH_RUN}" 7200; then
        log_error "Direct benchmark failed. Aborting."
        exit 1
    fi

    log_success "Direct benchmark completed!"
    echo ""

    # =========================================================================
    # SUMMARY
    # =========================================================================

    echo ""
    echo "╔════════════════════════════════════════════════════════════════════════════╗"
    echo "║                         BENCHMARK COMPARISON COMPLETE                      ║"
    echo "╚════════════════════════════════════════════════════════════════════════════╝"
    echo ""
    log_success "All phases completed successfully!"
    echo ""
    log_info "Results Summary:"
    log_info "  Gateway benchmark (with scheduling): ${GATEWAY_RUN}"
    log_info "  Direct deployment (deploy-only):     ${DIRECT_DEPLOY_RUN}"
    log_info "  Direct benchmark (no scheduling):    ${DIRECT_BENCH_RUN}"
    echo ""
    log_info "Compare results in MLflow:"
    log_info "  Experiment: michey-gpt-oss-120b-mooncake"
    log_info "  URL: https://mlflow.apps.aperdomo-lab.ibm.rhperfscale.org/#/experiments/43"
    echo ""
    log_info "Expected differences:"
    log_info "  - Gateway: Better TTFT via KV-cache-aware routing"
    log_info "  - Gateway: More balanced load across pods"
    log_info "  - Direct:  Round-robin, no cache optimization"
    log_info "  - Fresh deployment ensures no KV cache pollution"
    echo ""
}

main "$@"
