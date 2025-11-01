import socket
import sys
import time
import struct
import os
import math # Not needed for Reno, but safe to keep

# --- Constants ---
# Packet Header Format:
# - Sequence Number (I): 4 bytes, unsigned int
# - ACK Number (I): 4 bytes, unsigned int
# - Flags (H): 2 bytes, unsigned short (SYN=1, ACK=2, EOF=4)
# - SACK Start (I): 4 bytes
# - SACK End (I): 4 bytes
# - Padding (2x): 2 bytes
HEADER_FORMAT = "!IIHII2x"
HEADER_SIZE = 20
MSS_BYTES = 1200  # Max Segment Size (matches assignment)
PAYLOAD_SIZE = MSS_BYTES - HEADER_SIZE  # 1180 bytes

# --- Flags ---
SYN_FLAG = 0x1
ACK_FLAG = 0x2
EOF_FLAG = 0x4

# --- Congestion Control States ---
STATE_SLOW_START = 1
STATE_CONGESTION_AVOIDANCE = 2
STATE_FAST_RECOVERY = 3

# --- RTO Calculation Constants ---
ALPHA = 0.125  # For SRTT
BETA = 0.25   # For RTTVAR
K = 4.0
INITIAL_RTO = 1.0  # 1 second
MIN_RTO = 0.2     # 200ms


