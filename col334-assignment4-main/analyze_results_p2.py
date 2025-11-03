#!/usr/bin/env python3
"""
Analysis and plotting script for Part 2 experiments
Generates plots for all 4 experiments with fairness and utilization metrics
"""

import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
from scipy import stats
import sys
import os

def jain_fairness_index(alloc1, alloc2):
    """Calculate Jain's Fairness Index for two flows"""
    n = 2
    sum_alloc = alloc1 + alloc2
    sum_sq = alloc1**2 + alloc2**2
    if sum_sq == 0:
        return 0.0
    return (sum_alloc ** 2) / (n * sum_sq)

def analyze_fixed_bandwidth(csv_file):
    """Analyze and plot fixed bandwidth experiment"""
    print(f"\n{'='*60}")
    print("Experiment 1: Fixed Bandwidth")
    print('='*60)

    try:
        df = pd.read_csv(csv_file)
    except FileNotFoundError:
        print(f"Error: {csv_file} not found")
        return

    # Get unique bandwidth values
    bw_values = sorted(df['bw'].unique())

    results = []
    for bw in bw_values:
        subset = df[df['bw'] == bw]

        avg_util = subset['link_util'].mean()
        avg_jfi = subset['jfi'].mean()
        std_util = subset['link_util'].std()
        std_jfi = subset['jfi'].std()

        results.append({
            'bw': bw,
            'util': avg_util,
            'jfi': avg_jfi,
            'util_std': std_util,
            'jfi_std': std_jfi
        })

    results_df = pd.DataFrame(results)

    # Create plot with dual y-axes
    fig, ax1 = plt.subplots(figsize=(12, 6))

    color1 = 'tab:blue'
    ax1.set_xlabel('Bottleneck Bandwidth (Mbps)', fontsize=12)
    ax1.set_ylabel('Link Utilization', fontsize=12, color=color1)
    ax1.plot(results_df['bw'], results_df['util'], 'o-', color=color1, linewidth=2, markersize=8, label='Link Utilization')
    ax1.tick_params(axis='y', labelcolor=color1)
    ax1.grid(True, alpha=0.3)
    ax1.set_ylim(0, 1.1)

    ax2 = ax1.twinx()
    color2 = 'tab:red'
    ax2.set_ylabel('Jain Fairness Index', fontsize=12, color=color2)
    ax2.plot(results_df['bw'], results_df['jfi'], 's-', color=color2, linewidth=2, markersize=8, label='Fairness (JFI)')
    ax2.tick_params(axis='y', labelcolor=color2)
    ax2.set_ylim(0, 1.1)

    plt.title('Link Utilization and Fairness vs Bandwidth', fontsize=14)

    # Add legends
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc='lower right', fontsize=10)

    plt.tight_layout()
    plt.savefig('plot_p2_fixed_bandwidth.png', dpi=300, bbox_inches='tight')
    print("Saved: plot_p2_fixed_bandwidth.png")

    # Print statistics
    print("\nResults:")
    print("-" * 80)
    print(f"{'BW (Mbps)':<12} {'Util':<10} {'JFI':<10} {'Performance':<15}")
    print("-" * 80)
    for _, row in results_df.iterrows():
        perf = row['util'] * row['jfi']
        print(f"{row['bw']:<12.0f} {row['util']:<10.3f} {row['jfi']:<10.3f} {perf:<15.3f}")
    print("-" * 80)

    plt.show()

def analyze_varying_loss(csv_file):
    """Analyze and plot varying loss experiment"""
    print(f"\n{'='*60}")
    print("Experiment 2: Varying Loss")
    print('='*60)

    try:
        df = pd.read_csv(csv_file)
    except FileNotFoundError:
        print(f"Error: {csv_file} not found")
        return

    loss_values = sorted(df['loss'].unique())

    results = []
    for loss in loss_values:
        subset = df[df['loss'] == loss]
        avg_util = subset['link_util'].mean()
        std_util = subset['link_util'].std()

        results.append({
            'loss': loss,
            'util': avg_util,
            'util_std': std_util
        })

    results_df = pd.DataFrame(results)

    # Create plot
    plt.figure(figsize=(10, 6))
    plt.plot(results_df['loss'], results_df['util'], 'o-', linewidth=2, markersize=8, color='tab:green')
    plt.xlabel('Packet Loss Rate (%)', fontsize=12)
    plt.ylabel('Link Utilization', fontsize=12)
    plt.title('Link Utilization vs Packet Loss Rate', fontsize=14)
    plt.grid(True, alpha=0.3)
    plt.ylim(0, 1.1)
    plt.tight_layout()
    plt.savefig('plot_p2_varying_loss.png', dpi=300, bbox_inches='tight')
    print("Saved: plot_p2_varying_loss.png")

    # Print statistics
    print("\nResults:")
    print("-" * 60)
    print(f"{'Loss (%)':<12} {'Utilization':<15} {'Std Dev':<15}")
    print("-" * 60)
    for _, row in results_df.iterrows():
        print(f"{row['loss']:<12.1f} {row['util']:<15.3f} {row['util_std']:<15.3f}")
    print("-" * 60)

    plt.show()

