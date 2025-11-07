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
MAX_CWND = 8 * 1024 * 1024

# Flags
SYN_FLAG = 0x1
ACK_FLAG = 0x2
EOF_FLAG = 0x4

# States
STATE_SLOW_START = 1
STATE_CONGESTION_AVOIDANCE = 2

ALPHA = 0.125
BETA = 0.25  
K = 4.0      
INITIAL_RTO = 0.3
MIN_RTO = 0.1

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
        self.in_rto_recovery = False
        self.startup_delay = random.uniform(0, 0.005)
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

        self.q_delay = 0.0

        # Congestion control
        self.state = STATE_SLOW_START
        self.cwnd_bytes = 32 * MSS_BYTES
        self.ssthresh =  600 * MSS_BYTES

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
        self.beta_cubic = 0.7
        self.w_max_bytes = 0.0
        self.w_max_last_bytes = 0.0
        self.t_last_congestion = 0.0
        self.K = 0.0

        # --- Queue delay gradient tracking ---
        self.prev_q_delay = 0.0
        self.q_delay_time = time.time()
        self.q_grad_threshold = 0.004  # sec/sec; threshold for rapid queue buildup (~4ms per second)
        self.q_grad_reduction = 0.7    # cwnd reduction multiplier (reduce by 30%)


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
        if self.srtt == 0.0:
            self.srtt = rtt_sample
            self.rttvar = rtt_sample / 2.0
        else:
            # More aggressive filtering to handle UDP-induced variance
            self.rttvar = (1 - BETA) * self.rttvar + BETA * abs(self.srtt - rtt_sample)
            self.srtt = (1 - ALPHA) * self.srtt + ALPHA * rtt_sample
        

        # Update our "sensor" for queuing delay
        if self.rtt_min != float('inf'):
            self.q_delay = max(0.0, self.srtt - self.rtt_min)

            # --- Compute queue delay gradient ---
            now = time.time()
            dt = max(1e-6, now - self.q_delay_time)
            q_grad = (self.q_delay - self.prev_q_delay) / dt
            self.prev_q_delay = self.q_delay
            self.q_delay_time = now

            # --- React to fast-growing queue ---
            if q_grad > self.q_grad_threshold and self.cwnd_bytes > 8 * MSS_BYTES:
                old_cwnd = self.cwnd_bytes
                self.cwnd_bytes = max(int(self.cwnd_bytes * self.q_grad_reduction), 4 * MSS_BYTES)
                self.ssthresh = max(int(self.cwnd_bytes * 0.8), 8 * MSS_BYTES)
                self.state = STATE_CONGESTION_AVOIDANCE
                self.enter_cubic_congestion_avoidance()
                self.log_cwnd()
                # print(f"[QDELAY BACKOFF] q_grad={q_grad:.5f}, cwnd {old_cwnd}->{self.cwnd_bytes}")


        new_rto = self.srtt + K * self.rttvar
        
        if self.rto == 0:
            self.rto = max(MIN_RTO, new_rto)
        else:
            # Less smoothing - react faster to changes
            self.rto = 0.875 * self.rto + 0.125 * max(MIN_RTO, new_rto)
        
        self.rto = max(MIN_RTO, min(self.rto, 3.0))  # Reduce max RTO from 3.0 to 2.0

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
        self.t_last_congestion = time.time()
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
        
        if not self.in_rto_recovery:
            print("[TIMEOUT] RTO. Reducing cwnd.")
            self.in_rto_recovery = True
            
            q_delay_threshold = self.rtt_min * 0.20 if self.rtt_min != float('inf') else 0.05
            beta_reduction = 0.7 if self.q_delay > q_delay_threshold else 0.9
            
            self.ssthresh = max(int(self.cwnd_bytes * beta_reduction), 8 * MSS_BYTES)

            self.cwnd_bytes = self.ssthresh
            self.cwnd_bytes = max(self.cwnd_bytes, 4 * MSS_BYTES)
            self.state = STATE_CONGESTION_AVOIDANCE
            self.enter_cubic_congestion_avoidance()
            self.log_cwnd()
            
            # Faster RTO recovery
            self.rto = min(self.rto * 1.5, 3.0)
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

        pacing_delay = 0.0
        if self.srtt > 0 and self.cwnd_bytes > 0:
            packets_in_cwnd = self.cwnd_bytes / PAYLOAD_SIZE
            pacing_delay = self.srtt / (2.0 * packets_in_cwnd)  # Send at 2x rate
            pacing_delay = max(0.0001, min(pacing_delay, 0.001))  # Clamp to reasonable

        while inflight < self.cwnd_bytes:
            if self.connection_dead:
                break
            
            if self.srtt > 0 and self.cwnd_bytes > 0:
                packets_in_cwnd = max(1.0, self.cwnd_bytes / PAYLOAD_SIZE)
                pacing_delay = max(0.0003, self.srtt / packets_in_cwnd)
                pacing_delay *= random.uniform(0.92, 1.08)
                time.sleep(pacing_delay)


            data, seq_num, flags = self.get_next_content()
            if data is None:
                break
            header = self.pack_header(seq_num, 0, flags)
            packet = header + data
            try:
                self.socket.sendto(packet, self.client_addr)
                self.sent_packets[seq_num] = (packet, time.time(), 0)
                # print(f"[SEND] seq={seq_num}, inflight={self.next_seq_num - self.base_seq_num}, cwnd={int(self.cwnd_bytes)}, state={self.get_state_str()}")
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
                print("3 Dup-ACKs. Fast Retransmit.")
                
                # Use the same logic as in the RTO handler
                q_delay_threshold = self.rtt_min * 0.20 if self.rtt_min != float('inf') else 0.05
                beta_reduction = 0.7 if self.q_delay > q_delay_threshold else 0.9
                
                self.ssthresh = max(int(self.cwnd_bytes * beta_reduction), 8 * MSS_BYTES)

                self.cwnd_bytes = self.ssthresh
                self.cwnd_bytes = max(self.cwnd_bytes, 4 * MSS_BYTES)
                self.state = STATE_CONGESTION_AVOIDANCE
                self.enter_cubic_congestion_avoidance()
                
                # Resend missing packet
                for seq_num in list(self.sent_packets.keys()):
                    if seq_num >= self.base_seq_num and seq_num not in self.sacked_packets:
                        self.resend_packet(seq_num)
                        break
                
                self.log_cwnd()

            if self.cwnd_bytes != old_cwnd or self.ssthresh != old_ssthresh or self.state != old_state:
                self.log_cwnd()
            return "CONTINUE"

        # New ACK (cumulative ack advanced)
        if cum_ack > self.base_seq_num:
            self.in_rto_recovery = False
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


            # update base
            self.base_seq_num = cum_ack
            # print(f"[ACK] base={self.base_seq_num}, cwnd={int(self.cwnd_bytes)}, ssthresh={int(self.ssthresh)}, state={self.get_state_str()}, srtt={self.srtt:.4f}, rto={self.rto:.3f}")

            # Update cwnd: slow start or congestion avoidance
            if self.state == STATE_SLOW_START:
                self.cwnd_bytes = min(self.cwnd_bytes + acked_bytes, MAX_CWND)
                self.cwnd_bytes = max(self.cwnd_bytes, 4 * MSS_BYTES)
                # early switch to CA after modest cwnd (helps avoid synchronized overshoot)
                if self.cwnd_bytes >= self.ssthresh:
                    self.state = STATE_CONGESTION_AVOIDANCE
                    self.enter_cubic_congestion_avoidance()
            elif self.state == STATE_CONGESTION_AVOIDANCE:
                # CUBIC-like growth
                rtt_min_sec = self.rtt_min if self.rtt_min != float('inf') else max(self.srtt, INITIAL_RTO)
                t_elapsed = time.time() - self.t_last_congestion
                
                # If queue is building, be less aggressive (alpha=3.0)
                # If queue is empty, be super aggressive (alpha=6.0)
                q_delay_threshold = self.rtt_min * 0.20 if self.rtt_min != float('inf') else 0.05
                alpha_multiplier = 3.0 if self.q_delay > q_delay_threshold else 6.0
                
                alpha_cubic = (alpha_multiplier * self.beta_cubic / (2.0 - self.beta_cubic))
                w_tcp = self.ssthresh + alpha_cubic * (t_elapsed / rtt_min_sec) * PAYLOAD_SIZE

                # CUBIC growth curve
                t_now_minus_K = t_elapsed - self.K
                w_cubic_now = self.C * (t_now_minus_K ** 3) + self.w_max_bytes

                # Use max of TCP-friendly and CUBIC window
                target_cwnd = max(w_cubic_now, w_tcp)
                target_cwnd = min(target_cwnd, MAX_CWND)
                
                inc_per_ack = 0.0
                if self.cwnd_bytes < target_cwnd:
                    # Also scale the "super aggressive" part based on sensed queue delay
                    growth_multiplier = 1.5 if self.q_delay > q_delay_threshold else 3.0
                    inc_per_ack = growth_multiplier * (target_cwnd - self.cwnd_bytes) * PAYLOAD_SIZE / self.cwnd_bytes
                else:
                    inc_per_ack = (PAYLOAD_SIZE * PAYLOAD_SIZE) / self.cwnd_bytes
                
                self.ack_credits += inc_per_ack
                
                if self.ack_credits >= 1.0:
                    i = int(self.ack_credits)
                    self.cwnd_bytes = min(self.cwnd_bytes + i, MAX_CWND)
                    self.cwnd_bytes = max(self.cwnd_bytes, 4 * MSS_BYTES)

                    self.ack_credits -= i

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
            self.cwnd_log_file.write(f"{ts},{self.cwnd_bytes},{self.srtt},{self.q_delay},{self.prev_q_delay}\n")
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
            hs_packet, self.client_addr = self.socket.recvfrom(1024)
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