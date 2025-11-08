#!/usr/bin/env python3
"""
Part 1: Optimized Reliable UDP Client
Fast packet processing and efficient ACK generation
"""

import socket
import sys
import time
import struct
import os
import hashlib
from collections import OrderedDict

# Constants
MAX_PAYLOAD = 1200
HEADER_SIZE = 20
MAX_DATA_SIZE = MAX_PAYLOAD - HEADER_SIZE
REQUEST_TIMEOUT = 2.0
MAX_REQUEST_RETRIES = 5
ACK_DELAY = 0.0001  # Send ACKs extremely quickly (100μs) for maximum responsiveness
ACK_EVERY_N_PACKETS = 1  # Send ACK after every single packet for fastest feedback

class ReliableUDPClient:
    def __init__(self, server_ip, server_port):
        self.server_ip = server_ip
        self.server_port = server_port
        self.server_addr = (server_ip, server_port)
        
        # Create socket with larger buffers
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024*1024)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2*1024*1024)
        self.socket.setblocking(False)
        
        # Bind to any available port
        try:
            self.socket.bind(('', 0))
        except:
            pass
        
        # Receive buffer for out-of-order packets
        self.receive_buffer = {}
        self.next_expected = 0
        self.highest_received = 0
        
        # File handling
        self.output_file = 'received_data.txt'
        self.file_handle = None
        self.file_complete = False
        self.file_size = 0
        
        # Statistics
        self.packets_received = 0
        self.duplicate_packets = 0
        self.out_of_order_packets = 0
        
        # ACK optimization
        self.last_ack_sent = 0
        self.last_ack_time = 0
        
    def send_request(self):
        """Send initial file request to server"""
        request = b'\x01'
        
        for retry in range(MAX_REQUEST_RETRIES):
            self.socket.sendto(request, self.server_addr)
            print(f"Sent file request (attempt {retry + 1}/{MAX_REQUEST_RETRIES})")
            
            # Wait for first data packet
            start_time = time.time()
            while time.time() - start_time < REQUEST_TIMEOUT:
                try:
                    packet, addr = self.socket.recvfrom(MAX_PAYLOAD)
                    if addr[0] == self.server_ip:
                        # Save for processing
                        self.pending_packet = packet
                        return True
                except BlockingIOError:
                    time.sleep(0.001)
                    
        print("Failed to connect to server")
        return False
        
    def parse_packet(self, packet):
        """Fast packet parsing"""
        if len(packet) < HEADER_SIZE:
            return None, None
            
        seq_num = struct.unpack('!I', packet[:4])[0]
        data = packet[HEADER_SIZE:]
        
        return seq_num, data
        
    def create_ack(self):
        """Create ACK with optimized SACK blocks"""
        # Basic cumulative ACK
        ack_packet = struct.pack('!I', self.next_expected)

        # Add up to 2 SACK blocks
        sack_data = b''

        if self.receive_buffer:
            # Find contiguous blocks more efficiently
            sorted_seqs = sorted(self.receive_buffer.keys())
            blocks = []

            if sorted_seqs:
                block_start = sorted_seqs[0]
                block_end = block_start + len(self.receive_buffer[block_start])

                for seq in sorted_seqs[1:]:
                    # Check if this packet extends current block or starts new one
                    if seq <= block_end:
                        block_end = max(block_end, seq + len(self.receive_buffer[seq]))
                    else:
                        # Only add blocks that are beyond next_expected
                        if block_start >= self.next_expected:
                            blocks.append((block_start, block_end))
                        block_start = seq
                        block_end = seq + len(self.receive_buffer[seq])

                # Don't forget the last block
                if block_start >= self.next_expected:
                    blocks.append((block_start, block_end))

            # Add first 2 SACK blocks (most recent holes are most important)
            for i, (start, end) in enumerate(blocks[:2]):
                sack_data += struct.pack('!II', start, end)

        # Pad to 20 bytes
        ack_packet += sack_data + b'\x00' * (16 - len(sack_data))

        return ack_packet
        
    def send_ack(self, force=False):
        """Send ACK with optimized strategy for jitter"""
        current_time = time.time()

        # More aggressive ACK sending:
        # - Always send if forced
        # - Send if cumulative ACK advanced (important for progress)
        # - Send if enough time passed (helps in high jitter)
        # - Send if we have out-of-order packets (triggers SACK)
        should_send = (force or
                      self.next_expected != self.last_ack_sent or
                      current_time - self.last_ack_time > ACK_DELAY or
                      len(self.receive_buffer) > 0)

        if should_send:
            ack_packet = self.create_ack()
            try:
                self.socket.sendto(ack_packet, self.server_addr)
                self.last_ack_sent = self.next_expected
                self.last_ack_time = current_time
            except BlockingIOError:
                pass
                
    def write_buffered_data(self):
        """Write in-order data from buffer to file"""
        written = False
        
        while self.next_expected in self.receive_buffer:
            data = self.receive_buffer[self.next_expected]
            
            # Check for EOF
            if data == b'EOF':
                self.file_complete = True
                del self.receive_buffer[self.next_expected]
                return True
                
            # Write data
            self.file_handle.write(data)
            self.file_size += len(data)
            
            # Update next expected
            data_len = len(data)
            del self.receive_buffer[self.next_expected]
            self.next_expected += data_len
            written = True
            
        return written
        
    def process_packet(self, packet):
        """Process received packet efficiently"""
        seq_num, data = self.parse_packet(packet)
        
        if seq_num is None:
            return False
            
        # Track highest received
        self.highest_received = max(self.highest_received, seq_num)
        
        # Duplicate packet - just ACK it
        if seq_num < self.next_expected:
            self.duplicate_packets += 1
            return True  # Still send ACK
            
        # Expected packet
        if seq_num == self.next_expected:
            self.packets_received += 1
            
            # Check for EOF
            if data == b'EOF':
                self.file_complete = True
                return True
                
            # Write data immediately
            self.file_handle.write(data)
            self.file_size += len(data)
            self.next_expected += len(data)
            
            # Check if we can write buffered data
            self.write_buffered_data()
            
            return True
            
        # Out-of-order packet - buffer it
        elif seq_num > self.next_expected:
            if seq_num not in self.receive_buffer:
                self.out_of_order_packets += 1
                self.packets_received += 1
                self.receive_buffer[seq_num] = data
                
            return True
            
        return False
        
    def receive_file(self):
        """Main receiving loop"""
        print(f"Connecting to server at {self.server_ip}:{self.server_port}")
        
        # Send initial request
        if not self.send_request():
            return False
            
        # Open output file
        self.file_handle = open(self.output_file, 'wb')
        
        print("Receiving file...")
        start_time = time.time()
        last_packet_time = time.time()
        packets_since_ack = 0
        last_progress = -1
        
        # Process pending packet from connection
        if hasattr(self, 'pending_packet'):
            if self.process_packet(self.pending_packet):
                self.send_ack(force=True)
            last_packet_time = time.time()
            
        # Main receiving loop
        consecutive_timeouts = 0
        
        while not self.file_complete:
            try:
                # Try to receive multiple packets at once
                packets_received = 0
                max_batch = 200  # Larger batch for better throughput

                while packets_received < max_batch and not self.file_complete:
                    try:
                        packet, addr = self.socket.recvfrom(MAX_PAYLOAD)
                        
                        if addr[0] != self.server_ip:
                            continue
                            
                        last_packet_time = time.time()
                        consecutive_timeouts = 0
                        
                        if self.process_packet(packet):
                            packets_since_ack += 1
                            packets_received += 1
                            
                    except BlockingIOError:
                        break
                        
                # Send ACK after processing batch or every N packets
                if packets_since_ack >= ACK_EVERY_N_PACKETS:
                    self.send_ack()
                    packets_since_ack = 0
                elif packets_since_ack > 0 and packets_received == 0:
                    # Also send if we're done receiving this batch
                    self.send_ack()
                    packets_since_ack = 0
                    
                # Check for timeout with adaptive threshold
                current_time = time.time()
                # Use shorter timeout (500ms) for better responsiveness
                timeout_threshold = 0.5
                if current_time - last_packet_time > timeout_threshold:
                    consecutive_timeouts += 1

                    # Send duplicate ACK to trigger retransmission
                    self.send_ack(force=True)

                    if consecutive_timeouts > 8:  # Increased from 5 to allow more retries
                        print("Connection timeout - checking for completion")
                        
                        # Wait a bit more for EOF
                        time.sleep(0.5)
                        try:
                            packet, addr = self.socket.recvfrom(MAX_PAYLOAD)
                            seq_num, data = self.parse_packet(packet)
                            if data == b'EOF':
                                self.file_complete = True
                        except:
                            pass
                            
                        if not self.file_complete:
                            print("Transfer incomplete - timeout")
                            break
                            
                    last_packet_time = current_time
                    
                # Progress indicator
                if self.highest_received > 0:
                    # Estimate progress based on typical file size
                    progress = min(100, int(self.file_size * 100 / (6 * 1024 * 1024)))
                    if progress >= last_progress + 10:
                        print(f"Progress: ~{progress}%")
                        last_progress = progress
                        
                # No sleep - maximum responsiveness (CPU will spin but transfer is fast)
                # Only sleep if we've been idle for a while
                if packets_received == 0 and consecutive_timeouts > 2:
                    time.sleep(0.0001)  # 100μs only when idle
                    
            except KeyboardInterrupt:
                print("\nClient interrupted")
                break
                
        # Close file
        if self.file_handle:
            self.file_handle.close()
            
        # Print statistics
        if self.file_complete:
            end_time = time.time()
            duration = end_time - start_time
            file_size = os.path.getsize(self.output_file)
            throughput = (file_size * 8) / (duration * 1e6) if duration > 0 else 0
            
            print(f"\n=== Transfer Complete ===")
            print(f"File saved as: {self.output_file}")
            print(f"File size: {file_size} bytes")
            print(f"Duration: {duration:.2f} seconds")
            print(f"Throughput: {throughput:.2f} Mbps")
            print(f"Packets received: {self.packets_received}")
            print(f"Duplicate packets: {self.duplicate_packets}")
            print(f"Out-of-order packets: {self.out_of_order_packets}")
            
            # Compute MD5 hash
            with open(self.output_file, 'rb') as f:
                file_hash = hashlib.md5(f.read()).hexdigest()
            print(f"MD5 hash: {file_hash}")
            
            return True
        else:
            print("File transfer incomplete")
            return False

def main():
    if len(sys.argv) != 3:
        print("Usage: python3 p1_client.py <SERVER_IP> <SERVER_PORT>")
        sys.exit(1)
        
    server_ip = sys.argv[1]
    server_port = int(sys.argv[2])
    
    client = ReliableUDPClient(server_ip, server_port)
    
    try:
        success = client.receive_file()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\nClient interrupted")
        sys.exit(1)
    except Exception as e:
        print(f"Client error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()