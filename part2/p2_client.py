#!/usr/bin/env python3
"""
p2_client.py
Client for Part 2. Usage:
    python3 p2_client.py <SERVER_IP> <SERVER_PORT> <PREF>

Saves received file as <PREF>received_data.txt
Sends ACKs immediately (no deliberate delayed ACK), uses small socket timeout.
"""
import socket
import struct
import sys
import time
import heapq
import os

HEADER_FORMAT = "!IIHII2x"
HEADER_SIZE = 20
MSS_BYTES = 1200
PAYLOAD_SIZE = MSS_BYTES - HEADER_SIZE

SYN_FLAG = 0x1
ACK_FLAG = 0x2
EOF_FLAG = 0x4

MAX_RECV_WINDOW_PACKETS = 4000

class Client:
    def __init__(self, server_ip, server_port, prefix):
        self.server = (server_ip, int(server_port))
        self.prefix = prefix
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # keep short timeout to detect server death quickly
        self.sock.settimeout(30.0)
        # tune send buffer to ensure ACKs go out promptly
        try:
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 64 * 1024)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 256 * 1024)
        except Exception:
            pass

        self.next_expected = 0
        self.recv_heap = []          # min-heap of (seq, data, flags)
        self.recv_set = set()
        self.output_file = None
        self.start_time = None

    def pack_header(self, seq, ack, flags, sack_start=0, sack_end=0):
        return struct.pack(HEADER_FORMAT, int(seq), int(ack), int(flags), int(sack_start), int(sack_end))

    def unpack_header(self, pkt):
        try:
            seq, ack, flags, sack_start, sack_end = struct.unpack(HEADER_FORMAT, pkt[:HEADER_SIZE])
            data = pkt[HEADER_SIZE:]
            return seq, ack, flags, sack_start, sack_end, data
        except struct.error:
            return None, None, None, None, None, None

    def send_request(self):
        # send a 1-byte request; retry a couple times
        req = b'\x01'
        tries = 6
        for i in range(tries):
            try:
                self.sock.sendto(req, self.server)
                # wait for first packet
                pkt, _ = self.sock.recvfrom(MSS_BYTES)
                return pkt
            except socket.timeout:
                continue
            except Exception as e:
                print("Request error:", e)
                return None
        return None

    def send_ack(self, ack_num, flags=ACK_FLAG, sack_start=0, sack_end=0):
        hdr = self.pack_header(0, ack_num, flags, sack_start, sack_end)
        try:
            # send immediately; don't wait
            self.sock.sendto(hdr, self.server)
        except Exception:
            pass

    def find_first_sack(self):
        if not self.recv_heap:
            return 0, 0
        seq, data, flags = self.recv_heap[0]
        return seq, seq + len(data)

    def flush_contiguous(self):
        # pop heap while contiguous
        while self.recv_heap and self.recv_heap[0][0] == self.next_expected:
            seq, data, flags = heapq.heappop(self.recv_heap)
            self.recv_set.remove(seq)
            if flags & EOF_FLAG:
                # final ack and write nothing extra (EOF marker not part of file)
                self.send_ack(seq + 1, flags=ACK_FLAG | EOF_FLAG)
                return True
            # write data
            try:
                self.output_file.write(data)
            except Exception as e:
                print("Write error:", e)
            self.next_expected += len(data)
        return False

    def process_packet(self, pkt):
        seq, ack, flags, sack_s, sack_e, data = self.unpack_header(pkt)
        if seq is None:
            return "CONTINUE"
        # immediate ACK behavior:
        # If expected packet -> write and ack cumulative
        if flags & EOF_FLAG:
            # If EOF in-order
            if seq == self.next_expected:
                # send final ACK acknowledging EOF
                self.send_ack(seq + 1, flags=ACK_FLAG | EOF_FLAG)
                return "DONE"
            else:
                # out-of-order EOF, buffer
                if seq not in self.recv_set and len(self.recv_heap) < MAX_RECV_WINDOW_PACKETS:
                    heapq.heappush(self.recv_heap, (seq, data, flags))
                    self.recv_set.add(seq)
                sack_start, sack_end = self.find_first_sack()
                self.send_ack(self.next_expected, sack_start=sack_start, sack_end=sack_end)
                return "CONTINUE"

        if seq == self.next_expected:
            # write and slide
            try:
                self.output_file.write(data)
            except Exception:
                pass
            self.next_expected += len(data)
            # flush contiguous buffered segments
            done = self.flush_contiguous()
            if done:
                return "DONE"
            # send cumulative ack
            sack_start, sack_end = self.find_first_sack()
            self.send_ack(self.next_expected, sack_start=sack_start, sack_end=sack_end)
        elif seq > self.next_expected:
            # buffer out-of-order
            if seq not in self.recv_set and len(self.recv_heap) < MAX_RECV_WINDOW_PACKETS:
                heapq.heappush(self.recv_heap, (seq, data, flags))
                self.recv_set.add(seq)
            # send duplicate ACK + SACK pointing to the out-of-order block
            self.send_ack(self.next_expected, sack_start=seq, sack_end=seq + len(data))
        else:
            # old duplicate packet - resend ack
            sack_start, sack_end = self.find_first_sack()
            self.send_ack(self.next_expected, sack_start=sack_start, sack_end=sack_end)
        return "CONTINUE"

    def run(self):
        first = self.send_request()
        if first is None:
            print("Failed to contact server.")
            self.sock.close()
            return
        # open file
        outname = f"{self.prefix}received_data.txt"
        try:
            self.output_file = open(outname, "wb")
        except Exception as e:
            print("Could not open output file:", e)
            self.sock.close()
            return
        self.start_time = time.time()
        # process first pkt
        res = self.process_packet(first)
        if res == "DONE":
            self.cleanup()
            return
        # main loop
        running = True
        while running:
            try:
                pkt, _ = self.sock.recvfrom(MSS_BYTES + 64)
                r = self.process_packet(pkt)
                if r == "DONE":
                    running = False
                    break
            except socket.timeout:
                print("Timeout waiting for server; closing.")
                break
            except Exception as e:
                print("Recv error:", e)
                break
        self.cleanup()

    def cleanup(self):
        end = time.time()
        dur = end - (self.start_time or end)
        if self.output_file:
            self.output_file.close()
            try:
                size = os.path.getsize(f"{self.prefix}received_data.txt")
            except Exception:
                size = 0
            if dur > 0:
                thr = (size * 8) / (dur * 1e6)
                print(f"Downloaded {size} bytes in {dur:.3f}s => {thr:.3f} Mbps")
        self.sock.close()


def main():
    if len(sys.argv) != 4:
        print("Usage: python3 p2_client.py <SERVER_IP> <SERVER_PORT> <PREF>")
        sys.exit(1)
    c = Client(sys.argv[1], sys.argv[2], sys.argv[3])
    c.run()

if __name__ == "__main__":
    main()
