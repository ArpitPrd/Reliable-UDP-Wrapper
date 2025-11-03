# Complete Step-by-Step Instructions for Part 2

## What Part 2 Adds

Part 2 builds on Part 1 by adding **congestion control**—the mechanism that prevents network congestion and ensures fair bandwidth sharing.

**Key Difference from Part 1:**
- Part 1: Fixed sender window size (SWS)
- Part 2: Dynamic congestion window (cwnd) that adapts to network conditions

**What is Congestion Control?**
Think of a highway:
- **Without congestion control**: Everyone drives as fast as possible → traffic jam
- **With congestion control**: Everyone adjusts speed based on traffic → smooth flow

Your protocol will:
- Start slow (1 packet in-flight)
- Ramp up quickly when bandwidth is available (exponential growth)
- Slow down when congestion is detected (multiplicative decrease)
- Share bandwidth fairly with other flows

---

## Phase 1: Local Testing

### Step 1.1: Verify Part 1 Still Works

Part 2 code is separate from Part 1, but let's make sure everything is set up:

```bash
cd C:\Users\aksha\Downloads\col334-assignment4-main
```

### Step 1.2: Run Part 2 Local Tests

```bash
python test_local_p2.py
```

**What this does:**
1. **Test 1**: Single flow transfer (verifies basic functionality)
2. **Test 2**: Two concurrent flows (basic fairness test)

**Expected output:**
```
Test 1: Single Flow
✓ SUCCESS: File transferred correctly!
Throughput: X.XX Mbps

Test 2: Dual Flow (Fairness)
Flow 1: ✓ MD5 match
Flow 2: ✓ MD5 match
Jain's Fairness Index: 0.XXX
(Closer to 1.0 is better)
```

**If successful**: Your implementation works! Proceed to Mininet experiments.

---

## Phase 2: Mininet Experiments

Part 2 has **4 experiments** instead of 2. Each tests a different aspect of congestion control.

### Prerequisites

Same as Part 1:
- Linux environment
- Mininet installed
- Ryu controller installed

### Step 2.1: Start Ryu Controller

**Open Terminal 1** and keep it running:

```bash
ryu-manager ryu.app.simple_switch
```

Leave this running for ALL experiments!

### Step 2.2: Experiment 1 - Fixed Bandwidth

**Objective**: Test how your protocol scales across different link speeds

**In Terminal 2:**

```bash
cd /path/to/col334-assignment4-main
sudo python3 p2_exp.py fixed_bandwidth
```

**What it tests:**
- Bandwidth from 100 Mbps to 1000 Mbps (10 different speeds)
- Two flows competing for bandwidth
- Measures utilization and fairness

**Duration**: ~15-20 minutes

**Output file**: `p2_fairness_fixed_bandwidth.csv`

**What to watch for:**
- Progress messages showing both clients
- cwnd values increasing (visible in logs)
- "Completed experiments" message at end

### Step 2.3: Experiment 2 - Varying Loss

**Objective**: Test robustness to packet loss

```bash
sudo python3 p2_exp.py varying_loss
```

**What it tests:**
- Loss rates from 0% to 2% (5 different rates)
- How congestion control handles packet loss
- Measures link utilization

**Duration**: ~10-15 minutes

**Output file**: `p2_fairness_varying_loss.csv`

### Step 2.4: Experiment 3 - Asymmetric Flows

**Objective**: Test fairness when flows have different delays (RTTs)

```bash
sudo python3 p2_exp.py asymmetric_flows
```

**What it tests:**
- Flow 1: Low delay (5ms)
- Flow 2: Varying delay (5ms to 25ms)
- Does low-delay flow dominate?
- Measures fairness (JFI)

**Duration**: ~10-15 minutes

**Output file**: `p2_fairness_asymmetric_flows.csv`

### Step 2.5: Experiment 4 - Background UDP

**Objective**: Test interaction with non-responsive (UDP) traffic

```bash
sudo python3 p2_exp.py background_udp
```

**What it tests:**
- Two TCP flows (your protocol) + bursty UDP traffic
- Light, Medium, Heavy UDP traffic
- How TCP "plays nice" with UDP
- Measures utilization and fairness

**Duration**: ~10-15 minutes

**Output file**: `p2_fairness_background_udp.csv`

**Note**: This also uses `udp_server.py` and `udp_client.py` which were provided.

### Step 2.6: Stop Ryu

After all experiments complete:
- Go to Terminal 1 (Ryu)
- Press `Ctrl+C`

---

## Phase 3: Analysis and Plotting

### Step 3.1: Analyze All Experiments

```bash
python analyze_results_p2.py
```

**What happens:**
1. Reads all 4 CSV files
2. Calculates statistics (mean, std deviation)
3. Generates 4 plots (one per experiment)
4. Displays results

