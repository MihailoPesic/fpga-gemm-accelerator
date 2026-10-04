"""Control and inspect the DDR diagnostic; this is not a GEMM benchmark."""
import hashlib
import json
from pathlib import Path
import secrets
import struct
import time

from host.preview.protocol import DeviceError, Link, ProtocolError


ID, VERSION = 0x3144474E, 0x00000100
READY, BUSY, DONE, ERROR, DDR_READY, RESET_REQUIRED = (1 << i for i in range(6))
EXPECTED_READ_BEATS, EXPECTED_WRITE_BEATS = 1024, 648


def load_manifest(path):
    path = Path(path).resolve()
    manifest = json.loads(path.read_text(encoding="utf-8"))
    required = {"schema_version", "build_id", "bitstream", "bitstream_sha256", "core_hz", "baud"}
    if missing := required - manifest.keys():
        raise ValueError(f"build manifest missing {sorted(missing)}")
    if manifest["schema_version"] != 1 or not 0 <= manifest["build_id"] < (1 << 32):
        raise ValueError("invalid build manifest identity")
    if not 0 < manifest["core_hz"] < (1 << 32) or manifest["baud"] not in (115200, 1_000_000):
        raise ValueError("unsupported DDR diagnostic clock or baud")
    digest = hashlib.sha256((path.parent / manifest["bitstream"]).read_bytes()).hexdigest()
    if digest != manifest["bitstream_sha256"].lower():
        raise ValueError("selected bitstream SHA-256 differs from its build manifest")
    return manifest


class DDRDiagnostic:
    def __init__(self, link):
        self.link = link
        self.identity = None

    @classmethod
    def open(cls, port, baud, timeout=2.0, retries=2):
        import serial

        device = serial.Serial(port, baud, timeout=min(timeout, 0.05), write_timeout=timeout)
        device.reset_input_buffer()
        device.reset_output_buffer()
        return cls(Link(device, timeout=timeout, retries=retries))

    def close(self):
        self.link.serial.close()

    def _request(self, opcode, payload, length):
        response = self.link.request(opcode, payload)
        if len(response) != length:
            self.link.poisoned = True
            raise ProtocolError(f"opcode {opcode}: expected {length} response bytes, got {len(response)}")
        return response

    def read_reg(self, address):
        if address & 3 or not 0 <= address <= 0xFFFF:
            raise ValueError("register address must be aligned and fit u16")
        return struct.unpack("<I", self._request(2, struct.pack("<H", address), 4))[0]

    def write_reg(self, address, value):
        if address & 3 or not 0 <= address <= 0xFFFF or not 0 <= value < (1 << 32):
            raise ValueError("aligned u16 address and u32 value required")
        self._request(3, struct.pack("<HI", address, value), 0)

    def identify(self, manifest):
        self.identity = None
        nonce = secrets.randbits(32)
        try:
            reply = self._request(1, struct.pack("<I", nonce), 12)
        except DeviceError as exc:
            if exc.status != 6:
                raise
            reply = self._request(1, struct.pack("<I", nonce), 12)
        if struct.unpack("<III", reply) != (nonce, ID, VERSION):
            raise RuntimeError("connected image is not the expected DDR diagnostic")
        build_id, core_hz = self.read_reg(0x50), self.read_reg(0x48)
        if (build_id, core_hz) != (manifest["build_id"], manifest["core_hz"]):
            raise RuntimeError("DDR diagnostic BUILD_ID or clock differs from the manifest")
        self.identity = {"id": ID, "version": VERSION, "build_id": build_id, "core_hz": core_hz}
        return self.identity.copy()

    def snapshot(self):
        status = self.read_reg(0x0C)
        result = {"status": status, "error_code": self.read_reg(0x40)}
        # The engine freezes diagnostics at the first error even if it must
        # remain busy while outstanding AXI obligations drain.
        if status & BUSY and not status & ERROR:
            result["diagnostic_words_available"] = False
            return result
        result["first_fail_addr"] = self.read_reg(0x60)
        for name, address in (("expected", 0x68), ("actual", 0x70), ("cycles", 0x80),
                              ("read_beats", 0x90), ("write_beats", 0x98)):
            result[name] = self.read_reg(address) | (self.read_reg(address + 4) << 32)
        result["diagnostic_words_available"] = True
        return result

    def wait_ready(self, timeout=20.0):
        if self.identity is None:
            raise RuntimeError("identify the currently loaded bitstream first")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = self.read_reg(0x0C)
            if status & (ERROR | RESET_REQUIRED):
                raise RuntimeError(f"DDR diagnostic requires platform reset: {self.snapshot()}")
            if status & READY and status & DDR_READY and not status & BUSY:
                return
            time.sleep(0.01)
        raise TimeoutError("DDR calibration/idle readiness was not reached")

    def run(self, seed, timeout=30.0):
        if self.identity is None:
            raise RuntimeError("identify the currently loaded bitstream first")
        self.wait_ready(timeout)
        self.write_reg(0x14, seed)
        started = time.perf_counter_ns()
        self.write_reg(0x10, 1)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = self.read_reg(0x0C)
            if status & (ERROR | RESET_REQUIRED) or status & DONE and not status & BUSY:
                finished = time.perf_counter_ns()
                result = {"seed": seed, "wall_seconds": (finished-started)/1e9, **self.snapshot()}
                final_status = result["status"]
                result["passed"] = bool(final_status & DONE and not final_status & (BUSY | ERROR | RESET_REQUIRED))
                if result["passed"]:
                    if result["error_code"]:
                        result["passed"] = False
                        result["host_error"] = "completed diagnostic has a nonzero error code"
                    elif result["read_beats"] != EXPECTED_READ_BEATS or result["write_beats"] != EXPECTED_WRITE_BEATS:
                        result["passed"] = False
                        result["host_error"] = "completed diagnostic has unexpected transfer counts"
                    elif not result["cycles"]:
                        result["passed"] = False
                        result["host_error"] = "completed diagnostic has a zero cycle count"
                return result
            time.sleep(0.005)
        raise TimeoutError("DDR diagnostic did not finish before the host deadline")
