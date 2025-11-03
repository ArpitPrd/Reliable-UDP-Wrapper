# Complete Step-by-Step Instructions for Part 1

## What You Need to Know

**What is this assignment about?**
You're implementing a reliable file transfer system that works over UDP (an unreliable network protocol). Think of it like this:
- UDP is like shouting messages across a noisy room - some might get lost or arrive out of order
- Your job is to add a "reliability layer" that ensures all messages arrive correctly, in order
- This is similar to how TCP works, but you're building it yourself!

**What files have been provided to you?**
1. `p1_server.py` - The sender program (sends the file)
2. `p1_client.py` - The receiver program (receives the file)
3. `p1_exp.py` - Experiment runner (tests your protocol in Mininet)
4. `analyze_results.py` - Analysis script (creates plots from experiment data)
5. `test_local.py` - Local testing script (test without Mininet)
6. `data.txt` - The file to transfer (6.46 MB)
7. `REPORT_PART1.md` - Report template

---

## Phase 1: Local Testing (No Mininet Required)

**Purpose:** Verify your implementation works on your computer before running complex network experiments.

### Step 1.1: Install Required Software

Open a terminal/command prompt and run:

```bash
# Install Python packages for plotting
pip install pandas matplotlib scipy numpy
```

### Step 1.2: Verify Files Are Present

Make sure you're in the correct directory:

```bash
cd C:\Users\aksha\Downloads\col334-assignment4-main
```

List files to confirm everything is there:

```bash
dir  # On Windows
# or
ls   # On Linux/Mac
```

You should see: `p1_server.py`, `p1_client.py`, `data.txt`, etc.

### Step 1.3: Run Local Test

This test runs the file transfer on your local machine without any network simulation:

```bash
python test_local.py
```

**What happens:**
1. The script starts a server in the background
2. The script starts a client that connects to the server
3. The file `data.txt` is transferred from server to client
4. The file is saved as `received_data.txt`
5. MD5 hashes are compared to verify correctness

**Expected output:**
```
Local Test: Reliable UDP File Transfer
[1] Starting server...
[2] Starting client...

Test Results
Original file MD5:  [some hash]
Received file MD5:  [same hash]
Transfer time:      XX.XX seconds

✓ SUCCESS: File transferred correctly!
File size:          6,463,538 bytes (6.16 MB)
Throughput:         X.XX Mbps
```

**If you see SUCCESS:** Your implementation works! Proceed to Phase 2.

**If you see FAILURE:**
- Check that both files are in the same directory
- Make sure no other program is using port 6555
- Check the error messages for debugging hints

---

## Phase 2: Mininet Experiments (Network Simulation)

**Purpose:** Test your protocol under realistic network conditions (packet loss, delay, jitter).

### Step 2.1: Mininet Setup

**What is Mininet?**
Mininet is a network simulator that creates virtual networks on your computer. You'll need:
- Linux environment (Ubuntu recommended) or Linux VM
- Root/sudo access

**Install Mininet and Ryu Controller:**

```bash
# Install Mininet
sudo apt-get update
sudo apt-get install mininet

# Install Ryu controller (SDN controller)
sudo pip install ryu

# Verify installations
sudo mn --version
ryu-manager --version
```

### Step 2.2: Start Ryu Controller

**What is Ryu?**
Ryu is a controller that manages the virtual network switch in Mininet.

**Open a NEW terminal** and run:

```bash
ryu-manager ryu.app.simple_switch
```

**Expected output:**
```
loading app ryu.app.simple_switch
instantiating app ryu.app.simple_switch
...
(ready to accept connections)
```

**Keep this terminal open** - the controller must run during all experiments.

### Step 2.3: Run Loss Experiment

**What does this test?**
Tests how your protocol handles packet loss (1% to 5% of packets randomly dropped).

**In a NEW terminal** (while Ryu is still running), navigate to your directory:

```bash
cd /path/to/col334-assignment4-main
```

Run the experiment:

```bash
sudo python3 p1_exp.py loss
```

**What happens:**
1. Mininet creates a virtual network with 2 hosts and 1 switch
2. For each loss rate (1%, 2%, 3%, 4%, 5%):
   - Runs the transfer 5 times
   - Measures time to completion
   - Verifies MD5 hash
