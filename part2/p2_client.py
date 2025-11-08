import socket
import sys
import time
import struct
import os
import heapq

HEADER_FORMAT = "!IIHII2x"
SIZE_OF_HEADER = 20
MSS_BYTES = 1200  
PAYLOAD_SIZE = MSS_BYTES - SIZE_OF_HEADER  

# Flags 
SYN_FLAG = 0x1
ACK_FLAG = 0x2
EOF_FLAG = 0x4

# Receive Window
MAX_RECV_WINDOW_PACKETS = 2000

class Client:
    def __init__(self, server_ip, server_port, output_filename):
        self.server_addr = (server_ip, int(server_port))
        self.output_filename = output_filename
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.settimeout(30.0)
        
        self.output_file = None
        self.start_time = 0

        # Receiver State
        self.next_expected_seq_num = 0
        
        # Receive Buffer
        self.receive_buffer = []
        self.receive_buffer_seqs = set()
        
        print(f"Client ready to connect to {server_ip}:{server_port}")
        print(f"Will save the incoming file to: {output_filename}")

    def prepare_header(self, seq_num, ack_num, flags, sack_start=0, sack_end=0):
        """
        provides the packed header
        """
        return struct.pack(HEADER_FORMAT, seq_num, ack_num, flags, sack_start, sack_end)

    def read_header(self, packet):
        """
        black box to upack your header
        """
        try:
            seq_num, ack_num, flags, sack_start, sack_end = struct.unpack(HEADER_FORMAT, packet[:SIZE_OF_HEADER])
            data = packet[SIZE_OF_HEADER:]
            return seq_num, ack_num, flags, sack_start, sack_end, data
        except struct.error:
            return None, None, None, None, None, None

    def send_small_bit_request(self):
        """
        use this to send request, will provide only 5 tries
        """
        sample_request_packet = b'\x01'
        retries = 5
        for i in range(retries):
            try:
                print(f"Requesting File (Attempt Number {i+1}/{retries})...")
                self.socket.sendto(sample_request_packet, self.server_addr)
                packet, _ = self.socket.recvfrom(MSS_BYTES)
                print("Yahoo! Received first packet, connection established.")
                return packet  
            except socket.timeout:
                print("Request timed out.")
                continue
            except Exception as e:
                print(f"Error sending request: {e}")
                return None
        print("Failed to connect to server after 5 attempts.")
        return None

    def prepare_ack(self, ack_num, flags=ACK_FLAG, sack_start=0, sack_end=0):
        """
        Use this when want to prepare an ACK
        """
        header_of_packet = self.prepare_header(0, ack_num, flags, sack_start, sack_end)
        try:
            self.socket.sendto(header_of_packet, self.server_addr)
        except Exception as e:
            print(f"Error sending ACK {ack_num}: {e}")

    def write_to_txt(self, data):
        try:
            self.output_file.write(data)
        except Exception as e:
            print(f"Error writing to file: {e}")

    def update_rwnd(self):
        return max(0, MAX_RECV_WINDOW_PACKETS - len(self.receive_buffer))

    def get_first_sack_block_unit(self):
        if not self.receive_buffer:
            return 0, 0
        
        try:
            seq_num, data, flags = self.receive_buffer[0]
            return seq_num, seq_num + len(data)
        except (ValueError, KeyError, IndexError):
             return 0, 0

    def process_packet(self, packet):
        seq_num, ack_num, flags, sack_start, sack_end, data = self.read_header(packet)
        print(f"Received seq#={seq_num}, sending ack for={self.next_expected_seq_num}")
        if seq_num is None:
            return "CONTINUE" # Bad packet
        
        # Check for EOF
        if flags & EOF_FLAG:
            if seq_num == self.next_expected_seq_num:
                self.prepare_ack(seq_num, flags=ACK_FLAG | EOF_FLAG)
                print("Received EOF, sending the final ACK.")
                return "DONE"
            else:
                if seq_num > self.next_expected_seq_num and seq_num not in self.receive_buffer_seqs:
                    heapq.heappush(self.receive_buffer, (seq_num, data, flags))
                    self.receive_buffer_seqs.add(seq_num)
                
                sack_start_block, sack_end_block = self.get_first_sack_block_unit()
                self.prepare_ack(self.next_expected_seq_num, sack_start=sack_start_block, sack_end=sack_end_block)
                return "CONTINUE"

        # Process Data Packet
        
        # 1. Got the packet we expected
        if seq_num == self.next_expected_seq_num:
            self.write_to_txt(data)
            self.next_expected_seq_num += len(data)
            
            while self.receive_buffer and self.receive_buffer[0][0] == self.next_expected_seq_num:
                buffered_seq_num, buffered_data, buffered_flags = heapq.heappop(self.receive_buffer)
                self.receive_buffer_seqs.remove(buffered_seq_num)
                
                if buffered_flags & EOF_FLAG:
                    print("Processing buffered EOF.")
                    self.prepare_ack(self.next_expected_seq_num + 1, flags=ACK_FLAG | EOF_FLAG)
                    return "DONE"
                
                self.write_to_txt(buffered_data)
                self.next_expected_seq_num += len(buffered_data)
            
            sack_start_block, sack_end_block = self.get_first_sack_block_unit()
            self.prepare_ack(self.next_expected_seq_num, sack_start=sack_start_block, sack_end=sack_end_block)

        # 2. Got a packet from the future (out-of-order)
        elif seq_num > self.next_expected_seq_num:
            if seq_num not in self.receive_buffer_seqs:
                if len(self.receive_buffer) < MAX_RECV_WINDOW_PACKETS:
                    heapq.heappush(self.receive_buffer, (seq_num, data, flags))
                    self.receive_buffer_seqs.add(seq_num)
                else:
                    print("Receive buffer full, dropping packet.")
            
            self.prepare_ack(self.next_expected_seq_num, sack_start=seq_num, sack_end=seq_num + len(data))

        # 3. Got a packet from the past (already ACKed)
        else:
            sack_start_block, sack_end_block = self.get_first_sack_block_unit()
            self.prepare_ack(self.next_expected_seq_num, sack_start=sack_start_block, sack_end=sack_end_block)

        return "CONTINUE"


    def run(self):
        """Main client loop."""
        
        # 1. Send request and get first packet
        first_packet = self.send_small_bit_request()
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
                print("Server timed out. Will have to Closing connection.")
                running = False
            except Exception as e:
                print(f"Receive loop error: {e}")
                running = False
        
        # 5. Cleanup
        self.cleanup()

    def cleanup(self):
        end_time = time.time()
        duration = end_time - self.start_time
        if duration == 0: duration = 1e-6
        
        if self.output_file:
            self.output_file.close()
            file_size = 0
            try:
                file_size = os.path.getsize(self.output_filename)
            except os.error as e:
                print(f"Could not get file size: {e}")

            print("---------------------------------")
            print("File download complete.")
            if duration > 0.001:
                print(f"Duration: {duration:.2f} seconds")
            print(f"Saved to: {self.output_filename}")
            print(f"File size: {file_size} bytes")
            if duration > 0.001:
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