#!/usr/bin/env python3
"""
Local testing script for Part 1 implementation
Tests the reliability protocol without Mininet
"""

import subprocess
import time
import hashlib
import os
import sys

def compute_md5(file_path):
    """Compute MD5 hash of a file"""
    hasher = hashlib.md5()
    try:
        with open(file_path, 'rb') as f:
            while chunk := f.read(8192):
                hasher.update(chunk)
        return hasher.hexdigest()
    except FileNotFoundError:
        return None

def test_transfer():
    """Test file transfer locally"""
    SERVER_IP = "127.0.0.1"
    SERVER_PORT = 6555
    SWS = 5 * 1180  # 5 packets worth of window

    print("=" * 60)
    print("Local Test: Reliable UDP File Transfer")
    print("=" * 60)

    # Clean up old received file
    if os.path.exists("received_data.txt"):
        os.remove("received_data.txt")

    # Start server in background
    print("\n[1] Starting server...")
    server_process = subprocess.Popen(
        [sys.executable, "p1_server.py", SERVER_IP, str(SERVER_PORT), str(SWS)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    # Give server time to start
    time.sleep(1)

    # Start client
    print("[2] Starting client...")
    start_time = time.time()

    client_process = subprocess.Popen(
        [sys.executable, "p1_client.py", SERVER_IP, str(SERVER_PORT)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    # Wait for client to finish
    client_output, client_error = client_process.communicate(timeout=120)
    end_time = time.time()

    # Wait for server to finish
    try:
        server_process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        server_process.terminate()

    duration = end_time - start_time

    print("\n" + "=" * 60)
    print("Test Results")
    print("=" * 60)

    # Check if file was received
    if os.path.exists("received_data.txt"):
        original_md5 = compute_md5("data.txt")
        received_md5 = compute_md5("received_data.txt")

        print(f"\nOriginal file MD5:  {original_md5}")
        print(f"Received file MD5:  {received_md5}")
        print(f"Transfer time:      {duration:.2f} seconds")

        if original_md5 == received_md5:
            print("\n✓ SUCCESS: File transferred correctly!")

            # Calculate throughput
            file_size = os.path.getsize("data.txt")
            throughput_mbps = (file_size * 8) / (duration * 1e6)
            print(f"File size:          {file_size:,} bytes ({file_size/1024/1024:.2f} MB)")
            print(f"Throughput:         {throughput_mbps:.2f} Mbps")
        else:
            print("\n✗ FAILURE: File corrupted during transfer!")
    else:
        print("\n✗ FAILURE: received_data.txt not created!")

    # Print client output for debugging
    if client_output:
        print("\n" + "-" * 60)
        print("Client output:")
        print("-" * 60)
        print(client_output)

    if client_error:
        print("\n" + "-" * 60)
        print("Client errors:")
        print("-" * 60)
        print(client_error)

if __name__ == "__main__":
    test_transfer()
