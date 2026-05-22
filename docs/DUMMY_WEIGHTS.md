# Using Dummy Weights and Config-Only Downloads

BenchFlow supports two features for testing and development without requiring full model weights:

1. **Config-Only Model Downloads**: Download only configuration files without model weights
2. **Dummy Weights**: Run models with randomly initialized weights instead of trained weights

## Config-Only Model Downloads

When you only need model configuration files (for testing deployment, tokenizer setup, etc.), you can download just the config files without the large weight files.

### Usage

**Command Line:**
```bash
bflow model download \
  --run-plan-json runplan.json \
  --models-storage-path /path/to/storage \
  --config-only
```

**What Gets Downloaded:**

The following files are downloaded when `--config-only` is enabled:
- `config.json` - Model configuration
- `tokenizer_config.json` - Tokenizer configuration
- `tokenizer.model` - Tokenizer model file
- `special_tokens_map.json` - Special tokens mapping
- `vocab.json` - Vocabulary file
- `merges.txt` - BPE merges file
- `*.txt` - Other text files (README, etc.)
- `*.tiktoken` - Tiktoken files

**What Doesn't Get Downloaded:**
- `*.safetensors` - Model weights
- `*.bin` - PyTorch weight files
- `*.pt`, `*.pth` - PyTorch checkpoint files
- `*.gguf` - GGUF format files

### Benefits

- **Faster downloads**: Config files are KB/MB instead of GB/TB
- **Reduced storage**: No need for large model weights during testing
- **Quick iteration**: Test deployment configurations without waiting for weight downloads

## Dummy Weights

When you need to test model serving infrastructure but don't need actual inference quality, you can use dummy (randomly initialized) weights.

### Usage in Deployment Profiles

Add `use_dummy_weights: true` to the `runtime` section of your deployment profile:

```yaml
apiVersion: benchflow.io/v1alpha1
kind: DeploymentProfile
metadata:
  name: my-test-profile
spec:
  platform: llm-d
  mode: inference-scheduling
  runtime:
    replicas: 1
    tensor_parallelism: 1
    use_dummy_weights: true
    vllm_args:
      - --max-model-len=4096
      - --trust-remote-code
```

### What Happens

When `use_dummy_weights: true` is set, BenchFlow automatically adds `--load-format=dummy` to the vLLM arguments. This causes vLLM to:

1. Read the model architecture from config files
2. Initialize all weights with random values
3. Skip loading actual trained weights

### Benefits

- **Fast startup**: No need to load multi-GB weight files into memory
- **Reduced memory**: Weights are generated on-the-fly, no disk I/O
- **Test deployments**: Validate infrastructure without actual model weights
- **Benchmark infrastructure**: Test throughput/latency characteristics of serving infrastructure

### Limitations

- **No meaningful outputs**: Generated text will be gibberish (random weights)
- **Cannot validate quality**: Suitable only for infrastructure testing, not model evaluation
- **Architecture must match**: Config files must still match the model architecture

## Combined Usage

You can combine both features for ultra-fast testing:

```bash
# 1. Download only config files
bflow model download \
  --run-plan-json runplan.json \
  --models-storage-path /models \
  --config-only

# 2. Deploy with dummy weights (using a profile with use_dummy_weights: true)
bflow experiment run \
  --model meta-llama/Llama-3-8B \
  --deployment-profile dummy-weights-test \
  --benchmark-profile smoke-test
```

This combination:
- Downloads only KB/MB of config files (seconds)
- Starts vLLM with dummy weights (fast initialization)
- Allows full infrastructure testing without actual model weights

## Use Cases

### Infrastructure Testing
Test your deployment pipeline, networking, load balancers, and monitoring without downloading large models:
```yaml
use_dummy_weights: true
```

### Configuration Validation
Validate tokenizer settings, prompt templates, and API configurations:
```bash
bflow model download --config-only
```

### CI/CD Pipelines
Run deployment tests in CI without requiring model weight storage:
```yaml
runtime:
  use_dummy_weights: true
```

### Capacity Planning
Test maximum throughput and concurrent request handling:
```yaml
runtime:
  use_dummy_weights: true
  replicas: 8
```

## Examples

### Example 1: Quick Smoke Test

```bash
# Download config only
bflow model download \
  --run-plan-json plan.json \
  --models-storage-path /models \
  --config-only

# Run with dummy weights
bflow experiment run \
  --model Qwen/Qwen-7B \
  --deployment-profile llm-d-dummy-weights \
  --benchmark-profile concurrent-1k-1k
```

### Example 2: Profile with Dummy Weights

profiles/deployment/llm-d/dummy-weights-dev.yaml:
```yaml
apiVersion: benchflow.io/v1alpha1
kind: DeploymentProfile
metadata:
  name: llm-d-dummy-weights-dev
spec:
  platform: llm-d
  mode: inference-scheduling
  repo_url: https://github.com/llm-d/llm-d.git
  repo_ref: v0.6.0
  gateway: istio
  runtime:
    replicas: 4
    tensor_parallelism: 2
    use_dummy_weights: true
    vllm_args:
      - --max-model-len=8192
      - --gpu-memory-utilization=0.95
      - --trust-remote-code
```

### Example 3: Testing Different Scales

Test how your infrastructure handles different scales without model weights:

```bash
# Test single replica
bflow experiment run \
  --model meta-llama/Llama-3-70B \
  --deployment-profile dummy-weights-1replica \
  --benchmark-profile load-test

# Test 8 replicas
bflow experiment run \
  --model meta-llama/Llama-3-70B \
  --deployment-profile dummy-weights-8replicas \
  --benchmark-profile load-test
```

## Notes

- Dummy weights generate random output - do not use for quality evaluation
- Config-only downloads still require model architecture to be compatible with vLLM
- Both features are designed for development, testing, and infrastructure validation
- For production inference with real outputs, download full model weights and disable dummy weights