3. Results saved to `reliability_loss.csv`

**Duration:** Approximately 15-25 minutes (5 loss rates × 5 iterations × ~1 minute each)

**Expected output:**
```
--- Running topology with 1% packet loss, base delay 20ms and jitter 0ms (iter 1/5)
Server started on 10.0.0.1:6555 with SWS=5900
Client connecting to 10.0.0.1:6555
...
File transfer complete!
...
--- Completed all tests ---
```

**Output file:** `reliability_loss.csv`

### Step 2.4: Run Jitter Experiment

**What does this test?**
Tests how your protocol handles variable delays (jitter from 20ms to 100ms).

Run the experiment:

```bash
sudo python3 p1_exp.py jitter
```

**What happens:**
1. For each jitter value (20, 40, 60, 80, 100 ms):
   - Runs the transfer 5 times with 1% loss and 20ms base delay
   - Measures time to completion
2. Results saved to `reliability_jitter.csv`

**Duration:** Approximately 15-25 minutes

**Output file:** `reliability_jitter.csv`

### Step 2.5: Stop Ryu Controller

After experiments complete, go to the Ryu terminal and press `Ctrl+C` to stop it.

---

## Phase 3: Analysis and Plotting

**Purpose:** Generate plots and statistics from experimental data.

### Step 3.1: Generate Plots

Run the analysis script:

```bash
python analyze_results.py
```

**What happens:**
1. Reads `reliability_loss.csv` and `reliability_jitter.csv`
2. Calculates mean and 90% confidence intervals
3. Generates two plots:
   - `plot_loss_experiment.png` - Download time vs packet loss
   - `plot_jitter_experiment.png` - Download time vs jitter
4. Displays statistical summary

**Expected output:**
```
Part 1: Reliability Analysis and Plotting

Analyzing loss experiment...
Saved plot: plot_loss_experiment.png

Loss Experiment Results:
Loss %     Mean (s)     90% CI                    N
1          XX.XX        [XX.XX, XX.XX]           5
2          XX.XX        [XX.XX, XX.XX]           5
...

Analyzing jitter experiment...
Saved plot: plot_jitter_experiment.png
...
```

### Step 3.2: Review Plots

Open the generated PNG files:

**plot_loss_experiment.png:**
- Shows how download time increases with packet loss
- Blue line = average time
- Blue shaded area = confidence interval (uncertainty range)

**plot_jitter_experiment.png:**
- Shows how download time increases with delay variability
- Red line = average time
- Red shaded area = confidence interval

### Step 3.3: Verify Performance Targets

Compare your results with targets from `part1.txt`:

**Loss Experiment Targets:**
| Loss % | Target Time (s) | Your Time (s) | Status |
|--------|----------------|---------------|--------|
| 1      | 53             | ?             | ?      |
| 2      | 58             | ?             | ?      |
| 3      | 63             | ?             | ?      |
| 4      | 68             | ?             | ?      |
| 5      | 77             | ?             | ?      |

**Jitter Experiment Targets:**
| Jitter (ms) | Target Time (s) | Your Time (s) | Status |
|-------------|----------------|---------------|--------|
| 20          | 55             | ?             | ?      |
| 40          | 64             | ?             | ?      |
| 60          | 77             | ?             | ?      |
| 80          | 92             | ?             | ?      |
| 100         | 103            | ?             | ?      |

**How to read:**
- If your time is **below** the target: Excellent! ✓
- If your time is **slightly above** target (5-10%): Acceptable
- If your time is **much above** target (>20%): May need optimization

---

## Phase 4: Complete the Report

### Step 4.1: Add Experimental Data to Report

Open `REPORT_PART1.md` in a text editor.

In **Section 4.3 (Performance Targets)**, add a comparison table:

```markdown
### Actual Results vs Targets

| Experiment | Parameter | Target (s) | Achieved (s) | Difference |
|------------|-----------|-----------|--------------|------------|
| Loss       | 1%        | 53        | XX.XX        | +X.X%      |
| Loss       | 2%        | 58        | XX.XX        | +X.X%      |
...
```