**Expected output:**
```
Part 2: Congestion Control Analysis

Analyzing: fixed_bandwidth
✓ All transfers have consistent MD5 hashes
Saved: plot_p2_fixed_bandwidth.png
Results:
BW (Mbps)    Util       JFI        Performance
100          0.XXX      0.XXX      0.XXX
...

Analyzing: varying_loss
...
```

**Files created:**
- `plot_p2_fixed_bandwidth.png`
- `plot_p2_varying_loss.png`
- `plot_p2_asymmetric_flows.png`
- `plot_p2_background_udp.png`

### Step 3.2: Analyze Individual Experiment

You can also analyze a single experiment:

```bash
python analyze_results_p2.py fixed_bandwidth
```

This is useful if you re-run specific experiments.

### Step 3.3: Review Plots

Open each PNG file and understand what it shows:

#### Plot 1: Fixed Bandwidth
- **Type**: Dual y-axis line plot
- **Blue line**: Link utilization (efficiency)
- **Red line**: Jain Fairness Index (fairness)
- **Good result**: Both lines stay high (~0.9-1.0)

#### Plot 2: Varying Loss
- **Type**: Single line plot
- **Shows**: Link utilization vs packet loss rate
- **Good result**: Gradual decline (not steep drop)

#### Plot 3: Asymmetric Flows
- **Type**: Single line plot
- **Shows**: Fairness vs RTT difference
- **Expected**: Some decline (TCP Reno favors low-RTT flows)

#### Plot 4: Background UDP
- **Type**: Bar chart
- **Shows**: Two bars per traffic level (utilization and fairness)
- **Good result**: TCP maintains fairness despite UDP

---

## Phase 4: Complete the Report

### Step 4.1: Add Experimental Results

Open `REPORT_PART2.md` and add your results.

**Section 4.2 - Experiment 1 Observations:**

```markdown
**Fixed Bandwidth Results:**

Our protocol achieved high utilization across all bandwidths:
- 100 Mbps: Utilization = 0.XX, JFI = 0.XX
- 500 Mbps: Utilization = 0.XX, JFI = 0.XX
- 1000 Mbps: Utilization = 0.XX, JFI = 0.XX

The slow start mechanism effectively probed for available bandwidth,
reaching high throughput quickly. Fairness remained excellent (JFI > 0.90)
across all conditions, indicating that both flows shared bandwidth equally.

Performance scores (Utilization × JFI) ranged from 0.XX to 0.XX,
demonstrating that the protocol achieves both efficiency and fairness.
```

**Section 4.3 - Experiment 2 Observations:**

```markdown
**Varying Loss Results:**

Link utilization degraded gracefully with increasing loss:
- 0% loss: Utilization = 0.XX
- 1% loss: Utilization = 0.XX
- 2% loss: Utilization = 0.XX

Fast retransmit was highly effective, as evidenced by server logs showing
[X]% of losses recovered via duplicate ACKs rather than timeouts. The
congestion window adjusted appropriately to loss conditions, maintaining
stability without oscillation.
```

**Section 4.4 - Experiment 3 Observations:**

```markdown
**Asymmetric Flows Results:**

Fairness decreased as RTT asymmetry increased:
- RTT difference 0ms: JFI = 0.XX
- RTT difference 20ms: JFI = 0.XX
- RTT difference 40ms: JFI = 0.XX

This is expected behavior for TCP Reno, which increases cwnd based on
ACKs received. Lower-RTT flows receive ACKs faster, causing their windows
to grow faster. This is a known limitation of additive-increase
multiplicative-decrease (AIMD) algorithms.
```

**Section 4.5 - Experiment 4 Observations:**

```markdown
**Background UDP Results:**

TCP flows adapted to UDP traffic:
- Light UDP: Utilization = 0.XX, JFI = 0.XX
- Medium UDP: Utilization = 0.XX, JFI = 0.XX
- Heavy UDP: Utilization = 0.XX, JFI = 0.XX

TCP demonstrated "friendliness" by backing off in response to congestion
caused by UDP bursts. Fairness between the two TCP flows remained high
(JFI > 0.XX), showing that TCP flows maintained equity with each other
even while competing with non-responsive UDP traffic.
```

### Step 4.2: Include Plots

Add images to the report:

```markdown
### 6.1 Plot 1: Fixed Bandwidth

![Fixed Bandwidth](plot_p2_fixed_bandwidth.png)

*Figure 1: Link utilization and fairness remain high across all bandwidth
values, demonstrating the protocol's scalability.*

### 6.2 Plot 2: Varying Loss

![Varying Loss](plot_p2_varying_loss.png)

*Figure 2: Link utilization decreases with packet loss, but degradation
is gradual due to effective fast retransmit and SACK mechanisms.*

### 6.3 Plot 3: Asymmetric Flows

![Asymmetric Flows](plot_p2_asymmetric_flows.png)

*Figure 3: Fairness decreases with RTT asymmetry, a known characteristic
of TCP Reno. Lower-RTT flows achieve higher throughput.*

### 6.4 Plot 4: Background UDP

![Background UDP](plot_p2_background_udp.png)

*Figure 4: TCP flows maintain mutual fairness even in the presence of
non-responsive UDP traffic, demonstrating TCP-friendliness.*
```

