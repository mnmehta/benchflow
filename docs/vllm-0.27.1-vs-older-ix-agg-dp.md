# vLLM 0.27.1 vs older IX / `nnodes` multi-node DP launch

**Audience:** BenchFlow RHAIIS distributed `raw-vllm`  
**Date:** 2026-08-21 (updated: Kimi profile back on `ix-agg` + `kimi-k3` image)

## Current BenchFlow default for Kimi

The Kimi TP8×DP4×EP32 profile uses:

- Image: `vllm/vllm-openai:kimi-k3`
- `distributed.launch_style: ix-agg` → `--nnodes` / `--node-rank` / `--master-addr`
- Rank0 delay (`head_start_delay_seconds`) so workers start first
- `VLLM_ENGINE_READY_TIMEOUT_S=7200`

Stock **0.27.1** remains available as `launch_style: external-dp` (separate migration).

## Why this matters

Against **stock vLLM 0.27.1**, IX-style `--nnodes` + `--data-parallel-size` fails on workers with internal DPLB. The earlier successful H200 AgentX runs used the **`kimi-k3` image**, which accepts IX agg and reaches `/health` after ~5+ minutes of weight load.

## Side-by-side

| Concern | `ix-agg` (kimi-k3 image) | `external-dp` (stock 0.27.1) |
|---|---|---|
| Membership | `--nnodes` + `--node-rank` + `--master-addr` | `--data-parallel-size` + `--data-parallel-rank` + `--data-parallel-address` + `--data-parallel-rpc-port` |
| Workers | `--headless` | `--headless` |
| Rank0 start | Delayed (`head_start_delay_seconds`) | Immediate |
| Engine ready | Long timeout env (7200s) | Needs explicit timeout env |

## Residual gaps vs `deploy.sh`

BenchFlow does **not** yet run the in-pod recipe patches from `run-vllm-kimi-k3-recipe.sh` (MLA DCP, shm_broadcast, mamba). If bring-up regresses on those paths, port the patches or invoke the recipe script as the container entrypoint.

## References

- Renderer: `src/benchflow/renderers/deployment.py`
- Profile: `profiles/deployment/rhaiis/kimi-k3-tp8-dp4-ep32.yaml`
- Known-good logs: `kimi-k3/bench-results/ix-h200-tp8dp4ep32-vllm-balanced-agentx-c1/`
