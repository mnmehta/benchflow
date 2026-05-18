#!/usr/bin/env python3
import numpy as np
from scipy.stats import pearsonr

# Direct run data
direct_pods = {
    'de2pj8r': {'iterations': 47655, 'gen_reqs': 97327, 'avg_time': 9.00},
    'de4kjwq': {'iterations': 43451, 'gen_reqs': 100476, 'avg_time': 10.34},
    'de8fmr9': {'iterations': 46019, 'gen_reqs': 75678, 'avg_time': 8.46},
    'decg4l4': {'iterations': 48129, 'gen_reqs': 78087, 'avg_time': 8.28},
    'dehc7h2': {'iterations': 45271, 'gen_reqs': 73084, 'avg_time': 8.37},
    'del27pq': {'iterations': 41895, 'gen_reqs': 78372, 'avg_time': 9.02},
    'delfkwp': {'iterations': 44179, 'gen_reqs': 83742, 'avg_time': 9.20},
    'dewh68t': {'iterations': 47244, 'gen_reqs': 71256, 'avg_time': 7.74},
}

# Gateway run data
gateway_pods = {
    '599gm6j': {'iterations': 56268, 'gen_reqs': 87881, 'avg_time': 7.77},
    '59cb5sf': {'iterations': 56756, 'gen_reqs': 86158, 'avg_time': 7.83},
    '59dzq59': {'iterations': 55883, 'gen_reqs': 73636, 'avg_time': 7.89},
    '59f59j4': {'iterations': 56430, 'gen_reqs': 80412, 'avg_time': 8.04},
    '59fwj99': {'iterations': 57441, 'gen_reqs': 90981, 'avg_time': 7.50},
    '59ll5f6': {'iterations': 53569, 'gen_reqs': 84308, 'avg_time': 8.12},
    '59pxbwm': {'iterations': 53226, 'gen_reqs': 82564, 'avg_time': 8.09},
    '59zx8h2': {'iterations': 52739, 'gen_reqs': 84503, 'avg_time': 7.94},
}

def analyze_batch_density(pods, name):
    print(f"\n{'='*80}")
    print(f"{name} RUN - Batch Density Analysis")
    print(f"{'='*80}\n")

    # Calculate batch density for each pod
    data = []
    for pod, stats in pods.items():
        batch_density = stats['gen_reqs'] / stats['iterations']
        data.append({
            'pod': pod,
            'batch_density': batch_density,
            'avg_time': stats['avg_time']
        })

    # Sort by batch density
    data.sort(key=lambda x: x['batch_density'])

    print(f"| {'Pod':12} | {'Batch Density':>15} | {'Avg Time (ms)':>15} |")
    print(f"|{'-'*14}|{'-'*17}|{'-'*17}|")

    batch_densities = []
    avg_times = []

    for item in data:
        print(f"| {item['pod']:12} | {item['batch_density']:>15.2f} | {item['avg_time']:>15.2f} |")
        batch_densities.append(item['batch_density'])
        avg_times.append(item['avg_time'])

    # Calculate statistics
    batch_range = max(batch_densities) - min(batch_densities)
    batch_pct_range = (batch_range / min(batch_densities)) * 100
    time_range = max(avg_times) - min(avg_times)
    time_pct_range = (time_range / min(avg_times)) * 100

    print(f"\n**Batch Density Range:** {min(batch_densities):.2f} to {max(batch_densities):.2f} ({batch_pct_range:.1f}% spread)")
    print(f"**Avg Time Range:** {min(avg_times):.2f}ms to {max(avg_times):.2f}ms ({time_pct_range:.1f}% spread)")

    # Calculate correlation
    correlation, p_value = pearsonr(batch_densities, avg_times)

    print(f"\n**Pearson Correlation:** r = {correlation:.4f} (p = {p_value:.4f})")

    if p_value < 0.05:
        print(f"✅ **Statistically significant correlation!**")
    else:
        print(f"⚠️  Correlation not statistically significant")

    # Calculate R²
    r_squared = correlation ** 2
    print(f"**R² (variance explained):** {r_squared:.4f} ({r_squared*100:.1f}%)")

    # Linear regression
    slope, intercept = np.polyfit(batch_densities, avg_times, 1)
    print(f"\n**Linear Regression:** Avg Time = {slope:.3f} × Batch Density + {intercept:.3f}")
    print(f"**Interpretation:** Each +1.0 increase in batch density adds {slope:.3f}ms to avg iteration time")

    return {
        'batch_densities': batch_densities,
        'avg_times': avg_times,
        'correlation': correlation,
        'p_value': p_value,
        'r_squared': r_squared,
        'slope': slope
    }

