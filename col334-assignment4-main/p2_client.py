#!/usr/bin/env python3
"""
Part 2 Client: Reliable UDP File Transfer with Congestion Control
Receiver implementation with cumulative ACKs and SACK support
"""

import socket
import sys
import time
import struct
import select

# Constants
MAX_PAYLOAD = 1200
HEADER_SIZE = 20
DATA_SIZE = MAX_PAYLOAD - HEADER_SIZE
REQUEST_TIMEOUT = 2.0
MAX_REQUEST_RETRIES = 5
ACK_DELAY = 0.001


class ReliableUDPClient:
    def __init__(self, server_ip, server_port, prefix):
        self.server_ip = server_ip
        self.server_port = server_port
        self.prefix = prefix
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.settimeout(REQUEST_TIMEOUT)

        # Receiver state
        self.expected_seq = 0
        self.received_data = {}
        self.max_seq_received = -1

        print(f"Client connecting to {server_ip}:{server_port} (prefix: {prefix})")

    def create_ack_packet(self, ack_num, sack_blocks=None):
        """Create ACK packet with optional SACK blocks"""
        header = struct.pack('!I', ack_num)

        # Encode SACK blocks
        reserved = bytearray(16)
        if sack_blocks and len(sack_blocks) > 0:
            reserved[0] = min(len(sack_blocks), 1)
            offset = 1
            for start, end in sack_blocks[:1]:
                if offset + 8 <= 16:
                    reserved[offset:offset+4] = struct.pack('!I', start)
                    reserved[offset+4:offset+8] = struct.pack('!I', end)
                    offset += 8

        return header + bytes(reserved)

    def parse_packet(self, packet):
        """Parse received packet"""
        if len(packet) < HEADER_SIZE:
            return None, None

        seq_num = struct.unpack('!I', packet[:4])[0]
        data = packet[HEADER_SIZE:]

        return seq_num, data

    def send_request(self):
        """Send file request to server with retries"""
        request = b'R'

        for attempt in range(MAX_REQUEST_RETRIES):
            try:
                self.socket.sendto(request, (self.server_ip, self.server_port))
                print(f"Request sent (attempt {attempt + 1})")

                # Wait for first data packet
                ready = select.select([self.socket], [], [], REQUEST_TIMEOUT)
                if ready[0]:
                    return True

            except socket.timeout:
                print(f"Request timeout (attempt {attempt + 1})")
                continue

        print("Failed to connect to server after maximum retries")
        return False

    def get_sack_blocks(self):
        """Generate SACK blocks from received out-of-order data"""
        if not self.received_data:
            return []

        sorted_seqs = sorted([seq for seq in self.received_data.keys() if seq > self.expected_seq])

        if not sorted_seqs:
            return []

        blocks = []
        block_start = sorted_seqs[0]
        block_end = block_start + len(self.received_data[block_start])

        for seq in sorted_seqs[1:]:
            data_len = len(self.received_data[seq])
            if seq == block_end:
                block_end = seq + data_len
            else:
                blocks.append((block_start, block_end))
                block_start = seq
                block_end = seq + data_len

        blocks.append((block_start, block_end))
        return blocks[:4]

    def receive_file(self, output_file):
        """Receive file from server"""
        print(f"Receiving file to {output_file}")

        # Send initial request
        if not self.send_request():
            return False

        # Remove timeout for data reception
        self.socket.settimeout(None)

        start_time = time.time()
        last_print = start_time
        packets_received = 0
        last_ack_time = time.time()
        total_bytes = 0

        with open(output_file, 'wb') as f:
            while True:
                try:
                    # Receive packet
                    packet, addr = self.socket.recvfrom(MAX_PAYLOAD)
                    seq_num, data = self.parse_packet(packet)

                    if seq_num is None:
                        continue

                    # Check for EOF
                    if data == b"EOF":
                        print("EOF received")
                        break

                    packets_received += 1
                    current_time = time.time()

                    if seq_num > self.max_seq_received:
                        self.max_seq_received = seq_num

                    # Handle received packet
                    if seq_num == self.expected_seq:
                        # In-order packet
                        f.write(data)
                        total_bytes += len(data)
                        self.expected_seq += len(data)

                        # Check buffered packets
                        while self.expected_seq in self.received_data:
                            buffered_data = self.received_data.pop(self.expected_seq)
                            f.write(buffered_data)
                            total_bytes += len(buffered_data)
                            self.expected_seq += len(buffered_data)

                        # Send ACK with SACK
                        sack_blocks = self.get_sack_blocks()
                        ack_packet = self.create_ack_packet(self.expected_seq, sack_blocks)
                        self.socket.sendto(ack_packet, (self.server_ip, self.server_port))
                        last_ack_time = current_time

                    elif seq_num > self.expected_seq:
                        # Out-of-order packet
                        if seq_num not in self.received_data:
                            self.received_data[seq_num] = data

                            # Send duplicate ACK with SACK
                            sack_blocks = self.get_sack_blocks()
                            ack_packet = self.create_ack_packet(self.expected_seq, sack_blocks)
                            self.socket.sendto(ack_packet, (self.server_ip, self.server_port))
                            last_ack_time = current_time

                    # Periodic ACK
                    if current_time - last_ack_time > 0.5:
                        sack_blocks = self.get_sack_blocks()
                        ack_packet = self.create_ack_packet(self.expected_seq, sack_blocks)
                        self.socket.sendto(ack_packet, (self.server_ip, self.server_port))
                        last_ack_time = current_time

                    # Progress report
                    if current_time - last_print >= 2.0:
                        elapsed = current_time - start_time
                        rate = (total_bytes / 1024) / elapsed if elapsed > 0 else 0
                        buffer_size = len(self.received_data)
                        print(f"Received: {total_bytes} bytes ({packets_received} packets), "
                              f"Rate: {rate:.1f} KB/s, Buffer: {buffer_size} packets")
                        last_print = current_time

                except socket.timeout:
                    print("Socket timeout")
                    break
                except Exception as e:
                    print(f"Error receiving packet: {e}")
                    continue

        duration = time.time() - start_time
        rate = (total_bytes / 1024) / duration if duration > 0 else 0
        throughput_mbps = (total_bytes * 8) / (duration * 1e6) if duration > 0 else 0

        print(f"\nFile reception complete!")
        print(f"Total bytes: {total_bytes}, Packets: {packets_received}")
        print(f"Time: {duration:.2f}s, Average rate: {rate:.1f} KB/s ({throughput_mbps:.2f} Mbps)")

        return True

    def run(self):
        """Main client logic"""
        output_file = f"{self.prefix}received_data.txt"
        success = self.receive_file(output_file)
        self.socket.close()

        if success:
            print("Client finished successfully")
        else:
            print("Client finished with errors")

        return success


def main():
    if len(sys.argv) != 4:
        print("Usage: python3 p2_client.py <SERVER_IP> <SERVER_PORT> <PREF_FILENAME>")
        sys.exit(1)

    server_ip = sys.argv[1]
    server_port = int(sys.argv[2])
    prefix = sys.argv[3]

    client = ReliableUDPClient(server_ip, server_port, prefix)
    success = client.run()

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
