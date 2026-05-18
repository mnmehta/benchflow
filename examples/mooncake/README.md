# Mooncake Benchmark: Gateway vs Direct Comparison

This directory contains scripts and configurations for running the Mooncake conversation trace benchmark to compare llm-d's intelligent gateway routing against direct service access.

## Purpose

Test the performance impact of llm-d's KV-cache-aware scheduling by running the same Mooncake workload twice:

1. **Gateway mode**: Traffic routed through llm-d intelligent gateway with prefix cache scoring
2. **Direct mode**: Traffic sent directly to model pods via Kubernetes service (round-robin)

This measures the benefit of intelligent scheduling for workloads with significant prefix overlap.

## Files

- **`run-gateway-vs-direct-comparison.sh`**: Main benchmark script
- **`gpt-oss-120b-release-direct-deploy.yaml`**: Deploy-only experiment (Phase 2A)
- **`gpt-oss-120b-release-direct-benchmark.yaml`**: Benchmark-only experiment (Phase 2B)
- **`gpt-oss-120b-release-direct-combined.yaml`**: Combined deploy+benchmark using `force_deploy` (alternative to 2-stage)
- **`gpt-oss-120b-direct-service.yaml`**: Kubernetes service for direct pod access
- **`README.md`**: This documentation

## Prerequisites

- BenchFlow installed and configured
- Access to BenchFlow management cluster (default: `/home/michey/kubeconfigs/kubeconfig.alberto_bflow`)
- Access to target cluster (default: `/home/michey/kubeconfigs/kubeconfig.llmd.fra`)
- llm-d v0.6.0 available (or override with `REPO_REF`)

## Quick Start

### Default Run

```bash
cd examples/mooncake
./run-gateway-vs-direct-comparison.sh
```

### Custom Configuration

```bash
# Use different cluster
CLUSTER_NAME=my-cluster ./run-gateway-vs-direct-comparison.sh

# Use different llm-d version
REPO_REF=v0.5.0 ./run-gateway-vs-direct-comparison.sh

# Use different kubeconfigs
BFLOW_KUBECONFIG=/path/to/bflow-kubeconfig \
TARGET_KUBECONFIG=/path/to/target-kubeconfig \
./run-gateway-vs-direct-comparison.sh

# Combine multiple overrides
CLUSTER_NAME=my-cluster \
REPO_REF=v0.5.0 \
BFLOW_KUBECONFIG=/path/to/bflow-kubeconfig \
./run-gateway-vs-direct-comparison.sh
```

## What the Script Does

### Phase 1: Gateway Benchmark

1. Deploys gpt-oss-120b with llm-d gateway (8 replicas, TP=2)
2. Runs Mooncake conversation trace (500 prompts) via intelligent gateway
3. Waits for completion (~30-45 minutes)
4. Collects metrics from Prometheus

**Gateway Features:**
- KV-cache-aware routing (prefix-cache-scorer)
- Load-aware scheduling (load-aware-scorer)
- Request routing to pods with matching prefixes

### Phase 2: Direct Service Benchmark

1. Creates Kubernetes service pointing directly to model pods
2. Runs same Mooncake trace via direct service (bypasses gateway)
3. Waits for completion (~30-45 minutes)
4. Collects metrics from Prometheus

**Direct Service Behavior:**
- Standard Kubernetes service load balancing (round-robin)
- No KV-cache awareness
- No intelligent routing

## Expected Results

### Gateway Mode (With Scheduling)
- ✅ Better TTFT (prefix cache hits)
- ✅ More balanced load across pods
- ✅ Higher KV cache hit rate
- ✅ Better handling of bursty traffic

### Direct Mode (Without Scheduling)
- ⚠️ Higher TTFT variance (random routing)
- ⚠️ Uneven pod utilization
- ⚠️ Lower KV cache efficiency
- ⚠️ More cache evictions

## Monitoring Progress

### Watch PipelineRuns (BenchFlow Cluster)

```bash
export KUBECONFIG=/home/michey/kubeconfigs/kubeconfig.alberto_bflow
kubectl get pipelinerun -n benchflow -w
```

