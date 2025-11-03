import socket
import sys
import time
import struct
import os
import math # For CUBIC
import csv # For logging
import select # [OPTIMIZATION] Added for event-driven I/O
import collections # [OPTIMIZATION] Added for efficient timeout checking

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
        # [OPTIMIZATION] Set to non-blocking to use with select()
        self.socket.setblocking(False) 
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
        self.connection_dead = False
        
        # --- Congestion Control (Reno/CUBIC) ---
        self.state = STATE_SLOW_START
        self.cwnd_bytes = MSS_BYTES   # Congestion window in bytes
        self.ssthresh = 2 * 1024 * 1024 * 1024 # 2 GB
        
        # --- RTO Calculation ---
        self.rto = INITIAL_RTO
        self.srtt = 0.0
        self.rttvar = 0.0
        self.rtt_min = float('inf') # [CUBIC] Minimum observed RTT
        
        # --- Retransmission Buffer ---
        # [OPTIMIZATION] Use OrderedDict to find the oldest packet in O(1)
        self.sent_packets = collections.OrderedDict()
        
        # --- SACK & Fast Recovery ---
        self.dup_ack_count = 0
        self.sacked_packets = set()
        
        self.start_time = 0.0
        self.last_ack_time = 0.0
        
        # --- Reno AI (for before first congestion event) ---
        self.ack_credits = 0.0

        # --- CUBIC Parameters ---
        self.C = 0.4  # CUBIC constant
        self.beta_cubic = 0.7 # CUBIC multiplicative decrease factor
        self.w_max_bytes = 0.0  # [CUBIC] Window size just before last congestion
        # [OPTIMIZATION] Added for Fast Convergence
        self.w_max_last_bytes = 0.0 
        self.t_last_congestion = 0.0 # [CUBIC] Time of last congestion event
        self.K = 0.0 # [CUBIC] Time period to reach W_max
        

        # --- CWND Logging ---
        self.cwnd_log_file = None
        self.log_filename = f"cwnd_log_{self.port}.csv"

    def get_state_str(self):
        """Helper function to get a string for the current state."""
        if self.state == STATE_SLOW_START:
            return "SS"
        if self.state == STATE_CONGESTION_AVOIDANCE:
            # Show "CA" for Reno-AI and "CUBIC" for CUBIC-AI
            return "CUBIC" if self.t_last_congestion > 0 else "CA"
        if self.state == STATE_FAST_RECOVERY:
            return "FR"
        return "UNK"

    def log_cwnd(self):
        """Logs the current cwnd, ssthresh, and state to the log file."""
        if not self.cwnd_log_file:
            return
        
        try:
            timestamp = time.time() - self.start_time
            state_str = self.get_state_str()
            # Log: timestamp, cwnd_bytes, ssthresh_bytes, state
            self.cwnd_log_file.write(f"{timestamp:.4f},{int(self.cwnd_bytes)},{int(self.ssthresh)},{state_str}\n")
        except Exception as e:
            print(f"Error writing to log: {e}")

    def plot_cwnd(self, log_filename):
        """Tries to plot the CWND log file using matplotlib."""
        try:
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
        except ImportError:
            print("\n[Plotting] Matplotlib not found. Skipping plot generation.")
            print("To install: sudo apt-get install python3-matplotlib (or pip3 install matplotlib)")
            return

        timestamps = []
        cwnds_kb = []
        ssthreshs_kb = []
        states = []
        
        try:
            with open(log_filename, 'r') as f:
                reader = csv.reader(f)
                next(reader) # Skip header
                for row in reader:
                    timestamps.append(float(row[0]))
                    cwnds_kb.append(float(row[1]) / 1024.0)
                    ssthreshs_kb.append(float(row[2]) / 1024.0)
                    states.append(row[3])
        except Exception as e:
            print(f"Error reading log file for plotting: {e}")
            return

        if not timestamps:
            print("[Plotting] No data to plot.")
            return

        plot_filename = f"cwnd_plot_{self.port}.png"
        
        plt.figure(figsize=(12, 6))
        
        plt.plot(timestamps, cwnds_kb, label='cwnd (KB)', drawstyle='steps-post')
        plt.plot(timestamps, ssthreshs_kb, label='ssthresh (KB)', linestyle='--', color='gray', drawstyle='steps-post')
        
        # [CUBIC] Updated state colors
        state_colors = {
            'SS': (1.0, 0.0, 0.0, 0.1), # Red
            'CA': (0.0, 1.0, 0.0, 0.1), # Green (Reno-AI)
            'CUBIC': (0.0, 0.5, 0.5, 0.1), # Teal (Cubic-AI)
            'FR': (0.0, 0.0, 1.0, 0.1)  # Blue
        }
        
        if states: 
            last_state = states[0]
            start_time = timestamps[0]
            
            for i in range(1, len(timestamps)):
                if states[i] != last_state:
                    plt.axvspan(start_time, timestamps[i], facecolor=state_colors.get(last_state, (0.0, 0.0, 0.0, 0.1)), alpha=1.0)
                    start_time = timestamps[i]
                    last_state = states[i]
            plt.axvspan(start_time, timestamps[-1], facecolor=state_colors.get(last_state, (0.0, 0.0, 0.0, 0.1)), alpha=1.0)
        
        if states:
            handles, labels = plt.gca().get_legend_handles_labels()
            handles.append(plt.Rectangle((0,0),1,1, facecolor=state_colors['SS']))
            labels.append('Slow Start (SS)')
            handles.append(plt.Rectangle((0,0),1,1, facecolor=state_colors['CA']))
            labels.append('Congestion Avoidance (Reno)')
            handles.append(plt.Rectangle((0,0),1,1, facecolor=state_colors['CUBIC']))
            labels.append('Congestion Avoidance (CUBIC)')
            handles.append(plt.Rectangle((0,0),1,1, facecolor=state_colors['FR']))
            labels.append('Fast Recovery (FR)')
            plt.legend(handles, labels)
        else:
            plt.legend()


        plt.xlabel('Time (seconds)')
        plt.ylabel('Window Size (KB)')
        plt.title(f'Congestion Window (cwnd) over Time - Port {self.port}')
        plt.grid(True)
        plt.tight_layout()
        
        plt.savefig(plot_filename)
        print(f"[Plotting] CWND plot saved to {plot_filename}")


    def pack_header(self, seq_num, ack_num, flags, sack_start=0, sack_end=0):
        return struct.pack(HEADER_FORMAT, seq_num, ack_num, flags, sack_start, sack_end)

    def unpack_header(self, packet):
        try:
            return struct.unpack(HEADER_FORMAT, packet[:HEADER_SIZE])
        except struct.error:
            return None

    def update_rto(self, rtt_sample):
        """Updates RTO using Jacobson's algorithm."""
        
        # --- [CUBIC] Track minimum RTT ---
        self.rtt_min = min(self.rtt_min, rtt_sample)
        
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
                self.connection_dead = True # [OPTIMIZATION] Set connection dead
                return False 

            # [OPTIMIZATION] Must update send_time in OrderedDict
            # To do this, we must remove and re-add it to the end
            del self.sent_packets[seq_num]
            self.sent_packets[seq_num] = (packet_data, time.time(), retrans_count + 1)
            
            try:
                self.socket.sendto(packet_data, self.client_addr)
                return True 
            except OSError as e:
                # Handle non-blocking socket "errors"
                if e.errno in [11, 35, 10035]: # EAGAIN / EWOULDBLOCK
                    print("Socket busy, will retry resend later.")
                    # [OPTIMIZATION] Re-add packet to *front* of queue
                    del self.sent_packets[seq_num]
                    self.sent_packets[seq_num] = (packet_data, send_time, retrans_count)
                    self.sent_packets.move_to_end(seq_num, last=False)
                    return False
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
        """SACK-aware retransmission (for Fast Recovery)."""
        # [OPTIMIZATION] Iterate keys, as OrderedDict is safe
        for seq_num in list(self.sent_packets.keys()):
            if seq_num >= self.base_seq_num and seq_num not in self.sacked_packets:
                self.resend_packet(seq_num)
                return

    # --- [CUBIC] New function to handle congestion event ---
    def enter_cubic_congestion_avoidance(self):
        """Called on 3 Dup-ACKs or RTO to set CUBIC parameters."""
        self.t_last_congestion = time.time()
        
        # --- [OPTIMIZATION] CUBIC Fast Convergence ---
        new_w_max = self.cwnd_bytes # Store W_max *before* reduction
        
        if new_w_max < self.w_max_bytes:
            # New W_max is smaller, apply fast convergence reduction
            self.w_max_last_bytes = self.w_max_bytes # save old w_max
            self.w_max_bytes = new_w_max * (1.0 + self.beta_cubic) / 2.0
        else:
            # New W_max is larger, just update
            self.w_max_last_bytes = self.w_max_bytes # save old w_max
            self.w_max_bytes = new_w_max
        # --- End Optimization ---

        # Perform multiplicative decrease
        self.ssthresh = self.cwnd_bytes * self.beta_cubic
        
        # Calculate K (time to reach W_max)
        # K = (W_max * (1-beta) / C)^(1/3)
        w_max_mss = max(1.0, self.w_max_bytes / PAYLOAD_SIZE)
        k_numerator = w_max_mss * (1.0 - self.beta_cubic) / self.C
        self.K = (k_numerator ** (1.0/3.0)) if k_numerator > 0 else 0.0
        
        # Log will be done by the caller (find_timeouts or process_ack)
        

    # [OPTIMIZATION] Renamed from find_timeouts to handle_timeouts
    def handle_timeouts(self):
        """Checks for and retransmits timed-out packets."""
        now = time.time()
        packets_to_resend = []
        
        # [OPTIMIZATION] Only check oldest packets due to OrderedDict
        for seq_num, (packet, send_time, retrans_count) in self.sent_packets.items():
            if now - send_time > self.rto:
                packets_to_resend.append(seq_num)
            else:
                # Oldest packet hasn't timed out, so none have
                break 

        if not packets_to_resend:
            return

        # Only resend the *oldest* timed-out packet
        oldest_seq_num = packets_to_resend[0]
        
        if not self.resend_packet(oldest_seq_num):
            return # Resend failed (e.g., socket busy or dead)

        # --- Handle RTO Event (CUBIC) ---
        print("Timeout (RTO). Entering Slow Start.")
        
        # [CUBIC] This is a congestion event.
        # Enter CA to set W_max, K, ssthresh
        self.enter_cubic_congestion_avoidance() 
        
        # [CUBIC] On RTO, enter Slow Start and reset cwnd to 1
        self.state = STATE_SLOW_START
        self.cwnd_bytes = MSS_BYTES
        
        self.rto = min(self.rto * 2, 60.0) # Cap at 60s
        self.dup_ack_count = 0

        self.log_cwnd()

    # [OPTIMIZATION] New function to get the delay for select()
    def get_next_rto_delay(self):
        """Returns the time in seconds until the next RTO, or a default."""
        if not self.sent_packets:
            # No packets in flight, just check for client inactivity
            return 1.0 # Poll every 1 second
        
        try:
            # Get the *first* (oldest) item from OrderedDict
            oldest_seq_num = next(iter(self.sent_packets))
            _packet, send_time, _count = self.sent_packets[oldest_seq_num]
            
            expiry_time = send_time + self.rto
            delay = expiry_time - time.time()
            
            # Return a small positive delay, never 0 or negative
            return max(0.001, delay) 
            
        except StopIteration:
            # Should not happen if self.sent_packets is not empty, but good to check
            return 1.0


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
        inflight = self.next_seq_num - self.base_seq_num
        
        while inflight < self.cwnd_bytes:
            if self.connection_dead:
                break
                
            data, seq_num, flags = self.get_next_content()
            
            if data is None:
                break 
            
            header = self.pack_header(seq_num, 0, flags)
            packet = header + data
            
            try:
                self.socket.sendto(packet, self.client_addr)
                # [OPTIMIZATION] Add to end of OrderedDict
                self.sent_packets[seq_num] = (packet, time.time(), 0)
            except OSError as e:
                # [OPTIMIZATION] Handle non-blocking socket errors
                if e.errno in [11, 35, 10035]: # EAGAIN / EWOULDBLOCK
                    # Socket send buffer is full. Stop sending.
                    # print("Socket buffer full, pausing send.")
                    
                    # We failed to send, so roll back the seq num
                    if flags & EOF_FLAG:
                        self.eof_sent_seq = -1
                    else:
                        self.next_seq_num = seq_num
                    break # Stop trying to send
                
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
                break 
                
            inflight = self.next_seq_num - self.base_seq_num

    def process_incoming_ack(self, packet):
        """Handles an incoming ACK from the client."""
        
        old_cwnd = self.cwnd_bytes
        old_ssthresh = self.ssthresh
        old_state = self.state
        
        header_fields = self.unpack_header(packet)
        
        # [OPTIMIZATION] Removed debug print
        # print(f"The header fields in some server is: {header_fields}")

        if header_fields is None:
            return 
            
        seq_num, cum_ack, flags, sack_start, sack_end = header_fields
        
        if not (flags & ACK_FLAG):
            return 
            
        self.last_ack_time = time.time()
        
        # --- SACK Processing ---
        # *** MISTAKE 1 (FIX) ***
        # The original loop was incorrect as it assumed PAYLOAD_SIZE increments.
        # Our client sends a single packet SACK, so we just add the start.
        if sack_start > 0 and sack_end > sack_start:
            if sack_start in self.sent_packets:
                self.sacked_packets.add(sack_start)

        # --- 1. Duplicate ACK ---
        if cum_ack == self.base_seq_num:
            if self.state != STATE_FAST_RECOVERY:
                self.dup_ack_count += 1
            
            # --- 3 DUP-ACKs: Enter Fast Recovery (CUBIC) ---
            if self.dup_ack_count == 3 and self.state != STATE_FAST_RECOVERY:
                print("3 Dup-ACKs. Entering Fast Recovery.")
                
                # [CUBIC] This is a congestion event.
                # Set W_max, K, ssthresh
                self.enter_cubic_congestion_avoidance()
                
                # [CUBIC] Set cwnd = ssthresh
                self.cwnd_bytes = self.ssthresh 
                self.state = STATE_FAST_RECOVERY
                
                self.resend_missing_packet()
            
            # --- In Fast Recovery ---
            elif self.state == STATE_FAST_RECOVERY:
                # [CUBIC] Unlike Reno, CUBIC does NOT inflate the window
                # on subsequent DUP ACKs. It waits for the new ACK.
                pass
            
            if self.cwnd_bytes != old_cwnd or self.ssthresh != old_ssthresh or self.state != old_state:
                self.log_cwnd()
            
            return "CONTINUE"

        # --- 2. New ACK (Data is Acknowledged) ---
        if cum_ack > self.base_seq_num:
            self.dup_ack_count = 0
            
            # Calculate RTT sample
            ack_rtt_sample = -1
            newly_acked_packets = []
            
            # [OPTIMIZATION] Use list() to allow deletion during iteration
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
            
            # --- Update CWND (Reno/CUBIC Logic) ---
            if self.state == STATE_FAST_RECOVERY:
                # This ACK ends Fast Recovery
                self.state = STATE_CONGESTION_AVOIDANCE
            
            if self.state == STATE_SLOW_START:
                # Exponential growth
                self.cwnd_bytes += PAYLOAD_SIZE
                
                # Check for transition to Congestion Avoidance
                if self.cwnd_bytes >= self.ssthresh:
                    self.state = STATE_CONGESTION_AVOIDANCE
            
            elif self.state == STATE_CONGESTION_AVOIDANCE:
                
                if self.t_last_congestion == 0:
                    # --- Reno-like AI (no congestion event yet) ---
                    # Use float accumulator for: (MSS * MSS) / cwnd
                    if self.cwnd_bytes > 0:
                        increment_frac = (PAYLOAD_SIZE * PAYLOAD_SIZE) / float(self.cwnd_bytes)
                        self.ack_credits += increment_frac
                    
                    if self.ack_credits >= 1.0:
                        int_increment = int(self.ack_credits)
                        self.cwnd_bytes += int_increment
                        self.ack_credits -= int_increment
                
                else:
                    # --- [OPTIMIZATION] CUBIC Growth (with TCP-Friendly) ---
                    rtt_min_sec = INITIAL_RTO
                    if self.rtt_min != float('inf'):
                        rtt_min_sec = self.rtt_min
                    elif self.srtt > 0:
                        rtt_min_sec = self.srtt
                    
                    # t_elapsed = time since last congestion
                    t_elapsed = time.time() - self.t_last_congestion
                    
                    # 1. W_tcp(t) = Reno-friendly growth
                    # alpha_cubic = 3 * beta / (2-beta)
                    alpha_cubic = (3.0 * self.beta_cubic / (2.0 - self.beta_cubic))
                    # w_tcp = ssthresh + alpha_cubic * (t/RTT_min) * MSS
                    w_tcp = self.ssthresh + alpha_cubic * (t_elapsed / rtt_min_sec) * PAYLOAD_SIZE
                    
                    # 2. W_cubic(t + RTT_min) = CUBIC growth target
                    t_target = t_elapsed + rtt_min_sec
                    t_minus_K = t_target - self.K
                    w_cubic_target = self.C * (t_minus_K ** 3) + self.w_max_bytes
                    
                    # 3. CUBIC target is max of TCP-friendly and CUBIC curve
                    target_cwnd = max(w_cubic_target, w_tcp)
                    
                    # 4. Calculate per-ACK increment
                    cwnd_pkts = max(1.0, self.cwnd_bytes / PAYLOAD_SIZE)
                    # Increment = (W_target(t+RTT) - W(t)) / (W(t)/MSS)
                    increment_bytes = (target_cwnd - self.cwnd_bytes) / cwnd_pkts
                    self.cwnd_bytes += increment_bytes
                    # --- End Optimization ---


            # Check if this ACK is the final EOF ACK
            if flags & EOF_FLAG and cum_ack > self.eof_sent_seq:
                print("Final EOF ACK received.")
                if self.cwnd_bytes != old_cwnd or self.ssthresh != old_ssthresh or self.state != old_state:
                    self.log_cwnd()
                return "DONE"
        
        if self.cwnd_bytes != old_cwnd or self.ssthresh != old_ssthresh or self.state != old_state:
            self.log_cwnd()
            
        return "CONTINUE"


    def run(self):
        """Main server loop."""
        
        try:
            print("Waiting for client request...")
            # [OPTIMIZATION] Use select to wait for the first packet
            readable, _, _ = select.select([self.socket], [], [], 15.0) # 15s timeout
            if not readable:
                print("Timed out waiting for initial client.")
                self.socket.close()
                return
                
            packet, self.client_addr = self.socket.recvfrom(1024)
            print(f"Client connected from {self.client_addr}")
            
        except Exception as e:
            print(f"Error receiving initial request: {e}")
            self.socket.close()
            return
            
        self.start_time = time.time()
        self.last_ack_time = self.start_time

        try:
            self.cwnd_log_file = open(self.log_filename, "w", buffering=1) 
            self.cwnd_log_file.write("timestamp_s,cwnd_bytes,ssthresh_bytes,state\n")
            print(f"Logging CWND to {self.log_filename}")
            self.log_cwnd()
        except IOError as e:
            print(f"Error: Could not open log file {self.log_filename}: {e}")
            self.cwnd_log_file = None

        running = True
        
        # [OPTIMIZATION] New select-based main loop
        while running:
            if self.connection_dead:
                print("Connection dead, shutting down.")
                running = False
                break
            
            # 1. Calculate select timeout based on the next RTO
            timeout_delay = self.get_next_rto_delay()
            
            try:
                # 2. Wait for ACK or for RTO to expire
                readable, _, _ = select.select([self.socket], [], [], timeout_delay)
                
                # 3. If an ACK arrived, process it
                if readable:
                    ack_packet, _ = self.socket.recvfrom(MSS_BYTES)
                    if self.process_incoming_ack(ack_packet) == "DONE":
                        running = False
                        
            except socket.error as e:
                # Ignore non-blocking errors
                if e.errno not in [11, 35, 10035]:
                    print(f"Socket error in main loop: {e}")
                    running = False
            except Exception as e:
                print(f"Error in main loop: {e}")
                time.sleep(0.001) # Avoid busy-spin on other errors
                running = False

            if not running:
                break
            
            # 4. Handle any RTOs that may have expired
            self.handle_timeouts()
            
            # 5. Send new data if cwnd allows
            self.send_new_data()

            # 6. Check for client inactivity
            # *** MISTAKE 2 (FIX) ***
            # Changed timeout from 1e2 (100s) to 15.0s
            if time.time() - self.last_ack_time > 15.0:
                print("Client timed out (15s). Shutting down.")
                running = False
        # --- End Optimization Loop ---


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

        if self.cwnd_log_file:
            self.cwnd_log_file.close()
            print(f"CWND log saved to {self.log_filename}")
            self.plot_cwnd(self.log_filename)
        
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