# Analyze both
direct_results = analyze_batch_density(direct_pods, "DIRECT")
gateway_results = analyze_batch_density(gateway_pods, "GATEWAY")

# Comparison
print(f"\n{'='*80}")
print("COMPARISON")
print(f"{'='*80}\n")

print(f"| {'Metric':30} | {'Direct':>15} | {'Gateway':>15} | {'Difference':>15} |")
print(f"|{'-'*32}|{'-'*17}|{'-'*17}|{'-'*17}|")
print(f"| {'Correlation (r)':30} | {direct_results['correlation']:>15.4f} | {gateway_results['correlation']:>15.4f} | {direct_results['correlation'] - gateway_results['correlation']:>15.4f} |")
print(f"| {'R² (variance explained)':30} | {direct_results['r_squared']:>15.4f} | {gateway_results['r_squared']:>15.4f} | {direct_results['r_squared'] - gateway_results['r_squared']:>15.4f} |")
print(f"| {'Slope (ms per batch)':30} | {direct_results['slope']:>15.3f} | {gateway_results['slope']:>15.3f} | {direct_results['slope'] - gateway_results['slope']:>15.3f} |")
print(f"| {'Batch density range':30} | {max(direct_results['batch_densities']) - min(direct_results['batch_densities']):>15.2f} | {max(gateway_results['batch_densities']) - min(gateway_results['batch_densities']):>15.2f} | {(max(direct_results['batch_densities']) - min(direct_results['batch_densities'])) - (max(gateway_results['batch_densities']) - min(gateway_results['batch_densities'])):>15.2f} |")
print(f"| {'Avg time range (ms)':30} | {max(direct_results['avg_times']) - min(direct_results['avg_times']):>15.2f} | {max(gateway_results['avg_times']) - min(gateway_results['avg_times']):>15.2f} | {(max(direct_results['avg_times']) - min(direct_results['avg_times'])) - (max(gateway_results['avg_times']) - min(gateway_results['avg_times'])):>15.2f} |")

print(f"\n{'='*80}")
print("KEY INSIGHT")
print(f"{'='*80}\n")

print(f"The correlation between batch density and iteration time is:")
print(f"  • DIRECT: r = {direct_results['correlation']:.4f} (R² = {direct_results['r_squared']:.3f}, explaining {direct_results['r_squared']*100:.1f}% of variance)")
print(f"  • GATEWAY: r = {gateway_results['correlation']:.4f} (R² = {gateway_results['r_squared']:.3f}, explaining {gateway_results['r_squared']*100:.1f}% of variance)")
print()
print(f"Direct has a {direct_results['r_squared']/gateway_results['r_squared']:.1f}× stronger correlation!")
print()
print(f"Why? Direct's round-robin routing creates HOT PODS with high batch density,")
print(f"while Gateway's intelligent routing keeps batch density more uniform.")
print()
print(f"Result:")
print(f"  • Direct batch density varies by {(max(direct_results['batch_densities']) - min(direct_results['batch_densities'])):.2f} (1.51 to 2.31)")
print(f"  • Gateway batch density varies by {(max(gateway_results['batch_densities']) - min(gateway_results['batch_densities'])):.2f} (1.32 to 1.60)")
print(f"  • Direct avg time varies by {(max(direct_results['avg_times']) - min(direct_results['avg_times'])):.2f}ms (7.74 to 10.34)")
print(f"  • Gateway avg time varies by {(max(gateway_results['avg_times']) - min(gateway_results['avg_times'])):.2f}ms (7.50 to 8.12)")
print()
print("The gateway's better load balancing prevents hot pods, keeping decode efficient!")
