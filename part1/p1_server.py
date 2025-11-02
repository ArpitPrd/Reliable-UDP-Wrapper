import socket
import sys
import time
import struct
import os

# --- Constants ---
# Packet Header Format (same as p2 for compatibility)
# - Sequence Number (I): 4 bytes, unsigned int
# - ACK Number (I): 4 bytes, unsigned int
# - Flags (H): 2 bytes, unsigned short (SYN=1, ACK=2, EOF=4)
# - SACK Start (I): 4 bytes
# - SACK End (I): 4 bytes
# - Padding (2x): 2 bytes
HEADER_FORMAT = "!IIHII2x"
HEADER_SIZE = 20
MSS_BYTES = 1200  # Max Segment Size
PAYLOAD_SIZE = MSS_BYTES - HEADER_SIZE  # 1180 bytes

# --- Flags ---
SYN_FLAG = 0x1
ACK_FLAG = 0x2
EOF_FLAG = 0x4

# --- RTO Calculation Constants ---
ALPHA = 0.125  # For SRTT
BETA = 0.25   # For RTTVAR
K = 4.0
INITIAL_RTO = 1.0  # 1 second
MIN_RTO = 0.2     # 200ms


class Server:
    def __init__(self, ip, port, sws):
        self.ip = ip
        self.port = int(port)
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind((self.ip, self.port))
        self.client_addr = None
        print(f"Server started on {self.ip}:{self.port}")

        self.file_data = b""
        self.file_size = 0
        try:
            # Per assignment, server has data.txt
            with open("data.txt", "rb") as f:
                self.file_data = f.read()
            self.file_size = len(self.file_data)
        except IOError as e:
            print(f"Error reading data.txt: {e}")
            sys.exit(1)

        # --- Connection State ---
        self.next_seq_num = 0       # Next seq num to send
        self.base_seq_num = 0       # Oldest un-ACKed seq num
        self.eof_sent_seq = -1      # Seq num of the EOF packet
        self.connection_dead = False
        
        # --- Part 1: Fixed Sender Window Size ---
        self.sws_bytes = int(sws)
        print(f"Using fixed Sender Window Size (SWS): {self.sws_bytes} bytes")
        
        # --- RTO Calculation ---
        self.rto = INITIAL_RTO
        self.srtt = 0.0
        self.rttvar = 0.0
        
        # --- Retransmission Buffer ---
        # {seq_num: (packet, send_time, retrans_count)}
        self.sent_packets = {}
        
        # --- SACK & Fast Recovery ---
        self.dup_ack_count = 0
        self.sacked_packets = set()
        
        self.start_time = 0.0
        self.last_ack_time = 0.0
        
    def pack_header(self, seq_num, ack_num, flags, sack_start=0, sack_end=0):
        return struct.pack(HEADER_FORMAT, seq_num, ack_num, flags, sack_start, sack_end)

    def unpack_header(self, packet):
        try:
            return struct.unpack(HEADER_FORMAT, packet[:HEADER_SIZE])
        except struct.error:
            return None

    def update_rto(self, rtt_sample):
        """Updates RTO using Jacobson's algorithm."""
        if self.srtt == 0.0:
            # First sample
            self.srtt = rtt_sample
            self.rttvar = rtt_sample / 2.0
        else:
            # Subsequent samples
            self.rttvar = (1 - BETA) * self.rttvar + BETA * abs(self.srtt - rtt_sample)
            self.srtt = (1 - ALPHA) * self.srtt + ALPHA * rtt_sample
        
        self.rto = self.srtt + K * self.rttvar
        self.rto = max(MIN_RTO, self.rto) # Enforce minimum RTO

    def resend_packet(self, seq_num):
        """Resends a specific packet from the buffer."""
        if seq_num in self.sent_packets:
            packet_data, send_time, retrans_count = self.sent_packets[seq_num]
            
            if retrans_count > 10:
                print(f"Packet {seq_num} resend limit reached. Aborting.")
                self.connection_dead = True
                return False 

            # Update send time and retrans_count
            self.sent_packets[seq_num] = (packet_data, time.time(), retrans_count + 1)
            
            try:
                self.socket.sendto(packet_data, self.client_addr)
                # print(f"Resent packet {seq_num}")
                return True 
            except OSError as e:
                # Handle cases where client is unreachable
                if e.errno in [101, 111, 113]: 
                    print(f"Client unreachable on resend: {e}. Aborting transfer.")
                    self.connection_dead = True
                    return False 
                else:
                    raise 
            except Exception as e:
                print(f"Error in resend_packet: {e}")
                self.connection_dead = True
                return False
                
        return False

    def resend_missing_packet(self):
        """SACK-aware retransmission (for Fast Retransmit)."""
        # Find the first non-sacked packet >= base and resend it
        for seq_num in sorted(self.sent_packets.keys()):
            if seq_num >= self.base_seq_num and seq_num not in self.sacked_packets:
                print(f"Fast Retransmit: Resending {seq_num}")
                self.resend_packet(seq_num)
                return

    def find_timeouts(self):
        """Checks for and retransmits timed-out packets."""
        now = time.time()
        packets_to_resend = []
        for seq_num, (packet, send_time, retrans_count) in self.sent_packets.items():
            if now - send_time > self.rto:
                packets_to_resend.append(seq_num)

        if not packets_to_resend:
            return

        # Only resend the oldest timed-out packet
        oldest_seq_num = min(packets_to_resend)
        
        if not self.resend_packet(oldest_seq_num):
            return # Connection is likely dead

        # --- Handle RTO Event (Part 1) ---
        print(f"Timeout (RTO). Resending packet {oldest_seq_num}.")
        
        # Double the RTO (exponential backoff)
        self.rto = min(self.rto * 2, 60.0) # Cap at 60s
        self.dup_ack_count = 0


    def get_next_content(self):
        """Gets the next chunk of file data to send."""
        if self.next_seq_num < self.file_size:
            start = self.next_seq_num
            end = min(start + PAYLOAD_SIZE, self.file_size)
            data = self.file_data[start:end]
            seq_num = start
            self.next_seq_num = end
            return data, seq_num, 0
        
        elif self.eof_sent_seq == -1: # File done, EOF not yet sent
            self.eof_sent_seq = self.file_size
            return b"EOF", self.eof_sent_seq, EOF_FLAG
        
        else: # File and EOF already sent
            return None, -1, 0

    def send_new_data(self):
        """Sends new data packets as allowed by SWS."""
        # Calculate bytes in flight
        inflight = self.next_seq_num - self.base_seq_num
        
        # Use fixed SWS, not dynamic cwnd
        while inflight < self.sws_bytes:
            if self.connection_dead:
                break
                
            data, seq_num, flags = self.get_next_content()
            
            if data is None:
                break # No more data to send
            
            header = self.pack_header(seq_num, 0, flags)
            packet = header + data
            
            try:
                self.socket.sendto(packet, self.client_addr)
                # Store in buffer for retransmission
                self.sent_packets[seq_num] = (packet, time.time(), 0)
            except OSError as e:
                if e.errno in [101, 111, 113]:
                    print(f"Client unreachable on send: {e}. Aborting transfer.")
                    self.connection_dead = True
                    break 
                else:
                    raise 
            except Exception as e:
                print(f"Error in send_new_data: {e}")
                self.connection_dead = True
                break 
            
            if flags & EOF_FLAG:
                # print(f"Sent EOF packet {seq_num}")
                break # Don't send more after EOF
                
            # Update inflight bytes
            inflight = self.next_seq_num - self.base_seq_num

    def process_incoming_ack(self, packet):
        """Handles an incoming ACK from the client."""
        
        header_fields = self.unpack_header(packet)
        if header_fields is None:
            return "CONTINUE" # Bad packet
            
        seq_num, cum_ack, flags, sack_start, sack_end = header_fields
        
        if not (flags & ACK_FLAG):
            return "CONTINUE" # Not an ACK
            
        self.last_ack_time = time.time()
        
        # --- SACK Processing ---
        if sack_start > 0 and sack_end > sack_start:
            seq = sack_start
            while seq < sack_end:
                if seq in self.sent_packets:
                    self.sacked_packets.add(seq)
                seq += PAYLOAD_SIZE # Assuming SACK block is packet-aligned

        # --- 1. Duplicate ACK ---
        if cum_ack == self.base_seq_num:
            self.dup_ack_count += 1
            
            # --- 3 DUP-ACKs: Trigger Fast Retransmit ---
            if self.dup_ack_count >= 3:
                print("3 Dup-ACKs. Fast Retransmit.")
                self.resend_missing_packet()
                self.dup_ack_count = 0
            
            return "CONTINUE"

        # --- 2. New ACK (Data is Acknowledged) ---
        if cum_ack > self.base_seq_num:
            self.dup_ack_count = 0
            
            # Calculate RTT sample for non-retransmitted packets
            newly_acked_packets = []
            for seq in list(self.sent_packets.keys()):
                 if seq < cum_ack:
                    newly_acked_packets.append(seq)
            
            if newly_acked_packets:
                newest_acked_seq = max(newly_acked_packets)
                if newest_acked_seq in self.sent_packets:
                    packet_data, send_time, retrans_count = self.sent_packets[newest_acked_seq]
                    # Only use first transmission for RTT sample
                    if retrans_count == 0:
                        ack_rtt_sample = time.time() - send_time
                        self.update_rto(ack_rtt_sample)

            # Clean up sent_packets buffer
            for seq_num in newly_acked_packets:
                if seq_num in self.sent_packets:
                    del self.sent_packets[seq_num]
                self.sacked_packets.discard(seq_num)

            # Update base (slide the window)
            self.base_seq_num = cum_ack
            
            # --- NO CWND UPDATE IN PART 1 ---
            # All congestion control logic is removed.

            # Check if this ACK is the final EOF ACK
            if flags & EOF_FLAG and cum_ack > self.eof_sent_seq:
                print("Final EOF ACK received.")
                return "DONE"
        
        return "CONTINUE"


    def run(self):
        """Main server loop."""
        
        try:
            # Wait for the initial 1-byte request from the client
            print("Waiting for client request...")
            packet, self.client_addr = self.socket.recvfrom(1024)
            print(f"Client connected from {self.client_addr}")
            # Set a short timeout for non-blocking receives
            self.socket.settimeout(0.001) 
        except Exception as e:
            print(f"Error receiving initial request: {e}")
            self.socket.close()
            return
            
        self.start_time = time.time()
        self.last_ack_time = self.start_time

        running = True
        
        while running:
            if self.connection_dead:
                print("Connection dead, shutting down.")
                running = False
                break
                
            try:
                # Check for incoming ACKs
                ack_packet, _ = self.socket.recvfrom(MSS_BYTES)
                if self.process_incoming_ack(ack_packet) == "DONE":
                    running = False
                    
            except socket.timeout:
                pass # No ACK received, loop continues
            except Exception as e:
                # Ignore other potential socket errors for now
                time.sleep(0.001)

            if not running:
                break
            
            # Check for RTOs
            self.find_timeouts()
            
            # Send new data if window allows
            self.send_new_data()

            # Check for client timeout
            if time.time() - self.last_ack_time > 1e9:
                print("Client timed out (10s). Shutting down.")
                running = False

        # --- Transfer Finished ---
        end_time = time.time()
        duration = end_time - self.start_time
        if duration == 0: duration = 1e-6 
        
        throughput = (self.file_size * 8) / (duration * 1_000_000) # Mbps
        print("---------------------------------")
        print("File transfer complete.")
        print(f"Duration: {duration:.2f} seconds")
        print(f"Total Data: {self.file_size} bytes")
        print(f"Throughput: {throughput:.2f} Mbps")
        print("---------------------------------")

        # Wait a moment for final ACKs to clear
        time.sleep(1)
        self.socket.close()

def main():
    if len(sys.argv) != 4:
        print("Usage: python3 p1_server.py <SERVER_IP> <SERVER_PORT> <SWS>")
        sys.exit(1)
    
    server_ip = sys.argv[1]
    server_port = int(sys.argv[2])
    sws = int(sys.argv[3])
    
    server = Server(server_ip, server_port, sws)
    server.run()

if __name__ == "__main__":
    main()