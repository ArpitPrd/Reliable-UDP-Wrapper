#!/usr/bin/env python3
"""
Local testing script for Part 2 implementation
Tests congestion control without Mininet
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

def test_single_flow():
    """Test single flow transfer"""
    SERVER_IP = "127.0.0.1"
    SERVER_PORT = 6555
    PREFIX = "test_"

    print("=" * 60)
    print("Local Test: Congestion Control - Single Flow")
    print("=" * 60)

    # Clean up old received file
    output_file = f"{PREFIX}received_data.txt"
    if os.path.exists(output_file):
        os.remove(output_file)

    # Start server in background
    print("\n[1] Starting server...")
    server_process = subprocess.Popen(
        [sys.executable, "p2_server.py", SERVER_IP, str(SERVER_PORT)],
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
        [sys.executable, "p2_client.py", SERVER_IP, str(SERVER_PORT), PREFIX],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    # Wait for client to finish
    client_output, client_error = client_process.communicate(timeout=120)
    end_time = time.time()

    # Wait for server to finish
    try:
        server_output, server_error = server_process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        server_process.terminate()
        server_output, server_error = server_process.communicate()

    duration = end_time - start_time

    print("\n" + "=" * 60)
    print("Test Results")
    print("=" * 60)

    # Check if file was received
    if os.path.exists(output_file):
        original_md5 = compute_md5("data.txt")
        received_md5 = compute_md5(output_file)

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
        print(f"\n✗ FAILURE: {output_file} not created!")

    # Print server output for debugging
    if server_output:
        print("\n" + "-" * 60)
        print("Server output:")
        print("-" * 60)
        print(server_output)

    # Print client output
    if client_output:
        print("\n" + "-" * 60)
        print("Client output:")
        print("-" * 60)
        print(client_output)

def test_dual_flow():
    """Test two concurrent flows (basic fairness test)"""
    SERVER_IP1 = "127.0.0.1"
    SERVER_PORT1 = 6555
    SERVER_PORT2 = 6556
    PREFIX1 = "flow1_"
    PREFIX2 = "flow2_"

    print("\n" + "=" * 60)
    print("Local Test: Congestion Control - Dual Flow (Fairness)")
    print("=" * 60)

    # Clean up old files
    for prefix in [PREFIX1, PREFIX2]:
        output_file = f"{prefix}received_data.txt"
        if os.path.exists(output_file):
            os.remove(output_file)

    # Start servers
    print("\n[1] Starting two servers...")
    server1 = subprocess.Popen(
        [sys.executable, "p2_server.py", SERVER_IP1, str(SERVER_PORT1)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    server2 = subprocess.Popen(
        [sys.executable, "p2_server.py", SERVER_IP1, str(SERVER_PORT2)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    time.sleep(1)

    # Start clients
    print("[2] Starting two clients...")
    start_time = time.time()

    client1 = subprocess.Popen(
        [sys.executable, "p2_client.py", SERVER_IP1, str(SERVER_PORT1), PREFIX1],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    # Start second client slightly after first (to avoid perfect synchronization)
    time.sleep(0.1)

    client2 = subprocess.Popen(
        [sys.executable, "p2_client.py", SERVER_IP1, str(SERVER_PORT2), PREFIX2],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    # Wait for both clients
    client1.wait(timeout=180)
    end_time1 = time.time()

    client2.wait(timeout=180)
    end_time2 = time.time()

    # Clean up servers
    for server in [server1, server2]:
        try:
            server.terminate()
            server.wait(timeout=2)
        except:
            pass

    duration1 = end_time1 - start_time
    duration2 = end_time2 - start_time

    print("\n" + "=" * 60)
    print("Fairness Test Results")
    print("=" * 60)

    # Check both files
    original_md5 = compute_md5("data.txt")

    for i, prefix in enumerate([PREFIX1, PREFIX2], 1):
        output_file = f"{prefix}received_data.txt"
        if os.path.exists(output_file):
            received_md5 = compute_md5(output_file)
            match = "✓" if received_md5 == original_md5 else "✗"
            print(f"\nFlow {i}: {match} MD5 {'match' if received_md5 == original_md5 else 'MISMATCH'}")
        else:
            print(f"\nFlow {i}: ✗ File not created")

    # Calculate fairness
    print(f"\nFlow 1 time: {duration1:.2f}s")
    print(f"Flow 2 time: {duration2:.2f}s")

    # Jain's Fairness Index
    if duration1 > 0 and duration2 > 0:
        alloc1 = 1.0 / duration1
        alloc2 = 1.0 / duration2
        jfi = (alloc1 + alloc2) ** 2 / (2 * (alloc1**2 + alloc2**2))
        print(f"\nJain's Fairness Index: {jfi:.3f}")
        print(f"(1.0 = perfect fairness, closer to 1.0 is better)")

if __name__ == "__main__":
    print("Testing Part 2: Congestion Control\n")

    print("Test 1: Single Flow")
    test_single_flow()

    print("\n" + "=" * 60)
    print("\nTest 2: Dual Flow (Fairness)")
    test_dual_flow()

    print("\n" + "=" * 60)
    print("\nAll local tests complete!")
    print("If both tests passed, your implementation is ready for Mininet experiments.")
