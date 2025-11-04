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

# --- Constants ---
HEADER_FORMAT = "!IIHII2x"
HEADER_SIZE = 20
MSS_BYTES = 1200
PAYLOAD_SIZE = MSS_BYTES - HEADER_SIZE
MAX_CWND = 8 * 1024 * 1024

# Flags
SYN_FLAG = 0x1
ACK_FLAG = 0x2
EOF_FLAG = 0x4

# States
STATE_SLOW_START = 1
STATE_CONGESTION_AVOIDANCE = 2
# STATE_FAST_RECOVERY = 3

# RTO constants
ALPHA = 0.125  # Standard: 1/8
BETA = 0.25    # Standard: 1/4
K = 4.0        # Standard: 4
INITIAL_RTO = 0.15 # 150ms is fine for this low-latency network
MIN_RTO = 0.05


# Rate estimator window (seconds)
BW_WINDOW = 0.5  # 500 ms for smoothing and reactivity

# Minimum cwnd in bytes (stay able to probe)
MIN_CWND = 4 * MSS_BYTES

class Server:
    def __init__(self, ip, port):
        self.ip = ip
        self.port = int(port)
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind((self.ip, self.port))
        self.socket.setblocking(False)
        self.client_addr = None
        print(f"Server started on {self.ip}:{self.port}")

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

        # Congestion control
        self.state = STATE_SLOW_START
        offset_factor = (self.port % 7) / 7.0
        self.cwnd_bytes = (6 + 4 * offset_factor) * MSS_BYTES
        self.startup_delay = offset_factor * 0.0015  # tiny deterministic phase (ms-scale)
        self.ssthresh = 2048 * 1024

        # RTO
        self.rto = INITIAL_RTO
        self.srtt = 0.0
        self.rttvar = 0.0
        self.rtt_min = float('inf')

        # Sent buffer (seq -> (packet, send_time, retrans_count))
        self.sent_packets = collections.OrderedDict()

        # SACK & dup-acks
        self.dup_ack_count = 0
        self.sacked_packets = set()

        # bookkeeping
        self.start_time = 0.0
        self.last_ack_time = 0.0
        self.ack_credits = 0.0

        # CUBIC params (still used as loss fallback)
        self.C = 0.4
        self.beta_cubic = 0.8
        self.w_max_bytes = 0.0
        self.w_max_last_bytes = 0.0
        self.t_last_congestion = 0.0
        self.K = 0.0

        # bandwidth estimator: deque of (timestamp, bytes_acked)
        self.acked_history = deque()
        self.bw_est_bytes_per_sec = 0.0

        # initial deterministic phase offset used in enter_cubic
        self.phase_offset = (self.port % 5) * 0.025

        # logging
        self.cwnd_log_file = None
        self.log_filename = f"cwnd_log_{self.port}.csv"

    # --- helpers ---
    def get_state_str(self):
        if self.state == STATE_SLOW_START: return "SS"
        if self.state == STATE_CONGESTION_AVOIDANCE:
            return "CUBIC" if self.t_last_congestion > 0 else "CA"
        # if self.state == STATE_FAST_RECOVERY: return "FR"
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
        if self.srtt == 0.0:
            self.srtt = rtt_sample
            self.rttvar = rtt_sample / 2.0
        else:
            self.rttvar = (1 - BETA) * self.rttvar + BETA * abs(self.srtt - rtt_sample)
            self.srtt = (1 - ALPHA) * self.srtt + ALPHA * rtt_sample

        # Smoothed RTO to avoid violent swings
        new_rto = self.srtt + K * self.rttvar
        # initialize rto on first sample:
        if self.rto == 0:
            self.rto = max(MIN_RTO, new_rto)
        else:
            self.rto = 0.85 * self.rto + 0.15 * max(MIN_RTO, new_rto)
        self.rto = max(MIN_RTO, self.rto)

    # --- resend helpers ---
    def resend_packet(self, seq_num):
        if seq_num not in self.sent_packets:
            return False
        packet_data, send_time, retrans_count = self.sent_packets[seq_num]
        if retrans_count > 15:
            print("Packet resend limit reached. Aborting.")
            self.connection_dead = True
            return False
        # update send time and count
        del self.sent_packets[seq_num]
        self.sent_packets[seq_num] = (packet_data, time.time(), retrans_count + 1)
        try:
            self.socket.sendto(packet_data, self.client_addr)
            return True
        except OSError as e:
            if e.errno in [11, 35, 10035]:
                # put it back front
                del self.sent_packets[seq_num]
                self.sent_packets[seq_num] = (packet_data, send_time, retrans_count)
                self.sent_packets.move_to_end(seq_num, last=False)
                return False
            if e.errno in [101, 111, 113]:
                print(f"Client unreachable on resend: {e}. Aborting.")
                self.connection_dead = True
                return False
            raise
        except Exception as e:
            print(f"Error in resend_packet: {e}")
            self.connection_dead = True
            return False

    def resend_missing_packet(self):
        count = 0
        for seq_num in list(self.sent_packets.keys()):
            if seq_num >= self.base_seq_num and seq_num not in self.sacked_packets:
                self.resend_packet(seq_num)
                count += 1
                if count >= 2: break

    # --- CUBIC bookkeeping ---
    def enter_cubic_congestion_avoidance(self):
        # deterministic phase shift
        self.t_last_congestion = time.time() - self.phase_offset
        new_w_max = self.cwnd_bytes
        if new_w_max < self.w_max_bytes:
            self.w_max_last_bytes = self.w_max_bytes
            self.w_max_bytes = new_w_max * (1.0 + self.beta_cubic) / 2.0
        else:
            self.w_max_last_bytes = self.w_max_bytes
            self.w_max_bytes = new_w_max
        self.ssthresh = max(int(self.cwnd_bytes * self.beta_cubic), 2 * MSS_BYTES)
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
        print(f"[TIMEOUT] base={self.base_seq_num}, cwnd={int(self.cwnd_bytes)}, srtt={self.srtt:.4f}, rto={self.rto:.3f}, inflight={len(self.sent_packets)}")
        print("Timeout (RTO). Reducing window (NOT resetting to 1).")
        
        # This is the CUBIC response (beta_cubic = 0.7)
        # This sets self.ssthresh = max(int(self.cwnd_bytes * 0.7), 2 * MSS_BYTES)
        # It also resets the CUBIC clock.
        self.enter_cubic_congestion_avoidance()

        # --- THIS IS THE KEY CHANGE ---
        # DO NOT reset cwnd to 1 MSS. This is catastrophic for performance.
        # Instead, set cwnd to the new ssthresh (which is 0.7 * old_cwnd) 
        # and enter Congestion Avoidance directly.
        self.cwnd_bytes = self.ssthresh
        self.state = STATE_CONGESTION_AVOIDANCE
        # -------------------------------

        self.cwnd_bytes = max(self.cwnd_bytes, MIN_CWND)
        self.ssthresh = max(self.ssthresh, MIN_CWND)

        self.rto = min(self.rto * 1.5, 2.0) # Back off RTO timer
        self.dup_ack_count = 0
        self.log_cwnd()


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
        # small deterministic startup jitter (only while there is no inflight)
        if inflight == 0 and self.startup_delay > 0:
            time.sleep(self.startup_delay)

        # apply rate targeting on each send cycle if we have an estimator
        # self._apply_rate_targeting()

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
                print(f"[SEND] seq={seq_num}, inflight={self.next_seq_num - self.base_seq_num}, cwnd={int(self.cwnd_bytes)}, state={self.get_state_str()}")
            except OSError as e:
                if e.errno in [11, 35, 10035]:
                    if flags & EOF_FLAG:
                        self.eof_sent_seq = -1
                    else:
                        self.next_seq_num = seq_num
                    break
                if e.errno in [101, 111, 113]:
                    print(f"Client unreachable on send: {e}. Aborting transfer.")
                    self.connection_dead = True
                    break
                else:
                    raise
            except Exception as e:
                print(f"Error in send_new_data: {e}")
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

        # SACK processing (single SACK support)
        if sack_start > 0 and sack_end > sack_start:
            if sack_start in self.sent_packets:
                self.sacked_packets.add(sack_start)

        # Duplicate ACK
        if cum_ack == self.base_seq_num:
            self.dup_ack_count += 1
            
            if self.dup_ack_count == 3:
                print("3 Dup-ACKs. Performing CUBIC reduction (Fast Retransmit).")
                
                # This function sets ssthresh = cwnd * beta_cubic (0.7) 
                # and resets the CUBIC growth curve.
                self.enter_cubic_congestion_avoidance() 
                
                # Set the new cwnd to the new ssthresh (multiplicative decrease).
                self.cwnd_bytes = self.ssthresh
                
                # We STAY in STATE_CONGESTION_AVOIDANCE.
                self.resend_missing_packet()
            
            # If dup_ack_count > 3, CUBIC does nothing. It waits for the
            # new ACK to signal recovery from this loss event.

            if self.cwnd_bytes != old_cwnd or self.ssthresh != old_ssthresh or self.state != old_state:
                self.log_cwnd()
            return "CONTINUE"

        # New ACK (cumulative ack advanced)
        if cum_ack > self.base_seq_num:
            self.dup_ack_count = 0
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
                # accumulate acked bytes
                for s in newly_acked:
                    if s in self.sent_packets:
                        packet_data, stime, rc = self.sent_packets[s]
                        pkt_len = len(packet_data) - HEADER_SIZE
                        if pkt_len < 0: pkt_len = 0
                        acked_bytes += pkt_len

            # cleanup
            for s in newly_acked:
                if s in self.sent_packets:
                    del self.sent_packets[s]
                self.sacked_packets.discard(s)

            # record acked bytes for bandwidth estimator
            if acked_bytes > 0:
                self._record_acked_bytes(acked_bytes)

            # update base
            self.base_seq_num = cum_ack
            print(f"[ACK] base={self.base_seq_num}, cwnd={int(self.cwnd_bytes)}, ssthresh={int(self.ssthresh)}, state={self.get_state_str()}, srtt={self.srtt:.4f}, rto={self.rto:.3f}, bw_est_Mbps={(self.bw_est_bytes_per_sec*8)/1e6:.3f}")

            # Update cwnd: slow start or congestion avoidance
            if self.state == STATE_SLOW_START:
                self.cwnd_bytes = min(self.cwnd_bytes + acked_bytes, MAX_CWND)
                # early switch to CA after modest cwnd (helps avoid synchronized overshoot)
                if self.cwnd_bytes >= self.ssthresh:
                    self.state = STATE_CONGESTION_AVOIDANCE
                    self.enter_cubic_congestion_avoidance()
            elif self.state == STATE_CONGESTION_AVOIDANCE:
                if self.t_last_congestion == 0:
                    # Reno-like additive increase
                    if self.cwnd_bytes > 0:
                        inc = 1.2 * (PAYLOAD_SIZE * PAYLOAD_SIZE) / float(self.cwnd_bytes)
                        self.ack_credits += inc
                    if self.ack_credits >= 1.0:
                        i = int(self.ack_credits)
                        self.cwnd_bytes = min(self.cwnd_bytes + i, MAX_CWND)
                        self.ack_credits -= i
                else:
                    # CUBIC-like growth
                    rtt_min_sec = self.rtt_min if self.rtt_min != float('inf') else max(self.srtt, INITIAL_RTO)
                    t_elapsed = time.time() - self.t_last_congestion
                    
                    # Reno-friendly growth curve (W_tcp)
                    alpha_cubic = (3.0 * self.beta_cubic / (2.0 - self.beta_cubic))
                    w_tcp = self.ssthresh + alpha_cubic * (t_elapsed / rtt_min_sec) * PAYLOAD_SIZE

                    # CUBIC growth curve (W_cubic)
                    # We use t_elapsed to get the target for *now*
                    t_now_minus_K = t_elapsed - self.K
                    w_cubic_now = self.C * (t_now_minus_K ** 3) + self.w_max_bytes

                    # The target cwnd is the larger of the two
                    target_cwnd = max(w_cubic_now, w_tcp)
                    target_cwnd = min(target_cwnd, MAX_CWND) # Don't exceed max

                    # --- THIS IS THE FIX ---
                    # Now, grow towards the target_cwnd using the ack_credits system.
                    
                    inc_per_rtt = 0.0
                    if self.cwnd_bytes < target_cwnd:
                        # We are below the target, grow fast.
                        # Increment is (target - current) / (current / MSS)
                        # This scales the growth to be faster as the gap is larger
                        inc_per_rtt = (target_cwnd - self.cwnd_bytes) * PAYLOAD_SIZE / self.cwnd_bytes
                    else:
                        # We are at or above the target (e.g., in the "concave" region)
                        # Just do standard additive increase.
                        inc_per_rtt = (PAYLOAD_SIZE * PAYLOAD_SIZE) / self.cwnd_bytes

                    # Scale the RTT-based increment to a per-ACK increment
                    inc_per_ack = inc_per_rtt * PAYLOAD_SIZE / self.cwnd_bytes
                    self.ack_credits += inc_per_ack

                    # Apply the credits
                    if self.ack_credits >= 1.0:
                        i = int(self.ack_credits)
                        self.cwnd_bytes = min(self.cwnd_bytes + i, MAX_CWND)
                        self.ack_credits -= i

            # After ack processing, also nudge cwnd toward rate-target (if estimator present)
            # self._apply_rate_targeting()

            # final check for EOF ack
            if flags & EOF_FLAG and cum_ack > self.eof_sent_seq:
                print("Final EOF ACK received.")
                if self.cwnd_bytes != old_cwnd or self.ssthresh != old_ssthresh or self.state != old_state:
                    self.log_cwnd()
                return "DONE"

        # log changes
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
            print("[Plotting] matplotlib not available.")
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
        print(f"[Plotting] CWND plot saved to cwnd_plot_{self.port}.png")

    # --- main run loop ---
    def get_next_rto_delay(self):
        if not self.sent_packets:
            return 0.001
        try:
            oldest = next(iter(self.sent_packets))
            _pkt, send_time, _ = self.sent_packets[oldest]
            expiry = send_time + self.rto
            delay = expiry - time.time()
            return max(0.001, delay)
        except StopIteration:
            return 1.0

    def run(self):
        try:
            print("Waiting for client request...")
            readable, _, _ = select.select([self.socket], [], [], 15.0)
            if not readable:
                print("Timed out waiting for initial client.")
                self.socket.close()
                return
            packet, self.client_addr = self.socket.recvfrom(1024)
            print(f"Client connected from {self.client_addr}")
        except Exception as e:
            print(f"Error receiving initial request: {e}")
            self.socket.close()
            return

        self.start_time = time.time()
        self.last_ack_time = self.start_time

        try:
            self.cwnd_log_file = open(self.log_filename, "w", buffering=1)
            self.cwnd_log_file.write("timestamp_s,cwnd_bytes,ssthresh_bytes,state\n")
            print(f"Logging CWND to {self.log_filename}")
            self.log_cwnd()
        except IOError:
            self.cwnd_log_file = None

        running = True
        while running:
            if self.connection_dead:
                print("Connection dead, shutting down.")
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
                    print(f"Socket error in main loop: {e}")
                    running = False
            except Exception as e:
                print(f"Error in main loop: {e}")
                time.sleep(0.001)
                running = False

            if not running:
                break

            self.handle_timeouts()
            self.send_new_data()

            if time.time() - self.last_ack_time > 30.0:
                print("Client timed out (30s). Shutting down.")
                running = False

        # finish
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
        print("---------------------------------")

        if self.cwnd_log_file:
            self.cwnd_log_file.close()
            print(f"CWND log saved to {self.log_filename}")
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