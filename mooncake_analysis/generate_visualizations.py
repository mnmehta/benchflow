#!/usr/bin/env python3
"""
Generate visualizations for Gateway vs Direct comparison.
Creates interactive Plotly charts showing the feedback loop and load balancing differences.
"""
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import plotly.express as px
import numpy as np

# Data from the analysis
direct_pods = {
    'de2pj8r': {'batch': 2.04, 'time': 9.00, 'ctx_tokens': 3312992, 'ctx_early': 11864},
    'de4kjwq': {'batch': 2.31, 'time': 10.34, 'ctx_tokens': 4598723, 'ctx_early': 26585},
    'de8fmr9': {'batch': 1.64, 'time': 8.46, 'ctx_tokens': 2531717, 'ctx_early': 18449},
    'decg4l4': {'batch': 1.62, 'time': 8.28, 'ctx_tokens': 2965580, 'ctx_early': 13779},
    'dehc7h2': {'batch': 1.61, 'time': 8.37, 'ctx_tokens': 2849901, 'ctx_early': 7393},
    'del27pq': {'batch': 1.87, 'time': 9.02, 'ctx_tokens': 3132449, 'ctx_early': 270002},
    'delfkwp': {'batch': 1.90, 'time': 9.20, 'ctx_tokens': 3588702, 'ctx_early': 2387},
    'dewh68t': {'batch': 1.51, 'time': 7.74, 'ctx_tokens': 2526526, 'ctx_early': 17024},
}

gateway_pods = {
    '599gm6j': {'batch': 1.56, 'time': 7.77, 'ctx_tokens': 3106573, 'ctx_early': 23484},
    '59cb5sf': {'batch': 1.52, 'time': 7.83, 'ctx_tokens': 2347908, 'ctx_early': 44894},
    '59dzq59': {'batch': 1.32, 'time': 7.89, 'ctx_tokens': 2519655, 'ctx_early': 17727},
    '59f59j4': {'batch': 1.42, 'time': 8.04, 'ctx_tokens': 2776210, 'ctx_early': 10691},
    '59fwj99': {'batch': 1.58, 'time': 7.50, 'ctx_tokens': 2433764, 'ctx_early': 7393},
    '59ll5f6': {'batch': 1.57, 'time': 8.12, 'ctx_tokens': 2718017, 'ctx_early': 2387},
    '59pxbwm': {'batch': 1.55, 'time': 8.09, 'ctx_tokens': 2708429, 'ctx_early': 48069},
    '59zx8h2': {'batch': 1.60, 'time': 7.94, 'ctx_tokens': 2897903, 'ctx_early': 242166},
}

print("Generating visualizations...")

# 1. Batch Density vs Iteration Time (The Smoking Gun)
print("  1. Batch density vs iteration time correlation...")

fig1 = go.Figure()

# Direct scatter
direct_batch = [p['batch'] for p in direct_pods.values()]
direct_time = [p['time'] for p in direct_pods.values()]
direct_names = list(direct_pods.keys())

fig1.add_trace(go.Scatter(
    x=direct_batch,
    y=direct_time,
    mode='markers+text',
    name='Direct (Round-Robin)',
    text=direct_names,
    textposition='top center',
    marker=dict(size=12, color='red', symbol='circle'),
    hovertemplate='<b>%{text}</b><br>Batch: %{x:.2f}<br>Time: %{y:.2f}ms<extra></extra>'
))

# Gateway scatter
gateway_batch = [p['batch'] for p in gateway_pods.values()]
gateway_time = [p['time'] for p in gateway_pods.values()]
gateway_names = list(gateway_pods.keys())

fig1.add_trace(go.Scatter(
    x=gateway_batch,
    y=gateway_time,
    mode='markers+text',
    name='Gateway (Load-Aware)',
    text=gateway_names,
    textposition='bottom center',
    marker=dict(size=12, color='green', symbol='diamond'),
    hovertemplate='<b>%{text}</b><br>Batch: %{x:.2f}<br>Time: %{y:.2f}ms<extra></extra>'
))

# Add regression line for direct
z = np.polyfit(direct_batch, direct_time, 1)
p = np.poly1d(z)
x_line = np.linspace(min(direct_batch), max(direct_batch), 100)

fig1.add_trace(go.Scatter(
    x=x_line,
    y=p(x_line),
    mode='lines',
    name=f'Direct Trend (y={z[0]:.2f}x+{z[1]:.2f})',
    line=dict(color='red', dash='dash', width=2),
    showlegend=True
))

