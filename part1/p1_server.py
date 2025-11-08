import socket
import sys
import time
import struct
import os
from collections import defaultdict
import select


MAX_PAYLOAD = 1200
HEADER_SIZE = 20
MAX_DATA_SIZE = MAX_PAYLOAD - HEADER_SIZE


INITIAL_RTO = 0.06     
MIN_RTO = 0.02        
MAX_RTO = 0.5       
ALPHA = 0.125         
BETA = 0.25            
DUP_ACK_THRESHOLD = 3

class ReliableUDPServer:
    def __init__(self, server_ip, server_port, sws):
        self.server_ip = server_ip
        self.server_port = server_port
        self.sws = sws 

       
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 2*1024*1024)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2*1024*1024)

        
        
        try:
            self.socket.bind((server_ip, server_port))
            print(f"Bound to {server_ip}:{server_port}")
        except OSError as e:
            if e.errno == 99:
                print(f"Warning: Cannot bind to {server_ip}:{server_port}")
                print(f"Binding to 0.0.0.0:{server_port} (all interfaces)")
                self.socket.bind(('0.0.0.0', server_port))
                self.server_ip = '0.0.0.0'
            else:
                raise

        self.socket.setblocking(False)

        # RTT estimation
        self.srtt = None
        self.rttvar = None
        self.rto = INITIAL_RTO
        self.min_rtt = float('inf')

        
        self.base = 0
        self.next_seq = 0
        self.window = {}  # seq -> (data, send_time, retrans_count)
        self.dup_ack_count = defaultdict(int)
        self.highest_sacked = 0
        
        # File data
        self.file_data = b''
        self.file_size = 0
        self.client_addr = None
        
        # Performance tracking
        self.packets_sent = 0
        self.packets_retransmitted = 0
        self.last_ack_time = 0
        
        # Packet cache
        self.packet_cache = {}
        
    def load_file(self, filename='data.txt'):
        """Load file and prepare for transmission"""
        try:
            with open(filename, 'rb') as f:
                self.file_data = f.read()
            self.file_size = len(self.file_data)
            print(f"Loaded file '{filename}': {self.file_size} bytes")
            
            # Pre-create all packets
            print("Pre-creating packets...")
            seq = 0
            while seq < self.file_size:
                size = min(MAX_DATA_SIZE, self.file_size - seq)
                data = self.file_data[seq:seq + size]
                self.packet_cache[seq] = struct.pack('!I', seq) + b'\x00' * 16 + data
                seq += size
            print(f"Created {len(self.packet_cache)} packets")
            
            return True
        except FileNotFoundError:
            print(f"Error: File '{filename}' not found")
            return False
            
    def parse_ack(self, packet):
        """Fast ACK parsing"""
        if len(packet) < 4:
            return None, []
            
        ack_num = struct.unpack('!I', packet[:4])[0]
        sack_blocks = []
        
        # Quick SACK parsing
        if len(packet) >= 12:
            try:
                start = struct.unpack('!I', packet[4:8])[0]
                end = struct.unpack('!I', packet[8:12])[0]
                if start > 0 and end > start:
                    sack_blocks.append((start, end))
                    
                if len(packet) >= 20:
                    start = struct.unpack('!I', packet[12:16])[0]
                    end = struct.unpack('!I', packet[16:20])[0]
                    if start > 0 and end > start:
                        sack_blocks.append((start, end))
            except:
                pass
                
        return ack_num, sack_blocks
        
    def update_rtt(self, sample_rtt):
        """Update RTT estimates - aggressive for speed"""
        self.min_rtt = min(self.min_rtt, sample_rtt)

        if self.srtt is None:
            self.srtt = sample_rtt
            self.rttvar = sample_rtt / 2
        else:
            # Standard exponential weighted moving average
            self.rttvar = (1 - BETA) * self.rttvar + BETA * abs(self.srtt - sample_rtt)
            self.srtt = (1 - ALPHA) * self.srtt + ALPHA * sample_rtt

        # Aggressive RTO calculation - use standard 4x but with low minimums
        # This balances responsiveness with jitter tolerance
        self.rto = self.srtt + max(4 * self.rttvar, 0.01)  # At least 10ms variance component
        self.rto = max(MIN_RTO, min(self.rto, MAX_RTO))
        
    def send_window(self):
        """Send as many packets as window allows (using fixed SWS)"""
        # Calculate bytes in flight
        bytes_in_flight = len(self.window) * MAX_DATA_SIZE

        
        available = self.sws - bytes_in_flight

        packets_sent = 0
        max_burst = 200  

        while available >= MAX_DATA_SIZE and self.next_seq < self.file_size and packets_sent < max_burst:
            seq = self.next_seq
            
            
            if seq in self.packet_cache:
                packet = self.packet_cache[seq]
                data_size = min(MAX_DATA_SIZE, self.file_size - seq)
                
                # Send packet
                try:
                    self.socket.sendto(packet, self.client_addr)
                    self.packets_sent += 1
                    packets_sent += 1
                    
                    # Track in window
                    self.window[seq] = (data_size, time.time(), 0)
                    
                    self.next_seq += data_size
                    available -= data_size
                except BlockingIOError:
                    break  # Socket buffer full
            else:
                break
                
    def handle_ack(self, ack_num, sack_blocks):
        """Handle incoming ACK (NO congestion control in Part 1!)"""
        current_time = time.time()
        self.last_ack_time = current_time

        if ack_num > self.base:
            # New ACK - slide window forward
            old_base = self.base
            self.base = ack_num
            self.dup_ack_count.clear()

            # Update RTT from acknowledged packets
            for seq in list(self.window.keys()):
                if seq < self.base:
                    if seq in self.window and self.window[seq][2] == 0:
                        rtt = current_time - self.window[seq][1]
                        if rtt > 0:
                            self.update_rtt(rtt)
                    del self.window[seq]

           
            if sack_blocks:
                for start, end in sack_blocks:
                    self.highest_sacked = max(self.highest_sacked, end)
                    
                    for seq in list(self.window.keys()):
                        if start <= seq < end:
                            del self.window[seq]

        elif ack_num == self.base:
           
            self.dup_ack_count[ack_num] += 1

            if self.dup_ack_count[ack_num] == DUP_ACK_THRESHOLD:
                
                self.fast_retransmit()
                
    def fast_retransmit(self):
        """Perform fast retransmit (NO congestion control in Part 1!)"""
        if self.base in self.window and self.base in self.packet_cache:
            # Retransmit packet
            self.socket.sendto(self.packet_cache[self.base], self.client_addr)
            self.packets_retransmitted += 1

            # Update window entry
            size, _, count = self.window[self.base]
            self.window[self.base] = (size, time.time(), count + 1)

           
            
    def check_timeouts(self):
        """Check for packet timeouts - aggressive retransmission"""
        current_time = time.time()
        retransmitted = 0
        max_retrans_per_check = 5  

        for seq in sorted(self.window.keys()):
            if seq >= self.base and retransmitted < max_retrans_per_check:
                size, send_time, retrans_count = self.window[seq]

               
                if retrans_count == 0:
                    timeout = self.rto
                elif retrans_count == 1:
                    timeout = self.rto * 1.3  
                else:
                    timeout = self.rto * (1.5 ** min(retrans_count, 4))  

                if current_time - send_time > timeout:
                    if seq in self.packet_cache:
                        try:
                            self.socket.sendto(self.packet_cache[seq], self.client_addr)
                            self.window[seq] = (size, current_time, retrans_count + 1)
                            self.packets_retransmitted += 1
                            retransmitted += 1
                        except BlockingIOError:
                            break  # Socket buffer full
                            
    def send_eof(self):
        """Send EOF packet multiple times"""
        eof_packet = struct.pack('!I', self.file_size) + b'\x00' * 16 + b'EOF'
        for _ in range(10):
            try:
                self.socket.sendto(eof_packet, self.client_addr)
            except:
                pass
            time.sleep(0.02)
            
    def handle_client(self):
        """Main server loop"""
        print(f"Server listening on {self.server_ip}:{self.server_port}")
        print(f"Sender Window Size: {self.sws} bytes")
        
        # Wait for client request
        print("Waiting for client...")
        while self.client_addr is None:
            try:
                data, addr = self.socket.recvfrom(MAX_PAYLOAD)
                if data:
                    self.client_addr = addr
                    print(f"Client connected from {addr}")
                    
                    if not self.file_data:
                        if not self.load_file():
                            return
            except BlockingIOError:
                time.sleep(0.01)
                
        # Start transmission
        print("Starting transmission...")
        start_time = time.time()
        last_progress = -1
        last_timeout_check = time.time()
        
        # Initial aggressive send
        self.send_window()
        
        # Main transmission loop
        while self.base < self.file_size or self.window:
            
            readable, _, _ = select.select([self.socket], [], [], 0.0005)

            if readable:
                
                ack_count = 0
                while ack_count < 200: 
                    try:
                        ack_packet, addr = self.socket.recvfrom(MAX_PAYLOAD)
                        if addr == self.client_addr:
                            ack_num, sack_blocks = self.parse_ack(ack_packet)
                            if ack_num is not None:
                                self.handle_ack(ack_num, sack_blocks)
                                ack_count += 1
                    except BlockingIOError:
                        break

                # Send more data after processing ACKs
                if ack_count > 0:
                    self.send_window()

            # Check timeouts very frequently for fast recovery
            current_time = time.time()
            if current_time - last_timeout_check > 0.002:  
                self.check_timeouts()
                last_timeout_check = current_time

              
                self.send_window()
                
            # Progress indicator
            if self.file_size > 0:
                progress = int(self.base * 100 / self.file_size)
                if progress >= last_progress + 10:
                    print(f"Progress: {progress}%")
                    last_progress = progress
                    
        # Send EOF
        print("Sending EOF...")
        self.send_eof()
        
        # Print statistics
        end_time = time.time()
        duration = end_time - start_time
        throughput = (self.file_size * 8) / (duration * 1e6) if duration > 0 else 0
        
        print(f"\n=== Transmission Complete ===")
        print(f"File size: {self.file_size} bytes")
        print(f"Duration: {duration:.2f} seconds")
        print(f"Throughput: {throughput:.2f} Mbps")
        print(f"Packets sent: {self.packets_sent}")
        print(f"Packets retransmitted: {self.packets_retransmitted}")
        if self.packets_sent > 0:
            print(f"Retransmission rate: {self.packets_retransmitted*100/self.packets_sent:.1f}%")
        print(f"Final RTO: {self.rto*1000:.0f} ms")
        if self.min_rtt < float('inf'):
            print(f"Min RTT: {self.min_rtt*1000:.0f} ms")

def main():
    if len(sys.argv) != 4:
        print("Usage: python3 p1_server.py <SERVER_IP> <SERVER_PORT> <SWS>")
        sys.exit(1)
        
    server_ip = sys.argv[1]
    server_port = int(sys.argv[2])
    sws = int(sys.argv[3])
    
    server = ReliableUDPServer(server_ip, server_port, sws)
    
    try:
        server.handle_client()
    except KeyboardInterrupt:
        print("\nServer interrupted")
    except Exception as e:
        print(f"Server error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()