# START HERE - Part 2 Complete Solution

## What Part 2 Is About

Part 2 adds **Congestion Control** to the reliability mechanisms from Part 1.

**Simple Analogy:**
- **Part 1 (Reliability)**: Making sure every package arrives safely
- **Part 2 (Congestion Control)**: Deciding how many packages to send at once to avoid overwhelming the delivery system

**Why It Matters:**
Without congestion control, the internet would collapse. Everyone would send as fast as possible, causing network queues to overflow and performance to degrade.

---

## What You've Been Given

I've completed the entire Part 2 implementation for you. All code is ready to use.

### ✅ Core Implementation (Ready to Submit)
- **p2_server.py** - TCP Reno-style congestion control implementation
  - Slow Start (exponential growth)
  - Congestion Avoidance (linear growth)
  - Fast Recovery (quick loss recovery)
  - Dynamic window adjustment

- **p2_client.py** - Client with reliability (similar to Part 1)

### 📊 Testing and Analysis Tools
- **test_local_p2.py** - Quick local test (single + dual flow)
- **analyze_results_p2.py** - Generates 4 plots with statistics

### 📖 Documentation
- **START_HERE_PART2.md** ← You are here!
- **INSTRUCTIONS_PART2.md** ← Detailed step-by-step guide
- **REPORT_PART2.md** ← Technical report template
- **requirements.txt** ← Python dependencies (same as Part 1)

---

## Quick Decision Tree

