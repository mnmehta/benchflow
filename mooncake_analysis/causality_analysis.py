#!/usr/bin/env python3

print("=" * 80)
print("CAUSALITY ANALYSIS: WHAT CAUSES WHAT?")
print("=" * 80)
print()

print("MY NARRATIVE:")
print("  Large batches → Slow iterations")
print("  (Batch size is the CAUSE)")
print()

print("ALTERNATIVE NARRATIVE:")
print("  Slow iterations → Large batches")
print("  (Batch size is the EFFECT)")
print()

print("=" * 80)
print("THE QUEUEING THEORY EXPLANATION")
print("=" * 80)
print()

print("If iterations are SLOW (for any reason):")
print("  1. New requests arrive at constant rate while iteration is processing")
print("  2. Those requests pile up in the queue")
print("  3. Next iteration picks up more queued requests")
print("  4. Batch size grows!")
print()

print("If iterations are FAST:")
print("  1. Current iteration completes quickly")
print("  2. Queue drains before many new requests arrive")
print("  3. Next iteration has fewer queued requests")
print("  4. Batch size stays small!")
print()

print("This creates a FEEDBACK LOOP:")
print("  Slow iterations → Large batches → Even slower iterations → Even larger batches")
print("  (Vicious cycle)")
print()

print("=" * 80)
print("BUT BATCH SIZE ALSO CAUSES SLOW ITERATIONS!")
print("=" * 80)
print()

print("vLLM processes N requests per iteration:")
print("  • More requests = more attention computation")
print("  • More requests = more memory bandwidth")
print("  • More requests = longer iteration time")
print()

print("This is fundamental to batched inference - NOT debatable.")
print()

print("=" * 80)
print("SO WHICH IS IT?")
print("=" * 80)
print()

print("ANSWER: It's a BIDIRECTIONAL FEEDBACK LOOP!")
print()

print("  ┌─────────────────────────────────────────┐")
print("  │                                         │")
print("  │   Large Batch Size ─────────────────┐  │")
print("  │         │                           │  │")
print("  │         ↓                           │  │")
print("  │   More GPU Work                     │  │")
print("  │         │                           │  │")
print("  │         ↓                           │  │")
print("  │   Slow Iteration ───────────────────┘  │")
print("  │         │                              │")
print("  │         ↓                              │")
print("  │   Requests Queue Up                    │")
print("  │         │                              │")
print("  │         └──────────────────────────────┘")
print()

print("=" * 80)
print("WHAT'S THE ROOT CAUSE THAT STARTS THE CYCLE?")
print("=" * 80)
print()

# Direct pod data showing context tokens AND batch density
direct_data = [
    {'pod': 'de4kjwq', 'ctx_tokens': 4598723, 'batch': 2.31, 'time': 10.34},
    {'pod': 'delfkwp', 'ctx_tokens': 3588702, 'batch': 1.90, 'time': 9.20},
    {'pod': 'de2pj8r', 'ctx_tokens': 3312992, 'batch': 2.04, 'time': 9.00},
    {'pod': 'del27pq', 'ctx_tokens': 3132449, 'batch': 1.87, 'time': 9.02},
    {'pod': 'decg4l4', 'ctx_tokens': 2965580, 'batch': 1.62, 'time': 8.28},
    {'pod': 'dehc7h2', 'ctx_tokens': 2849901, 'batch': 1.61, 'time': 8.37},
    {'pod': 'de8fmr9', 'ctx_tokens': 2531717, 'batch': 1.64, 'time': 8.46},
    {'pod': 'dewh68t', 'ctx_tokens': 2526526, 'batch': 1.51, 'time': 7.74},
]

print("Looking at DIRECT pods (sorted by context tokens):")
print()
print(f"| {'Pod':12} | {'Ctx Tokens':>12} | {'Batch':>7} | {'Time':>8} |")
print(f"|{'-'*14}|{'-'*14}|{'-'*9}|{'-'*10}|")

for pod in sorted(direct_data, key=lambda x: x['ctx_tokens'], reverse=True):
    print(f"| {pod['pod']:12} | {pod['ctx_tokens']:>12,} | {pod['batch']:>7.2f} | {pod['time']:>7.2f}ms |")