def analyze_asymmetric_flows(csv_file):
    """Analyze and plot asymmetric flows experiment"""
    print(f"\n{'='*60}")
    print("Experiment 3: Asymmetric Flows")
    print('='*60)

    try:
        df = pd.read_csv(csv_file)
    except FileNotFoundError:
        print(f"Error: {csv_file} not found")
        return

    delay_values = sorted(df['delay_c2_ms'].unique())

    results = []
    for delay in delay_values:
        subset = df[df['delay_c2_ms'] == delay]
        avg_jfi = subset['jfi'].mean()
        std_jfi = subset['jfi'].std()

        # Calculate RTT (assuming delay_c1 = 5ms, bottleneck = 10ms each way)
        # RTT for flow 2 = 2 * (delay_c2 + 10 + 5) = 2 * (delay_c2 + 15)
        rtt = 2 * (delay + 15)

        results.append({
            'delay': delay,
            'rtt': rtt,
            'jfi': avg_jfi,
            'jfi_std': std_jfi
        })

    results_df = pd.DataFrame(results)

    # Create plot
    plt.figure(figsize=(10, 6))
    plt.plot(results_df['rtt'], results_df['jfi'], 'o-', linewidth=2, markersize=8, color='tab:purple')
    plt.xlabel('RTT of Flow 2 (ms)', fontsize=12)
    plt.ylabel('Jain Fairness Index', fontsize=12)
    plt.title('Fairness vs RTT Asymmetry\n(Flow 1 RTT = 40ms, Flow 2 RTT varies)', fontsize=14)
    plt.grid(True, alpha=0.3)
    plt.ylim(0, 1.1)
    plt.tight_layout()
    plt.savefig('plot_p2_asymmetric_flows.png', dpi=300, bbox_inches='tight')
    print("Saved: plot_p2_asymmetric_flows.png")

    # Print statistics
    print("\nResults:")
    print("-" * 70)
    print(f"{'Delay C2 (ms)':<15} {'RTT (ms)':<12} {'JFI':<10} {'Std Dev':<15}")
    print("-" * 70)
    for _, row in results_df.iterrows():
        print(f"{row['delay']:<15.0f} {row['rtt']:<12.0f} {row['jfi']:<10.3f} {row['jfi_std']:<15.3f}")
    print("-" * 70)

    plt.show()

def analyze_background_udp(csv_file):
    """Analyze and plot background UDP experiment"""
    print(f"\n{'='*60}")
    print("Experiment 4: Background UDP Traffic")
    print('='*60)

    try:
        df = pd.read_csv(csv_file)
    except FileNotFoundError:
        print(f"Error: {csv_file} not found")
        return

    # Map UDP off means to traffic levels
    udp_means = sorted(df['udp_off_mean'].unique(), reverse=True)
    traffic_labels = []

    for mean in udp_means:
        if mean >= 0.5:
            traffic_labels.append(f'Light\n({mean}s)')
        elif mean >= 0.2:
            traffic_labels.append(f'Medium\n({mean}s)')
        else:
            traffic_labels.append(f'Heavy\n({mean}s)')

    results = []
    for mean in udp_means:
        subset = df[df['udp_off_mean'] == mean]
        avg_util = subset['link_util'].mean()
        avg_jfi = subset['jfi'].mean()
        std_util = subset['link_util'].std()
        std_jfi = subset['jfi'].std()

        results.append({
            'udp_mean': mean,
            'util': avg_util,
            'jfi': avg_jfi,
            'util_std': std_util,
            'jfi_std': std_jfi
        })

    results_df = pd.DataFrame(results)

    # Create bar chart
    fig, ax = plt.subplots(figsize=(10, 6))

    x = np.arange(len(traffic_labels))
    width = 0.35

    bars1 = ax.bar(x - width/2, results_df['util'], width, label='Link Utilization', color='tab:blue', alpha=0.8)
    bars2 = ax.bar(x + width/2, results_df['jfi'], width, label='Jain Fairness Index', color='tab:red', alpha=0.8)

    ax.set_xlabel('Background UDP Traffic Level', fontsize=12)
    ax.set_ylabel('Metric Value', fontsize=12)
    ax.set_title('Impact of Background UDP Traffic on TCP Flows', fontsize=14)
    ax.set_xticks(x)
    ax.set_xticklabels(traffic_labels)
    ax.legend(fontsize=10)
    ax.grid(True, alpha=0.3, axis='y')
    ax.set_ylim(0, 1.1)

    # Add value labels on bars
    for bars in [bars1, bars2]:
        for bar in bars:
            height = bar.get_height()
            ax.text(bar.get_x() + bar.get_width()/2., height,
                   f'{height:.2f}',
                   ha='center', va='bottom', fontsize=9)

    plt.tight_layout()
    plt.savefig('plot_p2_background_udp.png', dpi=300, bbox_inches='tight')
    print("Saved: plot_p2_background_udp.png")

    # Print statistics
    print("\nResults:")
    print("-" * 70)
    print(f"{'Traffic':<15} {'OFF Mean (s)':<15} {'Util':<10} {'JFI':<10}")
    print("-" * 70)
    for i, (_, row) in enumerate(results_df.iterrows()):
        traffic = traffic_labels[i].split('\n')[0]
        print(f"{traffic:<15} {row['udp_mean']:<15.1f} {row['util']:<10.3f} {row['jfi']:<10.3f}")
    print("-" * 70)

    plt.show()

