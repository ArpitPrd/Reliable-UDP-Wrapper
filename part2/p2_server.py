import socket
import sys
import time
import struct
import os
import math
import csv
import select
import collections

# --- Constants ---
HEADER_FORMAT = "!IIHII2x"
HEADER_SIZE = 20
MSS_BYTES = 1200
PAYLOAD_SIZE = MSS_BYTES - HEADER_SIZE

# --- Flags ---
SYN_FLAG = 0x1
ACK_FLAG = 0x2
EOF_FLAG = 0x4

# --- Congestion Control States ---
STATE_SLOW_START = 1
STATE_CONGESTION_AVOIDANCE = 2
STATE_FAST_RECOVERY = 3

# --- RTO Constants ---
ALPHA = 0.125
BETA = 0.25
K = 4.0
INITIAL_RTO = 0.2   # improved
MIN_RTO = 0.05
MAX_RTO = 2.0


class Server:
    def __init__(self, ip, port):
        self.ip = ip
        self.port = int(port)
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind((self.ip, self.port))
        self.socket.setblocking(False)
        self.client_addr = None
        print(f"Server started on {self.ip}:{self.port}")

        try:
            with open("data.txt", "rb") as f:
                self.file_data = f.read()
            self.file_size = len(self.file_data)
        except IOError as e:
            print(f"Error reading data.txt: {e}")
            sys.exit(1)

        # --- State ---
        self.next_seq_num = 0
        self.base_seq_num = 0
        self.eof_sent_seq = -1
        self.connection_dead = False

        # --- Congestion Control ---
        self.state = STATE_SLOW_START
        self.cwnd_bytes = MSS_BYTES
        self.ssthresh = 2 * 1024 * 1024 * 1024  # 2GB
        self.dup_ack_count = 0

        # --- RTO ---
        self.rto = INITIAL_RTO
        self.srtt = 0.0
        self.rttvar = 0.0
        self.rtt_min = float('inf')

        # --- Bookkeeping ---
        self.sent_packets = collections.OrderedDict()
        self.sacked_packets = set()
        self.start_time = 0.0
        self.last_ack_time = 0.0
        self.ack_credits = 0.0

        # --- CUBIC ---
        self.C = 0.4
        self.beta_cubic = 0.7
        self.w_max_bytes = 0.0
        self.t_last_congestion = 0.0
        self.K = 0.0

        # --- Logging ---
        self.cwnd_log_file = None
        self.log_filename = f"cwnd_log_{self.port}.csv"

    # -----------------------------------
    def get_state_str(self):
        if self.state == STATE_SLOW_START:
            return "SS"
        if self.state == STATE_CONGESTION_AVOIDANCE:
            return "CA"
        if self.state == STATE_FAST_RECOVERY:
            return "FR"
        return "UNK"

    def log_cwnd(self):
        if not self.cwnd_log_file:
            return
        timestamp = time.time() - self.start_time
        self.cwnd_log_file.write(
            f"{timestamp:.4f},{int(self.cwnd_bytes)},{int(self.ssthresh)},{self.get_state_str()}\n"
        )

    def pack_header(self, seq_num, ack_num, flags, sack_start=0, sack_end=0):
        return struct.pack(HEADER_FORMAT, seq_num, ack_num, flags, sack_start, sack_end)

    def unpack_header(self, packet):
        try:
            return struct.unpack(HEADER_FORMAT, packet[:HEADER_SIZE])
        except struct.error:
            return None

    # -----------------------------------
    def update_rto(self, rtt_sample):
        self.rtt_min = min(self.rtt_min, rtt_sample)
        if self.srtt == 0.0:
            self.srtt = rtt_sample
            self.rttvar = rtt_sample / 2.0
        else:
            self.rttvar = (1 - BETA) * self.rttvar + BETA * abs(self.srtt - rtt_sample)
            self.srtt = (1 - ALPHA) * self.srtt + ALPHA * rtt_sample
        self.rto = min(self.srtt + K * self.rttvar, MAX_RTO)
        self.rto = max(self.rto, MIN_RTO)

    # -----------------------------------
    def resend_packet(self, seq_num):
        if seq_num not in self.sent_packets:
            return False
        packet_data, send_time, retrans_count = self.sent_packets[seq_num]
        if retrans_count > 10:
            print("Too many retransmissions, aborting.")
            self.connection_dead = True
            return False
        del self.sent_packets[seq_num]
        self.sent_packets[seq_num] = (packet_data, time.time(), retrans_count + 1)
        try:
            self.socket.sendto(packet_data, self.client_addr)
            return True
        except Exception as e:
            print(f"Error in resend_packet: {e}")
            return False

    def resend_missing_packet(self):
        for seq_num in list(self.sent_packets.keys()):
            if seq_num >= self.base_seq_num and seq_num not in self.sacked_packets:
                self.resend_packet(seq_num)
                return

    def enter_cubic_congestion_avoidance(self):
        self.t_last_congestion = time.time()
        new_w_max = self.cwnd_bytes
        if new_w_max < self.w_max_bytes:
            self.w_max_bytes = new_w_max * (1.0 + self.beta_cubic) / 2.0
        else:
            self.w_max_bytes = new_w_max
        self.ssthresh = self.cwnd_bytes * self.beta_cubic
        w_max_mss = max(1.0, self.w_max_bytes / PAYLOAD_SIZE)
        self.K = (w_max_mss * (1 - self.beta_cubic) / self.C) ** (1.0 / 3.0)

    def handle_timeouts(self):
        now = time.time()
        if not self.sent_packets:
            return
        oldest_seq, (pkt, sent, cnt) = next(iter(self.sent_packets.items()))
        if now - sent > self.rto:
            print("Timeout (RTO). Reducing cwnd.")
            self.enter_cubic_congestion_avoidance()
            # Gentle cwnd reduction
            self.cwnd_bytes = max(self.cwnd_bytes * self.beta_cubic, MSS_BYTES)
            self.state = STATE_CONGESTION_AVOIDANCE
            self.resend_packet(oldest_seq)
            self.log_cwnd()

    def get_next_rto_delay(self):
        if not self.sent_packets:
            return 0.001
        try:
            oldest_seq_num = next(iter(self.sent_packets))
            _packet, send_time, _count = self.sent_packets[oldest_seq_num]
            expiry_time = send_time + self.rto
            return max(0.001, expiry_time - time.time())
        except StopIteration:
            return 0.001

    # -----------------------------------
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
                print(
                    f"Sent seq={seq_num}, next_seq={self.next_seq_num}, cwnd={int(self.cwnd_bytes)}, base={self.base_seq_num}"
                )
                time.sleep(0.0005)  # gentle pacing
            except Exception as e:
                print(f"Send error: {e}")
                break
            if flags & EOF_FLAG:
                break
            inflight = self.next_seq_num - self.base_seq_num

    # -----------------------------------
    def process_incoming_ack(self, packet):
        header_fields = self.unpack_header(packet)
        if not header_fields:
            return "CONTINUE"
        seq_num, cum_ack, flags, sack_start, sack_end = header_fields
        if not (flags & ACK_FLAG):
            return "CONTINUE"
        self.last_ack_time = time.time()

        # ignore stale ACKs
        if cum_ack <= self.base_seq_num:
            return "CONTINUE"

        # remove acknowledged packets
        newly_acked = []
        for seq in list(self.sent_packets.keys()):
            if seq < cum_ack:
                newly_acked.append(seq)
        if newly_acked:
            newest = max(newly_acked)
            if newest in self.sent_packets:
                _, send_time, retrans_count = self.sent_packets[newest]
                if retrans_count == 0:
                    self.update_rto(time.time() - send_time)
        for seq in newly_acked:
            del self.sent_packets[seq]
            self.sacked_packets.discard(seq)
        self.base_seq_num = cum_ack

        # --- CWND update ---
        if self.state == STATE_SLOW_START:
            self.cwnd_bytes += PAYLOAD_SIZE
            if self.cwnd_bytes >= self.ssthresh:
                self.state = STATE_CONGESTION_AVOIDANCE
        elif self.state == STATE_CONGESTION_AVOIDANCE:
            if self.t_last_congestion == 0:
                increment = (PAYLOAD_SIZE * PAYLOAD_SIZE) / self.cwnd_bytes
                self.ack_credits += increment
                if self.ack_credits >= 1.0:
                    self.cwnd_bytes += PAYLOAD_SIZE
                    self.ack_credits -= 1.0
            else:
                t_elapsed = time.time() - self.t_last_congestion
                t_target = t_elapsed + self.rtt_min
                w_cubic_target = self.C * (t_target - self.K) ** 3 + self.w_max_bytes
                target = max(w_cubic_target, self.cwnd_bytes)
                diff = (target - self.cwnd_bytes) / max(1.0, self.cwnd_bytes / PAYLOAD_SIZE)
                self.cwnd_bytes += diff

        # --- EOF ---
        if flags & EOF_FLAG and cum_ack >= self.eof_sent_seq:
            print("Final EOF ACK received.")
            return "DONE"

        self.log_cwnd()
        return "CONTINUE"

    # -----------------------------------
    def run(self):
        print("Waiting for client request...")
        readable, _, _ = select.select([self.socket], [], [], 15.0)
        if not readable:
            print("Timed out waiting for client.")
            return
        packet, self.client_addr = self.socket.recvfrom(1024)
        print(f"Client connected from {self.client_addr}")

        self.start_time = time.time()
        self.last_ack_time = self.start_time
        self.cwnd_log_file = open(self.log_filename, "w", buffering=1)
        self.cwnd_log_file.write("timestamp_s,cwnd_bytes,ssthresh_bytes,state\n")
        print(f"Logging CWND to {self.log_filename}")
        self.log_cwnd()

        running = True
        while running:
            if self.connection_dead:
                break
            timeout_delay = self.get_next_rto_delay()
            readable, _, _ = select.select([self.socket], [], [], timeout_delay)
            if readable:
                ack_pkt, _ = self.socket.recvfrom(MSS_BYTES)
                if self.process_incoming_ack(ack_pkt) == "DONE":
                    break
            self.handle_timeouts()
            self.send_new_data()
            if time.time() - self.last_ack_time > 30.0:
                print("Client timed out (30s). Shutting down.")
                break

        duration = max(time.time() - self.start_time, 1e-6)
        throughput = (self.file_size * 8) / (duration * 1_000_000)
        print("---------------------------------")
        print("File transfer complete.")
        print(f"Duration: {duration:.2f} seconds")
        print(f"Total Data: {self.file_size} bytes")
        print(f"Throughput: {throughput:.2f} Mbps")
        print("---------------------------------")
        if self.cwnd_log_file:
            self.cwnd_log_file.close()
        self.socket.close()


def main():
    if len(sys.argv) != 3:
        print("Usage: python3 p2_server.py <SERVER_IP> <SERVER_PORT>")
        sys.exit(1)
    ip = sys.argv[1]
    port = int(sys.argv[2])
    s = Server(ip, port)
    s.run()


if __name__ == "__main__":
    main()
