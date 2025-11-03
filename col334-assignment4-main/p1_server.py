#!/usr/bin/env python3
"""
Part 1 Server: Reliable UDP File Transfer
Implements sliding window protocol with SACK, fast retransmit, and adaptive RTO
"""

import socket
import sys
import time
import struct
import select

# Constants
MAX_PAYLOAD = 1200
HEADER_SIZE = 20
DATA_SIZE = MAX_PAYLOAD - HEADER_SIZE  # 1180 bytes
INITIAL_RTO = 1.0  # Initial retransmission timeout
MIN_RTO = 0.2
MAX_RTO = 3.0
ALPHA = 0.125  # RTT smoothing factor
BETA = 0.25    # RTT variance smoothing factor
DUP_ACK_THRESHOLD = 3  # Fast retransmit after 3 duplicate ACKs


class ReliableUDPServer:
    def __init__(self, server_ip, server_port, sws):
        self.server_ip = server_ip
        self.server_port = server_port
        self.sws = sws  # Sender window size in bytes
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind((server_ip, server_port))

        # RTT estimation variables
        self.estimated_rtt = None
        self.dev_rtt = 0
        self.rto = INITIAL_RTO

        # Sliding window state
        self.base = 0  # First unacknowledged byte
        self.next_seq = 0  # Next byte to send
        self.packets = {}  # {seq_num: (data, timestamp)}
        self.dup_ack_count = 0
        self.last_ack = 0

        print(f"Server started on {server_ip}:{server_port} with SWS={sws}")

    def create_packet(self, seq_num, data, sack_blocks=None):
        """
        Create a packet with header (20 bytes) + data
        Header format:
        - Sequence number: 4 bytes
        - Reserved for SACK: 16 bytes (can include up to 4 SACK blocks of 4 bytes each)
        """
        header = struct.pack('!I', seq_num)  # 4 bytes: sequence number

        # Reserved 16 bytes - unused for data packets (server doesn't send SACK)
        reserved = b'\x00' * 16

        packet = header + reserved + data
        return packet

    def parse_ack(self, packet):
        """
        Parse ACK packet from client
        Returns: (ack_num, sack_blocks)
        SACK blocks format in reserved bytes: [(start1, end1), (start2, end2), ...]
        """
        if len(packet) < 4:
            return None, []

        ack_num = struct.unpack('!I', packet[:4])[0]

        # Parse SACK blocks from reserved 16 bytes
        sack_blocks = []
        if len(packet) >= 20:
            reserved = packet[4:20]
            # Each SACK block: 4 bytes start, 4 bytes end (but we'll use simpler format)
            # Format: number of blocks (1 byte) + blocks (each 8 bytes: 4 start + 4 end)
            try:
                num_blocks = reserved[0]
                offset = 1
                for i in range(min(num_blocks, 1)):  # Max 1 SACK block in 15 remaining bytes
                    if offset + 8 <= 16:
                        start = struct.unpack('!I', reserved[offset:offset+4])[0]
                        end = struct.unpack('!I', reserved[offset+4:offset+8])[0]
                        sack_blocks.append((start, end))
                        offset += 8
            except:
                pass

        return ack_num, sack_blocks

    def update_rtt(self, sample_rtt):
        """Update RTT estimates using Jacobson/Karels algorithm"""
        if self.estimated_rtt is None:
            self.estimated_rtt = sample_rtt
            self.dev_rtt = sample_rtt / 2
        else:
            self.dev_rtt = (1 - BETA) * self.dev_rtt + BETA * abs(sample_rtt - self.estimated_rtt)
            self.estimated_rtt = (1 - ALPHA) * self.estimated_rtt + ALPHA * sample_rtt

        self.rto = self.estimated_rtt + 4 * self.dev_rtt
        self.rto = max(MIN_RTO, min(MAX_RTO, self.rto))

    def send_file(self, client_addr, file_path):
        """Send file to client using sliding window protocol"""
        # Read entire file
        try:
            with open(file_path, 'rb') as f:
                file_data = f.read()
        except FileNotFoundError:
            print(f"Error: File {file_path} not found")
            return

        total_bytes = len(file_data)
        print(f"Sending file: {total_bytes} bytes ({total_bytes/DATA_SIZE:.0f} packets)")

        # Reset state
        self.base = 0
        self.next_seq = 0
        self.packets = {}
        self.dup_ack_count = 0
        self.last_ack = 0

        start_time = time.time()
        last_print = start_time

        while self.base < total_bytes:
            # Send new packets within window
            while self.next_seq < total_bytes and (self.next_seq - self.base) < self.sws:
                # Extract data chunk
                data_start = self.next_seq
                data_end = min(self.next_seq + DATA_SIZE, total_bytes)
                data = file_data[data_start:data_end]

                # Create and send packet
                packet = self.create_packet(self.next_seq, data)
                self.socket.sendto(packet, client_addr)

                # Store packet and timestamp for retransmission
                self.packets[self.next_seq] = (data, time.time())
                self.next_seq = data_end

            # Wait for ACK with timeout
            ready = select.select([self.socket], [], [], self.rto)

            if ready[0]:
                # Receive ACK
                try:
                    ack_packet, addr = self.socket.recvfrom(MAX_PAYLOAD)
                    ack_num, sack_blocks = self.parse_ack(ack_packet)

                    if ack_num is None:
                        continue

                    # Update RTT if this ACKs new data
                    if ack_num > self.base and self.base in self.packets:
                        sample_rtt = time.time() - self.packets[self.base][1]
                        self.update_rtt(sample_rtt)

                    # Handle SACK blocks - mark packets as received
                    for start, end in sack_blocks:
                        # Remove SACKed packets from retransmission queue
                        seqs_to_remove = [seq for seq in self.packets.keys()
                                         if start <= seq < end]
                        for seq in seqs_to_remove:
                            if seq >= ack_num:  # Only remove if beyond cumulative ACK
                                del self.packets[seq]

                    # Process cumulative ACK
                    if ack_num > self.base:
                        # New ACK - slide window
                        # Remove acknowledged packets
                        seqs_to_remove = [seq for seq in self.packets.keys() if seq < ack_num]
                        for seq in seqs_to_remove:
                            del self.packets[seq]

                        self.base = ack_num
                        self.dup_ack_count = 0
                        self.last_ack = ack_num

                    elif ack_num == self.last_ack:
                        # Duplicate ACK
                        self.dup_ack_count += 1

                        # Fast retransmit
                        if self.dup_ack_count == DUP_ACK_THRESHOLD:
                            if self.base in self.packets:
                                data, _ = self.packets[self.base]
                                packet = self.create_packet(self.base, data)
                                self.socket.sendto(packet, client_addr)
                                self.packets[self.base] = (data, time.time())
                                print(f"Fast retransmit: seq={self.base}")

                except Exception as e:
                    print(f"Error receiving ACK: {e}")
                    continue

            else:
                # Timeout - retransmit oldest unacked packet
                if self.base in self.packets:
                    data, _ = self.packets[self.base]
                    packet = self.create_packet(self.base, data)
                    self.socket.sendto(packet, client_addr)
                    self.packets[self.base] = (data, time.time())

                    # Exponential backoff
                    self.rto = min(self.rto * 1.5, MAX_RTO)
                    print(f"Timeout retransmit: seq={self.base}, RTO={self.rto:.3f}")

            # Progress report every 2 seconds
            current_time = time.time()
            if current_time - last_print >= 2.0:
                progress = (self.base / total_bytes) * 100
                elapsed = current_time - start_time
                rate = (self.base / 1024) / elapsed if elapsed > 0 else 0
                print(f"Progress: {progress:.1f}% ({self.base}/{total_bytes}), "
                      f"Rate: {rate:.1f} KB/s, RTO: {self.rto:.3f}s")
                last_print = current_time

        # Send EOF marker
        eof_packet = self.create_packet(total_bytes, b"EOF")
        for _ in range(5):  # Send EOF multiple times to ensure delivery
            self.socket.sendto(eof_packet, client_addr)
            time.sleep(0.1)

        duration = time.time() - start_time
        rate = (total_bytes / 1024) / duration
        print(f"\nFile transfer complete!")
        print(f"Time: {duration:.2f}s, Average rate: {rate:.1f} KB/s")

    def run(self):
        """Main server loop"""
        print("Waiting for client request...")

        # Wait for client request
        data, client_addr = self.socket.recvfrom(MAX_PAYLOAD)
        print(f"Request received from {client_addr}")

        # Send file
        self.send_file(client_addr, "data.txt")

        print("Server shutting down")
        self.socket.close()


def main():
    if len(sys.argv) != 4:
        print("Usage: python3 p1_server.py <SERVER_IP> <SERVER_PORT> <SWS>")
        sys.exit(1)

    server_ip = sys.argv[1]
    server_port = int(sys.argv[2])
    sws = int(sys.argv[3])

    server = ReliableUDPServer(server_ip, server_port, sws)
    server.run()


if __name__ == "__main__":
    main()
