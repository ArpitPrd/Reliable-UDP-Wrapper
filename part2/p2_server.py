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

        # --- Queue delay gradient tracking ---
        self.prev_q_delay = 0.0
        self.q_delay_time = time.time()
        self.q_grad_threshold = 0.02      # threshold for rapid queue growth (sec/sec)
        self.q_grad_reduction = 0.85      # cwnd reduction multiplier
        self.q_grad_filter = 0.6          # smoothing factor
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
        self.rtt_min = min(self.rtt_min, rtt_sample)
        if self.srtt == 0.0:
            self.srtt = rtt_sample
            self.rttvar = rtt_sample / 2.0
        else:
            self.rttvar = (1 - BETA) * self.rttvar + BETA * abs(self.srtt - rtt_sample)
            self.srtt = (1 - ALPHA) * self.srtt + ALPHA * rtt_sample

        if self.rtt_min != float('inf'):
            self.q_delay = max(0.0, self.srtt - self.rtt_min)

            # --- Compute and smooth queue delay gradient ---
            now = time.time()
            dt = max(1e-6, now - self.q_delay_time)
            q_grad_raw = (self.q_delay - self.prev_q_delay) / dt
            self.q_grad = self.q_grad_filter * self.q_grad + (1.0 - self.q_grad_filter) * q_grad_raw
            q_grad = self.q_grad
            self.prev_q_delay = self.q_delay
            self.q_delay_time = now

            # --- Gradient-based cwnd adjustment ---
            refractory = max(0.025, 2.0 * max(self.srtt, 0.001))

            # Backoff if queue grows rapidly
            if q_grad > self.q_grad_threshold and (now - self.last_grad_adjust) > refractory:
                old = self.cwnd_bytes
                self.cwnd_bytes = max(int(self.cwnd_bytes * self.q_grad_reduction), 16 * MSS_BYTES)
                self.ssthresh = max(int(self.cwnd_bytes * 0.85), 8 * MSS_BYTES)
                self.state = STATE_CONGESTION_AVOIDANCE
                self.enter_cubic_congestion_avoidance()
                self.log_cwnd()
                self.last_grad_adjust = now
                # print(f"[BACKOFF] q_grad={q_grad:.5f}, cwnd {old}->{self.cwnd_bytes}")

            # Accelerate if queue drains quickly
            if q_grad < -self.q_grad_threshold / 2.0 and self.state == STATE_CONGESTION_AVOIDANCE and (now - self.last_grad_adjust) > refractory:
                growth_factor = 1.0 + min(0.6, abs(q_grad) * 20.0)  # up to +60%
                old = self.cwnd_bytes
                self.cwnd_bytes = min(int(self.cwnd_bytes * growth_factor), MAX_CWND)
                self.cwnd_bytes = max(self.cwnd_bytes, 16 * MSS_BYTES)
                self.ssthresh = max(int(self.cwnd_bytes * 0.9), 8 * MSS_BYTES)
                self.log_cwnd()
                self.last_grad_adjust = now
                # print(f"[EXPAND] q_grad={q_grad:.5f}, cwnd {old}->{self.cwnd_bytes}")

        # --- RTO update ---
        new_rto = self.srtt + K * self.rttvar
        self.rto = 0.875 * self.rto + 0.125 * max(MIN_RTO, new_rto)
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
                print("[TIMEOUT] Reducing cwnd.")
                self.cwnd_bytes = max(int(self.cwnd_bytes * 0.7), 16 * MSS_BYTES)
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
                pacing_delay = max(0.00005, self.srtt / (2.0 * pkts))
                pacing_delay *= random.uniform(0.90, 1.10)
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

    # --- ACK processing (trimmed for brevity but identical core logic) ---
    def process_incoming_ack(self, packet):
        hdr = self.unpack_header(packet)
        if not hdr: return
        seq, cum_ack, flags, sack_start, sack_end = hdr
        if not (flags & ACK_FLAG):
            return
        self.last_ack_time = time.time()
        if cum_ack == self.base_seq_num:
            self.dup_ack_count += 1
            if self.dup_ack_count == 3:
                self.cwnd_bytes = max(int(self.cwnd_bytes * 0.7), 16 * MSS_BYTES)
                self.enter_cubic_congestion_avoidance()
                self.log_cwnd()
            return "CONTINUE"
        if cum_ack > self.base_seq_num:
            self.dup_ack_count = 0
            acked = [s for s in self.sent_packets if s < cum_ack]
            if acked:
                newest = max(acked)
                pkt, stime, rc = self.sent_packets[newest]
                if rc == 0:
                    self.update_rto(time.time() - stime)
            for s in acked:
                self.sent_packets.pop(s, None)
                self.sacked_packets.discard(s)
            self.base_seq_num = cum_ack
            if self.state == STATE_SLOW_START:
                self.cwnd_bytes = min(self.cwnd_bytes + PAYLOAD_SIZE, MAX_CWND)
                if self.cwnd_bytes >= self.ssthresh:
                    self.state = STATE_CONGESTION_AVOIDANCE
                    self.enter_cubic_congestion_avoidance()
            elif self.state == STATE_CONGESTION_AVOIDANCE:
                self.cwnd_bytes = min(self.cwnd_bytes + PAYLOAD_SIZE * 2, MAX_CWND)
            self.cwnd_bytes = max(self.cwnd_bytes, 16 * MSS_BYTES)
            self.log_cwnd()
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
    def run(self):
        print("Waiting for client request...")
        readable, _, _ = select.select([self.socket], [], [], 15.0)
        if not readable:
            print("Timed out waiting for client.")
            return
        _, self.client_addr = self.socket.recvfrom(1024)
        print(f"Client connected from {self.client_addr}")
        self.start_time = time.time()
        self.last_ack_time = self.start_time
        self.cwnd_log_file = open(self.log_filename, "w", buffering=1)
        self.cwnd_log_file.write("timestamp_s,cwnd_bytes,srtt,q_delay,q_grad\n")
        self.log_cwnd()

        running = True
        while running:
            if self.connection_dead:
                break
            timeout = max(0.001, self.rto)
            readable, _, _ = select.select([self.socket], [], [], timeout)
            if readable:
                ack_pkt, _ = self.socket.recvfrom(MSS_BYTES)
                if self.process_incoming_ack(ack_pkt) == "DONE":
                    running = False
                    break
            self.handle_timeouts()
            self.send_new_data()
            if time.time() - self.last_ack_time > 30.0:
                print("Client timeout.")
                break

        dur = max(1e-6, time.time() - self.start_time)
        thr = (self.file_size * 8) / (dur * 1_000_000)
        print("---------------------------------")
        print(f"File transfer complete.\nDuration: {dur:.2f}s\nThroughput: {thr:.2f} Mbps")
        print("---------------------------------")
        if self.cwnd_log_file:
            self.cwnd_log_file.close()
            print(f"CWND log saved to {self.log_filename}")
        self.socket.close()

def main():
    if len(sys.argv) != 3:
        print("Usage: python3 p2_server.py <SERVER_IP> <SERVER_PORT>")
        sys.exit(1)
    s = Server(sys.argv[1], int(sys.argv[2]))
    s.run()

if __name__ == "__main__":
    main()
