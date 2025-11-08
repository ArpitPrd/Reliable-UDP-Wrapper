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

# Flags
SYN_FLAG = 0x1
ACK_FLAG = 0x2
EOF_FLAG = 0x4

MAX_RETRIES = 5
REQUEST_TIMEOUT = 2.0  # per assignment: 2-second retry timeout
MAX_RECV_WINDOW_PACKETS = 2000

class P1Client:
    def __init__(self, server_ip, server_port):
        self.server = (server_ip, int(server_port))
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(REQUEST_TIMEOUT)
        self.next_expected = 0
        self.recv_heap = []      # (seq, data, flags)
        self.recv_set = set()
        self.output_file = None
        self.start_time = 0.0

    def pack_header(self, seq, ack, flags, sack_start=0, sack_end=0):
        return struct.pack(HEADER_FORMAT, seq, ack, flags, sack_start, sack_end)

    def unpack_header(self, pkt):
        if len(pkt) < HEADER_SIZE:
            return (None,)*6
        try:
            seq, ack, flags, sack_start, sack_end = struct.unpack(HEADER_FORMAT, pkt[:HEADER_SIZE])
            data = pkt[HEADER_SIZE:]
            return seq, ack, flags, sack_start, sack_end, data
        except struct.error:
            return (None,)*6

    def send_request(self):
        req = b'\x01'
        for i in range(MAX_RETRIES):
            try:
                # send request and wait for first data packet
                self.sock.sendto(req, self.server)
                pkt, _ = self.sock.recvfrom(MSS_BYTES)
                print("Received first packet from server.")
                return pkt
            except socket.timeout:
                print(f"Request attempt {i+1} timed out, retrying...")
                continue
            except Exception as e:
                print("Request error:", e)
                return None
        print("Failed to contact server after retries.")
        return None

    def prepare_ack(self, ack_num, flags=ACK_FLAG, sack_start=0, sack_end=0):
        hdr = self.pack_header(0, ack_num, flags, sack_start, sack_end)
        try:
            self.sock.sendto(hdr, self.server)
        except Exception:
            pass

    def find_first_sack_block(self):
        if not self.recv_heap:
            return 0, 0
        try:
            seq, data, flags = self.recv_heap[0]
            return seq, seq + len(data)
        except Exception:
            return 0, 0

    def write_to_file(self, data):
        try:
            self.output_file.write(data)
        except Exception as e:
            print("Write error:", e)

    def process_packet(self, pkt):
        seq, ack, flags, sack_start, sack_end, data = self.unpack_header(pkt)
        if seq is None:
            return "CONTINUE"
        # EOF handling
        if flags & EOF_FLAG:
            if seq == self.next_expected:
                # expected EOF -> ack and done
                self.prepare_ack(seq + 1, flags=ACK_FLAG | EOF_FLAG)
                print("Received EOF expected; sending final ACK.")
                return "DONE"
            else:
                # out-of-order EOF -> buffer and send ACK for current cumulative
                if seq not in self.recv_set:
                    heapq.heappush(self.recv_heap, (seq, data, flags))
                    self.recv_set.add(seq)
                sack_s, sack_e = self.find_first_sack_block()
                self.prepare_ack(self.next_expected, sack_start=sack_s, sack_end=sack_e)
                return "CONTINUE"

        # data packet
        if seq == self.next_expected:
            # write and advance
            self.write_to_file(data)
            self.next_expected += len(data)
            # consume buffered contiguous packets
            while self.recv_heap and self.recv_heap[0][0] == self.next_expected:
                s, d, f = heapq.heappop(self.recv_heap)
                self.recv_set.remove(s)
                if f & EOF_FLAG:
                    self.prepare_ack(self.next_expected + 1, flags=ACK_FLAG | EOF_FLAG)
                    return "DONE"
                self.write_to_file(d)
                self.next_expected += len(d)
            # send cumulative ack (with optional SACK if out-of-order blocks)
            sack_s, sack_e = self.find_first_sack_block()
            self.prepare_ack(self.next_expected, sack_start=sack_s, sack_end=sack_e)
        elif seq > self.next_expected:
            # out-of-order: buffer it
            if seq not in self.recv_set:
                if len(self.recv_heap) < MAX_RECV_WINDOW_PACKETS:
                    heapq.heappush(self.recv_heap, (seq, data, flags))
                    self.recv_set.add(seq)
            # send dup ack + SACK for this block
            self.prepare_ack(self.next_expected, sack_start=seq, sack_end=seq + len(data))
        else:
            # old duplicate -> resend ack
            sack_s, sack_e = self.find_first_sack_block()
            self.prepare_ack(self.next_expected, sack_start=sack_s, sack_end=sack_e)

        return "CONTINUE"

    def run(self):
        first_pkt = self.send_request()
        if first_pkt is None:
            self.sock.close()
            return

        # open output
        try:
            self.output_file = open("received_data.txt", "wb")
        except IOError as e:
            print("Cannot open output file:", e)
            self.sock.close()
            return

        self.start_time = time.time()

        # process first packet
        if self.process_packet(first_pkt) == "DONE":
            self.cleanup()
            return

        # set longer timeout for rest of transfer (server might stop sending)
        self.sock.settimeout(30.0)
        running = True
        while running:
            try:
                pkt, _ = self.sock.recvfrom(MSS_BYTES)
                if self.process_packet(pkt) == "DONE":
                    running = False
            except socket.timeout:
                print("Timeout waiting for server. Exiting.")
                running = False
            except Exception as e:
                print("Recv error:", e)
                running = False

        self.cleanup()

    def cleanup(self):
        if self.output_file:
            self.output_file.close()
            try:
                size = os.path.getsize("received_data.txt")
            except Exception:
                size = 0
            duration = time.time() - self.start_time if self.start_time > 0 else 0
            print("Download complete. Size:", size, "bytes. Duration: {:.2f}s".format(duration))
        self.sock.close()

def main():
    if len(sys.argv) != 3:
        print("Usage: python3 p1_client.py <SERVER_IP> <SERVER_PORT>")
        sys.exit(1)
    server_ip = sys.argv[1]
    server_port = int(sys.argv[2])
    client = P1Client(server_ip, server_port)
    client.run()

if __name__ == "__main__":
    main()