Copy the mean times from the analysis output.

### Step 4.2: Add Observations

In **Section 4.2 (Expected Results and Observations)**, replace "Expected" with actual observations:

**For Loss Experiment, write:**
- Did download time increase linearly or exponentially with loss?
- Were SACK and fast retransmit effective? (check server logs for retransmission counts)
- How did RTO values change during transfer?

**For Jitter Experiment, write:**
- How did jitter affect performance compared to loss?
- Were there more timeouts at high jitter?
- Did confidence intervals widen with more jitter?

**Example observation:**
```markdown
**Loss Experiment Observations:**
Download time increased approximately linearly from 52s (1% loss) to 75s (5% loss),
indicating effective loss recovery mechanisms. Fast retransmit handled ~80% of losses,
with only ~20% requiring timeout-based retransmission. SACK reduced redundant
retransmissions by approximately 30% compared to cumulative ACKs alone.
```

### Step 4.3: Include Plots in Report

Add the plots to the report:

```markdown
### 5.1 Plot 1: Download Time vs Packet Loss Rate

![Loss Experiment](plot_loss_experiment.png)

*Figure 1: Download time increases with packet loss rate. The protocol maintains
near-linear degradation due to efficient SACK and fast retransmit mechanisms.*

### 5.2 Plot 2: Download Time vs Delay Jitter

![Jitter Experiment](plot_jitter_experiment.png)

*Figure 2: Download time increases with delay jitter. Adaptive RTO helps mitigate
the effects, but higher jitter leads to more conservative timeouts.*
```

---

## Phase 5: Submission Checklist

### Required Files for Submission:

- [ ] `p1_server.py` - Server implementation
- [ ] `p1_client.py` - Client implementation
- [ ] `REPORT_PART1.md` - Report with observations and results (max 2 pages)
- [ ] `plot_loss_experiment.png` - Loss experiment plot
- [ ] `plot_jitter_experiment.png` - Jitter experiment plot

### Optional Files (for your reference):

- [ ] `reliability_loss.csv` - Raw loss experiment data
- [ ] `reliability_jitter.csv` - Raw jitter experiment data
- [ ] `analyze_results.py` - Analysis script
- [ ] `test_local.py` - Local testing script

---

## Troubleshooting Common Issues

### Issue 1: "Address already in use" error

**Cause:** Another program (or previous server instance) is using port 6555.

**Solution:**
```bash
# Find and kill process using the port
# On Linux:
sudo netstat -tulpn | grep 6555
sudo kill -9 [PID]

# On Windows:
netstat -ano | findstr 6555
taskkill /PID [PID] /F
```

### Issue 2: Mininet experiments fail to start

**Cause:** Ryu controller not running or Mininet not properly installed.

**Solution:**
1. Verify Ryu is running: Check the terminal where you started Ryu
2. Check Mininet installation: `sudo mn --test pingall`
3. Clean up old Mininet state: `sudo mn -c`

### Issue 3: File transfer times out

**Cause:** Protocol implementation issue or network configuration problem.

**Solution:**
1. Test locally first: `python test_local.py`
2. Check server logs for errors
3. Increase RTO if seeing many spurious retransmissions
4. Verify window size is appropriate (5900 bytes should work)

### Issue 4: MD5 hashes don't match

**Cause:** Data corruption during transfer.

