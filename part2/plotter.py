import pandas as pd
import matplotlib.pyplot as plt
import sys
import os
import numpy as np

def load_data(csv_filename, required_cols):
    """
    Helper function to load and preprocess CSV data.
    """
    try:
        # Use skipinitialspace=True to handle spaces after commas
        data = pd.read_csv(csv_filename, skipinitialspace=True)
        
        # Strip any leading/trailing whitespace from column names
        data.columns = data.columns.str.strip()
        
    except FileNotFoundError:
        print(f"Error: The file '{csv_filename}' was not found.")
        sys.exit(1)
    except Exception as e:
        print(f"Error reading CSV file: {e}")
        sys.exit(1)

    # Check for required columns
    missing_cols = [col for col in required_cols if col not in data.columns]
    if missing_cols:
        print(f"Error: Missing expected columns: {missing_cols}")
        print(f"Available columns are: {list(data.columns)}")
        sys.exit(1)
        
    return data

def save_plot(fig, csv_filename, plot_suffix=''):
    """
    Helper function to save the plot.
    """
    # Create an output filename based on the input CSV name
    base_name = os.path.splitext(csv_filename)[0]
    output_filename = f"{base_name}{plot_suffix}.png"
    
    try:
        fig.savefig(output_filename)
        print(f"\nPlot saved successfully to: {output_filename}")
    except Exception as e:
        print(f"Error saving plot: {e}")

def plot_bw_vs_util_jfi(csv_filename):
    """
    Reads the 'fixed_bandwidth' experiment CSV and plots 
    Link Utilization and JFI against Link Capacity (bw).
    """
    # --- 1. Load Data ---
    required_cols = ['bw', 'link_util', 'jfi']
    data = load_data(csv_filename, required_cols)

    # --- 2. Process Data ---
    # Group by 'bw' and calculate the mean for 'link_util' and 'jfi'
    avg_data = data.groupby('bw')[['link_util', 'jfi']].mean().reset_index()

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
    ax.set_ylim(0, 1.1)
    ax.set_xticks(avg_data['bw'])
    ax.legend()
    ax.grid(True, which='both', linestyle='--', linewidth=0.5)
    
    plt.tight_layout()

    # --- 5. Save ---
    save_plot(fig, csv_filename)

def plot_loss_vs_util(csv_filename):
    """
    Reads the 'varying_loss' experiment CSV and plots 
    Link Utilization against Loss Rate.
    """
    # --- 1. Load Data ---
    required_cols = ['loss', 'link_util']
    data = load_data(csv_filename, required_cols)

    # --- 2. Process Data ---
    # Group by 'loss' and calculate the mean for 'link_util'
    avg_data = data.groupby('loss')[['link_util']].mean().reset_index()

    print("Averaged Data:")
    print(avg_data)

    # --- 3. Create Plot ---
    fig, ax = plt.subplots(figsize=(10, 6))

    # Plot Link Utilization
    ax.plot(avg_data['loss'], avg_data['link_util'], 'o-', 
            label='Average Link Utilization', color='tab:blue', linewidth=2)

    # --- 4. Style Plot ---
    ax.set_title('Link Utilization vs. Packet Loss Rate')
    ax.set_xlabel('Loss Rate (%)')
    ax.set_ylabel('Average Link Utilization')
    ax.set_ylim(0, 1.1)
    ax.set_xticks(avg_data['loss'])
    ax.legend()
    ax.grid(True, which='both', linestyle='--', linewidth=0.5)
    
    plt.tight_layout()

    # --- 5. Save ---
    save_plot(fig, csv_filename)

def plot_delay_vs_jfi(csv_filename):
    """
    Reads the 'asymmetric_flows' experiment CSV and plots 
    JFI against Asymmetric Delay.
    """
    # --- 1. Load Data ---
    required_cols = ['delay_c2_ms', 'jfi']
    data = load_data(csv_filename, required_cols)

    # --- 2. Process Data ---
    # Group by 'delay_c2_ms' and calculate the mean for 'jfi'
    avg_data = data.groupby('delay_c2_ms')[['jfi']].mean().reset_index()

    print("Averaged Data:")
    print(avg_data)

    # --- 3. Create Plot ---
    fig, ax = plt.subplots(figsize=(10, 6))

    # Plot JFI
    ax.plot(avg_data['delay_c2_ms'], avg_data['jfi'], 's--', 
            label='Average Jain Fairness Index (JFI)', color='tab:red', linewidth=2)

    # --- 4. Style Plot ---
    ax.set_title('Fairness (JFI) vs. Asymmetric RTT')
    ax.set_xlabel('Asymmetric Delay (ms) for Flow 2')
    ax.set_ylabel('Average Jain Fairness Index (JFI)')
    ax.set_ylim(0, 1.1)
    ax.set_xticks(avg_data['delay_c2_ms'])
    ax.legend()
    ax.grid(True, which='both', linestyle='--', linewidth=0.5)
    
    plt.tight_layout()

    # --- 5. Save ---
    save_plot(fig, csv_filename)

