#!/usr/bin/env python3
import socket
import sys
import time
import struct
import heapq
import os

HEADER_FORMAT = "!IIHII2x"
HEADER_SIZE = 20
MSS_BYTES = 1200
PAYLOAD_SIZE = MSS_BYTES - HEADER_SIZE

ACK_FLAG = 0x2
EOF_FLAG = 0x4

MAX_RETRIES = 3
REQUEST_TIMEOUT = 1.0
MAX_RECV_WINDOW_PACKETS = 2000

class P1Client:
    def __init__(self, server_ip, server_port):
        self.server = (server_ip, int(server_port))
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(REQUEST_TIMEOUT)
        self.next_expected = 0
        self.recv_heap = []
        self.recv_set = set()
        self.output_file = None

    def pack_header(self, seq, ack, flags, sack_start=0, sack_end=0):
        return struct.pack(HEADER_FORMAT, seq, ack, flags, sack_start, sack_end)

    def unpack_header(self, pkt):
        if len(pkt) < HEADER_SIZE:
            return (None,)*6
        return (*struct.unpack(HEADER_FORMAT, pkt[:HEADER_SIZE]), pkt[HEADER_SIZE:])

    def send_request(self):
        req = b'\x01'
        for i in range(MAX_RETRIES):
            try:
                self.sock.sendto(req, self.server)
                pkt, _ = self.sock.recvfrom(MSS_BYTES)
                print("Connected to server.")
                return pkt
            except socket.timeout:
                print(f"Retry {i+1}")
        print("Failed to contact server.")
        return None

    def send_ack(self, ack_num, flags=ACK_FLAG, sack_start=0, sack_end=0):
        hdr = self.pack_header(0, ack_num, flags, sack_start, sack_end)
        self.sock.sendto(hdr, self.server)

    def find_sack(self):
        if not self.recv_heap:
            return 0, 0
        seq, data, flags = self.recv_heap[0]
        return seq, seq + len(data)

    def write_data(self, data):
        self.output_file.write(data)

    def process(self, pkt):
        seq, ack, flags, sack_start, sack_end, data = self.unpack_header(pkt)
        if seq is None:
            return "CONT"
        if flags & EOF_FLAG:
            if seq == self.next_expected:
                self.send_ack(seq + 1, ACK_FLAG | EOF_FLAG)
                return "DONE"
            else:
                if seq not in self.recv_set:
                    heapq.heappush(self.recv_heap, (seq, data, flags))
                    self.recv_set.add(seq)
                self.send_ack(self.next_expected, sack_start=seq, sack_end=seq + len(data))
                return "CONT"
        if seq == self.next_expected:
            self.write_data(data)
            self.next_expected += len(data)
            while self.recv_heap and self.recv_heap[0][0] == self.next_expected:
                s, d, f = heapq.heappop(self.recv_heap)
                self.recv_set.remove(s)
                self.write_data(d)
                self.next_expected += len(d)
                if f & EOF_FLAG:
                    self.send_ack(self.next_expected + 1, ACK_FLAG | EOF_FLAG)
                    return "DONE"
            s_s, s_e = self.find_sack()
            self.send_ack(self.next_expected, sack_start=s_s, sack_end=s_e)
        elif seq > self.next_expected:
            if seq not in self.recv_set:
                heapq.heappush(self.recv_heap, (seq, data, flags))
                self.recv_set.add(seq)
            self.send_ack(self.next_expected, sack_start=seq, sack_end=seq + len(data))
        else:
            s_s, s_e = self.find_sack()
            self.send_ack(self.next_expected, sack_start=s_s, sack_end=s_e)
        return "CONT"

    def run(self):
        first = self.send_request()
        if not first:
            return
        self.output_file = open("received_data.txt", "wb")
        self.sock.settimeout(15.0)
        if self.process(first) == "DONE":
            return
        while True:
            try:
                pkt, _ = self.sock.recvfrom(MSS_BYTES)
                if self.process(pkt) == "DONE":
                    break
            except socket.timeout:
                break
        self.output_file.close()
        print("File received successfully.")

def main():
    if len(sys.argv) != 3:
        print("Usage: python3 p1_client.py <SERVER_IP> <SERVER_PORT>")
        sys.exit(1)
    P1Client(sys.argv[1], sys.argv[2]).run()

if __name__ == "__main__":
    main()
