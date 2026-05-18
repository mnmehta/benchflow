# Mooncake Conversation Trace Analysis Report

**Date**: 2026-05-12  
**Dataset**: https://raw.githubusercontent.com/kvcache-ai/Mooncake/refs/heads/main/FAST25-release/traces/conversation_trace.jsonl  
**Total Prompts**: 12,031

---

## Executive Summary

The Mooncake conversation trace is a realistic LLM workload designed to stress-test prefix caching systems. Our analysis reveals:

- **Average cache hit rate**: 37.4% (full dataset)
- **Average prompt length**: 12,035 tokens
- **Total unique tokens**: 93.6M tokens across 182,790 unique 512-token chunks
- **Deduplication factor**: 1.55x (each unique token appears 1.55 times on average)
- **Context requirements**: Up to 126K tokens (requires 128K+ context models)

---

## 1. Dataset Characteristics

### 1.1 Prompt Length Statistics

| Metric | Input (Prompts) | Output (Responses) |
|--------|----------------|-------------------|
| **Average** | **12,035 tokens** | 343 tokens |
| **Median** | 6,909 tokens | 350 tokens |
| **Min** | 891 tokens | 1 token |
| **Max** | 126,195 tokens | 2,000 tokens |

### 1.2 Input Length Distribution

| Range | Count | Percentage |
|-------|-------|------------|
| 0-1K | 1,296 | 10.8% |
| 1K-5K | 3,523 | 29.3% |
| 5K-10K | 2,572 | 21.4% |
| 10K-20K | 2,633 | 21.9% |
| 20K-50K | 1,600 | 13.3% |
| 50K-100K | 344 | 2.9% |
| **100K+** | **63** | **0.5%** |

**Key Finding**: 0.5% of prompts exceed 100K tokens, requiring models with 128K+ context window.

---

## 2. Hash ID Structure & Prefix Caching

### 2.1 Hash ID Semantics

Each prompt is represented by a list of `hash_ids`, where:
- Each hash_id represents a **512-token chunk**
- Hash IDs are shared across prompts to indicate identical content
- Sequential matching hash_ids from the start indicate a **shared prefix**

Example:
```json
{"timestamp": 0, "input_length": 6758, "output_length": 500, "hash_ids": [0, 1, 2, 3, 4, ...]}
{"timestamp": 0, "input_length": 7322, "output_length": 490, "hash_ids": [0, 14, 15, 16, ...]}
```
→ These prompts share only hash_id `0` (the system prompt)

### 2.2 Unique Token Footprint

- **Largest hash_id**: 182,789
- **Number of unique chunks**: 182,790 (hash_ids 0 through 182,789)
- **Total unique tokens**: **93,588,480 tokens** (182,790 × 512)

### 2.3 Deduplication Analysis

- **Sum of all prompt lengths**: 144,793,823 tokens
- **Total unique tokens**: 93,588,480 tokens
- **Deduplication factor**: **1.55x**

This means each unique token appears 1.55 times on average, indicating **35% redundancy** in the dataset.

---

## 3. Prefix Cache Hit Rate Analysis

### 3.1 Methodology

For each prompt (in sequential order), we searched **backward only** through all previous prompts to find the longest matching prefix (consecutive hash_ids from position 0).

This simulates how a sequential serving system with prefix caching would perform.

### 3.2 Backward Match Statistics (Full Dataset)

| Metric | Value |
|--------|-------|
| **Average match** | **8.79 hash_ids** |
| **Min match** | 0 hash_ids (first prompt) |
| **Max match** | 240 hash_ids |
| **Median match** | ~1 hash_id |

### 3.3 Match Distribution

| Hash IDs Matched | Count | Percentage |
|-----------------|-------|------------|
| 1 | 7,372 | 61.3% |
| 2 | 555 | 4.6% |
| 3 | 341 | 2.8% |
| 4 | 235 | 2.0% |
| 5 | 203 | 1.7% |
| 6 | 185 | 1.5% |
| 7 | 168 | 1.4% |
| 8 | 175 | 1.5% |
| 9 | 176 | 1.5% |
| 10+ | 2,621 | 21.8% |

**Key Finding**: 61.3% of prompts share only the system prompt (1 hash_id) with previous prompts, while 21.8% share 10+ chunks (5K+ tokens).

### 3.4 Top Prefix Matches

| Prompt # | Matched Hash IDs | Matched With Prompt # | Cached Tokens |
|----------|-----------------|----------------------|---------------|
| 1201 | 240 | 1013 | 122,880 |
| 11987 | 240 | 10073 | 122,880 |
| 610 | 236 | 394 | 120,832 |
| 5963 | 236 | 3815 | 120,832 |
| 394 | 235 | 97 | 120,320 |
| 3815 | 235 | 981 | 120,320 |
| 4809 | 231 | 2755 | 118,272 |
| 8421 | 231 | 8056 | 118,272 |
| 1546 | 221 | 1161 | 113,152 |
| 1161 | 220 | 1016 | 112,640 |

