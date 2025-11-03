# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Computer Networks (COL334) assignment implementing a reliable file transfer protocol with congestion control over UDP. The project is split into two parts:

- **Part 1**: Reliability layer implementing sliding window protocol, ACKs, timeouts, and fast retransmit
- **Part 2**: Congestion control algorithm (TCP Reno/CUBIC-style) with slow start, congestion avoidance, and window management

## Running Commands

### Part 1: Reliability Testing

```bash
# Start server with fixed sender window size (SWS)
python3 p1_server.py <SERVER_IP> <SERVER_PORT> <SWS>

# Start client
python3 p1_client.py <SERVER_IP> <SERVER_PORT>

# Run experiments (requires Mininet and Ryu controller)
sudo python3 p1_exp.py loss    # Test varying packet loss (1%-5%)
sudo python3 p1_exp.py jitter  # Test varying delay jitter (20-100ms)
```

### Part 2: Congestion Control Testing

```bash
# Start server (no SWS parameter - uses dynamic cwnd)
python3 p2_server.py <SERVER_IP> <SERVER_PORT>

# Start client with filename prefix
python3 p2_client.py <SERVER_IP> <SERVER_PORT> <PREF_FILENAME>

# Run experiments (requires Mininet and Ryu controller)
sudo python3 p2_exp.py fixed_bandwidth    # Vary bandwidth 100Mbps-1Gbps
sudo python3 p2_exp.py varying_loss       # Vary loss 0%-2%
sudo python3 p2_exp.py asymmetric_flows   # Vary RTT asymmetry
sudo python3 p2_exp.py background_udp     # Test with bursty UDP traffic
```

## Protocol Architecture

### Packet Format (1200 bytes max UDP payload)

```
| Sequence Number (4 bytes) | Reserved/Optional (16 bytes) | Data (up to 1180 bytes) |
```

- **Sequence number**: Byte offset for data packets; next expected sequence for ACKs (cumulative)
- **Reserved 16 bytes**: Available for SACK, timestamps, or other extensions
- **Data payload**: Up to 1180 bytes per packet

### Key Design Requirements

**Reliability (Part 1):**
- Sliding window protocol with packet numbering
- ACK mechanism (cumulative ACKs recommended, SACK optional)
- RTO-based retransmission with timeout estimation
- Fast retransmit after 3 duplicate ACKs
- Fixed sender window size (SWS) passed as command-line argument

**Congestion Control (Part 2):**
- Initial window: 1 MSS (1200 bytes)
- Exponential growth phase (slow start)
- Additive increase phase (congestion avoidance)
- Multiplicative decrease on congestion events
- Different responses for timeouts vs duplicate ACKs
- No flow control implementation required

### File Transfer Protocol

1. Client sends 1-byte request to server (retries up to 5 times with 2s timeout)
2. Server sends `data.txt` in chunks using reliable UDP protocol
3. Client writes to `received_data.txt` (Part 1) or `<PREF>received_data.txt` (Part 2)
4. Server sends packet with `"EOF"` payload to signal completion
5. Both sides terminate after transfer completes

### Network Testing Setup

**Part 1**: Simple topology with 2 hosts (h1, h2) connected via switch s1
- Uses `tc qdisc` to inject packet loss and delay jitter
- Measures download time across varying network conditions

**Part 2**: Dumbbell topology (see `topology.jpeg`)
- Two client-server pairs sharing bottleneck link
- Bottleneck buffer sized as: `buffer_packets = (RTT * BW) / (MSS * 8)`
- Measures link utilization and Jain Fairness Index (JFI)
- Experiment 4 adds bursty UDP background traffic via `udp_server.py` / `udp_client.py`

## Important Implementation Notes

- The assignment assumes server handles one client at a time (Part 1)
- Part 2 experiments run two concurrent flows to test fairness
- Files currently contain only placeholder/skeleton code (single line each)
- MTU is 1200 bytes total (20 byte header + 1180 byte payload)
- Mininet experiments require root/sudo access
- Ryu controller must be running on 127.0.0.1:6653 for experiments

## Grading Criteria

**Part 1 (40%):**
- 50% correctness and completeness
- 25% meeting performance targets
- 25% relative efficiency (decile ranking among submissions)

**Part 2 (60%):**
- 70% meeting performance targets and report
- 30% relative efficiency (product of JFI × Link Utilization, averaged across experiments)

## Testing and Debugging

- Always plot congestion window (cwnd) evolution over time
- Compare sending rate vs received throughput
- Log key state variables: `ssthresh`, `acked_bytes`, mode transitions
- Watch for flow synchronization in multi-flow experiments
- Use structured logs (CSV/JSON) for post-processing
- Check queue lengths on switch using tcpdump if needed
