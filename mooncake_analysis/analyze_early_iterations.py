#!/usr/bin/env python3
import requests
import urllib3
import re
from collections import defaultdict

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

MLFLOW_URL = "https://mlflow.apps.aperdomo-lab.ibm.rhperfscale.org"
AUTH = ("mlflow", "Bo68kJrp0LRz")

runs = {
    "direct": "213999419ce74c109c5fcfd71ec684b5",
    "gateway": "987f07f1d18349498d4511c94bf98aff"
}

def download_artifact_stream(run_id, artifact_path, max_bytes=2000000):
    """Download first N bytes of artifact"""
    url = f"{MLFLOW_URL}/get-artifact"
    params = {"run_uuid": run_id, "path": artifact_path}
    response = requests.get(url, params=params, auth=AUTH, verify=False, stream=True)

    content = b''
    for chunk in response.iter_content(chunk_size=8192):
        content += chunk
        if len(content) >= max_bytes:
            break

    return content.decode('utf-8', errors='ignore')

def list_artifacts(run_id, path=""):
    url = f"{MLFLOW_URL}/api/2.0/mlflow/artifacts/list"
    params = {"run_id": run_id, "path": path}
    response = requests.get(url, params=params, auth=AUTH, verify=False)
    response.raise_for_status()
    return response.json()

def parse_iteration_line(line):
    match = re.search(
        r'Iteration\((\d+)\): (\d+) context requests, (\d+) context tokens, '
        r'(\d+) generation requests, (\d+) generation tokens, iteration elapsed time: ([\d.]+) ms',
        line
    )
    if match:
        return {
            'iteration': int(match.group(1)),
            'ctx_reqs': int(match.group(2)),
            'ctx_tokens': int(match.group(3)),
            'gen_reqs': int(match.group(4)),
            'gen_tokens': int(match.group(5)),
            'time_ms': float(match.group(6))
        }
    return None

def extract_timestamp(line):
    """Extract timestamp from vLLM log line"""
    match = re.search(r'INFO (\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})', line)
    if match:
        return f"{match.group(1)} {match.group(2)}"
    return None

def analyze_early_iterations(run_type, run_id, num_iterations=200):
    """Analyze first N iterations for each pod"""
    print(f"\n{'='*80}")
    print(f"ANALYZING EARLY ITERATIONS - {run_type.upper()} RUN")
    print(f"{'='*80}\n")

    artifact_list = list_artifacts(run_id, "logs/model")
    vllm_logs = [f for f in artifact_list['files'] if '_vllm.log' in f['path']][:8]

    pod_data = {}

    for i, log_file in enumerate(vllm_logs):
        log_path = log_file['path']
        pod_name = log_path.split('/')[-1].replace('_vllm.log', '').split('-')[-1]

        print(f"[{i+1}/8] Downloading early iterations for {pod_name}...", end=" ", flush=True)

        # Download first 2MB (should contain first few hundred iterations)
        content = download_artifact_stream(run_id, log_path, max_bytes=2000000)

        iterations = []
        for line in content.split('\n'):
            if 'Iteration(' in line:
                parsed = parse_iteration_line(line)
                if parsed and parsed['iteration'] < num_iterations:
                    iterations.append(parsed)

        if iterations:
            # Calculate rolling batch density
            cumulative_gen = 0
            cumulative_iters = 0
            rolling_data = []

            for iter_data in iterations:
                cumulative_gen += iter_data['gen_reqs']
                cumulative_iters += 1
                batch_density = cumulative_gen / cumulative_iters if cumulative_iters > 0 else 0

                rolling_data.append({
                    'iteration': iter_data['iteration'],
                    'ctx_reqs': iter_data['ctx_reqs'],
                    'ctx_tokens': iter_data['ctx_tokens'],
                    'gen_reqs': iter_data['gen_reqs'],
                    'time_ms': iter_data['time_ms'],
                    'cumulative_batch_density': batch_density
                })

            pod_data[pod_name] = rolling_data
            print(f"✓ ({len(iterations)} iterations)")
        else:
            print(f"✗ (no iterations found)")

    return pod_data

# Analyze both runs
direct_data = analyze_early_iterations("direct", runs["direct"], num_iterations=200)
gateway_data = analyze_early_iterations("gateway", runs["gateway"], num_iterations=200)

# Compare divergence
print(f"\n{'='*80}")
print("BATCH DENSITY EVOLUTION - FIRST 200 ITERATIONS")
print(f"{'='*80}\n")

print("DIRECT RUN - Batch density at iteration checkpoints:")
print(f"{'Pod':12} | {'Iter 50':>10} | {'Iter 100':>10} | {'Iter 150':>10} | {'Iter 200':>10} | {'Range':>10} |")
print(f"{'-'*14}|{'-'*12}|{'-'*12}|{'-'*12}|{'-'*12}|{'-'*12}|")

direct_ranges = {}
for pod, data in sorted(direct_data.items()):
    iter_50 = next((d['cumulative_batch_density'] for d in data if d['iteration'] >= 50), None)
    iter_100 = next((d['cumulative_batch_density'] for d in data if d['iteration'] >= 100), None)
    iter_150 = next((d['cumulative_batch_density'] for d in data if d['iteration'] >= 150), None)
    iter_200 = next((d['cumulative_batch_density'] for d in data if d['iteration'] >= 190), None)

    if all([iter_50, iter_100, iter_150, iter_200]):
        range_val = max(iter_50, iter_100, iter_150, iter_200) - min(iter_50, iter_100, iter_150, iter_200)
        direct_ranges[pod] = {
            50: iter_50, 100: iter_100, 150: iter_150, 200: iter_200, 'range': range_val
        }
        print(f"{pod:12} | {iter_50:>10.2f} | {iter_100:>10.2f} | {iter_150:>10.2f} | {iter_200:>10.2f} | {range_val:>10.2f} |")