### Follow Logs (BenchFlow Cluster)

```bash
export KUBECONFIG=/home/michey/kubeconfigs/kubeconfig.alberto_bflow

# Gateway run
kubectl logs -n benchflow -l tekton.dev/pipelineRun=<gateway-run-name> -f --all-containers

# Direct run
kubectl logs -n benchflow -l tekton.dev/pipelineRun=<direct-run-name> -f --all-containers
```

### Check Pod Status (Target Cluster)

```bash
export KUBECONFIG=/home/michey/kubeconfigs/kubeconfig.llmd.fra

# Model pods
kubectl get pods -n benchflow -l app.kubernetes.io/instance=gpt-oss-120b-llm-d-release

# Gateway pod
kubectl get pods -n benchflow -l app.kubernetes.io/component=gateway

# Direct service
kubectl get service gpt-oss-120b-direct -n benchflow
```

## Results Analysis

### MLflow Comparison

Both runs log to the same MLflow experiment:
- **Experiment**: `michey-gpt-oss-120b-mooncake`
- **URL**: https://mlflow.apps.aperdomo-lab.ibm.rhperfscale.org/#/experiments/43

**How to identify runs:**
- **Gateway run**: Tagged with `deployment_type: llm-d-inference-scheduling` (default)
- **Direct run**: Tagged with `routing_mode: direct-service` and note about bypassing gateway

Filter in MLflow by tags to easily find and compare the two runs.

### Key Metrics to Compare

| Metric | Gateway (Expected) | Direct (Expected) | Why Different? |
|--------|-------------------|------------------|----------------|
| **TTFT P50** | Lower | Higher | Cache-aware routing reduces prefill time |
| **TTFT P95** | Much lower | Much higher | Worst-case improved by cache hits |
| **Throughput** | Similar | Similar | Same compute capacity |
| **Prefix Cache Hit Rate** | Higher | Lower | Smart routing maximizes reuse |
| **Queue Depth Variance** | Lower | Higher | Better load balancing |

### Generate Comparison Report

```bash
# After both runs complete, generate comparison report
bflow benchmark plot comparison \
  --mlflow-run-ids <gateway-run-id> <direct-run-id> \
  --output mooncake-gateway-vs-direct.html
```

## Workload Details

**Dataset**: Mooncake conversation trace  
**Prompts**: 500 (capped from 12,031)  
**Avg Input Length**: ~12K tokens  
**Avg Output Length**: ~343 tokens  
**Expected Cache Hit Rate**: 16.4% (with 500 prompts)  
**Total Duration**: ~20-30 minutes per run  

See `../../MOONCAKE_ANALYSIS.md` for detailed workload analysis.

## Deployment Approaches

### Two-Stage Approach (Current Script Default)

The script uses a two-stage process to ensure fresh deployment and complete artifact collection:

**Phase 2A - Deploy Only:**
```bash
bflow experiment run gpt-oss-120b-release-direct-deploy.yaml --no-benchmark
```
- Deploys model with release name `gpt-oss-120b-llm-d-release-direct`
- Creates pods, gateway, services, etc.
- No benchmark runs

**Phase 2B - Benchmark Only:**
```bash
# First create the direct service
kubectl apply -f gpt-oss-120b-direct-service.yaml

# Then run benchmark
bflow experiment run gpt-oss-120b-release-direct-benchmark.yaml --no-deploy
```
- Benchmarks via `target.base_url` (direct service)
- Collects artifacts from deployed pods (via `metrics_release_name`)
- Skips deployment (already done in Phase 2A)

### Single-Stage Approach (Using `force_deploy`)

Alternatively, use the combined experiment with `force_deploy: true`:

```bash
# Create service first
kubectl apply -f gpt-oss-120b-direct-service.yaml

# Run combined deploy + benchmark
bflow experiment run gpt-oss-120b-release-direct-combined.yaml
```

**Key difference:** The combined experiment has `target.force_deploy: true`, which tells BenchFlow to:
1. Deploy the model (normally skipped when `target.base_url` is set)
2. Benchmark via the target URL (not the deployed gateway)
3. Collect complete artifacts from the deployment
4. Clean up the deployment

