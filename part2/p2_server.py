#!/usr/bin/env python3
import socket
import sys
import time
import struct
import os
import math
import csv
import select
import collections
from collections import deque
import random

# --- Constants ---
HEADER_FORMAT = "!IIHII2x"
HEADER_SIZE = 20
MSS_BYTES = 1200
PAYLOAD_SIZE = MSS_BYTES - HEADER_SIZE
MAX_CWND = 30 * 1024 * 1024  # 30MB for very high BW

# Flags
SYN_FLAG = 0x1
ACK_FLAG = 0x2
EOF_FLAG = 0x4

# States
STATE_SLOW_START = 1
STATE_CONGESTION_AVOIDANCE = 2

# RTO constants
ALPHA = 0.125
BETA = 0.25
K = 4.0
INITIAL_RTO = 0.1
MIN_RTO = 0.04

# Minimum cwnd
MIN_CWND = 4 * MSS_BYTES

class Server:
    def __init__(self, ip, port):
        self.ip = ip
        self.port = int(port)
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind((self.ip, self.port))
        self.socket.setblocking(False)
        self.client_addr = None
        self.in_rto_recovery = False
        
        # CRITICAL: Deterministic per-port jitter for fairness
        random.seed(self.port)
        self.startup_jitter = (self.port % 5) * 0.0008  # Reduced: 0-3.2ms
        
        print(f"Server on {self.ip}:{self.port} (jitter={self.startup_jitter*1000:.2f}ms)")

        # load file
        try:
            with open("data.txt", "rb") as f:
                self.file_data = f.read()
            self.file_size = len(self.file_data)
        except IOError as e:
            print(f"Error reading data.txt: {e}")
            sys.exit(1)

        # Connection state
        self.next_seq_num = 0
        self.base_seq_num = 0
        self.eof_sent_seq = -1
        self.connection_dead = False

        # Congestion control - optimized for BOTH fairness AND utilization
        self.state = STATE_SLOW_START
        self.cwnd_bytes = 16 * MSS_BYTES  # Larger IW16 for faster start
        self.ssthresh = 3000 * MSS_BYTES  # Very high - stay in SS

        # RTO
        self.rto = INITIAL_RTO
        self.srtt = 0.0
        self.rttvar = 0.0
        self.rtt_min = float('inf')

        # Sent buffer
        self.sent_packets = collections.OrderedDict()

        # SACK & dup-acks
        self.dup_ack_count = 0
        self.sacked_packets = set()

        # bookkeeping
        self.start_time = 0.0
        self.last_ack_time = 0.0
        self.ack_credits = 0.0
        self.applied_startup_jitter = False

        # CUBIC params
        self.C = 0.4
        self.beta_cubic = 0.7
        self.w_max_bytes = 0.0
        self.w_max_last_bytes = 0.0
        self.t_last_congestion = 0.0
        self.K = 0.0
        
        # HyStart++ 
        self.hystart_enabled = True
        self.hystart_found = False
        self.hystart_round_min_rtt = float('inf')
        self.hystart_last_round_min_rtt = float('inf')
        self.hystart_sample_count = 0
        
        # Loss tracking
        self.loss_events = 0
        self.consecutive_loss_free_acks = 0
        
        # Smart pacing - adaptive
        self.pacing_gain = 1.0
        self.last_send_time = 0.0

        # logging
        self.cwnd_log_file = None
        self.log_filename = f"cwnd_log_{self.port}.csv"

    # --- helpers ---
    def get_state_str(self):
        if self.state == STATE_SLOW_START: return "SS"
        if self.state == STATE_CONGESTION_AVOIDANCE:
            return "CUBIC" if self.t_last_congestion > 0 else "CA"
        return "UNK"

    def pack_header(self, seq_num, ack_num, flags, sack_start=0, sack_end=0):
        return struct.pack(HEADER_FORMAT, seq_num, ack_num, flags, sack_start, sack_end)

    def unpack_header(self, packet):
        try:
            return struct.unpack(HEADER_FORMAT, packet[:HEADER_SIZE])
        except struct.error:
            return None

    # --- RTO / RTT updates ---
    def update_rto(self, rtt_sample):
        self.rtt_min = min(self.rtt_min, rtt_sample)
        
        # HyStart tracking
        if self.state == STATE_SLOW_START and self.hystart_enabled and not self.hystart_found:
            self.hystart_round_min_rtt = min(self.hystart_round_min_rtt, rtt_sample)
            self.hystart_sample_count += 1
            
        if self.srtt == 0.0:
            self.srtt = rtt_sample
            self.rttvar = rtt_sample / 2.0
        else:
            self.rttvar = (1 - BETA) * self.rttvar + BETA * abs(self.srtt - rtt_sample)
            self.srtt = (1 - ALPHA) * self.srtt + ALPHA * rtt_sample

        new_rto = self.srtt + K * self.rttvar
        if self.rto == 0:
            self.rto = max(MIN_RTO, new_rto)
        else:
            self.rto = 0.88 * self.rto + 0.12 * max(MIN_RTO, new_rto)
        self.rto = max(MIN_RTO, min(self.rto, 1.5))

    # --- resend helpers ---
    def resend_packet(self, seq_num):
        if seq_num not in self.sent_packets:
            return False
        packet_data, send_time, retrans_count = self.sent_packets[seq_num]
        if retrans_count > 30:
            self.connection_dead = True
            return False
        del self.sent_packets[seq_num]
        self.sent_packets[seq_num] = (packet_data, time.time(), retrans_count + 1)
        try:
            self.socket.sendto(packet_data, self.client_addr)
            return True
        except OSError as e:
            if e.errno in [11, 35, 10035]:
                del self.sent_packets[seq_num]
                self.sent_packets[seq_num] = (packet_data, send_time, retrans_count)
                self.sent_packets.move_to_end(seq_num, last=False)
                return False
            if e.errno in [101, 111, 113]:
                self.connection_dead = True
                return False
            raise
        except Exception:
            self.connection_dead = True
            return False

    # --- CUBIC bookkeeping ---
    def enter_cubic_congestion_avoidance(self):
        self.t_last_congestion = time.time()
        new_w_max = self.cwnd_bytes
        
        if new_w_max < self.w_max_bytes:
            self.w_max_last_bytes = self.w_max_bytes
            self.w_max_bytes = new_w_max * (1.0 + self.beta_cubic) / 2.0
        else:
            self.w_max_last_bytes = self.w_max_bytes
            self.w_max_bytes = new_w_max
            
        self.ssthresh = max(int(self.cwnd_bytes * self.beta_cubic), MIN_CWND)
        w_mss = max(1.0, self.w_max_bytes / PAYLOAD_SIZE)
        num = w_mss * (1.0 - self.beta_cubic) / max(self.C, 1e-9)
        self.K = (num ** (1.0/3.0)) if num > 0 else 0.0

    # --- timeouts ---
    def handle_timeouts(self):
        now = time.time()
        timed = []
        for seq_num, (packet, send_time, retrans_count) in self.sent_packets.items():
            if now - send_time > self.rto:
                timed.append(seq_num)
            else:
                break
        if not timed:
            return
        
        oldest = timed[0]
        if not self.resend_packet(oldest):
            return
        
        if not self.in_rto_recovery:
            self.in_rto_recovery = True
            self.loss_events += 1
            self.consecutive_loss_free_acks = 0
            
            # Standard CUBIC response
            self.enter_cubic_congestion_avoidance()
            self.ssthresh = max(int(self.cwnd_bytes * self.beta_cubic), MIN_CWND)
            self.cwnd_bytes = self.ssthresh
            self.state = STATE_CONGESTION_AVOIDANCE
            self.log_cwnd()
        
        self.rto = min(self.rto * 1.5, 2.5)
        self.dup_ack_count = 0

    # --- send logic ---
    def get_next_content(self):
        if self.next_seq_num < self.file_size:
            start = self.next_seq_num
            end = min(start + PAYLOAD_SIZE, self.file_size)
            data = self.file_data[start:end]
            seq_num = start
            self.next_seq_num = end
            return data, seq_num, 0
        elif self.eof_sent_seq == -1:
            self.eof_sent_seq = self.file_size
            return b"EOF", self.eof_sent_seq, EOF_FLAG
        else:
            return None, -1, 0

    def send_new_data(self):
        inflight = self.next_seq_num - self.base_seq_num
        
        # Apply startup jitter ONCE
        if not self.applied_startup_jitter and inflight == 0:
            time.sleep(self.startup_jitter)
            self.applied_startup_jitter = True
        
        packets_sent = 0
        # Adaptive burst size based on cwnd
        burst_size = min(20, max(5, self.cwnd_bytes // (20 * MSS_BYTES)))
        
        while inflight < self.cwnd_bytes:
            if self.connection_dead:
                break
            data, seq_num, flags = self.get_next_content()
            if data is None:
                break
            header = self.pack_header(seq_num, 0, flags)
            packet = header + data
            
            try:
                self.socket.sendto(packet, self.client_addr)
                self.sent_packets[seq_num] = (packet, time.time(), 0)
                packets_sent += 1
                
                # Smart pacing: only when cwnd is large
                if packets_sent >= burst_size and self.cwnd_bytes > 80 * MSS_BYTES:
                    packets_sent = 0
                    # Minimal pacing - just enough for fairness
                    time.sleep(0.000003)  # 3 microseconds
                    
            except OSError as e:
                if e.errno in [11, 35, 10035]:
                    if flags & EOF_FLAG:
                        self.eof_sent_seq = -1
                    else:
                        self.next_seq_num = seq_num
                    break
                if e.errno in [101, 111, 113]:
                    self.connection_dead = True
                    break
                else:
                    raise
            except Exception:
                self.connection_dead = True
                break
            if flags & EOF_FLAG:
                break
            inflight = self.next_seq_num - self.base_seq_num

    # --- ACK processing ---
    def process_incoming_ack(self, packet):
        old_cwnd = self.cwnd_bytes
        old_ssthresh = self.ssthresh
        old_state = self.state

        header_fields = self.unpack_header(packet)
        if header_fields is None:
            return
        seq_num, cum_ack, flags, sack_start, sack_end = header_fields
        if not (flags & ACK_FLAG):
            return

        self.last_ack_time = time.time()

        # SACK processing
        if sack_start > 0 and sack_end > sack_start:
            if sack_start in self.sent_packets:
                self.sacked_packets.add(sack_start)

        # Duplicate ACK
        if cum_ack == self.base_seq_num:
            self.dup_ack_count += 1
            
            if self.dup_ack_count == 3:
                # Fast Retransmit
                self.loss_events += 1
                self.consecutive_loss_free_acks = 0
                
                # CUBIC reduction
                self.enter_cubic_congestion_avoidance()
                self.cwnd_bytes = self.ssthresh
                self.state = STATE_CONGESTION_AVOIDANCE
                
                # Resend one
                for seq in list(self.sent_packets.keys()):
                    if seq >= self.base_seq_num and seq not in self.sacked_packets:
                        self.resend_packet(seq)
                        break
                
                self.log_cwnd()

            if self.cwnd_bytes != old_cwnd or self.ssthresh != old_ssthresh or self.state != old_state:
                self.log_cwnd()
            return "CONTINUE"

        # New ACK
        if cum_ack > self.base_seq_num:
            self.in_rto_recovery = False
            self.dup_ack_count = 0
            self.consecutive_loss_free_acks += 1
            
            newly_acked = []
            for seq in list(self.sent_packets.keys()):
                if seq < cum_ack:
                    newly_acked.append(seq)

            acked_bytes = 0
            
            if newly_acked:
                newest = max(newly_acked)
                if newest in self.sent_packets:
                    packet_data, send_time, retrans_count = self.sent_packets[newest]
                    if retrans_count == 0:
                        ack_rtt_sample = time.time() - send_time
                        self.update_rto(ack_rtt_sample)
                        
                # Accumulate
                for s in newly_acked:
                    if s in self.sent_packets:
                        packet_data, stime, rc = self.sent_packets[s]
                        pkt_len = len(packet_data) - HEADER_SIZE
                        if pkt_len < 0: pkt_len = 0
                        acked_bytes += pkt_len

            # Cleanup
            for s in newly_acked:
                if s in self.sent_packets:
                    del self.sent_packets[s]
                self.sacked_packets.discard(s)

            self.base_seq_num = cum_ack

            # Cwnd update
            if self.state == STATE_SLOW_START:
                # HyStart check every 8 ACKs
                if self.hystart_enabled and not self.hystart_found and self.hystart_sample_count >= 8:
                    if (self.hystart_round_min_rtt < float('inf') and 
                        self.hystart_last_round_min_rtt < float('inf')):
                        
                        rtt_increase = self.hystart_round_min_rtt - self.hystart_last_round_min_rtt
                        # 1ms threshold
                        if rtt_increase > 0.001:
                            self.hystart_found = True
                            self.ssthresh = self.cwnd_bytes
                            self.state = STATE_CONGESTION_AVOIDANCE
                            self.enter_cubic_congestion_avoidance()
                    
                    # New round
                    self.hystart_last_round_min_rtt = self.hystart_round_min_rtt
                    self.hystart_round_min_rtt = float('inf')
                    self.hystart_sample_count = 0
                
                # Aggressive SS growth
                self.cwnd_bytes = min(self.cwnd_bytes + acked_bytes, MAX_CWND)
                
                # Traditional exit
                if self.cwnd_bytes >= self.ssthresh:
                    self.state = STATE_CONGESTION_AVOIDANCE
                    self.enter_cubic_congestion_avoidance()
                    
            elif self.state == STATE_CONGESTION_AVOIDANCE:
                if self.t_last_congestion == 0:
                    # AIMD - more aggressive multiplier
                    if self.cwnd_bytes > 0:
                        # 1.5x standard AIMD for better utilization
                        inc = 1.5 * (PAYLOAD_SIZE * PAYLOAD_SIZE) / float(self.cwnd_bytes)
                        self.ack_credits += inc * (acked_bytes / PAYLOAD_SIZE)
                    
                    if self.ack_credits >= 1.0:
                        increase = int(self.ack_credits)
                        self.cwnd_bytes = min(self.cwnd_bytes + increase, MAX_CWND)
                        self.ack_credits -= increase
                else:
                    # CUBIC growth
                    rtt_min_sec = self.rtt_min if self.rtt_min != float('inf') else max(self.srtt, 0.04)
                    t_elapsed = time.time() - self.t_last_congestion
                    
                    # TCP-friendly
                    alpha_cubic = 3.0 * self.beta_cubic / (2.0 - self.beta_cubic)
                    w_tcp = self.ssthresh + alpha_cubic * (t_elapsed / rtt_min_sec) * PAYLOAD_SIZE

                    # CUBIC
                    t_diff = t_elapsed - self.K
                    w_cubic = self.C * (t_diff ** 3) + self.w_max_bytes

                    # Target
                    target_cwnd = max(w_cubic, w_tcp)
                    target_cwnd = min(target_cwnd, MAX_CWND)
                    
                    # More aggressive increment calculation
                    if target_cwnd > self.cwnd_bytes:
                        cwnd_diff = target_cwnd - self.cwnd_bytes
                        
                        # Scale aggressiveness with distance
                        if cwnd_diff > 200 * MSS_BYTES:
                            # Very far - 4x aggressive
                            cnt = max(1.0, self.cwnd_bytes / (4.0 * cwnd_diff))
                        elif cwnd_diff > 80 * MSS_BYTES:
                            # Far - 2.5x aggressive
                            cnt = max(1.0, self.cwnd_bytes / (2.5 * cwnd_diff))
                        elif cwnd_diff > 20 * MSS_BYTES:
                            # Medium - 1.5x aggressive
                            cnt = max(1.0, self.cwnd_bytes / (1.5 * cwnd_diff))
                        else:
                            # Close - standard
                            cnt = max(1.0, self.cwnd_bytes / cwnd_diff)
                        
                        inc_per_ack = PAYLOAD_SIZE / cnt
                    else:
                        # At target - keep growing slowly
                        inc_per_ack = 1.2 * (PAYLOAD_SIZE * PAYLOAD_SIZE) / max(self.cwnd_bytes, MSS_BYTES)
                    
                    self.ack_credits += inc_per_ack * (acked_bytes / PAYLOAD_SIZE)
                    
                    if self.ack_credits >= 1.0:
                        increase = int(self.ack_credits)
                        self.cwnd_bytes = min(self.cwnd_bytes + increase, MAX_CWND)
                        self.ack_credits -= increase

            # EOF check
            if flags & EOF_FLAG and cum_ack > self.eof_sent_seq:
                if self.cwnd_bytes != old_cwnd or self.ssthresh != old_ssthresh or self.state != old_state:
                    self.log_cwnd()
                return "DONE"

        # Log
        if self.cwnd_bytes != old_cwnd or self.ssthresh != old_ssthresh or self.state != old_state:
            self.log_cwnd()

        return "CONTINUE"

    # --- logging ---
    def log_cwnd(self):
        if not self.cwnd_log_file:
            return
        try:
            ts = time.time() - self.start_time
            self.cwnd_log_file.write(f"{ts:.4f},{int(self.cwnd_bytes)},{int(self.ssthresh)},{self.get_state_str()}\n")
        except Exception:
            pass

    def plot_cwnd(self, log_filename):
        try:
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
        except Exception:
            return
        timestamps, cwnds_kb, ssth_kb, states = [], [], [], []
        try:
            with open(log_filename, 'r') as f:
                reader = csv.reader(f)
                next(reader)
                for row in reader:
                    timestamps.append(float(row[0]))
                    cwnds_kb.append(float(row[1]) / 1024.0)
                    ssth_kb.append(float(row[2]) / 1024.0)
                    states.append(row[3])
        except Exception:
            return
        if not timestamps:
            return
        plt.figure(figsize=(10,5))
        plt.plot(timestamps, cwnds_kb, drawstyle='steps-post', label='cwnd (KB)')
        plt.plot(timestamps, ssth_kb, drawstyle='steps-post', linestyle='--', label='ssthresh (KB)')
        plt.xlabel('Time (s)')
        plt.ylabel('KB')
        plt.legend()
        plt.grid(True)
        plt.tight_layout()
        plt.savefig(f"cwnd_plot_{self.port}.png")

    # --- main run loop ---
    def get_next_rto_delay(self):
        if not self.sent_packets:
            return 0.00003
        try:
            oldest = next(iter(self.sent_packets))
            _pkt, send_time, _ = self.sent_packets[oldest]
            expiry = send_time + self.rto
            delay = expiry - time.time()
            return max(0.00002, delay)
        except StopIteration:
            return 0.00003

    def run(self):
        try:
            readable, _, _ = select.select([self.socket], [], [], 15.0)
            if not readable:
                self.socket.close()
                return
            packet, self.client_addr = self.socket.recvfrom(1024)
        except Exception:
            self.socket.close()
            return

        self.start_time = time.time()
        self.last_ack_time = self.start_time

        try:
            self.cwnd_log_file = open(self.log_filename, "w", buffering=1)
            self.cwnd_log_file.write("timestamp_s,cwnd_bytes,ssthresh_bytes,state\n")
            self.log_cwnd()
        except IOError:
            self.cwnd_log_file = None

        running = True
        while running:
            if self.connection_dead:
                running = False
                break
            timeout_delay = self.get_next_rto_delay()
            try:
                readable, _, _ = select.select([self.socket], [], [], timeout_delay)
                if readable:
                    ack_packet, _ = self.socket.recvfrom(MSS_BYTES)
                    if self.process_incoming_ack(ack_packet) == "DONE":
                        running = False
            except socket.error as e:
                if e.errno not in [11, 35, 10035]:
                    running = False
            except Exception:
                running = False

            if not running:
                break

            self.handle_timeouts()
            self.send_new_data()

            if time.time() - self.last_ack_time > 30.0:
                running = False

        # Finish
        end_time = time.time()
        duration = end_time - self.start_time
        if duration == 0:
            duration = 1e-6
        throughput = (self.file_size * 8) / (duration * 1_000_000)
        print("---------------------------------")
        print("File transfer complete.")
        print(f"Duration: {duration:.2f} seconds")
        print(f"Total Data: {self.file_size} bytes")
        print(f"Throughput: {throughput:.2f} Mbps")
        print(f"Loss Events: {self.loss_events}")
        print("---------------------------------")

        if self.cwnd_log_file:
            self.cwnd_log_file.close()
            self.plot_cwnd(self.log_filename)
        time.sleep(0.5)
        self.socket.close()

def main():
    if len(sys.argv) != 3:
        print("Usage: python3 p2_server.py <SERVER_IP> <SERVER_PORT>")
        sys.exit(1)
    server_ip = sys.argv[1]
    server_port = int(sys.argv[2])
    s = Server(server_ip, server_port)
    s.run()

if __name__ == "__main__":
    main()