class Server:
    def __init__(self, ip, port):
        self.ip = ip
        self.port = int(port)
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind((self.ip, self.port))
        self.socket.settimeout(0.001) # Non-blocking
        self.client_addr = None
        print(f"Server started on {self.ip}:{self.port}")

        self.file_data = b""
        self.file_size = 0
        try:
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
        
        # --- Congestion Control (Reno) ---
        self.state = STATE_SLOW_START
        self.cwnd_bytes = MSS_BYTES   # Congestion window in bytes
        # High initial ssthresh, will be set on first packet loss
        self.ssthresh = 2 * 1024 * 1024 * 1024 # 2 GB
        
        # --- RTO Calculation ---
        self.rto = INITIAL_RTO
        self.srtt = 0.0
        self.rttvar = 0.0
        
        # --- Retransmission Buffer ---
        # {seq_num: (packet_data, send_time, retrans_count)}
        self.sent_packets = {}
        
        # --- SACK & Fast Recovery ---
        self.dup_ack_count = 0
        self.sacked_packets = set() # Set of seq_nums client has SACKed
        
        self.start_time = 0.0
        self.last_ack_time = 0.0

    def pack_header(self, seq_num, ack_num, flags, sack_start=0, sack_end=0):
        """Packs the header into bytes."""
        return struct.pack(HEADER_FORMAT, seq_num, ack_num, flags, sack_start, sack_end)

    def unpack_header(self, packet):
        """Unpacks the header from bytes."""
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
                print("Packet resend limit reached. Aborting.")
                return False # Failed

            # print(f"Resending packet: Seq={seq_num} (RTO={self.rto:.2f}s)")
            self.sent_packets[seq_num] = (packet_data, time.time(), retrans_count + 1)
            self.socket.sendto(packet_data, self.client_addr)
            return True # Success
        return False

    def resend_missing_packet(self):
        """SACK-aware retransmission (for Fast Recovery)."""
        # Find the first packet that is un-ACKed (>= base) and not SACKed
        for seq_num in sorted(self.sent_packets.keys()):
            if seq_num >= self.base_seq_num and seq_num not in self.sacked_packets:
                # print(f"Fast Retransmit (SACK): Resending {seq_num}")
                self.resend_packet(seq_num)
                return
        # print("Fast Retransmit: No non-SACKed packets to send.")

    def find_timeouts(self):
        """Checks for and retransmits timed-out packets."""
        now = time.time()
        packets_to_resend = []
        for seq_num, (packet, send_time, retrans_count) in self.sent_packets.items():
            if now - send_time > self.rto:
                packets_to_resend.append(seq_num)

        if not packets_to_resend:
            return

        # Only process the oldest timed-out packet
        oldest_seq_num = min(packets_to_resend)
        # print(f"Timeout detected for packet: Seq={oldest_seq_num}")
        
        if not self.resend_packet(oldest_seq_num):
             # Failed to resend, might be a closed connection
            return

        # --- Handle RTO Event (Reno) ---
        # This is a full timeout, the most severe congestion signal.
        if self.state != STATE_SLOW_START:
            print("Timeout (RTO). Entering Slow Start.")
            self.state = STATE_SLOW_START
        
        # Set ssthresh
        self.ssthresh = max(2 * PAYLOAD_SIZE, self.cwnd_bytes / 2)
        
        # Reset cwnd to 1 MSS
        self.cwnd_bytes = MSS_BYTES
        
        # Double the RTO (Karn's Algorithm / Exponential Backoff)
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
        """Sends new data packets as allowed by cwnd."""
        # 'inflight' is bytes currently un-ACKed
        inflight = self.next_seq_num - self.base_seq_num
        
        while inflight < self.cwnd_bytes:
            data, seq_num, flags = self.get_next_content()
            
            if data is None:
                break # No more data to send
            
            header = self.pack_header(seq_num, 0, flags)
            packet = header + data
            
            self.socket.sendto(packet, self.client_addr)
            self.sent_packets[seq_num] = (packet, time.time(), 0)
            
            if flags & EOF_FLAG:
                # print(f"Sent EOF: Seq={seq_num}")
                break # Don't send more after EOF
                
            inflight = self.next_seq_num - self.base_seq_num

    def process_incoming_ack(self, packet):
        """Handles an incoming ACK from the client."""
        header_fields = self.unpack_header(packet)
        if header_fields is None:
            return # Malformed packet
            
        seq_num, cum_ack, flags, sack_start, sack_end = header_fields
        
        if not (flags & ACK_FLAG):
            return # Not an ACK packet
            
        self.last_ack_time = time.time()
        
        # --- SACK Processing ---
        if sack_start > 0 and sack_end > sack_start:
            seq = sack_start
            while seq < sack_end:
                if seq in self.sent_packets:
                    self.sacked_packets.add(seq)
                seq += PAYLOAD_SIZE 
            # print(f"Received SACK for [{sack_start}, {sack_end})")

        # --- 1. Duplicate ACK ---
        if cum_ack == self.base_seq_num:
            if self.state != STATE_FAST_RECOVERY:
                self.dup_ack_count += 1
            
            # --- 3 DUP-ACKs: Enter Fast Recovery ---
            if self.dup_ack_count == 3 and self.state != STATE_FAST_RECOVERY:
                print("3 Dup-ACKs. Entering Fast Recovery.")
                
                self.state = STATE_FAST_RECOVERY
                
                # Multiplicative Decrease
                self.ssthresh = max(2 * PAYLOAD_SIZE, self.cwnd_bytes / 2)
                self.cwnd_bytes = self.ssthresh + 3 * PAYLOAD_SIZE # Inflate window
                
                self.resend_missing_packet() # Resend the missing packet
            
            # --- In Fast Recovery ---
            elif self.state == STATE_FAST_RECOVERY:
                # Inflate window for each subsequent DUP ACK
                self.cwnd_bytes += PAYLOAD_SIZE
                self.resend_missing_packet()
            
            return # End processing for DUP ACK

        # --- 2. New ACK (Data is Acknowledged) ---
        if cum_ack > self.base_seq_num:
            self.dup_ack_count = 0
            
            # Calculate RTT sample
            ack_rtt_sample = -1
            newly_acked_packets = []
            for seq in list(self.sent_packets.keys()):
                 if seq < cum_ack:
                    newly_acked_packets.append(seq)
            
            if newly_acked_packets:
                newest_acked_seq = max(newly_acked_packets)
                if newest_acked_seq in self.sent_packets:
                    packet_data, send_time, retrans_count = self.sent_packets[newest_acked_seq]
                    if retrans_count == 0:
                        ack_rtt_sample = time.time() - send_time
                        self.update_rto(ack_rtt_sample)

            # Clean up sent_packets buffer
            for seq_num in newly_acked_packets:
                if seq_num in self.sent_packets:
                    del self.sent_packets[seq_num]
                self.sacked_packets.discard(seq_num)

            # Update base
            self.base_seq_num = cum_ack
            
            # --- Update CWND (Reno Logic) ---
            if self.state == STATE_FAST_RECOVERY:
                # This ACK ends Fast Recovery
                self.state = STATE_CONGESTION_AVOIDANCE
                self.cwnd_bytes = self.ssthresh # Deflate window
            
            if self.state == STATE_SLOW_START:
                # Exponential growth
                self.cwnd_bytes += PAYLOAD_SIZE
                
                # Check for transition to Congestion Avoidance
                if self.cwnd_bytes >= self.ssthresh:
                    self.state = STATE_CONGESTION_AVOIDANCE
            
            elif self.state == STATE_CONGESTION_AVOIDANCE:
                # --- FIX: Additive Increase ---
                # (MSS * MSS) / cwnd_bytes
                # Add at least 1 byte to ensure window grows
                increment = max(1, int(PAYLOAD_SIZE * (PAYLOAD_SIZE / self.cwnd_bytes)))
                self.cwnd_bytes += increment


            # Check if this ACK is the final EOF ACK
            if flags & EOF_FLAG and cum_ack > self.eof_sent_seq:
                print("Final EOF ACK received.")
                return "DONE"
        
        return "CONTINUE"


    def run(self):
        """Main server loop."""
        
        # 1. Wait for initial client request
        try:
            print("Waiting for client request...")
            packet, self.client_addr = self.socket.recvfrom(1024)
            print(f"Client connected from {self.client_addr}")
        except Exception as e:
            print(f"Error receiving initial request: {e}")
            self.socket.close()
            return
            
        self.start_time = time.time()
        self.last_ack_time = self.start_time
        running = True
        
        while running:
            # 1. Check for incoming ACKs
            try:
                ack_packet, _ = self.socket.recvfrom(MSS_BYTES)
                if self.process_incoming_ack(ack_packet) == "DONE":
                    running = False
                    
            except socket.timeout:
                pass  # No ACK, continue
            except Exception as e:
                # print(f"Error receiving ACK: {e}")
                time.sleep(0.001)

            if not running:
                break
            
            # 2. Check for packet timeouts
            self.find_timeouts()

            # 3. Send new data if window allows
            self.send_new_data()

            # 4. Check for client timeout (e.g., 10s of no ACKs)
            if time.time() - self.last_ack_time > 10.0:
                print("Client timed out (10s). Shutting down.")
                running = False

        # --- Transfer Finished ---\
        end_time = time.time()
        duration = end_time - self.start_time
        if duration == 0: duration = 1e-6 # Avoid division by zero
        
        throughput = (self.file_size * 8) / (duration * 1_000_000) # Mbps
        print("---------------------------------")
        print("File transfer complete.")
        print(f"Duration: {duration:.2f} seconds")
        print(f"Total Data: {self.file_size} bytes")
        print(f"Throughput: {throughput:.2f} Mbps")
        print("---------------------------------")

        # Give client time to shut down
        time.sleep(1)
        self.socket.close()

def main():
    if len(sys.argv) != 3:
        print("Usage: python3 p2_server.py <SERVER_IP> <SERVER_PORT>")
        sys.exit(1)
    
    server_ip = sys.argv[1]
    server_port = int(sys.argv[2])
    
    server = Server(server_ip, server_port)
    server.run()

if __name__ == "__main__":
    main()

