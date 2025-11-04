#!/usr/bin/env python3
"""
p2_server.py
Server for Part 2: reliable UDP + congestion control (hybrid CUBIC + rate targeting).
Usage:
    python3 p2_server.py <SERVER_IP> <SERVER_PORT>

This server:
- Reads data.txt from current directory and sends it to a single client that requests it.
- Uses 1200-byte UDP packets with a 20-byte header (HEADER_FORMAT).
- Implements CUBIC-like growth with a continuous rate-targeting feedback loop using recent acks.
- Gentle multiplicative decrease on loss/timeouts (no collapse to 1 MSS).
- Micro-pacing to avoid burst losses that break fairness.
"""
import socket
import struct
import time
import sys
import os
import select
import collections
from collections import deque

# --- Header / sizes ---
HEADER_FORMAT = "!IIHII2x"   # seq, ack, flags, sack_start, sack_end, 2 pad
HEADER_SIZE = 20
MSS_BYTES = 1200
PAYLOAD_SIZE = MSS_BYTES - HEADER_SIZE

# Flags
SYN_FLAG = 0x1
ACK_FLAG = 0x2
EOF_FLAG = 0x4

# States
STATE_SLOW_START = 1
STATE_CONGESTION_AVOIDANCE = 2

# RTO constants (classic)
ALPHA = 0.125
BETA = 0.25
K = 4.0
INITIAL_RTO = 0.15
MIN_RTO = 0.05

# Bandwidth estimation window (seconds)
BW_WINDOW = 0.5

# cwnd floors and ceilings
MIN_CWND_BYTES = 4 * MSS_BYTES
MAX_CWND_BYTES = 16 * 1024 * 1024

# Simple table mapping measured bw to target utilization (from your benchmarks)
BANDWIDTH_UTIL_TABLE = [
    (100, 0.54),
    (200, 0.29),
    (300, 0.19),
    (400, 0.17),
    (500, 0.13),
    (600, 0.10),
    (700, 0.10),
    (800, 0.058),
    (900, 0.055),
    (1000, 0.056),
]

