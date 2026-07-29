#!/usr/bin/env python3
"""
Stage 3 test: round-trip all 256 byte values through the FPGA's UART echo.

All 256 values matter. An ASCII-only test passes on designs that mishandle
0x00 and 0xFF, and misses reversed bit ordering entirely.

    pip install pyserial
    python echo_test.py            # auto-detect port
    python echo_test.py COM7       # or /dev/ttyUSB1 on Linux
"""

import sys
import time

import serial
import serial.tools.list_ports

BAUD = 921600


def find_port():
    """Pick the Digilent/FTDI port. The Nexys A7 enumerates two channels;
    the UART is the second one."""
    candidates = []
    for p in serial.tools.list_ports.comports():
        blob = f"{p.description} {p.manufacturer} {p.hwid}".lower()
        if any(k in blob for k in ("ftdi", "digilent", "usb serial", "ft2232")):
            candidates.append(p.device)
    return candidates


def main():
    if len(sys.argv) > 1:
        port = sys.argv[1]
    else:
        found = find_port()
        if not found:
            print("No FTDI/Digilent port found. Ports seen:")
            for p in serial.tools.list_ports.comports():
                print(f"  {p.device}  {p.description}")
            print("\nPass the port explicitly: python echo_test.py COM7")
            return 1
        port = found[-1]  # UART is the second FTDI channel
        print(f"Ports found: {found}  -> using {port}")

    print(f"Opening {port} at {BAUD} baud")
    with serial.Serial(port, BAUD, timeout=2) as s:
        s.reset_input_buffer()
        s.reset_output_buffer()

        tx = bytes(range(256))
        t0 = time.perf_counter()
        s.write(tx)
        rx = s.read(256)
        dt = time.perf_counter() - t0

        print(f"sent {len(tx)}, received {len(rx)}, {dt*1000:.1f} ms")

        if len(rx) != 256:
            print(f"FAIL: short read -- got {len(rx)} of 256 bytes")
            if rx:
                print(f"  last byte received: 0x{rx[-1]:02x}")
                print("  bytes dropping mid-stream usually means the echo path "
                      "cannot keep up, or the baud rate is marginal")
            else:
                print("  nothing came back. Check:")
                print("   - is the design programmed? (heartbeat LED[1] blinking)")
                print("   - LED[14] lit = framing error = baud mismatch")
                print("   - TX/RX swapped: UART_TXD_IN is C4, UART_RXD_OUT is D4")
            return 1

        if rx == tx:
            print("PASS -- all 256 byte values round-tripped exactly")
            eff = 256 * 10 / dt
            print(f"effective rate {eff/1000:.1f} kbaud (link is {BAUD})")
            return 0

        bad = [(i, a, b) for i, (a, b) in enumerate(zip(tx, rx)) if a != b]
        print(f"FAIL: {len(bad)} mismatched byte(s)")
        for i, a, b in bad[:8]:
            print(f"  index {i:3d}: sent 0x{a:02x}  got 0x{b:02x}"
                  f"   (bitwise reversed: 0x{int(f'{a:08b}'[::-1], 2):02x})")
        if all(b == int(f"{a:08b}"[::-1], 2) for _, a, b in bad):
            print("  every byte is bit-reversed -> shift direction is wrong")
        return 1


if __name__ == "__main__":
    sys.exit(main())