---

## Phase 5: Submission Checklist

### Required Files:

- [ ] `p2_server.py` - Server with congestion control
- [ ] `p2_client.py` - Client implementation
- [ ] `REPORT_PART2.md` - Report with observations (max 2 pages)
- [ ] `plot_p2_fixed_bandwidth.png`
- [ ] `plot_p2_varying_loss.png`
- [ ] `plot_p2_asymmetric_flows.png`
- [ ] `plot_p2_background_udp.png`

### Verification:

- [ ] All 4 experiments completed successfully
- [ ] All MD5 hashes match in CSV files
- [ ] All 4 plots generated
- [ ] Report includes your observations
- [ ] Plots embedded in report
- [ ] Report is 2 pages or less

---

## Understanding Your Results

### What is "Good" Performance?

**Link Utilization:**
- Excellent: > 0.90 (using >90% of capacity)
- Good: 0.80-0.90
- Acceptable: 0.60-0.80
- Poor: < 0.60

**Jain Fairness Index:**
- Perfect: 1.0 (exactly equal sharing)
- Excellent: > 0.95
- Good: 0.90-0.95
- Acceptable: 0.75-0.90
- Poor: < 0.75

**Performance Score (for ranking):**
```
Score = Utilization × JFI
```
- Excellent: > 0.85
- Good: 0.70-0.85
- Acceptable: 0.50-0.70

### Expected Behavior

**Experiment 1 (Fixed Bandwidth):**
- Both metrics should stay high across all bandwidths
- Small decrease at very high BW is normal (TCP Reno limitation)

**Experiment 2 (Varying Loss):**
- Utilization should decrease roughly linearly with loss
- At 2% loss, should still be > 0.60

**Experiment 3 (Asymmetric Flows):**
- JFI will decrease with RTT difference (expected!)
- JFI > 0.75 is still acceptable given the asymmetry

**Experiment 4 (Background UDP):**
- Utilization may drop (TCP backs off for UDP)
- JFI among TCP flows should stay high (> 0.85)

---

## Troubleshooting

### Issue: Experiments Fail to Start

**Solution:**
```bash
# Clean up Mininet
sudo mn -c

# Kill old processes
sudo pkill -f p2_server
sudo pkill -f p2_client

# Restart Ryu
ryu-manager ryu.app.simple_switch
```

### Issue: Low Utilization in All Experiments

**Possible causes:**
1. cwnd not increasing properly (check slow start logic)
2. Too many timeouts (check RTO values)
3. Not enough buffering

**Debug:**
- Check server output for cwnd values
- Look for state transitions (SLOW_START → CONGESTION_AVOIDANCE)
- Count fast retransmits vs timeouts

### Issue: Poor Fairness (Low JFI)

**Possible causes:**
1. Flows not starting at same time
2. One flow getting stuck in timeouts
3. Cwnd growth imbalance

**Debug:**
- Check if both flows complete around same time
- Look at individual throughputs in CSV
- Verify both servers show similar cwnd behavior

### Issue: Plots Don't Generate

**Solution:**
```bash
# Check CSV files exist
ls -l p2_fairness_*.csv

# Check CSV format
head p2_fairness_fixed_bandwidth.csv

# Should show headers:
# bw,loss,delay_c2_ms,udp_off_mean,iter,md5_hash_1,md5_hash_2,...

# Reinstall packages
pip install --upgrade pandas matplotlib scipy numpy
```

### Issue: MD5 Hashes Don't Match

**Cause**: Data corruption

**Solution:**
1. Test locally first: `python test_local_p2.py`
2. Check packet parsing logic
3. Verify EOF handling
4. Look for sequence number gaps

---

## Understanding Congestion Control

### How cwnd Evolves (Example)

```
Time (RTT)  Event                    State              cwnd (MSS)   ssthresh
------------------------------------------------------------------------
0           Start                    SLOW_START         1            64
1           ACK received             SLOW_START         2            64
2           ACK received             SLOW_START         4            64
3           ACK received             SLOW_START         8            64
4           ACK received             SLOW_START         16           64
5           ACK received             SLOW_START         32           64
6           ACK received             SLOW_START         64           64
7           cwnd >= ssthresh         CONG_AVOID         65           64
8           ACK received             CONG_AVOID         66           64
9           3 dup ACKs               FAST_RECOVERY      35           33
10          New ACK                  CONG_AVOID         33           33
11          ACK received             CONG_AVOID         34           33
12          Timeout                  SLOW_START         1            17
13          ACK received             SLOW_START         2            17
...
```

