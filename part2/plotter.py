import pandas as pd
import matplotlib.pyplot as plt
import sys
import os

def plot_bw_vs_util_jfi(csv_filename):
    """
    Reads the experiment CSV and plots Link Utilization and JFI 
    against Link Capacity (bw).
    """
    
    # --- 1. Load Data ---
    try:
        # Use skipinitialspace=True to handle spaces after commas
        data = pd.read_csv(csv_filename, skipinitialspace=True)
        
        # --- FIX ---
        # Strip any leading/trailing whitespace from column names
        # This corrects issues like 'jfi ' being read instead of 'jfi'
        data.columns = data.columns.str.strip()
        
    except FileNotFoundError:
        print(f"Error: The file '{csv_filename}' was not found.")
        sys.exit(1)
    except Exception as e:
        print(f"Error reading CSV file: {e}")
        sys.exit(1)

    # --- 2. Process Data ---
    # The experiment might have multiple iterations (iter) for each bandwidth.
    # We should average the results for 'link_util' and 'jfi' for each 'bw'.
    
    try:
        # Group by 'bw' and calculate the mean for 'link_util' and 'jfi'
        avg_data = data.groupby('bw')[['link_util', 'jfi']].mean().reset_index()
    except KeyError as e:
        print(f"Error: Missing expected column {e} in the CSV file.")
        print("Please ensure your CSV has 'bw', 'link_util', and 'jfi' columns.")
        sys.exit(1)

    print("Averaged Data:")
    print(avg_data)

    # --- 3. Create Plot ---
    fig, ax = plt.subplots(figsize=(10, 6))

    # Plot Link Utilization
    ax.plot(avg_data['bw'], avg_data['link_util'], 'o-', 
            label='Average Link Utilization', color='tab:blue', linewidth=2)
    
    # Plot Jain Fairness Index (JFI)
    ax.plot(avg_data['bw'], avg_data['jfi'], 's--', 
            label='Average Jain Fairness Index (JFI)', color='tab:red', linewidth=2)

    # --- 4. Style Plot ---
    ax.set_title('Link Utilization and Fairness vs. Link Capacity')
    ax.set_xlabel('Link Capacity (Mbps)')
    ax.set_ylabel('Metric Value')
    
    # Set Y-axis limits from 0 to 1.1 (since both are 0-1 metrics)
    ax.set_ylim(0, 1.1)
    
    # Set X-axis ticks to match the data points
    ax.set_xticks(avg_data['bw'])
    
    ax.legend()
    ax.grid(True, which='both', linestyle='--', linewidth=0.5)
    
    plt.tight_layout()

    # --- 5. Save and Show ---
    # Create an output filename based on the input CSV name
    output_filename = os.path.splitext(csv_filename)[0] + '.png'
    
    try:
        plt.savefig(output_filename)
        print(f"\nPlot saved successfully to: {output_filename}")
        plt.show()
    except Exception as e:
        print(f"Error saving plot: {e}")

def plot_cwnd_with_time(filename):
    """
    mainly for understanding how the network changes
    """

    try:
        data = pd.read_csv(filename)
    except FileNotFoundError:
        print(f"File was not found")
    
    fig, ax = plt.subplots(figsize=(10, 6))

    # Plot Link Utilization
    ax.plot(data['timestamp_s'], data['cwnd_bytes'], 'o-', label='cwnd window', color='tab:blue', linewidth=2)
    
    # Plot Jain Fairness Index (JFI)
    ax.plot(data['timestamp_s'], data['ssthresh_bytes'], 's--', label='threshold', color='tab:red', linewidth=2)

    # --- 4. Style Plot ---
    ax.set_title('Cong. W and Thres. vs time')
    ax.set_xlabel('time')
    ax.set_ylabel('Bytes')
    
    ax.legend()
    ax.grid(True, which='both', linestyle='--', linewidth=0.5)
    
    plt.tight_layout()

    # --- 5. Save and Show ---
    # Create an output filename based on the input CSV name
    output_filename = os.path.splitext(filename)[0] + '.png'
    try:
        plt.savefig(output_filename)
        print(f"\nPlot saved successfully to: {output_filename}")
        plt.show()
    except Exception as e:
        print(f"Error saving plot: {e}")

def main():
    if len(sys.argv) != 2:
        print(f"Usage: python3 {sys.argv[0]} <path_to_csv_file>")
        print("Example: python3 plot_bandwidth_experiment.py p2_fairness_fixed_bandwidth.csv")
        sys.exit(1)
        
    csv_filename = sys.argv[1]
    if 'fixed_bandwidth' in csv_filename:
        plot_bw_vs_util_jfi(csv_filename)
    if 'cwnd' in csv_filename:
        plot_cwnd_with_time(csv_filename)

if __name__ == "__main__":
    main()

