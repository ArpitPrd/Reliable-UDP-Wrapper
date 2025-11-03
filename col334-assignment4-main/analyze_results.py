#!/usr/bin/env python3
"""
Analysis and plotting script for Part 1 experiments
Generates plots with 90% confidence intervals
"""

import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
from scipy import stats
import sys

def calculate_confidence_interval(data, confidence=0.90):
    """
    Calculate mean and confidence interval
    Returns: (mean, lower_bound, upper_bound)
    """
    n = len(data)
    if n == 0:
        return 0, 0, 0

    mean = np.mean(data)

    if n == 1:
        return mean, mean, mean

    std_err = stats.sem(data)
    interval = std_err * stats.t.ppf((1 + confidence) / 2, n - 1)

    return mean, mean - interval, mean + interval

def plot_loss_experiment(csv_file):
    """Plot download time vs packet loss rate"""
    print(f"\nAnalyzing loss experiment from {csv_file}")

    try:
        df = pd.read_csv(csv_file)
    except FileNotFoundError:
        print(f"Error: {csv_file} not found. Please run experiments first.")
        return

    # Group by loss rate
    loss_rates = sorted(df['loss'].unique())
    means = []
    lower_bounds = []
    upper_bounds = []

    for loss in loss_rates:
        times = df[df['loss'] == loss]['ttc'].values
        mean, lower, upper = calculate_confidence_interval(times)
        means.append(mean)
        lower_bounds.append(lower)
        upper_bounds.append(upper)

    # Create plot
    plt.figure(figsize=(10, 6))
    plt.plot(loss_rates, means, 'b-o', linewidth=2, markersize=8, label='Mean download time')
    plt.fill_between(loss_rates, lower_bounds, upper_bounds, alpha=0.3, label='90% Confidence Interval')

    plt.xlabel('Packet Loss Rate (%)', fontsize=12)
    plt.ylabel('Download Time (seconds)', fontsize=12)
    plt.title('Download Time vs Packet Loss Rate\n(Delay=20ms, Jitter=0ms)', fontsize=14)
    plt.grid(True, alpha=0.3)
    plt.legend(fontsize=10)
    plt.tight_layout()

    # Save plot
    plt.savefig('plot_loss_experiment.png', dpi=300, bbox_inches='tight')
    print("Saved plot: plot_loss_experiment.png")

    # Print statistics
    print("\nLoss Experiment Results:")
    print("-" * 60)
    print(f"{'Loss %':<10} {'Mean (s)':<12} {'90% CI':<25} {'N':<5}")
    print("-" * 60)
    for i, loss in enumerate(loss_rates):
        ci_str = f"[{lower_bounds[i]:.2f}, {upper_bounds[i]:.2f}]"
        n = len(df[df['loss'] == loss])
        print(f"{loss:<10} {means[i]:<12.2f} {ci_str:<25} {n:<5}")
    print("-" * 60)

    plt.show()

def plot_jitter_experiment(csv_file):
    """Plot download time vs delay jitter"""
    print(f"\nAnalyzing jitter experiment from {csv_file}")

    try:
        df = pd.read_csv(csv_file)
    except FileNotFoundError:
        print(f"Error: {csv_file} not found. Please run experiments first.")
        return

    # Group by jitter
    jitter_values = sorted(df['jitter'].unique())
    means = []
    lower_bounds = []
    upper_bounds = []

    for jitter in jitter_values:
        times = df[df['jitter'] == jitter]['ttc'].values
        mean, lower, upper = calculate_confidence_interval(times)
        means.append(mean)
        lower_bounds.append(lower)
        upper_bounds.append(upper)

    # Create plot
    plt.figure(figsize=(10, 6))
    plt.plot(jitter_values, means, 'r-o', linewidth=2, markersize=8, label='Mean download time')
    plt.fill_between(jitter_values, lower_bounds, upper_bounds, alpha=0.3, color='red', label='90% Confidence Interval')

    plt.xlabel('Delay Jitter (ms)', fontsize=12)
    plt.ylabel('Download Time (seconds)', fontsize=12)
    plt.title('Download Time vs Delay Jitter\n(Loss=1%, Base Delay=20ms)', fontsize=14)
    plt.grid(True, alpha=0.3)
    plt.legend(fontsize=10)
    plt.tight_layout()

    # Save plot
    plt.savefig('plot_jitter_experiment.png', dpi=300, bbox_inches='tight')
    print("Saved plot: plot_jitter_experiment.png")

    # Print statistics
    print("\nJitter Experiment Results:")
    print("-" * 60)
    print(f"{'Jitter (ms)':<12} {'Mean (s)':<12} {'90% CI':<25} {'N':<5}")
    print("-" * 60)
    for i, jitter in enumerate(jitter_values):
        ci_str = f"[{lower_bounds[i]:.2f}, {upper_bounds[i]:.2f}]"
        n = len(df[df['jitter'] == jitter])
        print(f"{jitter:<12} {means[i]:<12.2f} {ci_str:<25} {n:<5}")
    print("-" * 60)

    plt.show()

def verify_data_integrity(csv_file, expected_md5=None):
    """Verify that all transfers completed successfully with correct MD5"""
    try:
        df = pd.read_csv(csv_file)
    except FileNotFoundError:
        print(f"Error: {csv_file} not found")
        return

    print(f"\nData Integrity Check for {csv_file}")
    print("-" * 60)

    if 'md5_hash' in df.columns:
        unique_hashes = df['md5_hash'].unique()
        print(f"Unique MD5 hashes found: {len(unique_hashes)}")

        for hash_val in unique_hashes:
            count = len(df[df['md5_hash'] == hash_val])
            print(f"  {hash_val}: {count} transfers")

        if len(unique_hashes) == 1:
            print("\n✓ All transfers have consistent MD5 hash")
        else:
            print("\n✗ WARNING: Multiple different MD5 hashes detected!")

    print(f"\nTotal transfers: {len(df)}")
    print(f"Mean download time: {df['ttc'].mean():.2f}s")
    print(f"Min download time: {df['ttc'].min():.2f}s")
    print(f"Max download time: {df['ttc'].max():.2f}s")
    print("-" * 60)

def main():
    """Main analysis function"""
    print("=" * 60)
    print("Part 1: Reliability Analysis and Plotting")
    print("=" * 60)

    # Analyze loss experiment
    loss_csv = "reliability_loss.csv"
    if sys.argv and len(sys.argv) > 1:
        loss_csv = sys.argv[1]

    verify_data_integrity(loss_csv)
    plot_loss_experiment(loss_csv)

    # Analyze jitter experiment
    jitter_csv = "reliability_jitter.csv"
    if sys.argv and len(sys.argv) > 2:
        jitter_csv = sys.argv[2]

    verify_data_integrity(jitter_csv)
    plot_jitter_experiment(jitter_csv)

    print("\n" + "=" * 60)
    print("Analysis complete! Check the generated PNG files.")
    print("=" * 60)

if __name__ == "__main__":
    main()