class Server:
    def __init__(self, ip, port):
        self.ip = ip
        self.port = int(port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((self.ip, self.port))
        self.sock.setblocking(False)

        # load file
        try:
            with open("data.txt", "rb") as f:
                self.file_data = f.read()
            self.file_size = len(self.file_data)
        except Exception as e:
            print("Could not open data.txt:", e)
            sys.exit(1)

        # connection state
        self.client_addr = None
        self.base = 0                 # next byte expected acked (cumulative ack)
        self.next_seq = 0             # next byte to send (data seq)
        self.eof_sent_seq = -1

        # sent buffer: seq -> (packet_bytes, send_time, retrans_count)
        self.sent = collections.OrderedDict()

        # cwnd & CC
        self.state = STATE_SLOW_START
        self.cwnd = 2 * MSS_BYTES     # start somewhat above 1 MSS to help ramp (small bias)
        self.ssthresh = 1_000_000_000
        self.w_max = 0.0
        self.t_last_cong = time.time()
        self.C = 0.4
        self.beta = 0.7

        # rtt/rto
        self.srtt = 0.0
        self.rttvar = 0.0
        self.rto = INITIAL_RTO
        self.rtt_min = float('inf')

        # bw estimator (deque of (timestamp, bytes_acked))
        self.acked_history = deque()
        self.bw_bps = 0.0

        # duplicate ACKs, SACKs
        self.dup_ack_count = 0
        self.sacked = set()

        # logging
        self.start_time = 0.0

    # --- helpers ---
    def pack_header(self, seq, ack, flags, sack_start=0, sack_end=0):
        return struct.pack(HEADER_FORMAT, int(seq), int(ack), int(flags), int(sack_start), int(sack_end))

    def unpack_header(self, pkt):
        try:
            return struct.unpack(HEADER_FORMAT, pkt[:HEADER_SIZE])
        except struct.error:
            return None

    def update_rto(self, sample):
        self.rtt_min = min(self.rtt_min, sample)
        if self.srtt == 0.0:
            self.srtt = sample
            self.rttvar = sample / 2.0
        else:
            self.rttvar = (1 - BETA) * self.rttvar + BETA * abs(self.srtt - sample)
            self.srtt = (1 - ALPHA) * self.srtt + ALPHA * sample
        new = self.srtt + K * self.rttvar
        # smooth rto changes
        self.rto = max(MIN_RTO, 0.85 * self.rto + 0.15 * new)

    def record_acked(self, bytes_acked):
        now = time.time()
        self.acked_history.append((now, bytes_acked))
        # prune
        cutoff = now - BW_WINDOW
        while self.acked_history and self.acked_history[0][0] < cutoff:
            self.acked_history.popleft()
        total = sum(b for _t, b in self.acked_history)
        self.bw_bps = (total / BW_WINDOW) if BW_WINDOW > 0 else 0.0

    def choose_target_util(self):
        # convert to Mbps
        est_mbps = (self.bw_bps * 8) / 1e6
        if est_mbps <= 0:
            return 0.7
        best = min(BANDWIDTH_UTIL_TABLE, key=lambda x: abs(x[0] - est_mbps))
        return best[1]

    def apply_rate_target(self):
        # nudge cwnd toward target = bw * srtt * util
        if self.srtt <= 0 or self.bw_bps <= 0:
            return
        util = self.choose_target_util()
        target = self.bw_bps * max(self.srtt, 0.001) * util
        target = max(target, MIN_CWND_BYTES)
        # blend factor (lower -> faster adaptation). tuned for fairness+util.
        inertia = 0.75
        self.cwnd = inertia * self.cwnd + (1 - inertia) * target
        self.cwnd = min(self.cwnd, MAX_CWND_BYTES)
        if self.cwnd < MIN_CWND_BYTES:
            self.cwnd = MIN_CWND_BYTES

    def send_packet(self, seq_num, flags, data):
        header = self.pack_header(seq_num, 0, flags)
        pkt = header + data
        try:
            self.sock.sendto(pkt, self.client_addr)
            self.sent[seq_num] = (pkt, time.time(), 0)
        except Exception as e:
            # non-fatal; treat as transient
            print("send error:", e)

    def get_next_chunk(self):
        if self.next_seq < self.file_size:
            start = self.next_seq
            end = min(start + PAYLOAD_SIZE, self.file_size)
            data = self.file_data[start:end]
            seq = start
            self.next_seq = end
            return data, seq, 0
        elif self.eof_sent_seq == -1:
            self.eof_sent_seq = self.file_size
            return b"EOF", self.eof_sent_seq, EOF_FLAG
        return None, -1, 0

    def resend_oldest(self):
        # resend oldest outstanding
        if not self.sent:
            return
        seq, (pkt, stime, rcount) = next(iter(self.sent.items()))
        # update count & time
        self.sent.pop(seq)
        self.sent[seq] = (pkt, time.time(), rcount + 1)
        try:
            self.sock.sendto(pkt, self.client_addr)
        except Exception:
            pass

    def on_timeout(self):
        # Called when something timed out (oldest outstanding > rto)
        # Retransmit oldest and reduce cwnd gently (multiplicative)
        self.resend_oldest()
        print("[TIMEOUT] reducing cwnd")
        self.ssthresh = max(int(self.cwnd * self.beta), 2 * MSS_BYTES)
        self.cwnd = max(self.ssthresh, MIN_CWND_BYTES)
        self.state = STATE_CONGESTION_AVOIDANCE
        self.t_last_cong = time.time()
        # back off rto a bit
        self.rto = min(self.rto * 1.5, 2.0)

    def handle_ack_packet(self, pkt):
        header = self.unpack_header(pkt)
        if header is None:
            return False, None
        seq, cum_ack, flags, sack_start, sack_end = header
        if not (flags & ACK_FLAG):
            return False, None

        now = time.time()
        # If ack doesn't advance base -> dup ack
        if cum_ack <= self.base:
            self.dup_ack_count += 1
            if self.dup_ack_count == 3:
                # fast retransmit: resend first missing
                print("[DUP-ACKS] fast retransmit")
                self.resend_oldest()
                self.ssthresh = max(int(self.cwnd * self.beta), 2 * MSS_BYTES)
                self.cwnd = max(self.ssthresh, MIN_CWND_BYTES)
                self.state = STATE_CONGESTION_AVOIDANCE
                self.t_last_cong = time.time()
            return False, None

        # New cumulative ack
        advanced = cum_ack - self.base
        # remove acknowledged segments from sent buffer
        to_del = []
        acked_bytes = 0
        for s in list(self.sent.keys()):
            if s < cum_ack:
                pkt_bytes, send_time, rcount = self.sent[s]
                pkt_payload = len(pkt_bytes) - HEADER_SIZE
                if pkt_payload < 0:
                    pkt_payload = 0
                acked_bytes += pkt_payload
                # estimate rtt from newest acked packet's send_time
                last_send_time = send_time
                to_del.append(s)
        for s in to_del:
            self.sent.pop(s, None)
        # rtt sample from newest acked packet
        if acked_bytes > 0 and 'last_send_time' in locals():
            rtt_sample = now - last_send_time
            if rtt_sample > 0:
                self.update_rto(rtt_sample)
        # record in bw estimator
        if acked_bytes > 0:
            self.record_acked(acked_bytes)
        self.base = cum_ack
        self.dup_ack_count = 0

        # cwnd update
        if self.state == STATE_SLOW_START:
            # increase by acked bytes (byte-mode)
            self.cwnd = min(self.cwnd + acked_bytes, MAX_CWND_BYTES)
            if self.cwnd >= self.ssthresh:
                self.state = STATE_CONGESTION_AVOIDANCE
                self.t_last_cong = time.time()
                self.w_max = self.cwnd
        else:
            # CUBIC-like additive growth component (per ACK)
            # simplified: small increment proportional to (target - cwnd) per ACK
            # compute cubic target using elapsed time since last congestion event
            t = time.time() - self.t_last_cong
            # avoid division by zero
            rmin = max(self.rtt_min if self.rtt_min != float('inf') else self.srtt, 0.001)
            K_val = ((self.w_max * (1 - self.beta) / self.C) ** (1/3)) if self.w_max > 0 else 0.0
            t_minus_k = max(0.0, t - K_val)
            cubic_target = self.C * (t_minus_k ** 3) + self.w_max
            # tcp-friendly
            alpha = (3.0 * self.beta) / (2.0 - self.beta)
            w_tcp = self.ssthresh + alpha * ((t) / rmin) * PAYLOAD_SIZE
            target = max(cubic_target, w_tcp, self.cwnd)
            # divide increment over expected pkts in one RTT
            cwnd_pkts = max(1.0, self.cwnd / PAYLOAD_SIZE)
            inc = (target - self.cwnd) / cwnd_pkts
            if inc < 0:
                inc = 0.0
            self.cwnd = min(self.cwnd + inc, MAX_CWND_BYTES)

        # rate-targeting nudge after cwnd update
        self.apply_rate_target()

        # check EOF ack completion
        if (flags & EOF_FLAG) and cum_ack > self.eof_sent_seq:
            print("Transfer complete (EOF ack).")
            return True, "DONE"

        return True, "CONTINUE"

    def get_timer_delay(self):
        # compute time until earliest sending packet times out
        if not self.sent:
            return 0.002
        oldest_seq = next(iter(self.sent))
        pkt, send_time, rcount = self.sent[oldest_seq]
        expiry = send_time + self.rto
        delay = expiry - time.time()
        return max(0.002, delay)

    def run(self):
        print(f"Server listening on {self.ip}:{self.port}")
        # wait for initial client request (blocking select)
        try:
            readable, _, _ = select.select([self.sock], [], [], 15.0)
            if not readable:
                print("No initial client. Exiting.")
                self.sock.close()
                return
            req, addr = self.sock.recvfrom(1024)
            # treat any received packet as request
            self.client_addr = addr
            print("Client request from", addr)
        except Exception as e:
            print("Error waiting for client:", e)
            self.sock.close()
            return

        self.start_time = time.time()
        # main loop
        running = True
        while running:
            try:
                # send while we have room in cwnd
                inflight = self.next_seq - self.base
                # compute pacing interval: small sleep based on estimated bw to avoid bursts
                pacing_interval = 0.0
                if self.bw_bps > 0:
                    # send per-packet pacing: MSS / bw
                    pacing_interval = max(0.00002, (MSS_BYTES * 8) / float(self.bw_bps))
                else:
                    pacing_interval = 0.00002

                while (self.next_seq - self.base) < int(self.cwnd) and True:
                    chunk, seq, flags = self.get_next_chunk()
                    if chunk is None:
                        break
                    # send packet
                    self.send_packet(seq, flags, chunk)
                    # micro-pacing
                    if pacing_interval > 0:
                        time.sleep(pacing_interval)
                    if flags & EOF_FLAG:
                        break

                # decide select timeout (either until next rto expiry or small)
                timeout = min(0.1, self.get_timer_delay())
                readable, _, _ = select.select([self.sock], [], [], timeout)
                if readable:
                    pkt, addr = self.sock.recvfrom(MSS_BYTES + 64)
                    if addr != self.client_addr:
                        # ignore others
                        continue
                    done_flag, state = self.handle_ack_packet(pkt)
                    if done_flag and state == "DONE":
                        running = False
                        break
                    continue

                # if nothing readable -> check timeouts
                # find oldest outstanding and check rto expiry
                if self.sent:
                    oldest_seq = next(iter(self.sent))
                    pkt_bytes, send_time, rcount = self.sent[oldest_seq]
                    if time.time() - send_time > self.rto:
                        self.on_timeout()

            except KeyboardInterrupt:
                break
            except Exception as e:
                print("Main loop error:", e)
                # try to continue
                time.sleep(0.01)
        self.sock.close()
        print("Server: done.")


def main():
    if len(sys.argv) != 3:
        print("Usage: python3 p2_server.py <SERVER_IP> <SERVER_PORT>")
        sys.exit(1)
    srv = Server(sys.argv[1], sys.argv[2])
    srv.run()

if __name__ == "__main__":
    main()