**Example YAML:**
```yaml
spec:
  target:
    base_url: http://gpt-oss-120b-direct:8000
    metrics_release_name: gpt-oss-120b-llm-d-release-direct
    force_deploy: true  # Deploy AND use custom target URL
```

**When to use each approach:**
- **Two-stage**: More explicit control, easier to debug individual phases
- **Single-stage**: Simpler orchestration, guaranteed artifact completeness

## Cleanup

The script runs with `--no-cleanup` to preserve the deployment for the second benchmark.

To clean up manually after both runs:

```bash
# Delete service on target cluster
export KUBECONFIG=/home/michey/kubeconfigs/kubeconfig.llmd.fra
kubectl delete service gpt-oss-120b-direct -n benchflow
```

Or run a cleanup experiment via BenchFlow:

```bash
export KUBECONFIG=/home/michey/kubeconfigs/kubeconfig.alberto_bflow
bflow experiment run experiments/llm-d/gpt-oss-120b-release.yaml \
  --cluster-name psap-h200-fra-rhaiis \
  --no-deploy \
  --no-benchmark \
  --no-collect \
  --cleanup
```

This will clean up the deployment, gateway, and all llm-d resources on the target cluster.

## Troubleshooting

### PipelineRun Stuck or Failed (Use BenchFlow KUBECONFIG)

```bash
export KUBECONFIG=/home/michey/kubeconfigs/kubeconfig.alberto_bflow

# Check PipelineRun status
kubectl describe pipelinerun <run-name> -n benchflow

# Check TaskRun status
kubectl get taskrun -n benchflow -l tekton.dev/pipelineRun=<run-name>

# Check logs
kubectl logs -n benchflow -l tekton.dev/pipelineRun=<run-name> --all-containers
```

### Service Not Found (Use Target KUBECONFIG)

If the direct service benchmark fails with connection errors:

```bash
export KUBECONFIG=/home/michey/kubeconfigs/kubeconfig.llmd.fra

# Verify service exists
kubectl get service gpt-oss-120b-direct -n benchflow

# Check endpoints
kubectl get endpoints gpt-oss-120b-direct -n benchflow

# Verify service selector matches pods
kubectl get pods -n benchflow -l app.kubernetes.io/component=decode
```

### Gateway Not Found (Use Target KUBECONFIG)

If the gateway benchmark fails:

```bash
export KUBECONFIG=/home/michey/kubeconfigs/kubeconfig.llmd.fra

# Check if gateway was created
kubectl get gateway -n benchflow

# Should see: infra-gpt-oss-120b-llm-d-release-inference-gateway
```

This is the bug we fixed with the cherry-pick - make sure you're on a branch with the fix!

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `BFLOW_KUBECONFIG` | `/home/michey/kubeconfigs/kubeconfig.alberto_bflow` | Path to BenchFlow management cluster kubeconfig |
| `TARGET_KUBECONFIG` | `/home/michey/kubeconfigs/kubeconfig.llmd.fra` | Path to target cluster kubeconfig |
| `CLUSTER_NAME` | `psap-h200-fra-rhaiis` | BenchFlow target cluster name |
| `NAMESPACE` | `benchflow` | Kubernetes namespace |
| `REPO_REF` | `v0.6.0` | llm-d version to deploy |

### KUBECONFIG Usage

The script uses **two different kubeconfigs**:

1. **`BFLOW_KUBECONFIG`** - For BenchFlow operations:
   - Running `bflow experiment run` commands
   - Monitoring PipelineRuns and TaskRuns
   - Checking Tekton execution status

2. **`TARGET_KUBECONFIG`** - For target cluster operations:
   - Deploying Kubernetes services (`kubectl apply`)
   - Checking pods and deployments
   - Verifying gateway and service status

## References

- **Mooncake Dataset**: https://github.com/kvcache-ai/Mooncake
- **llm-d**: https://github.com/llm-d/llm-d
- **BenchFlow**: https://github.com/albertoperdomo2/benchflow
- **Workload Analysis**: ../../MOONCAKE_ANALYSIS.md
