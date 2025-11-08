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
SIZE_OF_HEADER = 20
MSS_BYTES = 1200
PAYLOAD_SIZE = MSS_BYTES - SIZE_OF_HEADER
MAX_CWND = 8 * 1024 * 1024

# States
STATE_SS = 1
STATE_CA = 2

# Flags
SYN_FLAG = 0x1
ACK_FLAG = 0x2
EOF_FLAG = 0x4

ALPHA = 0.125
BETA = 0.25
K = 4.0
INITIAL_RTO = 0.3
MIN_RTO = 0.1


class Server:
    def __init__(self, ip, port_no):
        self.ip = ip
        self.port_no = int(port_no)
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind((self.ip, self.port_no))
        self.socket.setblocking(False)
        self.client_addr = None
        self.is_in_rto_recovery = False
        print(f"Server started on the following details {self.ip}:{self.port_no}")

        # load file
        try:
            with open("data.txt", "rb") as f:
                self.file_data = f.read()
            self.file_size = len(self.file_data)
        except IOError as e:
            print(f"Error reading data.txt: {e}")
            sys.exit(1)

        # Congestion control
        self.state = STATE_SS
        self.cwnd_bytes = MSS_BYTES
        self.ssthresh = 600 * MSS_BYTES

        # Connection state
        self.next_seq_num = 0
        self.curr_base_seq_num = 0
        self.eof_sent_seq = -1
        self.is_connection_dead = False

        # RTT/RTO
        self.rto = INITIAL_RTO
        self.rtt_curr = 0.0
        self.rtt_variance = 0.0
        self.rtt_min = float('inf')
        self.queuing_delay = 0.0

        # bookkeeping
        self.start_time = 0.0
        self.last_ack_time_sent_from_server = 0.0
        self.ack_worth_count = 0.0

        # Buffers
        self.curr_in_flight_packets = collections.OrderedDict()
        self.n_dup_ack = 0
        self.sacked_packets = set()

        # CUBIC parameters
        self.C = 0.4
        self.beta_cubic = 0.7
        self.w_max_bytes = 0.0
        self.w_max_last_bytes = 0.0
        self.t_last_congestion = 0.0
        self.K = 0.0

        # Queue delay gradient tracking
        self.prev_q_delay = 0.0
        self.queuing_delay_time = time.time()
        self.q_grad_threshold = 0.03
        self.q_grad_prec_reduction = 0.90
        self.q_grad_filter = 0.75
        self.queuing_grad = 0.0
        self.last_grad_adjust = 0.0

        # logging
        self.cwnd_log_file = None
        self.log_filename = f"cwnd_log_{self.port_no}.csv"

    def prepare_header(self, seq_num, ack_num, flags, sack_start=0, sack_end=0):
        return struct.pack(HEADER_FORMAT, seq_num, ack_num, flags, sack_start, sack_end)

    def read_header(self, packet):
        try:
            return struct.unpack(HEADER_FORMAT, packet[:SIZE_OF_HEADER])
        except struct.error:
            return None

    def update_queueing_delays(self):
        if self.rtt_min == float('inf'):
            return

        self.queuing_delay = max(0.0, self.rtt_curr - self.rtt_min)
        now = time.time()
        dt = max(1e-6, now - self.queuing_delay_time)

        queuing_grad_raw = (self.queuing_delay - self.prev_q_delay) / dt
        self.queuing_grad = self.q_grad_filter * self.queuing_grad + (1.0 - self.q_grad_filter) * queuing_grad_raw
        queuing_grad = self.queuing_grad

        self.prev_q_delay = self.queuing_delay
        self.queuing_delay_time = now

        refractory = max(0.05, 3.0 * max(self.rtt_curr, 0.001))

        if queuing_grad > self.q_grad_threshold and (now - self.last_grad_adjust) > refractory:
            self.cwnd_bytes = max(int(self.cwnd_bytes * self.q_grad_prec_reduction), 8 * MSS_BYTES)
            self.ssthresh = max(int(self.cwnd_bytes * 0.9), 8 * MSS_BYTES)
            self.state = STATE_CA
            self.update_cubic_metrics()
            self.log_cwnd()
            self.last_grad_adjust = now
            return

        if queuing_grad < -(self.q_grad_threshold / 2.0) and self.state == STATE_CA and (now - self.last_grad_adjust) > refractory:
            mult_cap = 1.25
            additive_cap = 32 * PAYLOAD_SIZE

            mult_candidate = int(self.cwnd_bytes * min(mult_cap, 1.0 + abs(queuing_grad) * 10.0))
            add_candidate = self.cwnd_bytes + additive_cap
            target = min(mult_candidate, add_candidate)

            if target <= self.cwnd_bytes:
                target = self.cwnd_bytes + min(additive_cap, int(0.05 * self.cwnd_bytes) + PAYLOAD_SIZE)

            self.cwnd_bytes = min(target, MAX_CWND)
            self.cwnd_bytes = max(self.cwnd_bytes, 8 * MSS_BYTES)
            self.ssthresh = max(int(self.cwnd_bytes * 0.95), 8 * MSS_BYTES)
            self.log_cwnd()
            self.last_grad_adjust = now
    
    def update_rto(self, rtt_sample):
        self.rtt_min = min(self.rtt_min, rtt_sample)
        if self.rtt_curr == 0.0:
            self.rtt_curr = rtt_sample
            self.rtt_variance = rtt_sample / 2.0
        else:
            self.rtt_variance = (1 - BETA) * self.rtt_variance + BETA * abs(self.rtt_curr - rtt_sample)
            self.rtt_curr = (1 - ALPHA) * self.rtt_curr + ALPHA * rtt_sample

        self.update_queueing_delays()

        new_rto = self.rtt_curr + K * self.rtt_variance
        if self.rto == 0:
            self.rto = max(MIN_RTO, new_rto)
        else:
            self.rto = 0.9 * self.rto + 0.1 * max(MIN_RTO, new_rto)
        self.rto = max(MIN_RTO, min(self.rto, 3.0))


    def resend_packet(self, seq_num):
        if seq_num not in self.curr_in_flight_packets:
            return False
        packet_data, send_time, retrans_count = self.curr_in_flight_packets[seq_num]
        if retrans_count > 15:
            print("Packet resend limit reached. Aborting.")
            self.is_connection_dead = True
            return False
        del self.curr_in_flight_packets[seq_num]
        self.curr_in_flight_packets[seq_num] = (packet_data, time.time(), retrans_count + 1)
        try:
            self.socket.sendto(packet_data, self.client_addr)
            return True
        except OSError:
            return False

    def update_cubic_metrics(self):
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

    def handle_timeouts(self):
        now = time.time()
        for seq_num, (pkt, send_time, _) in list(self.curr_in_flight_packets.items()):
            if now - send_time > self.rto:
                self.cwnd_bytes = max(int(self.cwnd_bytes * 0.85), 8 * MSS_BYTES)
                self.ssthresh = max(int(self.cwnd_bytes * 0.9), 8 * MSS_BYTES)
                self.update_cubic_metrics()
                self.log_cwnd()
                self.resend_packet(seq_num)
                break

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

    def send_new_data_from_file(self):
        n_bytes_in_flight = self.next_seq_num - self.curr_base_seq_num
        while n_bytes_in_flight < self.cwnd_bytes:
            if self.is_connection_dead:
                break
            if self.rtt_curr > 0 and self.cwnd_bytes > 0:
                n_pkts = max(1.0, self.cwnd_bytes / PAYLOAD_SIZE)
                pacing_delay = max(0.0001, self.rtt_curr / (2.2 * n_pkts))
                pacing_delay *= random.uniform(0.92, 1.08)
                time.sleep(pacing_delay)
            data, seq, flags = self.get_next_content()
            if data is None:
                break
            packet = self.prepare_header(seq, 0, flags) + data
            try:
                self.socket.sendto(packet, self.client_addr)
                self.curr_in_flight_packets[seq] = (packet, time.time(), 0)
            except Exception:
                self.is_connection_dead = True
                break
            if flags & EOF_FLAG:
                break
            n_bytes_in_flight = self.next_seq_num - self.curr_base_seq_num

    def update_sacked_packets(self, sack_start, sack_end):
        if sack_start > 0 and sack_end > sack_start:
            if sack_start in self.curr_in_flight_packets:
                self.sacked_packets.add(sack_start)

    def handle_dup_acks(self, cum_ack):
        if cum_ack == self.curr_base_seq_num:
            self.n_dup_ack += 1
            if self.n_dup_ack == 3:
                self.cwnd_bytes = max(int(self.cwnd_bytes * 0.85), 8 * MSS_BYTES)
                self.update_cubic_metrics()
                self.log_cwnd()
            return True
        return False

    def incoming_ack_with_ss(self, acked_bytes):
        self.cwnd_bytes = min(self.cwnd_bytes + acked_bytes, MAX_CWND)
        self.cwnd_bytes = max(self.cwnd_bytes, 8 * MSS_BYTES)
        if self.cwnd_bytes >= self.ssthresh:
            self.state = STATE_CA
            self.update_cubic_metrics()

    def incoming_ack_with_ca(self, acked_bytes):
        self.ack_worth_count += max(1.0, acked_bytes / PAYLOAD_SIZE)
        if self.ack_worth_count >= 1.0:
            i = int(self.ack_worth_count)
            self.cwnd_bytes = min(self.cwnd_bytes + i * PAYLOAD_SIZE, MAX_CWND)
            self.ack_worth_count -= i
        self.cwnd_bytes = max(self.cwnd_bytes, 8 * MSS_BYTES)

    def handle_new_acks(self, cum_ack, flags):
        self.is_in_rto_recovery = False
        self.n_dup_ack = 0
        newly_acked = [s for s in list(self.curr_in_flight_packets.keys()) if s < cum_ack]
        acked_bytes = 0
        if newly_acked:
            newest = max(newly_acked)
            if newest in self.curr_in_flight_packets:
                p_data, send_time, rc = self.curr_in_flight_packets[newest]
                if rc == 0:
                    self.update_rto(time.time() - send_time)
            for s in newly_acked:
                if s in self.curr_in_flight_packets:
                    pktdata, _, _ = self.curr_in_flight_packets[s]
                    pkt_len = len(pktdata) - SIZE_OF_HEADER
                    acked_bytes += max(pkt_len, 0)
        for s in newly_acked:
            self.curr_in_flight_packets.pop(s, None)
            self.sacked_packets.discard(s)
        self.curr_base_seq_num = cum_ack
        if self.state == STATE_SS:
            self.incoming_ack_with_ss(acked_bytes)
        elif self.state == STATE_CA:
            self.incoming_ack_with_ca(acked_bytes)
        if flags & EOF_FLAG and cum_ack > self.eof_sent_seq:
            return "DONE"
        return "CONTINUE"

    def process_incoming_ack(self, packet):
        header_fields = self.read_header(packet)
        if header_fields is None:
            return
        seq_num, cum_ack, flags, sack_start, sack_end = header_fields
        if not (flags & ACK_FLAG):
            return
        self.last_ack_time_sent_from_server = time.time()
        self.update_sacked_packets(sack_start, sack_end)
        if self.handle_dup_acks(cum_ack):
            return "CONTINUE"
        if cum_ack > self.curr_base_seq_num:
            return self.handle_new_acks(cum_ack, flags)
        return "CONTINUE"

    def log_cwnd(self):
        if not self.cwnd_log_file:
            return
        try:
            ts = time.time() - self.start_time
            self.cwnd_log_file.write(f"{ts},{self.cwnd_bytes},{self.rtt_curr},{self.queuing_delay},{self.queuing_grad}\n")
        except Exception:
            pass

    def get_next_rto_delay(self):
        if not self.curr_in_flight_packets:
            return 0.001
        try:
            oldest = next(iter(self.curr_in_flight_packets))
            _, send_time, _ = self.curr_in_flight_packets[oldest]
            expiry = send_time + self.rto
            delay = expiry - time.time()
            return max(0.001, delay)
        except StopIteration:
            return 1.0

    def run(self):
        print("Waiting for some client request...")
        fd, _, _ = select.select([self.socket], [], [], 15.0)
        if not fd:
            print("Declaring time out waiting for client.")
            print("and closing myself, peace")
            self.socket.close()
            return
        hs_packet, self.client_addr = self.socket.recvfrom(1024)
        print(f"Client connected from {self.client_addr}")

        self.start_time = time.time()
        self.last_ack_time_sent_from_server = self.start_time
        try:
            self.cwnd_log_file = open(self.log_filename, "w", buffering=1)
            self.cwnd_log_file.write("timestamp_s,cwnd_bytes,rtt_curr,queuing_delay,queuing_grad\n")
            print(f"Logging CWND to {self.log_filename}")
            self.log_cwnd()
        except IOError:
            self.cwnd_log_file = None

        running = True
        while running:
            if self.is_connection_dead:
                print("Connection dead, shutting down.")
                running = False
                break
            td = self.get_next_rto_delay()
            try:
                fd, _, _ = select.select([self.socket], [], [], td)
                if fd:
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
            self.send_new_data_from_file()
            if time.time() - self.last_ack_time_sent_from_server > 30.0:
                print("Client timed out (30s). Shutting down.")
                running = False

        end_time = time.time()
        duration = max(end_time - self.start_time, 1e-6)
        throughput = (self.file_size * 8) / (duration * 1_000_000)
        print("File transfer complete.")
        print(f"Duration: {duration:.2f} seconds")
        print(f"Total Data: {self.file_size} bytes")
        print(f"Throughput: {throughput:.2f} Mbps")
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
