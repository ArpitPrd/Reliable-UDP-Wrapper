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
# - Reserved (10 bytes): Padding to 20 bytes
HEADER_FORMAT = "!IIH10x"
HEADER_SIZE = 20
MSS_BYTES = 1200  # Max Segment Size (matches assignment)
PAYLOAD_SIZE = MSS_BYTES - HEADER_SIZE  # 1180 bytes

# --- Flags ---
SYN_FLAG = 0x1
ACK_FLAG = 0x2
EOF_FLAG = 0x4

# --- Receive Window ---
# Max size of out-of-order buffer (in packets)
MAX_RECV_WINDOW_PACKETS = 200

class Client:
    def __init__(self, server_ip, server_port, output_filename):
        self.server_addr = (server_ip, int(server_port))
        self.output_filename = output_filename
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.settimeout(5.0)  # 5 second timeout for recv
        
        self.output_file = None
        self.start_time = 0

        # --- Receiver State ---
        self.next_expected_seq_num = 0
        
        # --- Receive Buffer ---
        # {seq_num: data} for out-of-order packets
        self.receive_buffer = {}
        
        print(f"Client ready to connect to {server_ip}:{server_port}")
        print(f"Will save to: {output_filename}")

    def pack_header(self, seq_num, ack_num, flags):
        """Packs the header into bytes."""
        return struct.pack(HEADER_FORMAT, seq_num, ack_num, flags)

    def unpack_header(self, packet):
        """Unpacks the header from bytes."""
        try:
            return struct.unpack(HEADER_FORMAT, packet[:HEADER_SIZE])
        except struct.error:
            return None, None, None, None

    def send_request(self):
        """Sends the initial 1-byte file request."""
        request_packet = b'\x01' # 1-byte request
        retries = 5
        for i in range(retries):
            try:
                print(f"Sending file request (Attempt {i+1}/{retries})...")
                self.socket.sendto(request_packet, self.server_addr)
                # Wait for the first data packet as confirmation
                packet, _ = self.socket.recvfrom(MSS_BYTES)
                print("Received first packet, connection established.")
                return packet  # Return the first packet
            except socket.timeout:
                print("Request timed out.")
                continue
            except Exception as e:
                print(f"Error sending request: {e}")
                return None
        print("Failed to connect to server after 5 attempts.")
        return None

    def prepare_ack(self, ack_num, flags=ACK_FLAG):
        """Prepares and sends an ACK packet."""
        # rwnd = self.update_rwnd()
        # For this assignment, rwnd isn't used by the server, so we send 0
        header = self.pack_header(0, ack_num, flags)
        try:
            self.socket.sendto(header, self.server_addr)
            # print(f"Sent ACK for: {ack_num}")
        except Exception as e:
            print(f"Error sending ACK {ack_num}: {e}")

    def write_to_txt(self, data):
        """Writes data to the output file."""
        try:
            self.output_file.write(data)
        except Exception as e:
            print(f"Error writing to file: {e}")

    def update_rwnd(self):
        """Calculates the available receive window (in packets)."""
        # This isn't strictly needed for Part 2, but good practice.
        return max(0, MAX_RECV_WINDOW_PACKETS - len(self.receive_buffer))

    def process_packet(self, packet):
        """Processes an incoming data packet."""
        header_data = self.unpack_header(packet)
        if header_data is None:
            return "CONTINUE" # Bad packet
            
        seq_num, ack_num, flags = header_data
        data = packet[HEADER_SIZE:]
        
        # --- Check for EOF ---
        if flags & EOF_FLAG:
            print(f"Received EOF with Seq={seq_num}")
            # If it's the one we expect
            if seq_num == self.next_expected_seq_num:
                # Send final ACK (seq_num + 1)
                self.prepare_ack(seq_num + 1, flags=ACK_FLAG | EOF_FLAG)
                return "DONE"
            else:
                # Got EOF out of order. Store it.
                if seq_num > self.next_expected_seq_num and seq_num not in self.receive_buffer:
                    self.receive_buffer[seq_num] = (data, flags)
                # Send ACK for what we are still missing
                self.prepare_ack(self.next_expected_seq_num)
                return "CONTINUE"

        # --- Process Data Packet ---
        
        # 1. Got the packet we expected
        if seq_num == self.next_expected_seq_num:
            # print(f"Received expected packet: Seq={seq_num}")
            self.write_to_txt(data)
            self.next_expected_seq_num += len(data)
            
            # Check buffer for contiguous packets
            while self.next_expected_seq_num in self.receive_buffer:
                buffered_data, buffered_flags = self.receive_buffer.pop(self.next_expected_seq_num)
                
                if buffered_flags & EOF_FLAG:
                    print("Processing buffered EOF.")
                    self.prepare_ack(self.next_expected_seq_num + 1, flags=ACK_FLAG | EOF_FLAG)
                    return "DONE"
                
                # print(f"Processing buffered packet: Seq={self.next_expected_seq_num}")
                self.write_to_txt(buffered_data)
                self.next_expected_seq_num += len(buffered_data)
            
            # Send cumulative ACK for the new next_expected
            self.prepare_ack(self.next_expected_seq_num)

        # 2. Got a packet from the future (out-of-order)
        elif seq_num > self.next_expected_seq_num:
            # print(f"Received out-of-order: Seq={seq_num} (Expected={self.next_expected_seq_num})")
            if seq_num not in self.receive_buffer:
                 # Check if buffer is full
                if len(self.receive_buffer) < MAX_RECV_WINDOW_PACKETS:
                    self.receive_buffer[seq_num] = (data, flags)
                else:
                    print("Receive buffer full, dropping packet.")
            
            # Send duplicate ACK for the packet we're still waiting for
            self.prepare_ack(self.next_expected_seq_num)

        # 3. Got a packet from the past (already ACKed)
        else: # seq_num < self.next_expected_seq_num
            # print(f"Received duplicate packet: Seq={seq_num}")
            # Resend ACK for what we've already received
            self.prepare_ack(self.next_expected_seq_num)

        return "CONTINUE"


    def run(self):
        """Main client loop."""
        
        # 1. Send request and get first packet
        first_packet = self.send_request()
        if first_packet is None:
            self.socket.close()
            return
            
        self.start_time = time.time()
        
        # 2. Open output file
        try:
            self.output_file = open(self.output_filename, 'wb')
        except IOError as e:
            print(f"Error opening output file {self.output_filename}: {e}")
            self.socket.close()
            return

        # 3. Process the first packet
        if self.process_packet(first_packet) == "DONE":
            self.cleanup()
            return
            
        # 4. Main receive loop
        running = True
        while running:
            try:
                packet, _ = self.socket.recvfrom(MSS_BYTES)
                if self.process_packet(packet) == "DONE":
                    running = False
                    
            except socket.timeout:
                print("Server timed out. Closing connection.")
                running = False
            except Exception as e:
                print(f"Receive loop error: {e}")
                running = False
        
        # 5. Cleanup
        self.cleanup()

    def cleanup(self):
        """Closes file and socket."""
        end_time = time.time()
        duration = end_time - self.start_time
        
        if self.output_file:
            self.output_file.close()
            file_size = os.path.getsize(self.output_filename)
            print("---------------------------------")
            print("File download complete.")
            print(f"Duration: {duration:.2f} seconds")
            print(f"Saved to: {self.output_filename}")
            print(f"File size: {file_size} bytes")
            if duration > 0:
                throughput = (file_size * 8) / (duration * 1_000_000) # Mbps
                print(f"Avg. Throughput: {throughput:.2f} Mbps")
            print("---------------------------------")
            
        self.socket.close()
        print("Client shut down.")

def main():
    if len(sys.argv) != 4:
        print("Usage: python3 p2_client.py <SERVER_IP> <SERVER_PORT> <PREF_FILENAME>")
        sys.exit(1)
    
    server_ip = sys.argv[1]
    server_port = int(sys.argv[2])
    pref_filename = sys.argv[3]
    
    output_filename = f"{pref_filename}received_data.txt"
    
    client = Client(server_ip, server_port, output_filename)
    client.run()

if __name__ == "__main__":
    main()
