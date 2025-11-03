#!/usr/bin/env python3
"""
Part 1 Client: Reliable UDP File Transfer
Implements receiver with cumulative ACKs and SACK support
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
REQUEST_TIMEOUT = 2.0  # Timeout for initial request
MAX_REQUEST_RETRIES = 5
ACK_DELAY = 0.001  # Small delay before sending ACK to batch them


class ReliableUDPClient:
    def __init__(self, server_ip, server_port):
        self.server_ip = server_ip
        self.server_port = server_port
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.settimeout(REQUEST_TIMEOUT)

        # Receiver state
        self.expected_seq = 0  # Next expected in-order byte
        self.received_data = {}  # {seq_num: data} - out-of-order packets
        self.max_seq_received = -1  # Highest sequence number received

        print(f"Client connecting to {server_ip}:{server_port}")

    def create_ack_packet(self, ack_num, sack_blocks=None):
        """
        Create ACK packet
        - ACK number: 4 bytes (cumulative ACK)
        - SACK blocks: up to 16 bytes for selective acknowledgments
        """
        header = struct.pack('!I', ack_num)

        # Encode SACK blocks in reserved 16 bytes
        reserved = bytearray(16)
        if sack_blocks and len(sack_blocks) > 0:
            # Format: num_blocks (1 byte) + blocks (each 8 bytes: 4 start + 4 end)
            reserved[0] = min(len(sack_blocks), 1)  # Max 1 SACK block
            offset = 1
            for start, end in sack_blocks[:1]:
                if offset + 8 <= 16:
                    reserved[offset:offset+4] = struct.pack('!I', start)
                    reserved[offset+4:offset+8] = struct.pack('!I', end)
                    offset += 8

        return header + bytes(reserved)

    def parse_packet(self, packet):
        """
        Parse received packet
        Returns: (seq_num, data)
        """
        if len(packet) < HEADER_SIZE:
            return None, None

        seq_num = struct.unpack('!I', packet[:4])[0]
        data = packet[HEADER_SIZE:]

        return seq_num, data

    def send_request(self):
        """Send file request to server with retries"""
        request = b'R'  # Simple 1-byte request

        for attempt in range(MAX_REQUEST_RETRIES):
            try:
                self.socket.sendto(request, (self.server_ip, self.server_port))
                print(f"Request sent (attempt {attempt + 1})")

                # Wait for first data packet as confirmation
                ready = select.select([self.socket], [], [], REQUEST_TIMEOUT)
                if ready[0]:
                    return True

            except socket.timeout:
                print(f"Request timeout (attempt {attempt + 1})")
                continue

        print("Failed to connect to server after maximum retries")
        return False

    def get_sack_blocks(self):
        """
        Generate SACK blocks from received out-of-order data
        Returns list of (start, end) tuples for contiguous received blocks
        """
        if not self.received_data:
            return []

        # Find contiguous blocks beyond expected_seq
        sorted_seqs = sorted([seq for seq in self.received_data.keys() if seq > self.expected_seq])

        if not sorted_seqs:
            return []

        blocks = []
        block_start = sorted_seqs[0]
        block_end = block_start + len(self.received_data[block_start])

        for seq in sorted_seqs[1:]:
            data_len = len(self.received_data[seq])
            if seq == block_end:
                # Contiguous - extend current block
                block_end = seq + data_len
            else:
                # Gap - save current block and start new one
                blocks.append((block_start, block_end))
                block_start = seq
                block_end = seq + data_len

        # Add final block
        blocks.append((block_start, block_end))

        return blocks[:4]  # Return max 4 SACK blocks

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

                    # Update max sequence received
                    if seq_num > self.max_seq_received:
                        self.max_seq_received = seq_num

                    # Handle received packet
                    if seq_num == self.expected_seq:
                        # In-order packet - write immediately
                        f.write(data)
                        total_bytes += len(data)
                        self.expected_seq += len(data)

                        # Check if we have buffered packets that are now in-order
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
                        # Out-of-order packet - buffer it
                        if seq_num not in self.received_data:
                            self.received_data[seq_num] = data

                            # Send duplicate ACK immediately to trigger fast retransmit
                            sack_blocks = self.get_sack_blocks()
                            ack_packet = self.create_ack_packet(self.expected_seq, sack_blocks)
                            self.socket.sendto(ack_packet, (self.server_ip, self.server_port))
                            last_ack_time = current_time

                    # else: seq_num < expected_seq - duplicate, ignore

                    # Periodic ACK even if no new in-order data (keepalive)
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
        print(f"\nFile reception complete!")
        print(f"Total bytes: {total_bytes}, Packets: {packets_received}")
        print(f"Time: {duration:.2f}s, Average rate: {rate:.1f} KB/s")

        return True

    def run(self, output_file="received_data.txt"):
        """Main client logic"""
        success = self.receive_file(output_file)
        self.socket.close()

        if success:
            print("Client finished successfully")
        else:
            print("Client finished with errors")

        return success


def main():
    if len(sys.argv) != 3:
        print("Usage: python3 p1_client.py <SERVER_IP> <SERVER_PORT>")
        sys.exit(1)

    server_ip = sys.argv[1]
    server_port = int(sys.argv[2])

    client = ReliableUDPClient(server_ip, server_port)
    success = client.run()

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
