# vLLM 0.27.1 vs older IX / `nnodes` multi-node DP launch

**Audience:** BenchFlow RHAIIS distributed `raw-vllm` (Kimi-K3 TP8×DP4×EP32 and similar)  
**Date:** 2026-08-21  
**Scope:** How multi-node data-parallel (DP) process groups are started — not model weights or EP topology.

## Why this matters

BenchFlow’s first distributed raw-vLLM renderer followed the **kimi-k3 / InferenceX aggregated DP** launch style (`--nnodes`, `--node-rank`, `--master-addr`, `--master-port`), which works with the custom recipe images used in `kimi-k3`.

Against **stock vLLM 0.27.1** (RHAIIS image), that same argv fails on worker ranks with:

```text
AssertionError: Attempting to launch core_engines from dp_rank > 0,
but found internal DPLB, which is incompatible.
```

Stock 0.27.1 treats “`--data-parallel-size` without an external LB / DP CLI layout” as **internal DPLB**, which must only start engines from rank 0. Multi-node workers must use the **external DP** CLI instead.

## Side-by-side

| Concern | Older kimi-k3 / IX_AGG path | Stock vLLM 0.27.1 (BenchFlow now) |
|---|---|---|
| Node membership | `--nnodes=N` | Implied by `--data-parallel-size` + per-node `--data-parallel-size-local` |
| This pod’s DP rank | `--node-rank=R` | `--data-parallel-start-rank=R` |
| Rendezvous host | `--master-addr=<leader DNS/IP>` | `--data-parallel-address=<leader IP>` |
| Rendezvous port | `--master-port=…` (torch-style) | `--data-parallel-rpc-port=…` |
| Worker role | `--headless` on `node_rank > 0` | `--headless` on `start_rank > 0` (same idea) |
| Load balancing | Often internal / recipe-managed | **`--data-parallel-external-lb` required** for multi-node engines |
| Local engines per node | Implicit (1 process / node) | Explicit `--data-parallel-size-local=1` |
| Typical failure if mixed | Recipe may accept hybrid argv | Workers assert on “internal DPLB” |

## Concrete argv shapes

### Older IX / kimi-k3 recipe (illustrative)

```bash
vllm serve … \
  --data-parallel-size=4 \
  --enable-expert-parallel \
  --nnodes=4 \
  --node-rank="${RANK}" \
  --master-addr="${LEADER}" \
  --master-port=29500 \
  --data-parallel-address="${POD_IP}"   # often local bind in IX recipes
  # workers: --headless
```

### Stock vLLM 0.27.1 (BenchFlow distributed renderer)

```bash
# Rank 0
vllm serve … \
  --data-parallel-size=4 \
  --data-parallel-size-local=1 \
  --data-parallel-start-rank=0 \
  --data-parallel-address="${POD_IP}" \
  --data-parallel-rpc-port=29500 \
  --data-parallel-external-lb \
  --enable-expert-parallel …

# Rank R > 0
vllm serve … \
  --data-parallel-size=4 \
  --data-parallel-size-local=1 \
  --data-parallel-start-rank="${R}" \
  --data-parallel-address="${LEADER_IP}" \
  --data-parallel-rpc-port=29500 \
  --data-parallel-external-lb \
  --headless \
  --enable-expert-parallel …
```

Profile still owns `--data-parallel-size`, EP, model, and serving flags. BenchFlow owns ordinal → start-rank, size-local, RPC port (`distributed.master_port`), external LB, address resolution, and `--headless`.

## Address / networking notes

| Topic | Guidance for 0.27.1 on CKS |
|---|---|
| `hostNetwork` | Still useful for RDMA / port layout; only one serve/RPC claim per node |
| Rank identity | Use `POD_NAME` (`…-<ordinal>`), never `HOSTNAME` under hostNetwork |
| Rank 0 address | `status.podIP` (equals node IP with hostNetwork) |
| Worker address | Resolve headless `release-vllm-0.…svc` to IP; do **not** bind to the leader DNS as a local listen address |
| ZMQ “Cannot assign requested address” | Happens if a worker tries to **bind** to the leader’s address; workers should **connect** to the leader IP via `--data-parallel-address` |

## What stayed the same

- Topology: one StatefulSet replica per node, TP within node, DP(+EP) across nodes  
- API surface: only ordinal 0 exposes `/v1` (ExternalName → `…-0`)  
- Profile fields: `distributed.enabled`, `master_port`, `host_network`, `host_ipc`  
- `master_port` meaning in BenchFlow: now maps to **`--data-parallel-rpc-port`**, not torch `--master-port`

## Operational checklist after this change

1. Rebuild/push the BenchFlow image that includes the renderer fix.  
2. Delete leftover hostNetwork releases that still hold ports on the H200 nodes.  
3. Redeploy with the new image (`--benchflow-image …`).  
4. Confirm workers log DP rendezvous / engine start, **not** the internal-DPLB assertion.  
5. Confirm only rank 0 becomes Ready on the HTTP health probe; workers Ready via process liveness.

## References

- BenchFlow renderer: `src/benchflow/renderers/deployment.py` (`_render_rhaiis_distributed_raw_vllm_manifests`)  
- Profile: `profiles/deployment/rhaiis/kimi-k3-tp8-dp4-ep32.yaml`  
- Cluster ops notes: `docs/ADVANCED.md` (distributed raw-vLLM section)  
- kimi-k3 IX issues (historical): `kimi-k3/docs/tp8-dp2-h200-deployment-issues.md` (sibling repo)