### Why These States?

**Slow Start**: Find available bandwidth quickly
- Doubles every RTT
- Gets to high throughput fast
- Stops at ssthresh or congestion

**Congestion Avoidance**: Carefully probe for more bandwidth
- Adds 1 MSS per RTT
- Conservative growth
- Prevents overloading network

**Fast Recovery**: Recover from loss without full restart
- Maintains high throughput
- Responds to mild congestion
- More efficient than timeout

---

## Performance Optimization Tips

If your results don't meet expectations:

### 1. Tune Initial ssthresh

```python
# In p2_server.py, line ~54:
self.ssthresh = 128000  # Try higher value (was 64000)
```

Higher ssthresh = longer slow start = faster ramp-up

### 2. Adjust RTO Parameters

```python
# In p2_server.py:
MIN_RTO = 0.15  # Lower min (was 0.2)
ALPHA = 0.1     # Faster adaptation (was 0.125)
```

More aggressive RTT tracking = quicker recovery

### 3. More Aggressive Congestion Avoidance

```python
# In increase_cwnd() for CONGESTION_AVOIDANCE:
increment = (1.5 * MSS * acked_bytes) / self.cwnd  # Was 1.0 * MSS
```

Faster growth = higher utilization (but maybe less fair)

### 4. Smaller Backoff on Timeout

```python
# In handle_timeout():
self.rto = min(self.rto * 1.2, MAX_RTO)  # Was 1.5
```

Less conservative = faster recovery (but risk of congestion)

**Warning**: These optimizations have tradeoffs! Understand them before changing.

---

## Key Concepts Explained

### What is Congestion Window (cwnd)?

The maximum amount of data that can be "in-flight" (sent but not acknowledged).

**Example:**
```
cwnd = 10 packets

Sent: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10
ACKed: 1, 2, 3
In-flight: 4, 5, 6, 7, 8, 9, 10 (10 packets)

Cannot send packet 11 until ACK for 4 arrives.
```

### What is Slow Start Threshold (ssthresh)?

The cwnd value where we switch from exponential to linear growth.

**Why?**
- Exponential growth finds capacity quickly
- But it can overshoot and cause congestion
- ssthresh remembers "where congestion happened"
- Linear growth is safer near known congestion point

### What is Link Utilization?

Percentage of link capacity being used.

```
Utilization = Actual_Throughput / Link_Capacity

Example:
Link = 100 Mbps
Flow 1 = 45 Mbps
Flow 2 = 50 Mbps
Total = 95 Mbps
Utilization = 95 / 100 = 0.95 (95%)
```

### What is Jain Fairness Index?

Measure of how equally resources are shared.

```
JFI = (x1 + x2)² / (2 * (x1² + x2²))

Example 1 (perfect fairness):
x1 = 50 Mbps, x2 = 50 Mbps
JFI = (50+50)² / (2*(50²+50²)) = 10000/10000 = 1.0

Example 2 (unfair):
x1 = 80 Mbps, x2 = 20 Mbps
JFI = (80+20)² / (2*(80²+20²)) = 10000/13200 = 0.76
```

---

## Questions and Answers

**Q: Why 4 experiments instead of 2?**
A: Congestion control needs to work in many scenarios: different bandwidths, loss rates, RTT asymmetry, and competing traffic.

**Q: What if my utilization is low but fairness is high?**
A: Protocol is "too conservative"—not using available bandwidth. Try more aggressive cwnd growth.

**Q: What if my utilization is high but fairness is low?**
A: One flow is dominating. Check that both flows start at similar times and experience similar conditions.

**Q: Why does TCP Reno favor low-RTT flows?**
A: Cwnd increases per ACK. Low-RTT flows get ACKs faster, so their windows grow faster.

**Q: How long do all experiments take?**
A: ~50-70 minutes total (mostly waiting).

**Q: Can I run experiments in parallel?**
A: No, each experiment needs the full network. Run sequentially.

**Q: What's a good overall performance score?**
A: Average of (Utilization × JFI) across all experiments > 0.70 is good.

---

## Summary

**Part 2 adds congestion control to Part 1's reliability:**

1. **Start slow** (1 MSS)
2. **Ramp up fast** (exponential in slow start)
3. **Probe carefully** (linear in congestion avoidance)
4. **Back off on congestion** (halve or reset window)
5. **Recover quickly** (fast retransmit + fast recovery)

**This achieves:**
- High throughput (efficiency)
- Fair sharing (equity)
- Congestion prevention (stability)

**Your implementation is complete and ready to run!**

---

**Next**: Follow the steps above, run all 4 experiments, generate plots, complete report, and submit!

Good luck! 🚀