**Solution:**
1. Check for bugs in packet parsing (sequence number, data extraction)
2. Verify in-order delivery logic in client
3. Check EOF handling (shouldn't write EOF to file)
4. Add debug logging to track sequence numbers

### Issue 5: Plots don't generate

**Cause:** Missing Python packages or incorrect CSV format.

**Solution:**
```bash
# Install/reinstall packages
pip install --upgrade pandas matplotlib scipy numpy

# Check CSV file format
head reliability_loss.csv
# Should show: iteration,loss,delay,jitter,md5_hash,ttc
```

---

## Understanding Your Protocol

### How It Works (Simple Explanation):

1. **Server (Sender):**
   - Breaks file into chunks of 1180 bytes each
   - Sends multiple chunks without waiting (up to window size)
   - Waits for acknowledgments (ACKs) from client
   - If no ACK arrives in time → resends the chunk (timeout)
   - If receives 3 duplicate ACKs → resends immediately (fast retransmit)
   - Adjusts timeout dynamically based on network speed

2. **Client (Receiver):**
   - Receives chunks and checks sequence numbers
   - If chunk is in correct order → writes to file immediately
   - If chunk is out of order → saves it temporarily
   - Sends ACKs telling server which chunks were received
   - Uses SACK to tell server about out-of-order chunks

3. **Why This Works:**
   - **Window**: Multiple chunks in-flight → faster transfer
   - **SACK**: Server knows what to resend → fewer duplicates
   - **Fast retransmit**: Quick recovery from loss → less waiting
   - **Adaptive timeout**: Adjusts to network conditions → balanced performance

---

## Performance Optimization Tips

If your results don't meet targets, try these optimizations:

### 1. Increase Window Size
```python
# In p1_exp.py, line 58:
SWS = 10 * 1180  # Try 10 packets instead of 5
```

### 2. Tune RTO Parameters
```python
# In p1_server.py, adjust:
MIN_RTO = 0.15  # Lower minimum (was 0.2)
ALPHA = 0.1     # Faster RTT adaptation (was 0.125)
```

### 3. More Aggressive Fast Retransmit
```python
# In p1_server.py:
DUP_ACK_THRESHOLD = 2  # Trigger after 2 dup ACKs (was 3)
```

### 4. Reduce ACK Delay
```python
# In p1_client.py, line 11:
ACK_DELAY = 0.0001  # Faster ACKs (was 0.001)
```

**Important:** Only change these if you understand the trade-offs!

---

## Key Concepts Explained

### What is a Sequence Number?
- A number that identifies the position of data in the file
- Example: Seq=0 means "this is the first byte", Seq=1180 means "this starts at byte 1180"

### What is an ACK (Acknowledgment)?
- A message from receiver to sender saying "I got data up to position X"
- Example: ACK=2360 means "I received everything before byte 2360, send me 2360 next"

### What is SACK (Selective Acknowledgment)?
- Extra information in ACK saying "I also have these other chunks"
- Example: ACK=1180, SACK=[2360-3540] means "I have 0-1179 and 2360-3539, but I'm missing 1180-2359"

### What is RTO (Retransmission Timeout)?
- How long to wait before assuming a packet was lost
- Too short → resend too early (waste bandwidth)
- Too long → wait unnecessarily (slow transfer)

### What is a Sliding Window?
- A limit on how much data can be "in flight" (sent but not acknowledged)
- Slides forward as ACKs arrive
- Enables pipeline: send multiple packets without waiting

---

## Questions and Answers

**Q: How long will experiments take?**
A: Each experiment (loss or jitter) takes 15-25 minutes. Total: ~30-50 minutes.

**Q: Do I need to modify the code?**
A: No! The provided implementation should work. Only modify if optimizing performance.

**Q: What if my times are slightly above targets?**
A: That's okay! Targets are guidelines. Focus on correctness first, then optimize.

**Q: Can I run experiments on Windows?**
A: Mininet requires Linux. Use a Linux VM (VirtualBox, VMware, WSL2) or cloud instance.

**Q: How do I know if my protocol is working correctly?**
A: Check MD5 hashes match in all experiments. If they match, data is correct!

**Q: What makes a protocol "efficient"?**
A: Fewer retransmissions, lower latency, higher throughput, better resource usage.

---

## Summary of Steps

1. ✓ **Local Test:** `python test_local.py` → Verify correctness
2. ✓ **Start Ryu:** `ryu-manager ryu.app.simple_switch` → Keep running
3. ✓ **Loss Exp:** `sudo python3 p1_exp.py loss` → 15-25 min
4. ✓ **Jitter Exp:** `sudo python3 p1_exp.py jitter` → 15-25 min
5. ✓ **Analysis:** `python analyze_results.py` → Generate plots
6. ✓ **Report:** Edit `REPORT_PART1.md` → Add observations
7. ✓ **Submit:** Server, client, report, and 2 plots

---

**You're all set!** Follow these steps in order, and you'll successfully complete Part 1.

Good luck with your assignment! 🚀
