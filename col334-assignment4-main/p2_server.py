#!/usr/bin/env python3
"""
Part 2 Server: Reliable UDP File Transfer with Congestion Control
Implements TCP Reno-style congestion control with slow start, congestion avoidance, and fast recovery
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
MSS = MAX_PAYLOAD  # Maximum Segment Size
INITIAL_RTO = 1.0
MIN_RTO = 0.2
MAX_RTO = 3.0
ALPHA = 0.125
BETA = 0.25
DUP_ACK_THRESHOLD = 3

# Congestion control states
STATE_SLOW_START = "SLOW_START"
STATE_CONGESTION_AVOIDANCE = "CONGESTION_AVOIDANCE"
STATE_FAST_RECOVERY = "FAST_RECOVERY"


class CongestionControlServer:
    def __init__(self, server_ip, server_port):
        self.server_ip = server_ip
        self.server_port = server_port
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind((server_ip, server_port))

        # RTT estimation
        self.estimated_rtt = None
        self.dev_rtt = 0
        self.rto = INITIAL_RTO

        # Sliding window state
        self.base = 0
        self.next_seq = 0
        self.packets = {}  # {seq_num: (data, timestamp)}
        self.dup_ack_count = 0
        self.last_ack = 0

        # Congestion control variables
        self.cwnd = MSS  # Start with 1 MSS
        self.ssthresh = 64000  # Initial threshold (arbitrary large value)
        self.state = STATE_SLOW_START

        # For cwnd increase tracking
        self.acked_bytes_this_rtt = 0
        self.bytes_acked_since_last_increase = 0

        # Statistics
        self.total_retransmissions = 0
        self.fast_retransmits = 0
        self.timeout_retransmits = 0

        print(f"Server started on {server_ip}:{server_port}")
        print(f"Initial cwnd={self.cwnd} bytes ({self.cwnd/MSS:.1f} MSS), ssthresh={self.ssthresh}")

    def create_packet(self, seq_num, data):
        """Create a packet with header (20 bytes) + data"""
        header = struct.pack('!I', seq_num)
        reserved = b'\x00' * 16
        packet = header + reserved + data
        return packet

    def parse_ack(self, packet):
        """Parse ACK packet from client"""
        if len(packet) < 4:
            return None, []

        ack_num = struct.unpack('!I', packet[:4])[0]

        # Parse SACK blocks
        sack_blocks = []
        if len(packet) >= 20:
            reserved = packet[4:20]
            try:
                num_blocks = reserved[0]
                offset = 1
                for i in range(min(num_blocks, 1)):
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

    def increase_cwnd(self, acked_bytes):
        """
        Increase congestion window based on current state
        - Slow start: increase by acked_bytes (exponential)
        - Congestion avoidance: increase by MSS * MSS / cwnd per ACK (linear)
        """
        if self.state == STATE_SLOW_START:
            # Exponential increase: cwnd += acked_bytes
            self.cwnd += acked_bytes

            # Check if we should transition to congestion avoidance
            if self.cwnd >= self.ssthresh:
                self.state = STATE_CONGESTION_AVOIDANCE
                print(f"’ CONGESTION_AVOIDANCE (cwnd={self.cwnd:.0f}, ssthresh={self.ssthresh:.0f})")

        elif self.state == STATE_CONGESTION_AVOIDANCE:
            # Linear increase: cwnd += MSS * (MSS / cwnd) per ACK
            # This adds up to MSS per RTT
            increment = (MSS * acked_bytes) / self.cwnd
            self.cwnd += increment

        elif self.state == STATE_FAST_RECOVERY:
            # In fast recovery, inflate cwnd for each duplicate ACK
            # This is handled in the duplicate ACK section
            pass

    def handle_timeout(self):
        """Handle timeout event - severe congestion signal"""
        self.timeout_retransmits += 1

        # Set ssthresh to half of current cwnd
        self.ssthresh = max(self.cwnd / 2, 2 * MSS)

        # Reset cwnd to 1 MSS (slow start)
        self.cwnd = MSS

        # Back to slow start
        self.state = STATE_SLOW_START

        # Exponential backoff
        self.rto = min(self.rto * 1.5, MAX_RTO)

        print(f"TIMEOUT! ’ SLOW_START (cwnd={self.cwnd:.0f}, ssthresh={self.ssthresh:.0f}, RTO={self.rto:.3f})")

    def handle_duplicate_ack(self):
        """Handle duplicate ACK - possible packet loss"""
        self.dup_ack_count += 1

        if self.dup_ack_count == DUP_ACK_THRESHOLD:
            # Fast retransmit and fast recovery
            self.fast_retransmits += 1

            # Set ssthresh to half of current cwnd
            self.ssthresh = max(self.cwnd / 2, 2 * MSS)

            # Set cwnd to ssthresh + 3 * MSS (for the 3 dup ACKs)
            self.cwnd = self.ssthresh + 3 * MSS

            # Enter fast recovery
            self.state = STATE_FAST_RECOVERY

            print(f"FAST_RETRANSMIT ’ FAST_RECOVERY (cwnd={self.cwnd:.0f}, ssthresh={self.ssthresh:.0f})")

            return True  # Signal to retransmit

        elif self.state == STATE_FAST_RECOVERY:
            # Inflate cwnd for each additional duplicate ACK
            self.cwnd += MSS

        return False

    def handle_new_ack(self, acked_bytes):
        """Handle new cumulative ACK"""
        # Update congestion window
        self.increase_cwnd(acked_bytes)

        # If we were in fast recovery, exit to congestion avoidance
        if self.state == STATE_FAST_RECOVERY:
            self.cwnd = self.ssthresh
            self.state = STATE_CONGESTION_AVOIDANCE
            print(f"NEW_ACK ’ CONGESTION_AVOIDANCE (cwnd={self.cwnd:.0f}, ssthresh={self.ssthresh:.0f})")

        # Reset duplicate ACK counter
        self.dup_ack_count = 0

    def send_file(self, client_addr, file_path):
        """Send file to client using congestion control"""
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

        # Reset congestion control
        self.cwnd = MSS
        self.ssthresh = 64000
        self.state = STATE_SLOW_START

        start_time = time.time()
        last_print = start_time
        last_cwnd_print = start_time

        while self.base < total_bytes:
            # Send new packets within congestion window
            while self.next_seq < total_bytes and (self.next_seq - self.base) < self.cwnd:
                # Extract data chunk
                data_start = self.next_seq
                data_end = min(self.next_seq + DATA_SIZE, total_bytes)
                data = file_data[data_start:data_end]

                # Create and send packet
                packet = self.create_packet(self.next_seq, data)
                self.socket.sendto(packet, client_addr)

                # Store packet and timestamp
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

                    # Handle SACK blocks
                    for start, end in sack_blocks:
                        seqs_to_remove = [seq for seq in self.packets.keys()
                                         if start <= seq < end]
                        for seq in seqs_to_remove:
                            if seq >= ack_num:
                                del self.packets[seq]

                    # Process cumulative ACK
                    if ack_num > self.base:
                        # New ACK - data acknowledged
                        acked_bytes = ack_num - self.base

                        # Remove acknowledged packets
                        seqs_to_remove = [seq for seq in self.packets.keys() if seq < ack_num]
                        for seq in seqs_to_remove:
                            del self.packets[seq]

                        # Update congestion control
                        self.handle_new_ack(acked_bytes)

                        self.base = ack_num
                        self.last_ack = ack_num

                    elif ack_num == self.last_ack and ack_num < total_bytes:
                        # Duplicate ACK
                        should_retransmit = self.handle_duplicate_ack()

                        if should_retransmit and self.base in self.packets:
                            # Fast retransmit
                            data, _ = self.packets[self.base]
                            packet = self.create_packet(self.base, data)
                            self.socket.sendto(packet, client_addr)
                            self.packets[self.base] = (data, time.time())
                            self.total_retransmissions += 1

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
                    self.total_retransmissions += 1

                    # Handle timeout for congestion control
                    self.handle_timeout()

            # Progress report every 2 seconds
            current_time = time.time()
            if current_time - last_print >= 2.0:
                progress = (self.base / total_bytes) * 100
                elapsed = current_time - start_time
                rate = (self.base / 1024) / elapsed if elapsed > 0 else 0
                print(f"Progress: {progress:.1f}% ({self.base}/{total_bytes}), "
                      f"Rate: {rate:.1f} KB/s, cwnd: {self.cwnd:.0f} ({self.cwnd/MSS:.1f} MSS), "
                      f"State: {self.state}, RTO: {self.rto:.3f}s")
                last_print = current_time

        # Send EOF marker
        eof_packet = self.create_packet(total_bytes, b"EOF")
        for _ in range(5):
            self.socket.sendto(eof_packet, client_addr)
            time.sleep(0.1)

        duration = time.time() - start_time
        rate = (total_bytes / 1024) / duration
        throughput_mbps = (total_bytes * 8) / (duration * 1e6)

        print(f"\nFile transfer complete!")
        print(f"Time: {duration:.2f}s, Average rate: {rate:.1f} KB/s ({throughput_mbps:.2f} Mbps)")
        print(f"Final cwnd: {self.cwnd:.0f} bytes ({self.cwnd/MSS:.1f} MSS)")
        print(f"Final ssthresh: {self.ssthresh:.0f} bytes")
        print(f"Total retransmissions: {self.total_retransmissions}")
        print(f"  - Fast retransmits: {self.fast_retransmits}")
        print(f"  - Timeout retransmits: {self.timeout_retransmits}")

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
    if len(sys.argv) != 3:
        print("Usage: python3 p2_server.py <SERVER_IP> <SERVER_PORT>")
        sys.exit(1)

    server_ip = sys.argv[1]
    server_port = int(sys.argv[2])

    server = CongestionControlServer(server_ip, server_port)
    server.run()


if __name__ == "__main__":
    main()