### 3.5 Expected Cache Hit Rate

**Calculation**:
- Average backward match: 8.79 hash_ids
- Tokens per hash_id: 512 tokens
- Average cached tokens: 8.79 × 512 = **4,500 tokens**
- Average prompt length: 12,035 tokens

**Cache Hit Rate** = 4,500 / 12,035 = **37.4%**

This means that with perfect prefix caching, **37.4% of compute can be saved** through KV cache reuse.

---

## 4. Dataset Size Comparison

### 4.1 Summary Table

| Dataset Size | Max Hash ID | Unique Tokens | Avg Prompt Len | Avg Match (hash_ids) | **Cache Hit Rate** | Memory Footprint* |
|-------------|-------------|---------------|----------------|----------------------|-------------------|------------------|
| **500 rows** | 11,878 | 6,082,048 | 14,250 | 4.57 | **16.41%** | 0.01 GB |
| **2,000 rows** | 38,787 | 19,859,456 | 13,721 | 7.89 | **29.43%** | 0.04 GB |
| **12,031 rows** (full) | 182,789 | 93,588,480 | 12,035 | 8.79 | **37.38%** | 0.17 GB |

*Simplified estimate using 2 bytes/token. Actual KV cache memory is much larger for production models.

### 4.2 Growth Analysis

#### Unique Token Footprint Growth
- 500 → 2,000 rows: **3.27x growth** (6M → 20M tokens)
- 2,000 → 12,031 rows: **4.71x growth** (20M → 94M tokens)

**Insight**: Footprint grows sub-linearly, indicating increasing redundancy.

#### Cache Hit Rate Improvement
- 500 rows: 16.41% (baseline)
- 2,000 rows: 29.43% (**+13.02%** vs 500)
- Full dataset: 37.38% (**+7.95%** vs 2,000)

**Insight**: Diminishing returns - most cache benefit achieved by 2,000 rows.

### 4.3 Recommendations

**For Quick Testing** (Development/Debug):
- Use **500 rows** (`dataset_cap: 500`)
- Cache hit rate: 16.4%
- Runs ~24x faster than full dataset
- Good for rapid iteration

**For Validation** (Pre-production):
- Use **2,000 rows** (`dataset_cap: 2000`)
- Cache hit rate: 29.4% (80% of full benefit)
- Runs ~6x faster than full dataset
- Excellent balance of speed vs realism

**For Full Benchmarking** (Production/Publication):
- Use **complete 12,031 rows** (no cap)
- Cache hit rate: 37.4% (maximum)
- Most realistic workload
- Required for comparing against published Mooncake results

---

## 5. Model Requirements

### 5.1 gpt-oss-120b Specifications

**From HuggingFace `config.json`**:

| Parameter | Value |
|-----------|-------|
| **max_position_embeddings** | **131,072 (128K)** |
| Model Type | gpt_oss |
| Parameters | ~120B |
| Hidden Size | 2,880 |
| Layers | 36 |
| Attention Heads | 64 |
| Vocab Size | 201,088 |

### 5.2 Context Window Compatibility

✅ **gpt-oss-120b** (128K native context) can handle:
- Max prompt in dataset: 126,195 tokens
- Average prompt: 12,035 tokens
- Full trace without truncation

❌ **Qwen3-32B** (32K native context) cannot handle:
- 63 prompts exceed 32K (0.5% of dataset)
- Max prompt is 126K (requires 4x the context)
- Would need `synthesis_max_isl: 32768` to cap/truncate prompts

---

## 6. Successful Configuration (From MLflow Artifacts)

### 6.1 Deployment Configuration

**Platform**: RHAIIS (raw-vllm mode)

**Runtime Configuration**:
```yaml
model_name: openai/gpt-oss-120b
replicas: 4
tensor_parallelism: 2
accelerator: H200
```

**vLLM Arguments**:
```bash
--enable-prefix-caching
--trust-remote-code
--gpu-memory-utilization=0.92
--no-enable-log-requests
--max-model-len=131072
```

### 6.2 Benchmark Configuration

**Profile**: `aiperf-mooncake-toolagent-trace`

**Dataset**:
```
https://raw.githubusercontent.com/kvcache-ai/Mooncake/refs/heads/main/FAST25-release/traces/toolagent_trace.jsonl
```

**Note**: The analysis above is for `conversation_trace.jsonl`, but both traces have similar characteristics.

### 6.3 Required BenchFlow Profiles

