#!/usr/bin/env python3
import socket
import sys
import time
import struct
import math
import csv
import select
import collections
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

        # Congestion control
        self.state = STATE_SLOW_START
        self.cwnd_bytes = 32 * MSS_BYTES
        self.ssthresh = 600 * MSS_BYTES

        # RTT/RTO
        self.rto = INITIAL_RTO
        self.srtt = 0.0
        self.rttvar = 0.0
        self.rtt_min = float('inf')
        self.q_delay = 0.0

        # Buffers
        self.sent_packets = collections.OrderedDict()
        self.dup_ack_count = 0
        self.sacked_packets = set()

        # bookkeeping
        self.start_time = 0.0
        self.last_ack_time = 0.0
        self.ack_credits = 0.0

        # CUBIC parameters
        self.C = 0.4
        self.beta_cubic = 0.7
        self.w_max_bytes = 0.0
        self.w_max_last_bytes = 0.0
        self.t_last_congestion = 0.0
        self.K = 0.0

        # --- Queue delay gradient tracking (conservative defaults) ---
        self.prev_q_delay = 0.0
        self.q_delay_time = time.time()
        self.q_grad_threshold = 0.03      # higher threshold -> less sensitivity
        self.q_grad_reduction = 0.90      # gentler backoff (10% cut)
        self.q_grad_filter = 0.75         # stronger smoothing
        self.q_grad = 0.0
        self.last_grad_adjust = 0.0       # timestamp of last gradient-based adjustment

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

    # --- RTT / RTO updates ---
    def update_rto(self, rtt_sample):
        # Update min/srtt/rttvar
        self.rtt_min = min(self.rtt_min, rtt_sample)
        if self.srtt == 0.0:
            self.srtt = rtt_sample
            self.rttvar = rtt_sample / 2.0
        else:
            self.rttvar = (1 - BETA) * self.rttvar + BETA * abs(self.srtt - rtt_sample)
            self.srtt = (1 - ALPHA) * self.srtt + ALPHA * rtt_sample

        # Update q_delay and its smoothed gradient
        if self.rtt_min != float('inf'):
            self.q_delay = max(0.0, self.srtt - self.rtt_min)

            now = time.time()
            dt = max(1e-6, now - self.q_delay_time)
            q_grad_raw = (self.q_delay - self.prev_q_delay) / dt
            # exponential smoothing (stronger)
            self.q_grad = self.q_grad_filter * self.q_grad + (1.0 - self.q_grad_filter) * q_grad_raw
            q_grad = self.q_grad
            self.prev_q_delay = self.q_delay
            self.q_delay_time = now

            # Conservative refractory: at least 50 ms or 3 * srtt
            refractory = max(0.05, 3.0 * max(self.srtt, 0.001))

            # --- Multiplicative backoff when queues grow quickly ---
            if q_grad > self.q_grad_threshold and (now - self.last_grad_adjust) > refractory:
                old_cwnd = self.cwnd_bytes
                # gentler reduction and floor
                self.cwnd_bytes = max(int(self.cwnd_bytes * self.q_grad_reduction), 8 * MSS_BYTES)
                self.ssthresh = max(int(self.cwnd_bytes * 0.9), 8 * MSS_BYTES)
                self.state = STATE_CONGESTION_AVOIDANCE
                self.enter_cubic_congestion_avoidance()
                self.log_cwnd()
                self.last_grad_adjust = now
                # print(f"[BACKOFF] q_grad={q_grad:.6f}, cwnd {old_cwnd}->{self.cwnd_bytes}")

            # --- Controlled expansion when queue drains ---
            # Use cautious growth: limited additive + limited multiplicative (<= +25%)
            if q_grad < - (self.q_grad_threshold / 2.0) and self.state == STATE_CONGESTION_AVOIDANCE and (now - self.last_grad_adjust) > refractory:
                old_cwnd = self.cwnd_bytes
                # multiplicative cap: up to +25%
                mult_cap = 1.25
                # additive cap in bytes (so small flows still gain)
                additive_cap = 32 * PAYLOAD_SIZE
                # compute multiplicative candidate and additive candidate
                mult_candidate = int(self.cwnd_bytes * min(mult_cap, 1.0 + abs(q_grad) * 10.0))
                add_candidate = self.cwnd_bytes + additive_cap
                # choose conservative increase: the smaller of the two growths beyond current cwnd
                target = min(mult_candidate, add_candidate)
                # ensure not decreasing
                if target <= self.cwnd_bytes:
                    target = self.cwnd_bytes + min(additive_cap, int(0.05 * self.cwnd_bytes) + PAYLOAD_SIZE)
                # cap and apply
                self.cwnd_bytes = min(target, MAX_CWND)
                self.cwnd_bytes = max(self.cwnd_bytes, 8 * MSS_BYTES)
                self.ssthresh = max(int(self.cwnd_bytes * 0.95), 8 * MSS_BYTES)
                self.log_cwnd()
                self.last_grad_adjust = now
                # print(f"[EXPAND] q_grad={q_grad:.6f}, cwnd {old_cwnd}->{self.cwnd_bytes}")

        # --- RTO update (stable smoothing) ---
        new_rto = self.srtt + K * self.rttvar
        if self.rto == 0:
            self.rto = max(MIN_RTO, new_rto)
        else:
            self.rto = 0.9 * self.rto + 0.1 * max(MIN_RTO, new_rto)
        self.rto = max(MIN_RTO, min(self.rto, 3.0))

    # --- resend helpers ---
    def resend_packet(self, seq_num):
        if seq_num not in self.sent_packets:
            return False
        packet_data, send_time, retrans_count = self.sent_packets[seq_num]
        if retrans_count > 15:
            print("Packet resend limit reached. Aborting.")
            self.connection_dead = True
            return False
        del self.sent_packets[seq_num]
        self.sent_packets[seq_num] = (packet_data, time.time(), retrans_count + 1)
        try:
            self.socket.sendto(packet_data, self.client_addr)
            return True
        except OSError:
            return False

    def resend_missing_packet(self):
        count = 0
        for seq_num in list(self.sent_packets.keys()):
            if seq_num >= self.base_seq_num and seq_num not in self.sacked_packets:
                self.resend_packet(seq_num)
                count += 1
                if count >= 2:
                    break

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
        self.ssthresh = max(int(self.cwnd_bytes * self.beta_cubic), 2 * MSS_BYTES)
        w_mss = max(1.0, self.w_max_bytes / PAYLOAD_SIZE)
        num = w_mss * (1.0 - self.beta_cubic) / max(self.C, 1e-9)
        self.K = (num ** (1.0 / 3.0)) if num > 0 else 0.0

    # --- timeouts ---
    def handle_timeouts(self):
        now = time.time()
        for seq_num, (pkt, send_time, _) in list(self.sent_packets.items()):
            if now - send_time > self.rto:
                # Softer timeout reaction: cut to 85% and keep floor
                print("[TIMEOUT] reducing cwnd (soft).")
                self.cwnd_bytes = max(int(self.cwnd_bytes * 0.85), 8 * MSS_BYTES)
                self.ssthresh = max(int(self.cwnd_bytes * 0.9), 8 * MSS_BYTES)
                self.enter_cubic_congestion_avoidance()
                self.log_cwnd()
                self.resend_packet(seq_num)
                break

    # --- send logic ---
    def get_next_content(self):
        if self.next_seq_num < self.file_size:
            start = self.next_seq_num
            end = min(start + PAYLOAD_SIZE, self.file_size)
            data = self.file_data[start:end]
            seq = start
            self.next_seq_num = end
            return data, seq, 0
        elif self.eof_sent_seq == -1:
            self.eof_sent_seq = self.file_size
            return b"EOF", self.eof_sent_seq, EOF_FLAG
        else:
            return None, -1, 0

    def send_new_data(self):
        inflight = self.next_seq_num - self.base_seq_num
        while inflight < self.cwnd_bytes:
            if self.connection_dead:
                break
            if self.srtt > 0 and self.cwnd_bytes > 0:
                pkts = max(1.0, self.cwnd_bytes / PAYLOAD_SIZE)
                # moderate pacing (not too aggressive)
                pacing_delay = max(0.0001, self.srtt / (2.2 * pkts))
                pacing_delay *= random.uniform(0.92, 1.08)
                time.sleep(pacing_delay)
            data, seq, flags = self.get_next_content()
            if data is None:
                break
            packet = self.pack_header(seq, 0, flags) + data
            try:
                self.socket.sendto(packet, self.client_addr)
                self.sent_packets[seq] = (packet, time.time(), 0)
            except Exception:
                self.connection_dead = True
                break
            if flags & EOF_FLAG:
                break
            inflight = self.next_seq_num - self.base_seq_num

    # --- ACK processing ---
    def process_incoming_ack(self, packet):
        header_fields = self.unpack_header(packet)
        if header_fields is None:
            return
        seq_num, cum_ack, flags, sack_start, sack_end = header_fields
        if not (flags & ACK_FLAG):
            return

        self.last_ack_time = time.time()

        # SACK
        if sack_start > 0 and sack_end > sack_start:
            if sack_start in self.sent_packets:
                self.sacked_packets.add(sack_start)

        # Duplicate ACK
        if cum_ack == self.base_seq_num:
            self.dup_ack_count += 1
            if self.dup_ack_count == 3:
                # fast retransmit: softer reduction
                self.cwnd_bytes = max(int(self.cwnd_bytes * 0.85), 8 * MSS_BYTES)
                self.enter_cubic_congestion_avoidance()
                self.log_cwnd()
            return "CONTINUE"

        # New ACK
        if cum_ack > self.base_seq_num:
            self.in_rto_recovery = False
            self.dup_ack_count = 0
            newly_acked = [s for s in list(self.sent_packets.keys()) if s < cum_ack]
            acked_bytes = 0
            if newly_acked:
                newest = max(newly_acked)
                if newest in self.sent_packets:
                    p_data, send_time, rc = self.sent_packets[newest]
                    if rc == 0:
                        self.update_rto(time.time() - send_time)
                for s in newly_acked:
                    if s in self.sent_packets:
                        pktdata, st, rc = self.sent_packets[s]
                        pkt_len = len(pktdata) - HEADER_SIZE
                        if pkt_len < 0: pkt_len = 0
                        acked_bytes += pkt_len

            for s in newly_acked:
                self.sent_packets.pop(s, None)
                self.sacked_packets.discard(s)

            self.base_seq_num = cum_ack

            # Update cwnd
            if self.state == STATE_SLOW_START:
                self.cwnd_bytes = min(self.cwnd_bytes + acked_bytes, MAX_CWND)
                self.cwnd_bytes = max(self.cwnd_bytes, 8 * MSS_BYTES)
                if self.cwnd_bytes >= self.ssthresh:
                    self.state = STATE_CONGESTION_AVOIDANCE
                    self.enter_cubic_congestion_avoidance()
            elif self.state == STATE_CONGESTION_AVOIDANCE:
                # modest per-ack increment (safe)
                self.ack_credits += max(1.0, acked_bytes / PAYLOAD_SIZE)
                if self.ack_credits >= 1.0:
                    i = int(self.ack_credits)
                    self.cwnd_bytes = min(self.cwnd_bytes + i * PAYLOAD_SIZE, MAX_CWND)
                    self.ack_credits -= i
                self.cwnd_bytes = max(self.cwnd_bytes, 8 * MSS_BYTES)

            if flags & EOF_FLAG and cum_ack > self.eof_sent_seq:
                return "DONE"

        return "CONTINUE"

    # --- logging ---
    def log_cwnd(self):
        if not self.cwnd_log_file:
            return
        try:
            ts = time.time() - self.start_time
            self.cwnd_log_file.write(f"{ts},{self.cwnd_bytes},{self.srtt},{self.q_delay},{self.q_grad}\n")
        except Exception:
            pass

    # --- main loop ---
    def get_next_rto_delay(self):
        if not self.sent_packets:
            return 0.001
        try:
            oldest = next(iter(self.sent_packets))
            _, send_time, _ = self.sent_packets[oldest]
            expiry = send_time + self.rto
            delay = expiry - time.time()
            return max(0.001, delay)
        except StopIteration:
            return 1.0

    def run(self):
        print("Waiting for client request...")
        readable, _, _ = select.select([self.socket], [], [], 15.0)
        if not readable:
            print("Timed out waiting for client.")
            self.socket.close()
            return
        hs_packet, self.client_addr = self.socket.recvfrom(1024)
        print(f"Client connected from {self.client_addr}")

        self.start_time = time.time()
        self.last_ack_time = self.start_time

        try:
            self.cwnd_log_file = open(self.log_filename, "w", buffering=1)
            self.cwnd_log_file.write("timestamp_s,cwnd_bytes,srtt,q_delay,q_grad\n")
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
        duration = end_time - self.start_time if end_time - self.start_time > 0 else 1e-6
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
