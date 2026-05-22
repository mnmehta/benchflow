# BenchFlow Examples

This directory contains example configurations demonstrating BenchFlow features.

## Ultra-Fast Infrastructure Testing with Dummy Weights

**File:** `dummy-weights-config-only.yaml`

This example demonstrates how to test your LLM serving infrastructure without downloading or loading large model weights, enabling:

- **Ultra-fast downloads**: Download only config files (KB/MB) instead of model weights (GB/TB)
- **Fast startup**: vLLM initializes with random weights (no disk I/O for weight loading)
- **Full infrastructure testing**: Test deployment, routing, load balancing, and throughput
- **CI/CD friendly**: Run in automated pipelines without model weight storage requirements

### Features Used

1. **Config-Only Downloads** (`download_config_only: true`)
   - Downloads only: config.json, tokenizer files, vocab files
   - Skips: *.safetensors, *.bin, *.pt (model weights)
   - Time savings: ~2MB download vs 140GB for Llama-3-70B

2. **Dummy Weights** (`use_dummy_weights: true`)
   - vLLM initializes random weights instead of loading from disk
   - Fast startup: no weight loading overhead
   - Generated outputs will be gibberish (random weights)

### Usage

**Method 1: Run the example YAML**
```bash
bflow experiment run examples/dummy-weights-config-only.yaml
```

**Method 2: Use CLI flags**
```bash
bflow experiment run \
  --model meta-llama/Llama-3-70B \
  --deployment-profile llm-d-dummy-weights-dev \
  --benchmark-profile concurrent-8k-1k \
  --download-config-only \
  --replicas 8 \
  --tp 2
```

### What Gets Tested

With this configuration, you can validate:

- ✅ Deployment pipeline and automation
- ✅ Network routing and load balancers
- ✅ Gateway/scheduler configuration
- ✅ Multi-replica scaling
- ✅ Tensor parallelism setup
- ✅ Throughput and concurrency handling
- ✅ Monitoring and metrics collection
- ✅ Infrastructure capacity planning
- ❌ Model output quality (random weights generate gibberish)

### Time Comparison

| Task | With Full Weights | With Config-Only + Dummy Weights |
|------|------------------|----------------------------------|
| **Download** | 1-4 hours (140GB for Llama-3-70B) | 5-30 seconds (2MB) |
| **Startup** | 2-5 minutes (loading weights to GPU) | 10-30 seconds (random init) |
| **Total** | ~2 hours | **~1 minute** |

### Use Cases

1. **CI/CD Pipeline Testing**
   ```yaml
   # .github/workflows/test-deployment.yml
   - name: Test Deployment
     run: |
       bflow experiment run \
         examples/dummy-weights-config-only.yaml \
         --no-cleanup  # Keep for inspection
   ```

2. **Infrastructure Capacity Planning**
   - Test different replica counts (1, 4, 8, 16)
   - Test different tensor parallelism (1, 2, 4, 8)
   - Measure throughput limits without model weights

3. **Development and Iteration**
   - Quickly test deployment changes
   - Validate configuration updates
   - Debug routing issues

4. **Multi-Region Testing**
   - Test deployments across multiple clusters
   - Validate cross-region failover
   - No need to sync large model weights

### File Structure

The example uses two files:

1. **Experiment**: `examples/dummy-weights-config-only.yaml`
2. **Deployment Profile**: `profiles/deployment/llm-d-dummy-weights-dev.yaml`

The deployment profile is stored separately in the `profiles/deployment/` directory and referenced by name in the experiment.

### Creating Your Own Profile

To create a deployment profile with dummy weights, create a file in `profiles/deployment/`:

**profiles/deployment/my-dummy-weights-profile.yaml:**
```yaml
apiVersion: benchflow.io/v1alpha1
kind: DeploymentProfile
metadata:
  name: my-dummy-weights-profile
spec:
  platform: llm-d
  mode: inference-scheduling
  runtime:
    use_dummy_weights: true  # Enable dummy weights
    vllm_args:
      - --max-model-len=8192
      - --trust-remote-code
```

Then use it in your experiment:

**experiments/my-test.yaml:**
```yaml
apiVersion: benchflow.io/v1alpha1
kind: Experiment
metadata:
  name: my-test
spec:
  model: any-model-name  # Model architecture must exist
  deployment_profile:
    - my-dummy-weights-profile
  benchmark_profile:
    - concurrent-1k-1k
  stages:
    download_config_only: true  # Config files only
```

### Limitations

⚠️ **Important**: Dummy weights generate random output. This approach is only suitable for:
- Infrastructure testing
- Performance benchmarking (throughput, latency)
- Deployment validation

**Not suitable for**:
- Model quality evaluation
- Accuracy testing
- Production inference

### See Also

- [Full Documentation](../docs/DUMMY_WEIGHTS.md)
- [Config-Only Downloads](../docs/DUMMY_WEIGHTS.md#config-only-model-downloads)
- [Dummy Weights](../docs/DUMMY_WEIGHTS.md#dummy-weights)