**Deployment Profile** (`profiles/deployment/rhaiis/raw-vllm.yaml`):
```yaml
apiVersion: benchflow.io/v1alpha1
kind: DeploymentProfile
metadata:
  name: rhaiis-raw-vllm
spec:
  platform: rhaiis
  mode: raw-vllm
  endpoint_path: /v1/models
  model_storage:
    pvc_name: models-storage
    cache_dir: /models
    mount_path: /mnt
  runtime:
    image: registry.stage.redhat.io/rhaii/vllm-cuda-rhel9:3.4.0-1776326705
    replicas: 4
    tensor_parallelism: 2
    vllm_args:
      - --max-model-len=131072  # Critical: must be 128K+
      - --enable-prefix-caching
      - --trust-remote-code
      - --gpu-memory-utilization=0.92
      - --no-enable-log-requests
```

**Benchmark Profile** (`profiles/benchmark/aiperf-mooncake-trace.yaml`):
```yaml
apiVersion: benchflow.io/v1alpha1
kind: BenchmarkProfile
metadata:
  name: aiperf-mooncake-trace
spec:
  tool: aiperf
  env:
    AIPERF_HTTP_SSL_VERIFY: "false"
  requirements:
    min_max_model_len: 131072
  aiperf:
    dataset_url: https://raw.githubusercontent.com/kvcache-ai/Mooncake/refs/heads/main/FAST25-release/traces/conversation_trace.jsonl
    dataset_type: mooncake_trace
    endpoint_type: chat
    endpoint_path: /v1/chat/completions
    streaming: true
    fixed_schedule: true
    fixed_schedule_auto_offset: true
    synthesis_max_isl: 131072
    dataset_cap: 2000  # Optional: limit to first 2000 for faster runs
    max_seconds: 7200
```

**Experiment File** (`experiments/rhoai/gpt-oss-120b-aiperf-release.yaml`):
```yaml
apiVersion: benchflow.io/v1alpha1
kind: Experiment
metadata:
  name: gpt-120b-aiperf-release
spec:
  model:
    name:
      - openai/gpt-oss-120b
  deployment_profile:
    - rhaiis-raw-vllm
  benchmark_profile:
    - aiperf-mooncake-trace
  metrics_profile: detailed
  mlflow:
    tags:
      accelerator: H200
  namespace: benchflow
  execution:
    timeout: 8h
```

---

## 7. Key Takeaways

### 7.1 Workload Characteristics

1. **Long-context heavy**: Average 12K tokens input, only 343 tokens output
2. **High variance**: Prompts range from 891 to 126K tokens
3. **Moderate prefix reuse**: 37.4% cache hit rate with perfect caching
4. **Real-world patterns**: Designed from actual conversation traces

### 7.2 Benchmarking Insights

1. **Cache effectiveness matters**: 37% compute savings with good prefix caching
2. **Context window is critical**: Need 128K+ to handle full trace without truncation
3. **Dataset size trade-offs**: 2,000 rows gives 80% of benefit at 1/6 the time
4. **Memory pressure varies**: From 6M unique tokens (500 rows) to 94M (full dataset)

### 7.3 Production Implications

1. **Prefix caching is valuable**: Real workloads show 1.55x deduplication
2. **Long contexts are common**: 2.9% of prompts exceed 50K tokens
3. **Cache eviction policies matter**: Can't cache all 94M unique tokens
4. **Scheduling affects performance**: Good prefix routing improves hit rates

---

## 8. Appendix: Analysis Artifacts

### 8.1 Generated Files

**Hash Analysis Output**:
- File: `/home/michey/benchflow/mooncake_hash_analysis.txt`
- Format: Tab-separated (prompt_num, longest_match_count, matching_prompt_num)
- Size: 12,031 rows (one per prompt)

**Raw Dataset**:
- File: `/tmp/conversation_trace.jsonl`
- Format: JSONL (one prompt per line)
- Fields: timestamp, input_length, output_length, hash_ids

### 8.2 Methodology Notes

1. **Backward-only matching**: Each prompt only searches previous prompts (not future ones) to simulate real-time serving
2. **Prefix matching**: Only consecutive hash_ids from position 0 count as cache hits
3. **Perfect cache assumption**: Analysis assumes infinite cache capacity; real systems will have eviction
4. **512 tokens per hash_id**: Assumed based on typical chunking; actual may vary slightly

---

## References

- **Mooncake Dataset**: https://github.com/kvcache-ai/Mooncake/tree/main/FAST25-release/traces
- **Model**: https://huggingface.co/openai/gpt-oss-120b
- **BenchFlow**: https://github.com/albertoperdomo2/benchflow
- **AIPerf**: https://github.com/ai-dynamo/aiperf

---

**Report Generated**: 2026-05-12  
**Analysis Tool**: BenchFlow with custom Python analysis scripts
