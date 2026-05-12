# llm-d 73-Capacity Benchmark

Standalone scripts to run the official [llm-d guide.yaml capacity benchmark](https://github.com/llm-d/llm-d/blob/main/guides/optimized-baseline/benchmark-templates/guide.yaml) with persistent storage.

## Overview

This benchmark evaluates KV cache scheduling strategies under controlled load conditions using the official llm-d optimized baseline configuration.

### Workload Characteristics

- **Cluster**: 8 pods with 307,328 KV tokens each (2,458,624 total capacity)
- **Data size**: ~73.2% of total capacity
- **System prompt**: 6,000 tokens (shared prefix)
- **User questions**: 1,200 tokens (cached per user)
- **Users per group**: 5
- **Total groups**: 150 (750 unique prompts total)
- **Output**: 1,000 tokens per request

### Load Pattern (Official guide.yaml)

1. **Warmup**: 15 req/s for 50 seconds (750 total requests)
2. **Main ladder**: 16 stages with optimized durations:
   - 3, 10, 15, 20, 22, 25, 30, 35, 40, 43, 46, 49, 52, 55, 57, 60 req/s
   - Stage durations: 20-38 seconds (optimized for each QPS level)

**Total**: ~15,334 requests over ~535 seconds (~9 minutes)

## Files

- **`deploy_benchmark_with_pvc.sh`** - Deploy benchmark pod with persistent storage
- **`copy_benchmark_results.sh`** - Retrieve results from PVC after completion
- **`check_deployment.sh`** - Verify deployment and auto-detect endpoints
- **`qwen3-32b-direct-service.yaml`** - Optional: K8s service for direct model access
- **`README.md`** - This documentation

## Prerequisites

- llm-d deployment with 8 replicas (e.g., Qwen/Qwen3-32B)
- Kubernetes cluster with kubectl/oc access
- PersistentVolumeClaim support (NFS or similar)

Default values (customizable via environment variables):
- Namespace: `benchflow`
- Release name: `qwen3-32b`
- Model: `Qwen/Qwen3-32B`

## Quick Start

### 1. Verify Your Deployment

```bash
# Check if your deployment is ready
./check_deployment.sh
```

### 2. Run the Benchmark

```bash
# Deploy benchmark pod with persistent storage (default: gateway mode)
./deploy_benchmark_with_pvc.sh

# OR: Run in direct service mode (bypass gateway)
USE_DIRECT_SERVICE=true ./deploy_benchmark_with_pvc.sh
```

This creates:
- PersistentVolumeClaim for results (survives pod deletion)
- ConfigMap with workload configuration
- Benchmark pod using `ghcr.io/llm-d/llm-d-benchmark:v0.6.0`

### 3. Monitor Progress

```bash
export KUBECONFIG=/path/to/your/kubeconfig
kubectl logs -f benchmark-capacity-73-qwen3-32b -n benchflow
```

### 4. Retrieve Results (after ~9 minutes)

```bash
# Copy results from PVC to local directory
./copy_benchmark_results.sh
```

Results are saved to `~/benchflow/capacity_73_results/`

## Understanding the Results

### Key Metrics

1. **Throughput**
   - Output tokens per second
   - Requests per second
   - Input tokens per second

2. **Latency**
   - Time to First Token (TTFT) - Critical for interactive workloads
   - Time Per Output Token (TPOT) - Decode performance
   - Inter-Token Latency (ITL) - Consistency between tokens
   - End-to-End latency

3. **Queue Behavior**
   - Queue depth over time
   - Request queuing patterns
   - Scheduling effectiveness

4. **Cache Efficiency**
   - KV cache hit rate
   - Cache utilization
   - Prefix reuse patterns

### Result Files

After running `copy_benchmark_results.sh`, you'll find:

```
~/benchflow/capacity_73_results/
├── summary_lifecycle_metrics.json     # Overall performance summary
├── stage_N_lifecycle_metrics.json     # Per-stage metrics (17 stages)
├── per_request_lifecycle_metrics.json # Detailed per-request data
├── benchmark.log                      # Full benchmark logs
└── analysis/                          # Visualizations
    ├── latency_vs_qps.png
    ├── throughput_vs_qps.png
    └── throughput_vs_latency.png
```

### Expected Performance Tiers

Based on the llm-d reference results:

| Strategy | Throughput (tok/s) | TTFT p90 (s) | Mean Queue |
|----------|-------------------|--------------|------------|
| Precise scheduling | 8,730 | 0.542 | 0.1 |
| Estimated scheduling | 6,944 | ~31 | 8.1 |
| Random/Load scheduling | ~4,400 | 85-94 | High |

### Analysis Tips

1. **Look for queuing bottlenecks**
   - If mean queue depth > 1, requests are backing up
   - High TTFT indicates scheduling inefficiency

2. **Check cache utilization**
   - With shared prefixes, expect high cache hit rates
   - Low cache hits may indicate poor request routing

3. **Identify saturation point**
   - The 16-stage ladder provides fine-grained characterization
   - Watch for latency increases in the 40-60 req/s range

4. **Compare with baseline**
   - Precise-scheduling should maintain stable latency
   - Other strategies show degradation at high QPS

## Customization

### Environment Variables

```bash
export NAMESPACE=my-namespace          # Kubernetes namespace
export RELEASE_NAME=my-model           # Deployment release name
export MODEL_NAME=MyOrg/MyModel        # HuggingFace model name
export USE_DIRECT_SERVICE=true         # Bypass gateway, use direct model service (default: false)
export DIRECT_SVC=my-custom-svc        # Custom service name for direct mode (default: ms-${RELEASE_NAME})
```

#### Endpoint Modes

**Gateway Mode (default)**: Routes traffic through llm-d's inference gateway and scheduler
- Endpoint: `infra-{release}-inference-gateway-istio.{namespace}.svc.cluster.local:80`
- Enables advanced features: request routing, KV cache scheduling, load balancing
- Best for testing scheduler strategies (precise, estimated, random)

**Direct Service Mode**: Bypasses gateway, sends requests directly to model pods
- Endpoint: `ms-{release}.{namespace}.svc.cluster.local:8000`
- No gateway overhead, no advanced scheduling
- Useful for baseline measurements or debugging
- Enable with: `export USE_DIRECT_SERVICE=true`

To create a custom direct service (optional):
```bash
kubectl apply -f qwen3-32b-direct-service.yaml
export DIRECT_SVC=qwen3-32b-direct
```

### Workload Parameters

Edit `deploy_benchmark_with_pvc.sh` to modify:

```bash
SYSTEM_PROMPT_LEN=6000    # Shared prefix length
QUESTION_LEN=1200         # Unique question length
USERS_PER_GROUP=5         # Prompts per shared prefix
OUTPUT_LEN=1000           # Generated tokens
NUM_GROUPS=150            # Total distinct prefixes
```

### Load Stages

The stages in `deploy_benchmark_with_pvc.sh` match the official guide.yaml. Modify the `stages:` section to customize the load pattern.

## Troubleshooting

### Gateway not found

Use the check script to identify the correct endpoint:

```bash
./check_deployment.sh
```

Or manually check:

```bash
kubectl get gateway -n benchflow
kubectl get svc -n benchflow | grep gateway
```

### Pod OOMKilled

The benchmark requires ~10-15Gi memory for 750 unique prompts with large contexts. Memory limits are set to 24Gi in the script. If still OOMing, increase in `deploy_benchmark_with_pvc.sh`:

```yaml
resources:
  limits:
    memory: "32Gi"
```

### Results not found after pod completes

This is why we use PersistentVolumeClaim! Results survive pod deletion. Run:

```bash
./copy_benchmark_results.sh
```

Even hours/days after the benchmark completes.

### Benchmark takes too long

The official guide.yaml stages complete in ~9 minutes. If taking longer:
- Check if model is still warming up caches
- Verify 8 replicas are all running
- Check Prometheus metrics for queueing

## Cleanup

```bash
# Delete the benchmark pod
kubectl delete pod benchmark-capacity-73-qwen3-32b -n benchflow

# Delete the ConfigMap
kubectl delete configmap capacity-73-workload -n benchflow

# Delete PVC (WARNING: deletes all results!)
kubectl delete pvc benchmark-results -n benchflow
```

## References

- [llm-d official guide.yaml](https://github.com/llm-d/llm-d/blob/main/guides/optimized-baseline/benchmark-templates/guide.yaml)
- [llm-d capacity benchmark docs](https://github.com/llm-d/llm-d-kv-cache/tree/main/benchmarking/73-capacity)
- [llm-d-benchmark repository](https://github.com/llm-d/llm-d-benchmark)
- [inference-perf documentation](https://github.com/kubernetes-sigs/inference-perf)