fig1.update_layout(
    title='Batch Density vs Iteration Time: The Feedback Loop<br><sub>Direct shows r=0.96 correlation (vicious cycle), Gateway shows r=-0.15 (no correlation)</sub>',
    xaxis_title='Batch Density (concurrent requests per iteration)',
    yaxis_title='Average Iteration Time (ms)',
    hovermode='closest',
    height=600,
    font=dict(size=12),
    legend=dict(x=0.02, y=0.98, bgcolor='rgba(255,255,255,0.8)')
)

fig1.write_html('/home/michey/benchflow/mooncake_analysis/1_batch_density_correlation.html')

# 2. Early Context Token Imbalance
print("  2. Early context token distribution (root cause)...")

fig2 = make_subplots(
    rows=2, cols=1,
    subplot_titles=('Direct Run: Early Prefill Imbalance (First 50 Iterations)',
                    'Gateway Run: Early Prefill Load'),
    vertical_spacing=0.15
)

# Direct early context
direct_ctx_early = [p['ctx_early'] for p in direct_pods.values()]
direct_names_sorted = sorted(direct_pods.keys(), key=lambda k: direct_pods[k]['ctx_early'], reverse=True)
direct_ctx_sorted = [direct_pods[k]['ctx_early'] for k in direct_names_sorted]

fig2.add_trace(go.Bar(
    x=direct_names_sorted,
    y=direct_ctx_sorted,
    name='Direct',
    marker=dict(color=direct_ctx_sorted, colorscale='Reds', showscale=True,
                colorbar=dict(title='Tokens', x=1.02, y=0.75, len=0.4)),
    text=[f'{v:,}' for v in direct_ctx_sorted],
    textposition='outside',
    hovertemplate='<b>%{x}</b><br>Context Tokens: %{y:,}<extra></extra>'
), row=1, col=1)

# Gateway early context
gateway_ctx_early = [p['ctx_early'] for p in gateway_pods.values()]
gateway_names_sorted = sorted(gateway_pods.keys(), key=lambda k: gateway_pods[k]['ctx_early'], reverse=True)
gateway_ctx_sorted = [gateway_pods[k]['ctx_early'] for k in gateway_names_sorted]

fig2.add_trace(go.Bar(
    x=gateway_names_sorted,
    y=gateway_ctx_sorted,
    name='Gateway',
    marker=dict(color=gateway_ctx_sorted, colorscale='Greens', showscale=True,
                colorbar=dict(title='Tokens', x=1.02, y=0.25, len=0.4)),
    text=[f'{v:,}' for v in gateway_ctx_sorted],
    textposition='outside',
    hovertemplate='<b>%{x}</b><br>Context Tokens: %{y:,}<extra></extra>'
), row=2, col=1)

fig2.update_xaxes(title_text='Pod', row=1, col=1)
fig2.update_xaxes(title_text='Pod', row=2, col=1)
fig2.update_yaxes(title_text='Context Tokens', row=1, col=1)
fig2.update_yaxes(title_text='Context Tokens', row=2, col=1)

fig2.update_layout(
    height=800,
    showlegend=False,
    title_text='Early Prefill Imbalance Triggers Feedback Loop<br><sub>Direct: 270K vs 2K tokens (113× difference!), Gateway: 242K vs 2K but gets corrected</sub>',
    font=dict(size=12)
)

fig2.write_html('/home/michey/benchflow/mooncake_analysis/2_early_prefill_imbalance.html')

# 3. Final State Comparison
print("  3. Final state comparison...")

fig3 = make_subplots(
    rows=1, cols=2,
    subplot_titles=('Batch Density (Lower is Better)', 'Avg Iteration Time (Lower is Better)'),
    specs=[[{"type": "bar"}, {"type": "bar"}]]
)

# Combine all pods for comparison
all_pods_batch = (
    [(f'D-{k}', v['batch']) for k, v in direct_pods.items()] +
    [(f'G-{k}', v['batch']) for k, v in gateway_pods.items()]
)
all_pods_time = (
    [(f'D-{k}', v['time']) for k, v in direct_pods.items()] +
    [(f'G-{k}', v['time']) for k, v in gateway_pods.items()]
)

# Sort by value
all_pods_batch.sort(key=lambda x: x[1], reverse=True)
all_pods_time.sort(key=lambda x: x[1], reverse=True)

batch_names = [p[0] for p in all_pods_batch]
batch_values = [p[1] for p in all_pods_batch]
batch_colors = ['red' if p.startswith('D-') else 'green' for p in batch_names]

time_names = [p[0] for p in all_pods_time]
time_values = [p[1] for p in all_pods_time]
time_colors = ['red' if p.startswith('D-') else 'green' for p in time_names]

fig3.add_trace(go.Bar(
    x=batch_names,
    y=batch_values,
    marker=dict(color=batch_colors),
    text=[f'{v:.2f}' for v in batch_values],
    textposition='outside',
    showlegend=False,
    hovertemplate='<b>%{x}</b><br>Batch: %{y:.2f}<extra></extra>'
), row=1, col=1)

