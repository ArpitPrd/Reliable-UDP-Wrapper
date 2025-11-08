#!/usr/bin/env python3
import socket, sys, time, struct, select, collections

HEADER_FORMAT = "!IIHII2x"
HEADER_SIZE = 20
MSS_BYTES = 1200
PAYLOAD_SIZE = MSS_BYTES - HEADER_SIZE

ACK_FLAG = 0x2
EOF_FLAG = 0x4

ALPHA, BETA = 0.125, 0.25
INITIAL_RTO, MIN_RTO, MAX_RTO = 0.05, 0.02, 0.8
FAST_DUP_THRESH = 2
BATCH_RETX_LIMIT = 5

class P1Server:
    def __init__(self, ip, port, sws_bytes):
        self.ip, self.port, self.sws = ip, int(port), int(sws_bytes)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((self.ip, self.port))
        self.sock.setblocking(False)

        with open("data.txt", "rb") as f:
            self.data = f.read()
        self.size = len(self.data)

        self.base = 0
        self.next_seq = 0
        self.sent = collections.OrderedDict()
        self.client = None

        self.srtt = 0.0
        self.rttvar = 0.0
        self.rto = INITIAL_RTO

        self.dup_acks = 0
        self.eof_sent = False
        self.start_time = 0

        print(f"[Server] Ready on {self.ip}:{self.port} | SWS={self.sws}")

    def hdr(self, seq, ack, flags, s_s=0, s_e=0):
        return struct.pack(HEADER_FORMAT, seq, ack, flags, s_s, s_e)

    def unpack(self, pkt):
        return struct.unpack(HEADER_FORMAT, pkt[:HEADER_SIZE])

    def update_rto(self, sample):
        if self.srtt == 0:
            self.srtt, self.rttvar = sample, sample / 2
        else:
            self.rttvar = (1 - BETA) * self.rttvar + BETA * abs(self.srtt - sample)
            self.srtt = (1 - ALPHA) * self.srtt + ALPHA * sample
        self.rto = max(MIN_RTO, min(self.srtt + 4 * self.rttvar, MAX_RTO))

    def send_pkt(self, seq, data, flags):
        pkt = self.hdr(seq, 0, flags) + data
        self.sock.sendto(pkt, self.client)
        self.sent[seq] = (pkt, time.time(), 0)

    def get_chunk(self):
        if self.next_seq < self.size:
            s = self.next_seq
            e = min(s + PAYLOAD_SIZE, self.size)
            d = self.data[s:e]
            self.next_seq = e
            return s, d, 0
        elif not self.eof_sent:
            self.eof_sent = True
            return self.size, b"EOF", EOF_FLAG
        return None, None, 0

    def fill_pipe(self):
        in_flight = self.next_seq - self.base
        limit = min(8 * self.sws, self.size)  # large burst cap
        while in_flight < limit:
            seq, data, flags = self.get_chunk()
            if seq is None:
                break
            self.send_pkt(seq, data, flags)
            if flags & EOF_FLAG:
                break
            in_flight = self.next_seq - self.base

    def handle_ack(self, pkt):
        seq, ack, flags, sack_s, sack_e = self.unpack(pkt)
        now = time.time()
        if ack == self.base:
            self.dup_acks += 1
            if self.dup_acks >= FAST_DUP_THRESH:
                for s in list(self.sent.keys())[:BATCH_RETX_LIMIT]:
                    self.sock.sendto(self.sent[s][0], self.client)
                self.rto = min(self.rto * 1.3, MAX_RTO)
            return "CONT"
        if ack > self.base:
            self.dup_acks = 0
            acked = [s for s in self.sent if s < ack]
            if acked:
                newest = max(acked)
                pkt, stime, rc = self.sent[newest]
                if rc == 0:
                    self.update_rto(now - stime)
            for s in acked:
                self.sent.pop(s, None)
            self.base = ack
            if flags & EOF_FLAG and ack > self.size:
                return "DONE"
        if sack_e > sack_s:
            for s in list(self.sent.keys()):
                if sack_s <= s < sack_e:
                    self.sent.pop(s, None)
        return "CONT"

    def check_timeouts(self):
        now = time.time()
        expired = [s for s, (_, st, _) in self.sent.items() if now - st > self.rto]
        for s in expired[:BATCH_RETX_LIMIT]:
            pkt, _, rc = self.sent[s]
            self.sent[s] = (pkt, now, rc + 1)
            self.sock.sendto(pkt, self.client)
        if expired:
            self.rto = min(self.rto * 1.3, MAX_RTO)

    def run(self):
        print("Awaiting client...")
        readable, _, _ = select.select([self.sock], [], [], 15)
        if not readable:
            print("No client, exiting.")
            return
        _, addr = self.sock.recvfrom(64)
        self.client = addr
        self.start_time = time.time()
        print(f"Client {addr}")

        while True:
            rlist, _, _ = select.select([self.sock], [], [], 0.001)
            if rlist:
                pkt, _ = self.sock.recvfrom(MSS_BYTES)
                res = self.handle_ack(pkt)
                if res == "DONE":
                    break
            self.check_timeouts()
            self.fill_pipe()
            if self.eof_sent and not self.sent:
                break
        dur = time.time() - self.start_time
        print(f"✅ Transfer complete: {self.size} bytes in {dur:.2f}s")
        self.sock.close()

def main():
    if len(sys.argv) != 4:
        print("Usage: python3 p1_server.py <IP> <PORT> <SWS>")
        sys.exit(1)
    P1Server(sys.argv[1], int(sys.argv[2]), int(sys.argv[3])).run()

if __name__ == "__main__":
    main()
