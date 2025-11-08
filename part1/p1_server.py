#!/usr/bin/env python3
import socket
import sys
import time
import struct
import select
import collections
import os

# --- Header / sizes ---
HEADER_FORMAT = "!IIHII2x"
HEADER_SIZE = 20
MSS_BYTES = 1200
PAYLOAD_SIZE = MSS_BYTES - HEADER_SIZE

# Flags
SYN_FLAG = 0x1
ACK_FLAG = 0x2
EOF_FLAG = 0x4

# RTO / RTT constants (simple RFC-style)
ALPHA = 0.125
BETA = 0.25
INITIAL_RTO = 0.3
MIN_RTO = 0.1
MAX_RETRANSMIT = 12

class P1Server:
    def __init__(self, ip, port, sws_bytes):
        self.ip = ip
        self.port = int(port)
        self.sws = int(sws_bytes)  # sender window size in bytes (fixed)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((self.ip, self.port))
        self.sock.setblocking(False)
        print(f"P1 Server listening on {self.ip}:{self.port} with SWS={self.sws} bytes")

        # load file
        try:
            with open("data.txt", "rb") as f:
                self.file_data = f.read()
            self.file_size = len(self.file_data)
        except IOError as e:
            print(f"Error reading data.txt: {e}")
            sys.exit(1)

        # sender state
        self.base = 0                   # cumulative ack (next expected by receiver)
        self.next_seq = 0               # next byte offset to take from file
        self.eof_sent_seq = -1
        # sent packets: seq -> (packet_bytes, send_time, retrans_count)
        self.sent = collections.OrderedDict()

        # RTT/RTO estimation
        self.srtt = 0.0
        self.rttvar = 0.0
        self.rto = INITIAL_RTO
        self.rtt_min = float('inf')

        # duplicate ACK tracking for fast retransmit
        self.dup_ack_count = 0

        self.client_addr = None
        self.start_time = 0.0
        self.last_ack_time = 0.0

    def pack_header(self, seq, ack, flags, sack_start=0, sack_end=0):
        return struct.pack(HEADER_FORMAT, seq, ack, flags, sack_start, sack_end)

    def unpack_header(self, pkt):
        if len(pkt) < HEADER_SIZE:
            return None
        try:
            return struct.unpack(HEADER_FORMAT, pkt[:HEADER_SIZE])
        except struct.error:
            return None

    def get_next_chunk(self):
        if self.next_seq < self.file_size:
            s = self.next_seq
            e = min(s + PAYLOAD_SIZE, self.file_size)
            data = self.file_data[s:e]
            self.next_seq = e
            return s, data, 0
        elif self.eof_sent_seq == -1:
            # send one EOF segment
            self.eof_sent_seq = self.file_size
            self.next_seq = self.file_size + 1  # stop further data
            return self.eof_sent_seq, b"EOF", EOF_FLAG
        else:
            return None, None, 0

    def update_rto(self, sample_rtt):
        self.rtt_min = min(self.rtt_min, sample_rtt)
        if self.srtt == 0.0:
            self.srtt = sample_rtt
            self.rttvar = sample_rtt / 2.0
        else:
            self.rttvar = (1 - BETA) * self.rttvar + BETA * abs(self.srtt - sample_rtt)
            self.srtt = (1 - ALPHA) * self.srtt + ALPHA * sample_rtt
        new_rto = self.srtt + 4 * self.rttvar
        self.rto = max(MIN_RTO, min(new_rto, 10.0))

    def send_packet(self, seq, data, flags):
        header = self.pack_header(seq, 0, flags)
        pkt = header + data
        try:
            self.sock.sendto(pkt, self.client_addr)
        except Exception:
            pass
        # store send info
        self.sent[seq] = (pkt, time.time(), 0)

    def resend_packet(self, seq):
        if seq not in self.sent:
            return False
        pkt, _, rc = self.sent[seq]
        if rc >= MAX_RETRANSMIT:
            print("Max retransmit reached. Aborting.")
            return False
        # update retransmit count and send_time
        self.sent[seq] = (pkt, time.time(), rc + 1)
        try:
            self.sock.sendto(pkt, self.client_addr)
            return True
        except Exception:
            return False

    def send_while_window_allows(self):
        in_flight = self.next_seq - self.base
        # while bytes in flight less than SWS, send next chunk
        while in_flight < self.sws:
            seq, data, flags = self.get_next_chunk()
            if seq is None:
                break
            self.send_packet(seq, data, flags)
            if flags & EOF_FLAG:
                break
            in_flight = self.next_seq - self.base

    def handle_ack(self, pkt):
        fields = self.unpack_header(pkt)
        if fields is None:
            return "CONTINUE"
        seq, cum_ack, flags, sack_start, sack_end = fields

        if not (flags & ACK_FLAG):
            return "CONTINUE"

        self.last_ack_time = time.time()

        # duplicate ACK?
        if cum_ack == self.base:
            self.dup_ack_count += 1
            if self.dup_ack_count == 3:
                # fast retransmit of lowest outstanding segment (base)
                if self.base in self.sent:
                    print("[FAST-RETRANS] retransmitting base", self.base)
                    self.resend_packet(self.base)
            return "CONTINUE"

        if cum_ack > self.base:
            # New cumulative ACK
            self.dup_ack_count = 0
            # for rtt sample, find the newest acked seq among sent that is < cum_ack
            acked_seqs = [s for s in list(self.sent.keys()) if s < cum_ack]
            if acked_seqs:
                newest = max(acked_seqs)
                pktdata, stime, rc = self.sent.get(newest, (None, None, None))
                if stime and rc == 0:
                    # only estimate RTT from first-time acked packets
                    self.update_rto(time.time() - stime)
            # remove acked from sent
            for s in acked_seqs:
                self.sent.pop(s, None)
            self.base = cum_ack

            # check for EOF acked
            if flags & EOF_FLAG and cum_ack > self.eof_sent_seq:
                return "DONE"

        # SACK handling (basic): if server sees sacks, remove them from outstanding
        if sack_start and sack_end and sack_end > sack_start:
            # sack_start is seq number of a contiguous block that receiver has
            to_remove = [s for s in list(self.sent.keys()) if s >= sack_start and s < sack_end]
            for s in to_remove:
                self.sent.pop(s, None)

        return "CONTINUE"

    def get_next_timeout(self):
        if not self.sent:
            return 0.1
        # find oldest outstanding packet
        oldest_seq = next(iter(self.sent))
        _, send_time, _ = self.sent[oldest_seq]
        expire = send_time + self.rto
        delay = expire - time.time()
        return max(0.001, delay)

    def run(self):
        print("Waiting for client request (one-byte)...")
        # wait for a short handshake: client sends 1 byte to request file
        # blocking with select to allow timeout
        readable, _, _ = select.select([self.sock], [], [], 30.0)
        if not readable:
            print("No client request received. Exiting.")
            self.sock.close()
            return
        req, addr = self.sock.recvfrom(1024)
        self.client_addr = addr
        print("Client connected:", addr)

        self.start_time = time.time()
        self.last_ack_time = self.start_time

        running = True
        while running:
            # wait either for ACK or timeout for retransmit
            timeout = self.get_next_timeout()
            try:
                rlist, _, _ = select.select([self.sock], [], [], timeout)
                if rlist:
                    pkt, _ = self.sock.recvfrom(MSS_BYTES)
                    res = self.handle_ack(pkt)
                    if res == "DONE":
                        running = False
                        break
                else:
                    # timeout expired -> retransmit oldest outstanding
                    if self.sent:
                        oldest = next(iter(self.sent))
                        print("[TIMEOUT] retransmitting", oldest)
                        # soft exponential backoff of RTO on retransmit
                        self.resend_packet(oldest)
                        # double the RTO but cap it (conservative)
                        self.rto = min(self.rto * 2.0, 10.0)
            except Exception as e:
                print("Socket/select error:", e)
                running = False
                break

            # send new data while window allows
            self.send_while_window_allows()

            # termination safeguard: if nothing acked for long time
            if time.time() - self.last_ack_time > 60.0:
                print("No ACKs for 60s. Terminating.")
                running = False

        # finished
        duration = time.time() - self.start_time if time.time() - self.start_time > 0 else 1e-6
        print("Transfer finished. Duration: {:.2f}s, bytes: {}".format(duration, self.file_size))
        self.sock.close()

def main():
    if len(sys.argv) != 4:
        print("Usage: python3 p1_server.py <SERVER_IP> <SERVER_PORT> <SWS>")
        sys.exit(1)
    ip = sys.argv[1]
    port = int(sys.argv[2])
    sws = int(sys.argv[3])
    server = P1Server(ip, port, sws)
    server.run()

if __name__ == "__main__":
    main()