fig3.add_trace(go.Bar(
    x=time_names,
    y=time_values,
    marker=dict(color=time_colors),
    text=[f'{v:.1f}' for v in time_values],
    textposition='outside',
    showlegend=False,
    hovertemplate='<b>%{x}</b><br>Time: %{y:.2f}ms<extra></extra>'
), row=1, col=2)

# Add average lines
direct_avg_batch = np.mean([p['batch'] for p in direct_pods.values()])
gateway_avg_batch = np.mean([p['batch'] for p in gateway_pods.values()])

fig3.add_hline(y=direct_avg_batch, line_dash='dash', line_color='red', opacity=0.5,
               annotation_text=f'Direct Avg: {direct_avg_batch:.2f}', row=1, col=1)
fig3.add_hline(y=gateway_avg_batch, line_dash='dash', line_color='green', opacity=0.5,
               annotation_text=f'Gateway Avg: {gateway_avg_batch:.2f}', row=1, col=1)

direct_avg_time = np.mean([p['time'] for p in direct_pods.values()])
gateway_avg_time = np.mean([p['time'] for p in gateway_pods.values()])

fig3.add_hline(y=direct_avg_time, line_dash='dash', line_color='red', opacity=0.5,
               annotation_text=f'Direct Avg: {direct_avg_time:.2f}ms', row=1, col=2)
fig3.add_hline(y=gateway_avg_time, line_dash='dash', line_color='green', opacity=0.5,
               annotation_text=f'Gateway Avg: {gateway_avg_time:.2f}ms', row=1, col=2)

fig3.update_xaxes(tickangle=-45, row=1, col=1)
fig3.update_xaxes(tickangle=-45, row=1, col=2)
fig3.update_yaxes(title_text='Batch Density', row=1, col=1)
fig3.update_yaxes(title_text='Time (ms)', row=1, col=2)

fig3.update_layout(
    height=600,
    title_text='Final State: Gateway Maintains Uniform Load, Direct Creates Hot Pods<br><sub>D-* = Direct, G-* = Gateway. Gateway keeps all pods efficient, Direct has 33% variance</sub>',
    font=dict(size=12)
)

fig3.write_html('/home/michey/benchflow/mooncake_analysis/3_final_state_comparison.html')

# 4. Feedback Loop Diagram (as a flow chart)
print("  4. Feedback loop visualization...")

fig4 = go.Figure()

# Define nodes for the feedback loop
nodes = [
    {"name": "Round-Robin<br>Routing", "x": 0.5, "y": 1.0, "color": "lightblue"},
    {"name": "Early Prefill<br>Imbalance", "x": 0.2, "y": 0.8, "color": "orange"},
    {"name": "Slow<br>Iterations", "x": 0.2, "y": 0.6, "color": "red"},
    {"name": "Queue<br>Buildup", "x": 0.2, "y": 0.4, "color": "red"},
    {"name": "Large<br>Batches", "x": 0.2, "y": 0.2, "color": "red"},
    {"name": "Gateway<br>Load-Aware", "x": 0.8, "y": 0.8, "color": "lightgreen"},
    {"name": "Balanced<br>Distribution", "x": 0.8, "y": 0.6, "color": "green"},
    {"name": "Uniform<br>Performance", "x": 0.8, "y": 0.4, "color": "green"},
]

# Add nodes
for node in nodes:
    fig4.add_trace(go.Scatter(
        x=[node["x"]],
        y=[node["y"]],
        mode='markers+text',
        marker=dict(size=80, color=node["color"], line=dict(width=2, color='black')),
        text=[node["name"]],
        textposition='middle center',
        textfont=dict(size=10, color='black'),
        showlegend=False,
        hoverinfo='text',
        hovertext=node["name"]
    ))

# Add arrows for direct path (feedback loop)
arrows = [
    # Direct path
    {"x0": 0.5, "y0": 0.95, "x1": 0.25, "y1": 0.85, "color": "orange", "width": 3},  # Start to imbalance
    {"x0": 0.2, "y0": 0.75, "x1": 0.2, "y1": 0.65, "color": "red", "width": 3},  # Imbalance to slow
    {"x0": 0.2, "y0": 0.55, "x1": 0.2, "y1": 0.45, "color": "red", "width": 3},  # Slow to queue
    {"x0": 0.2, "y0": 0.35, "x1": 0.2, "y1": 0.25, "color": "red", "width": 3},  # Queue to batches
    {"x0": 0.15, "y0": 0.2, "x1": 0.15, "y1": 0.6, "color": "red", "width": 3},  # Feedback
    # Gateway path
    {"x0": 0.5, "y0": 0.95, "x1": 0.75, "y1": 0.85, "color": "green", "width": 3},  # Start to gateway
    {"x0": 0.8, "y0": 0.75, "x1": 0.8, "y1": 0.65, "color": "green", "width": 3},  # Gateway to balanced
    {"x0": 0.8, "y0": 0.55, "x1": 0.8, "y1": 0.45, "color": "green", "width": 3},  # Balanced to uniform
]