print()
print("Hypothesis: Pods that got MORE CONTEXT TOKENS early on:")
print("  1. Had expensive prefill operations")
print("  2. This slowed down their iterations")
print("  3. Slow iterations → requests queued up")
print("  4. Queue → larger batches")
print("  5. Larger batches → even slower iterations")
print("  6. VICIOUS CYCLE")
print()

print("Evidence:")
print("  • de4kjwq: Most context tokens (4.6M) → largest batch (2.31) → slowest (10.34ms)")
print("  • dewh68t: Least context tokens (2.5M) → smallest batch (1.51) → fastest (7.74ms)")
print()

print("=" * 80)
print("TESTING: CORRELATION OF CONTEXT TOKENS WITH TIME")
print("=" * 80)
print()

from scipy.stats import pearsonr
import numpy as np

ctx_tokens = [p['ctx_tokens'] for p in direct_data]
times = [p['time'] for p in direct_data]
batches = [p['batch'] for p in direct_data]

corr_ctx_time, p_ctx_time = pearsonr(ctx_tokens, times)
corr_ctx_batch, p_ctx_batch = pearsonr(ctx_tokens, batches)
corr_batch_time, p_batch_time = pearsonr(batches, times)

print("Correlations:")
print(f"  Context Tokens ↔ Time:        r = {corr_ctx_time:.4f} (p = {p_ctx_time:.4f})")
print(f"  Context Tokens ↔ Batch Size:  r = {corr_ctx_batch:.4f} (p = {p_ctx_batch:.4f})")
print(f"  Batch Size ↔ Time:            r = {corr_batch_time:.4f} (p = {p_batch_time:.4f})")
print()

if corr_ctx_time > 0.8:
    print("✅ Context tokens strongly correlate with time!")
    print("   This suggests context processing (prefill) is a root cause")

if corr_ctx_batch > 0.8:
    print("✅ Context tokens strongly correlate with batch size!")
    print("   This suggests expensive prefills → slow iterations → batch buildup")

print()
print("=" * 80)
print("THE COMPLETE CAUSAL CHAIN")
print("=" * 80)
print()

print("ROOT CAUSE: Round-robin routing is BLIND to request cost")
print()
print("Step 1: Some pods randomly get more/larger prefill requests")
print("        (de4kjwq got 4.6M context tokens vs dewh68t's 2.5M)")
print()
print("Step 2: Prefill operations are EXPENSIVE (100-2000ms)")
print("        Pods with more prefills slow down")
print()
print("Step 3: Slow iterations → requests queue up")
print("        (New requests arrive while iteration processes)")
print()
print("Step 4: Queue buildup → larger batches")
print("        (Next iteration picks up queued requests)")
print()
print("Step 5: Larger batches → more GPU work → even slower iterations")
print("        (Now decode AND prefill are competing)")
print()
print("Step 6: POSITIVE FEEDBACK LOOP")
print("        Slow → Queue → Large batch → Slower → Bigger queue → Larger batch...")
print()

print("=" * 80)
print("WHY GATEWAY AVOIDS THIS")
print("=" * 80)
print()

print("Gateway's intelligent routing:")
print("  1. Considers current pod load (queue depth)")
print("  2. Avoids sending requests to already-busy pods")
print("  3. Prevents initial imbalance from occurring")
print("  4. No pod gets stuck in vicious cycle")
print("  5. All pods maintain steady state (~1.5 batch, ~7.9ms)")
print()

print("Result:")
print("  • Context tokens vary by ±28% (vs ±65% for direct)")
print("  • Batch density stays uniform (1.32-1.60 vs 1.51-2.31)")
print("  • No feedback loop → stable performance")
print()

print("=" * 80)
print("FINAL ANSWER")
print("=" * 80)
print()

print("You're RIGHT - I had the causality partially wrong!")
print()
print("It's not just: 'Large batches cause slow iterations'")
print()
print("It's: 'Initial load imbalance → slow iterations → queue buildup →")
print("      large batches → even slower iterations → FEEDBACK LOOP'")
print()
print("The r=0.96 correlation between batch size and time doesn't tell us")
print("which causes which - they REINFORCE each other in a vicious cycle.")
print()
print("The gateway wins by preventing the INITIAL imbalance that kicks off")
print("the feedback loop, not by reducing batch size per se.")