print(f"\nGATEWAY RUN - Batch density at iteration checkpoints:")
print(f"{'Pod':12} | {'Iter 50':>10} | {'Iter 100':>10} | {'Iter 150':>10} | {'Iter 200':>10} | {'Range':>10} |")
print(f"{'-'*14}|{'-'*12}|{'-'*12}|{'-'*12}|{'-'*12}|{'-'*12}|")

gateway_ranges = {}
for pod, data in sorted(gateway_data.items()):
    iter_50 = next((d['cumulative_batch_density'] for d in data if d['iteration'] >= 50), None)
    iter_100 = next((d['cumulative_batch_density'] for d in data if d['iteration'] >= 100), None)
    iter_150 = next((d['cumulative_batch_density'] for d in data if d['iteration'] >= 150), None)
    iter_200 = next((d['cumulative_batch_density'] for d in data if d['iteration'] >= 190), None)

    if all([iter_50, iter_100, iter_150, iter_200]):
        range_val = max(iter_50, iter_100, iter_150, iter_200) - min(iter_50, iter_100, iter_150, iter_200)
        gateway_ranges[pod] = {
            50: iter_50, 100: iter_100, 150: iter_150, 200: iter_200, 'range': range_val
        }
        print(f"{pod:12} | {iter_50:>10.2f} | {iter_100:>10.2f} | {iter_150:>10.2f} | {iter_200:>10.2f} | {range_val:>10.2f} |")

# Analyze early prefill events
print(f"\n{'='*80}")
print("EARLY PREFILL LOAD (First 50 iterations)")
print(f"{'='*80}\n")

print("DIRECT RUN:")
print(f"{'Pod':12} | {'Ctx Reqs':>10} | {'Ctx Tokens':>12} | {'Heavy Prefills':>15} | {'Batch@50':>10} |")
print(f"{'-'*14}|{'-'*12}|{'-'*14}|{'-'*17}|{'-'*12}|")

for pod, data in sorted(direct_data.items()):
    early_data = [d for d in data if d['iteration'] < 50]
    ctx_reqs = sum(d['ctx_reqs'] for d in early_data)
    ctx_tokens = sum(d['ctx_tokens'] for d in early_data)
    heavy_prefills = sum(1 for d in early_data if d['ctx_tokens'] > 4000)
    batch_50 = direct_ranges.get(pod, {}).get(50, 0)
    print(f"{pod:12} | {ctx_reqs:>10} | {ctx_tokens:>12,} | {heavy_prefills:>15} | {batch_50:>10.2f} |")

print(f"\nGATEWAY RUN:")
print(f"{'Pod':12} | {'Ctx Reqs':>10} | {'Ctx Tokens':>12} | {'Heavy Prefills':>15} | {'Batch@50':>10} |")
print(f"{'-'*14}|{'-'*12}|{'-'*14}|{'-'*17}|{'-'*12}|")

for pod, data in sorted(gateway_data.items()):
    early_data = [d for d in data if d['iteration'] < 50]
    ctx_reqs = sum(d['ctx_reqs'] for d in early_data)
    ctx_tokens = sum(d['ctx_tokens'] for d in early_data)
    heavy_prefills = sum(1 for d in early_data if d['ctx_tokens'] > 4000)
    batch_50 = gateway_ranges.get(pod, {}).get(50, 0)
    print(f"{pod:12} | {ctx_reqs:>10} | {ctx_tokens:>12,} | {heavy_prefills:>15} | {batch_50:>10.2f} |")

# Find divergence point
print(f"\n{'='*80}")
print("KEY FINDINGS")
print(f"{'='*80}\n")

if direct_ranges and gateway_ranges:
    direct_variance_50 = max(d[50] for d in direct_ranges.values()) - min(d[50] for d in direct_ranges.values())
    direct_variance_200 = max(d[200] for d in direct_ranges.values()) - min(d[200] for d in direct_ranges.values())

    gateway_variance_50 = max(d[50] for d in gateway_ranges.values()) - min(d[50] for d in gateway_ranges.values())
    gateway_variance_200 = max(d[200] for d in gateway_ranges.values()) - min(d[200] for d in gateway_ranges.values())

    print(f"Batch Density Variance (spread across pods):")
    print(f"  DIRECT:  Iter 50 = {direct_variance_50:.2f}, Iter 200 = {direct_variance_200:.2f} ({(direct_variance_200/direct_variance_50 - 1)*100:+.0f}% growth)")
    print(f"  GATEWAY: Iter 50 = {gateway_variance_50:.2f}, Iter 200 = {gateway_variance_200:.2f} ({(gateway_variance_200/gateway_variance_50 - 1)*100:+.0f}% growth)")
    print()

    if direct_variance_200 > direct_variance_50 * 1.5:
        print("✅ FEEDBACK LOOP DETECTED in direct!")
        print("   Variance GROWS over time - pods diverge as iterations progress")
    else:
        print("⚠️  No clear feedback loop growth detected")

    if gateway_variance_200 < direct_variance_200 / 2:
        print("✅ GATEWAY PREVENTS DIVERGENCE!")
        print("   Variance stays low throughout - pods remain balanced")

print()
print("Interpretation:")
print("  If direct shows GROWING variance: Initial imbalance → feedback loop")
print("  If gateway shows STABLE variance: Load balancing prevents divergence")
