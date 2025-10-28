import socket
import sys
import time
import struct
import os

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
        self.socket.settimeout(0.001)  # 1ms timeout for non-blocking loop
        print(f"Server started on {self.ip}:{self.port}")

        self.client_addr = None
        self.file_data = None
        self.file_size = 0
        self.start_time = 0

        # --- Connection State ---
        self.base_seq_num = 0
        self.next_seq_num = 0
        self.eof_seq_num = -1  # Sequence number of the EOF packet
        self.last_ack_time = 0

        # --- In-flight Packet Buffer ---
        # {seq_num: (data, send_time, retransmit_count)}
        self.sent_packets = {}
        
        # --- SACK State ---
        self.sacked_packets = set() # Holds seq_nums client has SACKed

        # --- Congestion Control Variables ---
        self.cwnd = 1 * PAYLOAD_SIZE  # Start with 1 MSS
        self.ssthresh = 64 * 1024   # 64KB (a common initial value)
        self.state = STATE_SLOW_START
        self.dup_ack_count = 0

        # --- RTO Variables ---
        self.srtt = 0.0
        self.rttvar = 0.0
        self.rto = INITIAL_RTO
        self.rto_first_measurement = True

    def pack_header(self, seq_num, ack_num, flags, sack_start=0, sack_end=0):
        """Packs the header into bytes."""
        return struct.pack(HEADER_FORMAT, seq_num, ack_num, flags, sack_start, sack_end)

    def unpack_header(self, packet):
        """Unpacks the header from bytes."""
        try:
            return struct.unpack(HEADER_FORMAT, packet[:HEADER_SIZE])
        except struct.error:
            return None, None, None, None, None

    def update_rto(self, sample_rtt):
        """Updates RTO using Jacobson/Karels algorithm."""
        if self.rto_first_measurement:
            self.srtt = sample_rtt
            self.rttvar = sample_rtt / 2.0
            self.rto_first_measurement = False
        else:
            self.rttvar = (1 - BETA) * self.rttvar + BETA * abs(self.srtt - sample_rtt)
            self.srtt = (1 - ALPHA) * self.srtt + ALPHA * sample_rtt
        
        self.rto = self.srtt + K * self.rttvar
        self.rto = max(MIN_RTO, self.rto) # Enforce a minimum RTO

    def load_file(self, filename="data.txt"):
        """Loads the file to be sent."""
        try:
            with open(filename, 'rb') as f:
                self.file_data = f.read()
            self.file_size = len(self.file_data)
            print(f"Loaded {filename}, size: {self.file_size} bytes")
            return True
        except FileNotFoundError:
            print(f"Error: {filename} not found.")
            return False

    def wait_for_request(self):
        """Waits for the initial 1-byte file request from a client."""
        print("Waiting for client request...")
        while self.client_addr is None:
            try:
                data, addr = self.socket.recvfrom(1024)
                if len(data) == 1:  # Simple 1-byte request
                    self.client_addr = addr
                    print(f"Client connected from {self.client_addr}")
                    if not self.load_file():
                        self.client_addr = None # Reset if file not found
            except socket.timeout:
                continue
            except Exception as e:
                print(f"Error waiting for request: {e}")

    def send_packet(self, seq_num, data, flags=0):
        """Sends a data packet to the client."""
        # Data packets don't send ACKs or SACKs
        header = self.pack_header(seq_num, 0, flags, 0, 0)
        packet = header + data
        
        try:
            self.socket.sendto(packet, self.client_addr)
            
            # Store packet for retransmission
            # Add timestamp for RTO calculation on ACK
            self.sent_packets[seq_num] = (data, time.time(), 0, flags) 
            
            if flags & EOF_FLAG:
                print(f"Sent EOF: Seq={seq_num}")
                self.eof_seq_num = seq_num
            # else:
            #     print(f"Sent: Seq={seq_num}, Size={len(data)}")

        except Exception as e:
            print(f"Error sending packet {seq_num}: {e}")

    def resend_packet(self, seq_num):
        """Resends a packet from the sent_packets buffer."""
        if seq_num in self.sent_packets:
            data, _, retransmit_count, flags = self.sent_packets[seq_num]
            
            # Update packet info with new send time and incremented count
            self.sent_packets[seq_num] = (data, time.time(), retransmit_count + 1, flags)
            
            header = self.pack_header(seq_num, 0, flags, 0, 0)
            packet = header + data
            
            try:
                self.socket.sendto(packet, self.client_addr)
                print(f"Resent: Seq={seq_num} (Count={retransmit_count+1})")
            except Exception as e:
                print(f"Error resending packet {seq_num}: {e}")
        else:
            # This can happen if an ACK for it arrived just before resend
            # print(f"Warning: Tried to resend {seq_num}, but it's not in buffer.")
            pass

    def find_timeouts(self):
        """Finds and retransmits any timed-out packets."""
        now = time.time()
        # Find the oldest un-ACKed packet
        if self.base_seq_num in self.sent_packets:
            _, send_time, _, _ = self.sent_packets[self.base_seq_num]
            
            if now - send_time > self.rto:
                # --- TIMEOUT ---
                print(f"Timeout detected for Seq={self.base_seq_num}. RTO={self.rto:.3f}s")
                self.update_cwnd(case="TLE")
                self.resend_packet(self.base_seq_num)
                # Double the RTO for the next check (exponential backoff)
                self.rto = min(self.rto * 2, 60.0) # Cap at 60s
                # Reset RTO calculation
                self.rto_first_measurement = True

    def update_cwnd(self, case):
        """Updates cwnd and ssthresh based on the event (Reno-like)."""
        if case == "TLE":
            # Timeout Limit Exceeded
            self.state = STATE_SLOW_START
            self.ssthresh = max(self.cwnd / 2, 2 * PAYLOAD_SIZE)
            self.cwnd = 1 * PAYLOAD_SIZE
            self.dup_ack_count = 0
            self.sacked_packets.clear() # Clear SACK info on timeout
            print(f"State -> SLOW_START (Timeout). ssthresh={self.ssthresh}, cwnd={self.cwnd}")

        elif case == "DUP":
            # 3 Duplicate ACKs (Fast Retransmit)
            self.state = STATE_FAST_RECOVERY
            self.ssthresh = max(self.cwnd / 2, 2 * PAYLOAD_SIZE)
            # Per Reno: set cwnd to ssthresh + 3 MSS
            self.cwnd = self.ssthresh + 3 * PAYLOAD_SIZE
            print(f"State -> FAST_RECOVERY. ssthresh={self.ssthresh}, cwnd={self.cwnd}")
            # Resend is handled in process_incoming_ack

        elif case == "NEW_ACK":
            # New ACK received
            if self.state == STATE_SLOW_START:
                self.cwnd += 1 * PAYLOAD_SIZE
                # print(f"State=SLOW_START, cwnd increased to {self.cwnd}")
                if self.cwnd >= self.ssthresh:
                    self.state = STATE_CONGESTION_AVOIDANCE
                    print(f"State -> CONGESTION_AVOIDANCE. ssthresh={self.ssthresh}, cwnd={self.cwnd}")
            
            elif self.state == STATE_CONGESTION_AVOIDANCE:
                # Additive Increase: ~1 MSS per RTT
                self.cwnd += (1 * PAYLOAD_SIZE * PAYLOAD_SIZE) / self.cwnd
                # print(f"State=CONGESTION_AVOIDANCE, cwnd increased to {self.cwnd}")

            elif self.state == STATE_FAST_RECOVERY:
                # Deflate window back to ssthresh
                self.cwnd = self.ssthresh
                self.state = STATE_CONGESTION_AVOIDANCE
                self.dup_ack_count = 0
                print(f"State -> CONGESTION_AVOIDANCE (Exiting Fast Recovery). cwnd={self.cwnd}")
        
        elif case == "INFLATE":
            # In Fast Recovery, inflate window for each extra dup ACK
            if self.state == STATE_FAST_RECOVERY:
                self.cwnd += 1 * PAYLOAD_SIZE
                # print(f"State=FAST_RECOVERY, cwnd inflated to {self.cwnd}")

    def get_next_content(self):
        """Reads the next chunk of data from the file."""
        start = self.next_seq_num
        end = start + PAYLOAD_SIZE
        if start >= self.file_size:
            return None  # No more data
        
        data = self.file_data[start:end]
        return data

    def send_new_data(self):
        """Sends new data packets based on cwnd."""
        # Send packets as long as the window allows
        # (next_seq_num < base_seq_num + cwnd)
        while (self.next_seq_num < self.base_seq_num + self.cwnd) and (self.eof_seq_num == -1):
            
            data = self.get_next_content()
            
            if data:
                # Send data packet
                self.send_packet(self.next_seq_num, data, flags=0)
                self.next_seq_num += len(data)
            elif self.eof_seq_num == -1:
                # No more data, send EOF
                self.send_packet(self.next_seq_num, b'EOF', flags=EOF_FLAG)
                break # Stop sending after EOF
            else:
                # EOF already sent
                break

    def resend_missing_packet(self):
        """SACK-aware retransmission."""
        # Find the first packet > base_seq_num that is in-flight
        # but has not been SACKed.
        
        # This is a simple linear scan. For high performance,
        # a more complex data structure would be used.
        sorted_keys = sorted(self.sent_packets.keys())
        for seq in sorted_keys:
            if seq <= self.base_seq_num:
                continue
            
            if seq not in self.sacked_packets:
                # Found the next missing packet
                print(f"SACK Resend: Found missing packet {seq}")
                self.resend_packet(seq)
                self.sacked_packets.add(seq) # Mark as re-sent
                return # Only send one per DUP ACK

    def process_incoming_ack(self, packet):
        """Processes an incoming ACK packet."""
        header_data = self.unpack_header(packet)
        if header_data is None:
            return "CONTINUE"

        seq_num, cum_ack, flags, sack_start, sack_end = header_data
        
        if not (flags & ACK_FLAG):
            return "CONTINUE" # Not an ACK packet

        # Check if this is the final ACK for EOF
        if self.eof_seq_num != -1 and cum_ack > self.eof_seq_num:
            print(f"Got final ACK for EOF (ACK={cum_ack}). Transfer complete.")
            return "DONE" # Signal completion

        if cum_ack > self.base_seq_num:
            # --- NEW ACK ---
            
            # Calculate RTT sample
            if self.base_seq_num in self.sent_packets:
                _, send_time, _, _ = self.sent_packets[self.base_seq_num]
                sample_rtt = time.time() - send_time
                self.update_rto(sample_rtt)
            
            # Update base sequence number
            self.base_seq_num = cum_ack
            self.dup_ack_count = 0

            # Clean up buffers (remove ACKed packets)
            acked_keys = [k for k in self.sent_packets if k < self.base_seq_num]
            for k in acked_keys:
                del self.sent_packets[k]
            
            acked_sack_keys = [k for k in self.sacked_packets if k < self.base_seq_num]
            for k in acked_sack_keys:
                self.sacked_packets.remove(k)
            
            # Update cwnd (will exit Fast Recovery if in it)
            self.update_cwnd(case="NEW_ACK")
            
        elif cum_ack == self.base_seq_num:
            # --- DUPLICATE ACK ---
            
            # Process SACK information
            if sack_start > 0 and sack_end > sack_start:
                # print(f"Received SACK block: [{sack_start}, {sack_end})")
                seq = sack_start
                while seq < sack_end:
                    if seq in self.sent_packets:
                        self.sacked_packets.add(seq)
                    seq += PAYLOAD_SIZE # Assuming SACK block is packet-aligned

            if self.state != STATE_FAST_RECOVERY:
                self.dup_ack_count += 1
            else:
                # We are in Fast Recovery, inflate window
                self.update_cwnd(case="INFLATE")

            if self.dup_ack_count == 3:
                # --- FAST RETRANSMIT ---
                if self.state != STATE_FAST_RECOVERY:
                    print(f"3 Duplicate ACKs received for Seq={cum_ack}")
                    self.update_cwnd(case="DUP")
                    self.resend_packet(self.base_seq_num)
                    self.sacked_packets.add(self.base_seq_num) # Mark as re-sent
            
            if self.state == STATE_FAST_RECOVERY:
                # SACK logic: resend the next *actually* missing packet
                self.resend_missing_packet()

        
        return "CONTINUE"


    def run(self):
        """Main server loop."""
        self.wait_for_request()
        if self.client_addr is None:
            print("Server shutting down (no client).")
            return

        print("Starting file transfer...")
        self.start_time = time.time()
        self.last_ack_time = self.start_time
        
        running = True
        while running:
            # 1. Check for incoming ACKs
            try:
                ack_data, _ = self.socket.recvfrom(1024)
                self.last_ack_time = time.time()
                result = self.process_incoming_ack(ack_data)
                if result == "DONE":
                    running = False
            except socket.timeout:
                pass  # No ACK, continue
            except Exception as e:
                print(f"Error receiving ACK: {e}")
                time.sleep(0.01)

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

        # --- Transfer Finished ---
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

