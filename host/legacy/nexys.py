#!/usr/bin/env python3
"""Host driver for the Nexys A7 DDR2 accelerator.

Protocol (big-endian):
    0x01 PING                      -> b'NXA7'
    0x02 WRITE  addr[4] data[16]   -> b'\\x5a'
    0x03 READ   addr[4]            -> 16 bytes

`addr` is an app_addr value, NOT a byte address. One 128-bit transaction covers
16 bytes = 8 app_addr units, so consecutive words are 8 apart. Use
Nexys.word_addr(index) rather than doing that arithmetic by hand.
"""

import struct
import sys

import serial
import serial.tools.list_ports

BAUD = 921600

OP_PING = 0x01
OP_WRITE = 0x02
OP_READ = 0x03

ACK = 0x5A
ERR_NOT_CALIB = 0xEE

ADDR_STRIDE = 8          # app_addr units per 128-bit word


def find_port():
    hits = []
    for p in serial.tools.list_ports.comports():
        blob = f"{p.description} {p.manufacturer} {p.hwid}".lower()
        if any(k in blob for k in ("ftdi", "digilent", "usb serial", "ft2232")):
            hits.append(p.device)
    return hits


class Nexys:
    def __init__(self, port=None, timeout=2.0):
        if port is None:
            hits = find_port()
            if not hits:
                raise RuntimeError(
                    "No FTDI/Digilent port found. Available: "
                    + ", ".join(p.device for p in serial.tools.list_ports.comports())
                )
            port = hits[-1]          # UART is the second FTDI channel
        self.port = port
        self.s = serial.Serial(port, BAUD, timeout=timeout)
        self.s.reset_input_buffer()
        self.s.reset_output_buffer()

    def close(self):
        self.s.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    # -- primitives ---------------------------------------------------------

    def _expect(self, n, what):
        got = self.s.read(n)
        if len(got) != n:
            raise TimeoutError(f"{what}: expected {n} bytes, got {len(got)}: {got!r}")
        return got

    @staticmethod
    def word_addr(index):
        """app_addr for the index'th 128-bit word."""
        return index * ADDR_STRIDE

    def ping(self):
        self.s.write(bytes([OP_PING]))
        r = self._expect(4, "ping")
        if r != b"NXA7":
            raise RuntimeError(f"bad ping reply {r!r} (expected b'NXA7')")
        return r

    def write_word(self, addr, data16):
        if len(data16) != 16:
            raise ValueError(f"need exactly 16 bytes, got {len(data16)}")
        self.s.write(bytes([OP_WRITE]) + struct.pack(">I", addr) + bytes(data16))
        r = self._expect(1, "write")[0]
        if r == ERR_NOT_CALIB:
            raise RuntimeError("FPGA reports DDR2 not calibrated")
        if r != ACK:
            raise RuntimeError(f"bad write ack 0x{r:02x}")

    def read_word(self, addr):
        self.s.write(bytes([OP_READ]) + struct.pack(">I", addr))
        r = self._expect(16, "read")
        if len(r) == 1 and r[0] == ERR_NOT_CALIB:
            raise RuntimeError("FPGA reports DDR2 not calibrated")
        return r


# -- self test --------------------------------------------------------------

def main():
    port = sys.argv[1] if len(sys.argv) > 1 else None

    with Nexys(port) as d:
        print(f"port {d.port} @ {BAUD}")

        print("PING ...", end=" ")
        print(d.ping().decode())

        # 1. round-trip one word
        pattern = bytes(range(16))
        d.write_word(d.word_addr(0), pattern)
        got = d.read_word(d.word_addr(0))
        print(f"word 0 write {pattern.hex()}")
        print(f"word 0 read  {got.hex()}", "OK" if got == pattern else "MISMATCH")
        if got != pattern:
            return 1

        # 2. distinct data in adjacent words -- this is the real stride test.
        #    A wrong stride makes word 1 overwrite word 0, or land 16 units away
        #    leaving a hole. Either shows up here and nowhere else.
        words = {i: bytes([(i * 16 + k) & 0xFF for k in range(16)]) for i in range(8)}
        for i, w in words.items():
            d.write_word(d.word_addr(i), w)
        bad = 0
        for i, w in words.items():
            got = d.read_word(d.word_addr(i))
            if got != w:
                print(f"  word {i}: wrote {w.hex()}  read {got.hex()}")
                bad += 1
        print(f"8 adjacent words: {8 - bad}/8 correct")
        if bad:
            print("  -> stride wrong, or writes aliasing. Expected stride is 8.")
            return 1

        # 3. spread across banks and rows, well beyond one 16 KiB region
        spread = [0, 1, 1023, 1024, 65535, 65536, 1 << 20]
        bad = 0
        for i in spread:
            w = struct.pack(">IIII", i, ~i & 0xFFFFFFFF, 0xA5A5A5A5, i * 3 & 0xFFFFFFFF)
            d.write_word(d.word_addr(i), w)
        for i in spread:
            w = struct.pack(">IIII", i, ~i & 0xFFFFFFFF, 0xA5A5A5A5, i * 3 & 0xFFFFFFFF)
            got = d.read_word(d.word_addr(i))
            if got != w:
                print(f"  index {i}: wrote {w.hex()}  read {got.hex()}")
                bad += 1
        print(f"scattered words: {len(spread) - bad}/{len(spread)} correct")
        if bad:
            return 1

        print("\nPASS -- DDR2 read/write verified through the full path")
        return 0


if __name__ == "__main__":
    sys.exit(main())
