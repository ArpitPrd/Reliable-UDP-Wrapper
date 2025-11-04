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
        # plt.show() # Removed plt.show()
    except Exception as e:
        print(f"Error saving plot: {e}")

def plot_cwnd_with_time(filename):
    """
    Plots cwnd and ssthresh over time from a cwnd log CSV.
    """

    try:
        data = pd.read_csv(filename)
    except FileNotFoundError:
        print(f"Error: The file '{filename}' was not found.")
        sys.exit(1)
    except Exception as e:
        print(f"Error reading CSV file: {e}")
        sys.exit(1)
        
    # Convert bytes to KiloBytes for readability
    data['cwnd_KB'] = data['cwnd_bytes'] / 1024.0
    # Handle potential large ssthresh values
    data['ssthresh_KB'] = data['ssthresh_bytes'].apply(
        lambda x: x / 1024.0 if x < 2**30 else float('nan') # Set very large values to NaN
    )
    
    fig, ax = plt.subplots(figsize=(12, 6)) # Made plot wider

    # Plot cwnd (no markers, just a line)
    ax.plot(data['timestamp_s'], data['cwnd_KB'], '-', 
            label='cwnd (KB)', color='tab:blue', linewidth=1.5)
    
    # Plot ssthresh (no markers, just a dashed line)
    ax.plot(data['timestamp_s'], data['ssthresh_KB'], '--', 
            label='ssthresh (KB)', color='tab:red', linewidth=1.5)

    # --- 4. Style Plot ---
    ax.set_title('Congestion Window (cwnd) vs. Time')
    ax.set_xlabel('Time (s)')
    ax.set_ylabel('Window Size (KB)')
    
    # Set y-axis to start from 0
    ax.set_ylim(bottom=0)
    # Set x-axis to start from 0
    ax.set_xlim(left=0)
    
    ax.legend()
    ax.grid(True, which='both', linestyle='--', linewidth=0.5)
    
    plt.tight_layout()

    # --- 5. Save and Show ---
    # Create an output filename based on the input CSV name
    output_filename = os.path.splitext(filename)[0] + '.png'
    try:
        plt.savefig(output_filename)
        print(f"\nPlot saved successfully to: {output_filename}")
        # plt.show() # Removed plt.show()
    except Exception as e:
        print(f"Error saving plot: {e}")

def main():
    if len(sys.argv) != 2:
        print(f"Usage: python3 {sys.argv[0]} <path_to_csv_file>")
        print("Example: python3 plotter.py p2_fairness_fixed_bandwidth.csv")
        print("Example: python3 plotter.py cwnd_log_6556.csv")
        sys.exit(1)
        
    csv_filename = sys.argv[1]
    
    # Logic to call the correct plotting function
    if 'fixed_bandwidth' in csv_filename:
        plot_bw_vs_util_jfi(csv_filename)
    elif 'cwnd' in csv_filename:
        plot_cwnd_with_time(csv_filename)
    else:
        print(f"Error: Don't know how to plot '{csv_filename}'.")
        print("Filename must contain 'fixed_bandwidth' or 'cwnd'.")
        sys.exit(1)

if __name__ == "__main__":
    main()