def plot_udp_vs_util_jfi(csv_filename):
    """
    Reads the 'background_udp' experiment CSV and plots 
    Link Utilization and JFI as a bar chart.
    """
    # --- 1. Load Data ---
    required_cols = ['udp_off_mean', 'link_util', 'jfi']
    data = load_data(csv_filename, required_cols)

    # --- 2. Process Data ---
    # Group by 'udp_off_mean' and calculate the mean for 'link_util' and 'jfi'
    avg_data = data.groupby('udp_off_mean')[['link_util', 'jfi']].mean().reset_index()
    
    # Map 'udp_off_mean' to categorical labels
    # (Based on p2_exp.py: 1.5 is light, 0.5 is heavy)
    label_map = {
        1.5: 'Light\n(1.5s off)',
        0.8: 'Medium\n(0.8s off)',
        0.5: 'Heavy\n(0.5s off)'
    }
    avg_data['condition'] = avg_data['udp_off_mean'].map(label_map)
    
    # Sort by 'udp_off_mean' descending to get "Light, Medium, Heavy" order
    avg_data = avg_data.sort_values(by='udp_off_mean', ascending=False)
    
    if avg_data.empty or avg_data['condition'].isnull().any():
        print("Error: Could not map 'udp_off_mean' values to conditions.")
        print("Expected values are 1.5, 0.8, 0.5. Check your CSV.")
        sys.exit(1)

    print("Averaged Data:")
    print(avg_data)

    # --- 3. Create Plot ---
    labels = avg_data['condition']
    x = np.arange(len(labels))  # the label locations
    width = 0.35  # the width of the bars

    fig, ax = plt.subplots(figsize=(10, 6))
    
    rects1 = ax.bar(x - width/2, avg_data['link_util'], width, 
                    label='Link Utilization', color='tab:blue')
    rects2 = ax.bar(x + width/2, avg_data['jfi'], width, 
                    label='JFI', color='tab:red')

    # --- 4. Style Plot ---
    ax.set_ylabel('Metric Value')
    ax.set_title('Performance vs. Background UDP Load')
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylim(0, 1.1)
    ax.legend()
    ax.grid(True, which='major', axis='y', linestyle='--', linewidth=0.5)
    
    # Add bar labels
    ax.bar_label(rects1, padding=3, fmt='%.3f')
    ax.bar_label(rects2, padding=3, fmt='%.3f')
    
    plt.tight_layout()

    # --- 5. Save ---
    save_plot(fig, csv_filename)

def plot_cwnd_with_time(filename):
    """
    Plots cwnd and ssthresh over time from a cwnd log CSV.
    """
    # --- 1. Load Data ---
    required_cols = ['timestamp_s', 'cwnd_bytes', 'ssthresh_bytes']
    data = load_data(filename, required_cols)
        
    # --- 2. Process Data ---
    # Convert bytes to KiloBytes for readability
    data['cwnd_KB'] = data['cwnd_bytes'] / 1024.0
    # Handle potential large ssthresh values (set to NaN so they don't plot)
    data['ssthresh_KB'] = data['ssthresh_bytes'].apply(
        lambda x: x / 1024.0 if x < 2**30 else float('nan') 
    )
    
    # --- 3. Create Plot ---
    fig, ax = plt.subplots(figsize=(12, 6)) # Made plot wider

    # Plot cwnd 
    ax.plot(data['timestamp_s'], data['cwnd_KB'], 
            label='cwnd (KB)', color='tab:blue', linewidth=1.5, drawstyle='steps-post')
    
    # Plot ssthresh
    ax.plot(data['timestamp_s'], data['ssthresh_KB'], '--', 
            label='ssthresh (KB)', color='tab:red', linewidth=1.5, drawstyle='steps-post')

    # --- 4. Style Plot ---
    ax.set_title('Congestion Window (cwnd) vs. Time')
    ax.set_xlabel('Time (s)')
    ax.set_ylabel('Window Size (KB)')
    ax.set_ylim(bottom=0)
    ax.set_xlim(left=0)
    ax.legend()
    ax.grid(True, which='both', linestyle='--', linewidth=0.5)
    
    plt.tight_layout()

    # --- 5. Save ---
    save_plot(fig, filename, plot_suffix='_cwnd')


def main():
    if len(sys.argv) != 2:
        print(f"Usage: python3 {sys.argv[0]} <path_to_csv_file>")
        print("\nExamples:")
        print("  python3 plotter.py p2_fairness_fixed_bandwidth.csv")
        print("  python3 plotter.py p2_fairness_varying_loss.csv")
        print("  python3 plotter.py p2_fairness_asymmetric_flows.csv")
        print("  python3 plotter.py p2_fairness_background_udp.csv")
        print("  python3 plotter.py cwnd_log_6556.csv")
        sys.exit(1)
        
    csv_filename = sys.argv[1]
    
    # Logic to call the correct plotting function
    if 'fixed_bandwidth' in csv_filename:
        plot_bw_vs_util_jfi(csv_filename)
    elif 'varying_loss' in csv_filename:
        plot_loss_vs_util(csv_filename)
    elif 'asymmetric_flows' in csv_filename:
        plot_delay_vs_jfi(csv_filename)
    elif 'background_udp' in csv_filename:
        plot_udp_vs_util_jfi(csv_filename)
    elif 'cwnd' in csv_filename:
        plot_cwnd_with_time(csv_filename)
    else:
        print(f"Error: Don't know how to plot '{csv_filename}'.")
        print("Filename must contain one of: 'fixed_bandwidth', 'varying_loss', 'asymmetric_flows', 'background_udp', or 'cwnd'.")
        sys.exit(1)

if __name__ == "__main__":
    main()