def verify_data_integrity(csv_file):
    """Verify MD5 hashes and data integrity"""
    try:
        df = pd.read_csv(csv_file)
    except FileNotFoundError:
        print(f"Error: {csv_file} not found")
        return

    print(f"\nData Integrity Check: {csv_file}")
    print("-" * 60)

    # Check MD5 hashes
    if 'md5_hash_1' in df.columns and 'md5_hash_2' in df.columns:
        unique_hash1 = df['md5_hash_1'].unique()
        unique_hash2 = df['md5_hash_2'].unique()

        print(f"Unique MD5 hashes for flow 1: {len(unique_hash1)}")
        print(f"Unique MD5 hashes for flow 2: {len(unique_hash2)}")

        if len(unique_hash1) == 1 and len(unique_hash2) == 1:
            print("✓ All transfers have consistent MD5 hashes")
        else:
            print("✗ WARNING: Multiple different MD5 hashes detected!")

    print(f"Total experiments: {len(df)}")
    print("-" * 60)

def main():
    """Main analysis function"""
    print("=" * 60)
    print("Part 2: Congestion Control Analysis")
    print("=" * 60)

    experiments = {
        'fixed_bandwidth': 'p2_fairness_fixed_bandwidth.csv',
        'varying_loss': 'p2_fairness_varying_loss.csv',
        'asymmetric_flows': 'p2_fairness_asymmetric_flows.csv',
        'background_udp': 'p2_fairness_background_udp.csv'
    }

    # Allow command-line specification of experiment
    if len(sys.argv) > 1:
        exp_name = sys.argv[1]
        if exp_name in experiments:
            csv_file = experiments[exp_name]
            print(f"\nAnalyzing: {exp_name}")
            verify_data_integrity(csv_file)

            if exp_name == 'fixed_bandwidth':
                analyze_fixed_bandwidth(csv_file)
            elif exp_name == 'varying_loss':
                analyze_varying_loss(csv_file)
            elif exp_name == 'asymmetric_flows':
                analyze_asymmetric_flows(csv_file)
            elif exp_name == 'background_udp':
                analyze_background_udp(csv_file)
        else:
            print(f"Unknown experiment: {exp_name}")
            print(f"Available: {', '.join(experiments.keys())}")
    else:
        # Analyze all experiments that have data
        for exp_name, csv_file in experiments.items():
            if os.path.exists(csv_file):
                print(f"\nAnalyzing: {exp_name}")
                verify_data_integrity(csv_file)

                if exp_name == 'fixed_bandwidth':
                    analyze_fixed_bandwidth(csv_file)
                elif exp_name == 'varying_loss':
                    analyze_varying_loss(csv_file)
                elif exp_name == 'asymmetric_flows':
                    analyze_asymmetric_flows(csv_file)
                elif exp_name == 'background_udp':
                    analyze_background_udp(csv_file)
            else:
                print(f"\nSkipping {exp_name}: {csv_file} not found")

    print("\n" + "=" * 60)
    print("Analysis complete!")
    print("=" * 60)

if __name__ == "__main__":
    main()
