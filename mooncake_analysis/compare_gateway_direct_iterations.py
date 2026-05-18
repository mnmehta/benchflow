#!/usr/bin/env python3
import json
import statistics
from collections import defaultdict

# Load iteration data
with open('/tmp/gateway_direct_iterations.json', 'r') as f:
    raw_data = json.load(f)

# We need to re-load the full iteration data (not just counts)
# Let me reload from the pickle or recreate
print("Loading full iteration data...")

import requests
import urllib3
import re
import os

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

MLFLOW_URL = "https://mlflow.apps.aperdomo-lab.ibm.rhperfscale.org"
AUTH = ("mlflow", "Bo68kJrp0LRz")

runs = {
    "direct": "213999419ce74c109c5fcfd71ec684b5",
    "gateway": "987f07f1d18349498d4511c94bf98aff"
}

def download_artifact(run_id, artifact_path):
    url = f"{MLFLOW_URL}/get-artifact"
    params = {"run_uuid": run_id, "path": artifact_path}
    response = requests.get(url, params=params, auth=AUTH, verify=False, stream=True)
    return response.content.decode('utf-8', errors='ignore')

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

def analyze_iterations(iterations):
    """Calculate statistics from iteration list"""
    total_iters = len(iterations)
    total_ctx_reqs = sum(i['ctx_reqs'] for i in iterations)
    total_ctx_tokens = sum(i['ctx_tokens'] for i in iterations)
    total_gen_reqs = sum(i['gen_reqs'] for i in iterations)
    total_gen_tokens = sum(i['gen_tokens'] for i in iterations)
    total_time_ms = sum(i['time_ms'] for i in iterations)

    # Categorize iterations
    gen_only = sum(1 for i in iterations if i['ctx_reqs'] == 0 and i['gen_reqs'] > 0)
    ctx_only = sum(1 for i in iterations if i['ctx_reqs'] > 0 and i['gen_reqs'] == 0)
    mixed = sum(1 for i in iterations if i['ctx_reqs'] > 0 and i['gen_reqs'] > 0)
    idle = sum(1 for i in iterations if i['ctx_reqs'] == 0 and i['gen_reqs'] == 0)

    # Time percentiles
    times = [i['time_ms'] for i in iterations]
    times.sort()

    return {
        'total_iterations': total_iters,
        'total_ctx_reqs': total_ctx_reqs,
        'total_ctx_tokens': total_ctx_tokens,
        'total_gen_reqs': total_gen_reqs,
        'total_gen_tokens': total_gen_tokens,
        'total_time_sec': total_time_ms / 1000,
        'avg_iter_time_ms': total_time_ms / total_iters if total_iters > 0 else 0,
        'gen_only': gen_only,
        'ctx_only': ctx_only,
        'mixed': mixed,
        'idle': idle,
        'time_p50': times[int(len(times) * 0.50)] if times else 0,
        'time_p95': times[int(len(times) * 0.95)] if times else 0,
        'time_p99': times[int(len(times) * 0.99)] if times else 0,
        'time_p999': times[int(len(times) * 0.999)] if times else 0,
        'time_max': times[-1] if times else 0,
        'avg_ctx_tokens_per_req': total_ctx_tokens / total_ctx_reqs if total_ctx_reqs > 0 else 0,
        'avg_output_tokens_per_prompt': total_gen_tokens / (total_ctx_reqs / 2) if total_ctx_reqs > 0 else 0
    }

# Download one log from each to get sample
print("Downloading sample logs for detailed analysis...")

results = {}

for run_type, run_id in runs.items():
    print(f"\n{'='*80}")
    print(f"Analyzing {run_type.upper()} run...")
    print(f"{'='*80}")

    artifact_list = list_artifacts(run_id, "logs/model")
    vllm_logs = [f for f in artifact_list['files'] if '_vllm.log' in f['path']]

    all_iterations = []
    pod_stats = {}

    for i, log_file in enumerate(vllm_logs):
        log_path = log_file['path']
        pod_name = os.path.basename(log_path).replace('_vllm.log', '')
        pod_short = pod_name.split('-')[-1]  # Get last part

        print(f"  [{i+1}/{len(vllm_logs)}] Processing {pod_short}...", end=" ", flush=True)

        content = download_artifact(run_id, log_path)
        iterations = []
        for line in content.split('\n'):
            if 'Iteration(' in line:
                parsed = parse_iteration_line(line)
                if parsed:
                    iterations.append(parsed)

        stats = analyze_iterations(iterations)
        pod_stats[pod_short] = stats
        all_iterations.extend(iterations)

        print(f"✓ ({len(iterations)} iterations)")

    overall_stats = analyze_iterations(all_iterations)

    results[run_type] = {
        'pod_stats': pod_stats,
        'overall': overall_stats,
        'all_iterations': all_iterations
    }

# Save detailed results
print(f"\n{'='*80}")
print("ANALYSIS COMPLETE - Saving results...")
print(f"{'='*80}")

with open('/tmp/gateway_direct_detailed_analysis.json', 'w') as f:
    # Save without the huge iteration arrays to keep file size reasonable
    save_data = {}
    for run_type, data in results.items():
        save_data[run_type] = {
            'pod_stats': data['pod_stats'],
            'overall': data['overall']
        }
    json.dump(save_data, f, indent=2)

print("Results saved to: /tmp/gateway_direct_detailed_analysis.json")
print(f"\nDirect iterations: {results['direct']['overall']['total_iterations']}")
print(f"Gateway iterations: {results['gateway']['overall']['total_iterations']}")