for arrow in arrows:
    fig4.add_annotation(
        x=arrow["x1"], y=arrow["y1"],
        ax=arrow["x0"], ay=arrow["y0"],
        xref='x', yref='y',
        axref='x', ayref='y',
        showarrow=True,
        arrowhead=2,
        arrowsize=1.5,
        arrowwidth=arrow["width"],
        arrowcolor=arrow["color"],
        standoff=20,
        startstandoff=20
    )

# Add text annotations
fig4.add_annotation(x=0.2, y=0.1, text="VICIOUS CYCLE", showarrow=False,
                    font=dict(size=14, color='red', family='Arial Black'))
fig4.add_annotation(x=0.8, y=0.2, text="STABLE STATE", showarrow=False,
                    font=dict(size=14, color='green', family='Arial Black'))

fig4.update_layout(
    title='The Feedback Loop: Why Direct Fails and Gateway Succeeds<br><sub>Red path: Vicious cycle from initial imbalance. Green path: Load-aware routing maintains stability</sub>',
    xaxis=dict(showgrid=False, zeroline=False, showticklabels=False, range=[-0.1, 1.1]),
    yaxis=dict(showgrid=False, zeroline=False, showticklabels=False, range=[0, 1.1]),
    height=700,
    font=dict(size=12),
    plot_bgcolor='white'
)

fig4.write_html('/home/michey/benchflow/mooncake_analysis/4_feedback_loop_diagram.html')

# 5. Context Tokens vs Final Batch Density (showing causation)
print("  5. Context tokens vs final batch density...")

fig5 = go.Figure()

# Direct pods
direct_ctx = [p['ctx_tokens'] / 1e6 for p in direct_pods.values()]  # Convert to millions
direct_batch_final = [p['batch'] for p in direct_pods.values()]

fig5.add_trace(go.Scatter(
    x=direct_ctx,
    y=direct_batch_final,
    mode='markers+text',
    name='Direct',
    text=list(direct_pods.keys()),
    textposition='top center',
    marker=dict(size=12, color='red', symbol='circle'),
    hovertemplate='<b>%{text}</b><br>Ctx Tokens: %{x:.2f}M<br>Final Batch: %{y:.2f}<extra></extra>'
))

# Gateway pods
gateway_ctx = [p['ctx_tokens'] / 1e6 for p in gateway_pods.values()]
gateway_batch_final = [p['batch'] for p in gateway_pods.values()]

fig5.add_trace(go.Scatter(
    x=gateway_ctx,
    y=gateway_batch_final,
    mode='markers+text',
    name='Gateway',
    text=list(gateway_pods.keys()),
    textposition='bottom center',
    marker=dict(size=12, color='green', symbol='diamond'),
    hovertemplate='<b>%{text}</b><br>Ctx Tokens: %{x:.2f}M<br>Final Batch: %{y:.2f}<extra></extra>'
))

# Regression line for direct
z = np.polyfit(direct_ctx, direct_batch_final, 1)
p = np.poly1d(z)
x_line = np.linspace(min(direct_ctx), max(direct_ctx), 100)

fig5.add_trace(go.Scatter(
    x=x_line,
    y=p(x_line),
    mode='lines',
    name=f'Direct Trend (r=0.95)',
    line=dict(color='red', dash='dash', width=2)
))

fig5.update_layout(
    title='Total Context Tokens vs Final Batch Density<br><sub>Direct: r=0.95 - heavy prefill load leads to permanent hot pods. Gateway: no correlation</sub>',
    xaxis_title='Total Context Tokens Processed (Millions)',
    yaxis_title='Final Batch Density',
    hovermode='closest',
    height=600,
    font=dict(size=12),
    legend=dict(x=0.02, y=0.98, bgcolor='rgba(255,255,255,0.8)')
)

fig5.write_html('/home/michey/benchflow/mooncake_analysis/5_context_tokens_vs_batch.html')

print("\n✓ All visualizations generated!")
print("\nCreated files:")
print("  - 1_batch_density_correlation.html")
print("  - 2_early_prefill_imbalance.html")
print("  - 3_final_state_comparison.html")
print("  - 4_feedback_loop_diagram.html")
print("  - 5_context_tokens_vs_batch.html")
