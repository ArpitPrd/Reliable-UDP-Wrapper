#!/usr/bin/env python3
import socket, sys, time, struct, heapq, os

HEADER_FORMAT = "!IIHII2x"
HEADER_SIZE = 20
MSS_BYTES = 1200
PAYLOAD_SIZE = MSS_BYTES - HEADER_SIZE
ACK_FLAG, EOF_FLAG = 0x2, 0x4
REQ_TIMEOUT = 0.5
MAX_RETRY = 3

class P1Client:
    def __init__(self, ip, port):
        self.server = (ip, int(port))
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(REQ_TIMEOUT)
        self.next_exp = 0
        self.buf = []
        self.bufset = set()

    def hdr(self, seq, ack, flags, s_s=0, s_e=0):
        return struct.pack(HEADER_FORMAT, seq, ack, flags, s_s, s_e)

    def unpack(self, pkt):
        if len(pkt) < HEADER_SIZE: return (None,)*6
        seq, ack, flags, s_s, s_e = struct.unpack(HEADER_FORMAT, pkt[:HEADER_SIZE])
        return seq, ack, flags, s_s, s_e, pkt[HEADER_SIZE:]

    def send_req(self):
        req = b'\x01'
        for i in range(MAX_RETRY):
            try:
                self.sock.sendto(req, self.server)
                pkt, _ = self.sock.recvfrom(MSS_BYTES)
                print("Connected to server.")
                return pkt
            except socket.timeout:
                print("Retry", i+1)
        print("Server unreachable.")
        return None

    def send_ack(self, ack, flags=ACK_FLAG, s_s=0, s_e=0):
        self.sock.sendto(self.hdr(0, ack, flags, s_s, s_e), self.server)

    def sack_hint(self):
        if not self.buf: return 0,0
        s,d,f = self.buf[0]
        return s, s+len(d)

    def write_data(self, f, d): f.write(d)

    def handle(self, pkt, f):
        seq, _, flags, s_s, s_e, data = self.unpack(pkt)
        if seq is None: return "CONT"
        if flags & EOF_FLAG and seq == self.next_exp:
            self.send_ack(seq+1, ACK_FLAG|EOF_FLAG)
            return "DONE"
        if seq == self.next_exp:
            self.write_data(f, data)
            self.next_exp += len(data)
            while self.buf and self.buf[0][0] == self.next_exp:
                s,d,f2 = heapq.heappop(self.buf)
                self.bufset.remove(s)
                self.write_data(f, d)
                self.next_exp += len(d)
            s_s, s_e = self.sack_hint()
            self.send_ack(self.next_exp, s_s=s_s, s_e=s_e)
        elif seq > self.next_exp and seq not in self.bufset:
            heapq.heappush(self.buf, (seq, data, flags))
            self.bufset.add(seq)
            self.send_ack(self.next_exp, s_s=seq, s_e=seq+len(data))
        else:
            s_s, s_e = self.sack_hint()
            self.send_ack(self.next_exp, s_s=s_s, s_e=s_e)
        return "CONT"

    def run(self):
        pkt = self.send_req()
        if not pkt: return
        f = open("received_data.txt", "wb")
        if self.handle(pkt, f) == "DONE": f.close(); return
        self.sock.settimeout(15)
        while True:
            try:
                pkt, _ = self.sock.recvfrom(MSS_BYTES)
                if self.handle(pkt, f) == "DONE": break
            except socket.timeout: break
        f.close()
        print("File received OK")

def main():
    if len(sys.argv)!=3:
        print("Usage: python3 p1_client.py <IP> <PORT>")
        sys.exit(1)
    P1Client(sys.argv[1], sys.argv[2]).run()

if __name__=="__main__":
    main()
