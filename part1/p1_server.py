#!/usr/bin/env python3
import socket
import sys
import time
import struct
import select
import collections

HEADER_FORMAT = "!IIHII2x"
HEADER_SIZE = 20
MSS_BYTES = 1200
PAYLOAD_SIZE = MSS_BYTES - HEADER_SIZE

SYN_FLAG = 0x1
ACK_FLAG = 0x2
EOF_FLAG = 0x4

# RTO parameters (faster response)
ALPHA = 0.125
BETA = 0.25
INITIAL_RTO = 0.05
MIN_RTO = 0.05
MAX_RTO = 1.0

class P1Server:
    def __init__(self, ip, port, sws_bytes):
        self.ip = ip
        self.port = int(port)
        self.sws = int(sws_bytes)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((self.ip, self.port))
        self.sock.setblocking(False)
        print(f"[P1-Server] Listening on {self.ip}:{self.port}, SWS={self.sws} bytes")

        # load file
        with open("data.txt", "rb") as f:
            self.file_data = f.read()
        self.file_size = len(self.file_data)

        # sender state
        self.base = 0
        self.next_seq = 0
        self.eof_sent_seq = -1
        self.sent = collections.OrderedDict()  # seq -> (pkt, send_time, retrans_count)
        self.client_addr = None

        # RTO / RTT estimation
        self.srtt = 0.0
        self.rttvar = 0.0
        self.rto = INITIAL_RTO

        # dup ACK tracking
        self.dup_ack_count = 0
        self.last_ack_time = 0
        self.start_time = 0

    def pack_header(self, seq, ack, flags, sack_start=0, sack_end=0):
        return struct.pack(HEADER_FORMAT, seq, ack, flags, sack_start, sack_end)

    def unpack_header(self, pkt):
        if len(pkt) < HEADER_SIZE:
            return None
        return struct.unpack(HEADER_FORMAT, pkt[:HEADER_SIZE])

    def update_rto(self, sample_rtt):
        if self.srtt == 0.0:
            self.srtt = sample_rtt
            self.rttvar = sample_rtt / 2
        else:
            self.rttvar = (1 - BETA) * self.rttvar + BETA * abs(self.srtt - sample_rtt)
            self.srtt = (1 - ALPHA) * self.srtt + ALPHA * sample_rtt
        self.rto = max(MIN_RTO, min(self.srtt + 4 * self.rttvar, MAX_RTO))

    def get_next_chunk(self):
        if self.next_seq < self.file_size:
            s = self.next_seq
            e = min(s + PAYLOAD_SIZE, self.file_size)
            data = self.file_data[s:e]
            self.next_seq = e
            return s, data, 0
        elif self.eof_sent_seq == -1:
            self.eof_sent_seq = self.file_size
            self.next_seq = self.file_size + 1
            return self.eof_sent_seq, b"EOF", EOF_FLAG
        else:
            return None, None, 0

    def send_packet(self, seq, data, flags):
        header = self.pack_header(seq, 0, flags)
        pkt = header + data
        self.sock.sendto(pkt, self.client_addr)
        self.sent[seq] = (pkt, time.time(), 0)

    def resend_packet(self, seq):
        if seq not in self.sent:
            return
        pkt, _, rc = self.sent[seq]
        self.sent[seq] = (pkt, time.time(), rc + 1)
        self.sock.sendto(pkt, self.client_addr)

    def send_window(self):
        # allow burst send = 2×SWS in startup
        in_flight = self.next_seq - self.base
        limit = 2 * self.sws if self.base == 0 else self.sws
        while in_flight < limit:
            seq, data, flags = self.get_next_chunk()
            if seq is None:
                break
            self.send_packet(seq, data, flags)
            if flags & EOF_FLAG:
                break
            in_flight = self.next_seq - self.base

    def handle_ack(self, pkt):
        seq, cum_ack, flags, sack_start, sack_end = self.unpack_header(pkt)
        now = time.time()

        if cum_ack == self.base:
            self.dup_ack_count += 1
            if self.dup_ack_count >= 2:  # faster fast-retransmit
                if self.base in self.sent:
                    print(f"[FAST-RETX] seq={self.base}")
                    self.resend_packet(self.base)
                    self.rto = min(self.rto * 1.5, MAX_RTO)
            return

        if cum_ack > self.base:
            self.dup_ack_count = 0
            acked = [s for s in self.sent.keys() if s < cum_ack]
            if acked:
                newest = max(acked)
                pkt, stime, rc = self.sent[newest]
                if rc == 0:
                    self.update_rto(now - stime)
            for s in acked:
                self.sent.pop(s, None)
            self.base = cum_ack

            if flags & EOF_FLAG and cum_ack > self.eof_sent_seq:
                return "DONE"

        # optional sack cleanup
        if sack_end > sack_start:
            for s in list(self.sent.keys()):
                if sack_start <= s < sack_end:
                    self.sent.pop(s, None)
        return "CONTINUE"

    def check_timeouts(self):
        now = time.time()
        expired = []
        for seq, (pkt, stime, rc) in self.sent.items():
            if now - stime >= self.rto:
                expired.append(seq)
        for seq in expired[:3]:  # batch retransmit up to 3
            print(f"[TIMEOUT] retrans seq={seq}")
            self.resend_packet(seq)
        if expired:
            self.rto = min(self.rto * 1.5, MAX_RTO)

    def run(self):
        print("Waiting for client request...")
        readable, _, _ = select.select([self.sock], [], [], 30.0)
        if not readable:
            print("No client request. Exit.")
            return
        req, addr = self.sock.recvfrom(1024)
        self.client_addr = addr
        print("Client:", addr)

        self.start_time = time.time()
        self.last_ack_time = self.start_time
        running = True

        while running:
            timeout = 0.01
            rlist, _, _ = select.select([self.sock], [], [], timeout)
            if rlist:
                pkt, _ = self.sock.recvfrom(MSS_BYTES)
                res = self.handle_ack(pkt)
                if res == "DONE":
                    running = False
                    break
                self.last_ack_time = time.time()
            else:
                self.check_timeouts()

            self.send_window()

            if time.time() - self.last_ack_time > 10:
                print("No ACKs for 10 s, ending.")
                break

        duration = time.time() - self.start_time
        print(f"Transfer complete: {self.file_size} bytes in {duration:.2f}s")
        self.sock.close()


def main():
    if len(sys.argv) != 4:
        print("Usage: python3 p1_server.py <SERVER_IP> <SERVER_PORT> <SWS>")
        sys.exit(1)
    ip, port, sws = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    P1Server(ip, port, sws).run()


if __name__ == "__main__":
    main()
