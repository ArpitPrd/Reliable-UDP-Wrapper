# Quick Start Guide - Part 1

## Installation

```bash
# Install Python dependencies
pip install -r requirements.txt

# For Mininet experiments (Linux only):
sudo apt-get install mininet
sudo pip install ryu
```

## Quick Test (No Mininet)

```bash
python test_local.py
```

Expected: File transfers successfully, MD5 hashes match.

## Running Experiments (Requires Mininet + Linux)

### Terminal 1: Start Ryu Controller
```bash
ryu-manager ryu.app.simple_switch
```
Keep this running!

### Terminal 2: Run Experiments
```bash
# Navigate to project directory
cd /path/to/col334-assignment4-main

# Run loss experiment (~20 minutes)
sudo python3 p1_exp.py loss

# Run jitter experiment (~20 minutes)
sudo python3 p1_exp.py jitter
```

## Generate Plots

```bash
python analyze_results.py
```

Output:
- `plot_loss_experiment.png`
- `plot_jitter_experiment.png`
- Statistical summaries in console

## Files to Submit

1. `p1_server.py` ✓ (provided)
2. `p1_client.py` ✓ (provided)
3. `REPORT_PART1.md` (complete with your observations)
4. `plot_loss_experiment.png` (generated from experiments)
5. `plot_jitter_experiment.png` (generated from experiments)

## Performance Targets

Your implementation should complete transfers within these times:

**Loss Experiment:**
- 1% loss: 53s
- 2% loss: 58s
- 3% loss: 63s
- 4% loss: 68s
- 5% loss: 77s

**Jitter Experiment:**
- 20ms jitter: 55s
- 40ms jitter: 64s
- 60ms jitter: 77s
- 80ms jitter: 92s
- 100ms jitter: 103s

## Troubleshooting

**"Address already in use":**
```bash
# Kill process on port 6555
sudo netstat -tulpn | grep 6555
sudo kill -9 [PID]
```

**Mininet cleanup:**
```bash
sudo mn -c
```

**Missing packages:**
```bash
pip install -r requirements.txt
```

## Protocol Features

✓ Sliding window protocol (configurable SWS)
✓ Cumulative ACKs
✓ Selective ACKs (SACK)
✓ Adaptive RTO (Jacobson/Karels algorithm)
✓ Fast retransmit (3 duplicate ACKs)
✓ Exponential backoff on timeout

## File Descriptions

| File | Purpose |
|------|---------|
| `p1_server.py` | Server implementation (sender) |
| `p1_client.py` | Client implementation (receiver) |
| `p1_exp.py` | Mininet experiment runner |
| `test_local.py` | Local testing without Mininet |
| `analyze_results.py` | Generate plots and statistics |
| `REPORT_PART1.md` | Technical report template |
| `INSTRUCTIONS.md` | Detailed step-by-step guide |
| `data.txt` | File to transfer (6.46 MB) |

## Support

For detailed instructions, see `INSTRUCTIONS.md`.

For protocol design details, see `REPORT_PART1.md`.