**Did you complete Part 1?**
- **YES** → Great! Part 2 is similar but has 4 experiments instead of 2
- **NO** → Complete Part 1 first (it's prerequisite knowledge)

**Do you understand congestion control concepts?**
- **YES** → Read INSTRUCTIONS_PART2.md and run experiments
- **NO** → Read the "Understanding Congestion Control" section below first

**Just want to run and submit?**
- Follow the "Minimum Steps" section below

---

## Understanding Congestion Control (5-Minute Version)

### The Problem

Imagine a highway (network link) with capacity for 100 cars/minute:
- If everyone sends 100 cars/minute, the highway is full (good!)
- If everyone sends 200 cars/minute, traffic jam! (bad!)
- How do drivers know how fast to go?

### The Solution: Adaptive Window

Your protocol uses a **congestion window (cwnd)** that grows and shrinks:

```
Start slow:  cwnd = 1 packet  (cautious)
Grow fast:   cwnd = 2, 4, 8, 16, 32... (exponential - "slow start")
Grow slowly: cwnd = 33, 34, 35, 36... (linear - "congestion avoidance")
Back off:    cwnd = 18 (halve on congestion signal)
Restart:     cwnd = 1 (reset on timeout)
```

### Three States

```
┌─────────────────┐  cwnd >= threshold   ┌──────────────────────┐
│   SLOW START    │──────────────────────>│ CONGESTION AVOIDANCE │
│ (exponential)   │                       │      (linear)        │
└─────────────────┘                       └──────────────────────┘
         ▲                                           │
         │                                           │
         │         ┌───────────────────┐            │
         └─────────│  FAST RECOVERY    │<───────────┘
          timeout  │  (after 3 dup ACK)│  3 dup ACKs
                   └───────────────────┘
```

**Slow Start**: "How fast can I go?" → double every round trip
**Congestion Avoidance**: "I'm near capacity" → increase slowly
**Fast Recovery**: "Oops, lost a packet" → quick recovery

---

## Minimum Steps to Complete Part 2

### Step 1: Test Locally (5 minutes)

```bash
python test_local_p2.py
```

Expected: Two successful tests with MD5 matches

### Step 2: Run 4 Mininet Experiments (60 minutes)

**Terminal 1** (keep running):
```bash
ryu-manager ryu.app.simple_switch
```

**Terminal 2** (run sequentially):
```bash
sudo python3 p2_exp.py fixed_bandwidth    # ~15 min
sudo python3 p2_exp.py varying_loss       # ~10 min
sudo python3 p2_exp.py asymmetric_flows   # ~10 min
sudo python3 p2_exp.py background_udp     # ~10 min
```

### Step 3: Generate Plots (1 minute)

```bash
python analyze_results_p2.py
```

Creates 4 PNG files.

### Step 4: Complete Report (30 minutes)

1. Open `REPORT_PART2.md`
2. Add your results from analysis output
3. Write observations (see INSTRUCTIONS_PART2.md for examples)

### Step 5: Submit 7 Files

- [ ] p2_server.py
- [ ] p2_client.py
- [ ] REPORT_PART2.md
- [ ] plot_p2_fixed_bandwidth.png
- [ ] plot_p2_varying_loss.png
- [ ] plot_p2_asymmetric_flows.png
- [ ] plot_p2_background_udp.png

**Total time: ~2 hours**

---

## The 4 Experiments Explained

### Experiment 1: Fixed Bandwidth (Scalability Test)

**Question**: Does your protocol work on both slow and fast networks?

**Test**: Vary link speed from 100 Mbps to 1000 Mbps

**Good Result:**
- High utilization (>80%) at all speeds
- High fairness (JFI > 0.90)
- Both metrics stay consistent

**What It Tests:**
- Can slow start find high bandwidth quickly?
- Does congestion avoidance maintain high utilization?
- Do flows share fairly at all speeds?

---

### Experiment 2: Varying Loss (Robustness Test)

**Question**: Does your protocol handle packet loss well?

**Test**: Vary loss from 0% to 2%

**Good Result:**
- Gradual degradation (not steep drop)
- Utilization > 60% even at 2% loss

**What It Tests:**
- Is fast retransmit working?
- Does cwnd adapt appropriately?
- Can protocol recover quickly from loss?

---

### Experiment 3: Asymmetric Flows (Fairness Test)

**Question**: Is your protocol fair when flows have different delays?

**Test**: Flow 1 has low delay, Flow 2 has increasing delay

**Good Result:**
- JFI decreases gradually
- JFI > 0.75 even with large RTT difference

**What It Tests:**
- RTT fairness (known TCP weakness)
- Do both flows get reasonable throughput?
- How bad is the RTT bias?

**Note**: Some unfairness is expected and acceptable!

---

### Experiment 4: Background UDP (Friendliness Test)

**Question**: Does your protocol "play nice" with other traffic?

**Test**: Add bursty UDP traffic (light, medium, heavy)

**Good Result:**
- TCP backs off for UDP (utilization may drop)
- TCP flows maintain fairness with each other (JFI > 0.85)

**What It Tests:**
- Does TCP respond to congestion caused by UDP?
- Do TCP flows stay fair to each other?
- Is the protocol "TCP-friendly"?

---

## Performance Expectations

### What Gets Graded

**Part 2 = 60% of total assignment**

- **70%**: Performance targets + report quality
- **30%**: Ranking compared to other students

**Ranking Formula:**
```
For each experiment condition:
  Performance = Link_Utilization × Jain_Fairness_Index

Rank students by performance
Average rank across all conditions
Decile-based scoring
```

### Target Metrics

**Good Performance:**
- Link Utilization: > 0.80
- Jain Fairness Index: > 0.90
- Performance Score: > 0.72

**Acceptable Performance:**
- Link Utilization: > 0.60
- Jain Fairness Index: > 0.75
- Performance Score: > 0.45

**Your implementation should achieve "good" performance in most experiments.**

---

## Key Protocol Features

### What's Implemented

✅ **Initial cwnd = 1 MSS** (as required)
✅ **Slow Start** (exponential growth until ssthresh)
✅ **Congestion Avoidance** (linear growth after ssthresh)
✅ **Fast Retransmit** (on 3 duplicate ACKs)
✅ **Fast Recovery** (maintain high cwnd during recovery)
✅ **Timeout Handling** (reset to 1 MSS, restart slow start)
✅ **ssthresh Management** (remember congestion point)
✅ **State Tracking** (explicit state machine)
✅ **Statistics** (retransmit counts, state transitions)

### Based on TCP Reno

Our implementation follows **TCP Reno** specification:
- Well-understood and proven algorithm
- Balances efficiency and fairness
- Simple enough to implement correctly
- Complex enough to perform well

**Why not CUBIC or BBR?**
- CUBIC: More complex, better for very high bandwidth
- BBR: Very different approach, harder to implement
- Reno: Perfect for learning and this assignment

---

## File Structure for Part 2

```
col334-assignment4-main/
│
├── Part 2 Core
│   ├── p2_server.py          ← Submit this (congestion control)
│   ├── p2_client.py          ← Submit this (receiver)
│   └── data.txt              (same file as Part 1)
│
├── Part 2 Testing
│   ├── test_local_p2.py      (local test script)
│   ├── analyze_results_p2.py (plotting script)
│   └── p2_exp.py             (provided by instructor)
│
├── Part 2 Documentation
│   ├── START_HERE_PART2.md   ← You are here!
│   ├── INSTRUCTIONS_PART2.md ← Step-by-step guide
│   └── REPORT_PART2.md       ← Submit this (with observations)
│
├── UDP Traffic (for Exp 4)
│   ├── udp_server.py         (provided)
│   └── udp_client.py         (provided)
│
└── Generated Files
    ├── p2_fairness_fixed_bandwidth.csv
    ├── p2_fairness_varying_loss.csv
    ├── p2_fairness_asymmetric_flows.csv
    ├── p2_fairness_background_udp.csv
    ├── plot_p2_fixed_bandwidth.png      ← Submit
    ├── plot_p2_varying_loss.png         ← Submit
    ├── plot_p2_asymmetric_flows.png     ← Submit
    └── plot_p2_background_udp.png       ← Submit
```

---

## Common Questions

**Q: Is Part 2 harder than Part 1?**
A: Not really! The code is similar complexity. Just more experiments (4 vs 2).

**Q: Do I need to complete Part 1 first?**
A: Strongly recommended. Part 2 builds on Part 1 concepts.

**Q: Can I use Part 1 code for Part 2?**
A: No, they're separate. Part 2 has its own server/client with different parameters.

**Q: Why are there 4 experiments?**
A: Congestion control must work in many scenarios. Each experiment tests a different aspect.

**Q: How long do experiments take?**
A: ~60 minutes total for all 4 (mostly waiting for transfers).

**Q: What if my results aren't perfect?**
A: That's okay! Focus on correctness first, then understand why performance varies.

**Q: What's the most important metric?**
A: Depends on experiment:
- Exp 1 & 2: Both utilization and fairness
- Exp 3: Fairness (utilization less important)
- Exp 4: Both metrics matter

**Q: Can I optimize the code?**
A: Yes! See INSTRUCTIONS_PART2.md for tuning tips. But default settings should work well.

---

## Success Criteria

You'll know you're done when:

✅ Local test shows 2 successful transfers
✅ All 4 experiments completed (4 CSV files)
✅ All MD5 hashes match (data integrity)
✅ 4 plots generated and look reasonable
✅ Report includes your observations
✅ Link utilization > 0.60 in most experiments
✅ JFI > 0.75 in most experiments
✅ All 7 files ready for submission

---

## Next Steps

### For First-Time Users:

1. **Read**: INSTRUCTIONS_PART2.md (15 min)
2. **Understand**: Congestion control concepts (above)
3. **Test**: Run `test_local_p2.py`
4. **Experiment**: Follow experiment steps
5. **Analyze**: Run analysis script
6. **Document**: Complete report
7. **Submit**: 7 files

### For Experienced Users:

1. `python test_local_p2.py`
2. Start Ryu
3. Run all 4 experiments
4. `python analyze_results_p2.py`
5. Complete REPORT_PART2.md
6. Submit

---

## Comparison: Part 1 vs Part 2

| Aspect | Part 1 | Part 2 |
|--------|--------|--------|
| **Focus** | Reliability | Congestion Control |
| **Window** | Fixed (SWS parameter) | Dynamic (cwnd) |
| **States** | None | 3 states (SS, CA, FR) |
| **Experiments** | 2 (loss, jitter) | 4 (bandwidth, loss, RTT, UDP) |
| **Metrics** | Download time | Utilization, Fairness |
| **Flows** | 1 flow per test | 2 concurrent flows |
| **Topology** | Simple (2 hosts) | Dumbbell (4 hosts + UDP) |
| **Time** | ~40 min experiments | ~60 min experiments |
| **Grading** | 40% of assignment | 60% of assignment |

**Both parts use**: SACK, fast retransmit, adaptive RTO

---

## Tips for Success

### 1. Test Locally First
Always run `test_local_p2.py` before Mininet experiments. Catches most bugs quickly.

### 2. Understand the Metrics
- **Utilization**: Are you using available bandwidth?
- **Fairness**: Are you sharing fairly?
- **Both matter!**

### 3. Watch cwnd Evolution
Check server logs to see cwnd growing:
```
SLOW_START (cwnd=1200)
SLOW_START (cwnd=2400)
SLOW_START (cwnd=4800)
→ CONGESTION_AVOIDANCE (cwnd=64000)
FAST_RETRANSMIT → FAST_RECOVERY
```

### 4. Compare Experiments
- Which experiment has lowest utilization? Why?
- Which has lowest fairness? Why?
- Do results make sense given network conditions?

### 5. Write Good Observations
Don't just list numbers. Explain WHY:
- "Utilization decreased because cwnd was frequently halved due to loss"
- "Fairness was poor because low-RTT flow received ACKs 2x faster"

---

## Help and Support

**If stuck on concepts:**
- See "Understanding Congestion Control" section above
- Read REPORT_PART2.md Section 1 (Algorithm Design)
- Compare with TCP Reno documentation online

**If stuck on experiments:**
- See INSTRUCTIONS_PART2.md troubleshooting section
- Check CSV files for partial results
- Verify Mininet/Ryu are running correctly

**If stuck on analysis:**
- Check that CSV files have correct format
- Verify Python packages installed
- Try analyzing one experiment at a time

---

## Ready to Start?

### Path 1: Beginner
1. Read INSTRUCTIONS_PART2.md (understand experiments)
2. Read "Understanding Congestion Control" above (understand algorithm)
3. Follow steps in INSTRUCTIONS_PART2.md
4. Complete!

### Path 2: Intermediate
1. Skim INSTRUCTIONS_PART2.md
2. Run experiments
3. Analyze results
4. Complete report
5. Done!

### Path 3: Expert
1. `python test_local_p2.py`
2. Run 4 experiments
3. `python analyze_results_p2.py`
4. Edit REPORT_PART2.md
5. Submit 7 files

---

## Your Implementation is Complete!

All code has been written and tested. You just need to:

1. ✅ **Run** experiments
2. ✅ **Collect** results
3. ✅ **Analyze** data
4. ✅ **Document** observations
5. ✅ **Submit** files

**Estimated time: 2-2.5 hours**

---

## Final Checklist

Before starting:
- [ ] Part 1 completed (recommended)
- [ ] Understand basic congestion control concepts
- [ ] Linux environment with Mininet ready
- [ ] Ryu controller installed
- [ ] Python packages installed

After completing:
- [ ] All 4 experiments successful
- [ ] All plots generated
- [ ] Report completed with observations
- [ ] All 7 submission files ready
- [ ] Understand why your protocol performed as it did

---

**You have everything you need to succeed in Part 2!**

Good luck! 🚀

---

**Next**: Choose your path above and follow INSTRUCTIONS_PART2.md!
