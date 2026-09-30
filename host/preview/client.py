"""Host API for the P4/T32 BRAM preview, using Python integers as the oracle."""

from dataclasses import dataclass
import hashlib
import json
import operator
from pathlib import Path
import secrets
import struct
import time

from .protocol import DeviceError, Link, ProtocolError


ID = 0x3142474E  # Little-endian bytes N,G,B,1.
VERSION = 0x00000100
GEOMETRY = 0x01002004
A_BASE, BT_BASE, C_BASE = 0x0000, 0x2000, 0x4000
INPUT_STRIDE, OUTPUT_STRIDE = 256, 128
FIXED_REGISTERS = {0x24: A_BASE, 0x28: BT_BASE, 0x2C: C_BASE,
                   0x30: INPUT_STRIDE, 0x34: INPUT_STRIDE, 0x38: OUTPUT_STRIDE}
READY, BUSY, DONE, ERROR, DDR_READY, RESET_REQUIRED = (1 << i for i in range(6))


@dataclass(frozen=True)
class Descriptor:
    m: int
    n: int
    k: int

    def __post_init__(self):
        if any(not isinstance(value, int) for value in (self.m, self.n, self.k)):
            raise ValueError("descriptor dimensions must be integers")
        if not (1 <= self.m <= 32 and 1 <= self.n <= 32 and 1 <= self.k <= 256):
            raise ValueError("preview dimensions require M,N=1..32 and K=1..256")


@dataclass(frozen=True)
class PackedInputs:
    descriptor: Descriptor
    a: tuple
    b: tuple
    a_rows: tuple
    bt_rows: tuple


def _matrix(matrix, name):
    rows = tuple(tuple(operator.index(value) for value in row) for row in matrix)
    if not rows or not rows[0] or any(len(row) != len(rows[0]) for row in rows):
        raise ValueError(f"{name} must be a nonempty rectangular matrix")
    if any(not -128 <= value <= 127 for row in rows for value in row):
        raise ValueError(f"{name} elements must be signed INT8")
    return rows


def pack_inputs(a, b, padding_seed=0):
    """Accept A[M][K], B[K][N]; transpose B and pad each stored row to 8 bytes."""
    a, b = _matrix(a, "A"), _matrix(b, "B")
    if len(a[0]) != len(b):
        raise ValueError("A columns must equal B rows")
    desc = Descriptor(len(a), len(b[0]), len(b))
    padded_k = (desc.k + 7) & ~7

    def pack_row(row, matrix_index, row_index):
        # Nonzero, deterministic padding exercises the hardware K mask.
        padding = bytes(
            1 + (padding_seed + 97 * matrix_index + 29 * row_index + 17 * k) % 255
            for k in range(desc.k, padded_k)
        )
        return bytes(value & 255 for value in row) + padding

    a_rows = tuple(pack_row(row, 0, i) for i, row in enumerate(a))
    bt_rows = tuple(pack_row(tuple(b[k][j] for k in range(desc.k)), 1, j)
                    for j in range(desc.n))
    return PackedInputs(desc, a, b, a_rows, bt_rows)


def golden(a, b):
    """Independent mathematical reference; multiplication uses Python ints."""
    return [[sum(int(a[i][k]) * int(b[k][j]) for k in range(len(b)))
             for j in range(len(b[0]))] for i in range(len(a))]


def validate(actual, a, b):
    expected = golden(a, b)
    if len(actual) != len(expected) or any(len(row) != len(expected[0]) for row in actual):
        raise AssertionError("output shape mismatch")
    for i, row in enumerate(expected):
        for j, value in enumerate(row):
            if not -(1 << 31) <= value < (1 << 31):
                raise AssertionError("oracle result exceeds INT32")
            if actual[i][j] != value:
                raise AssertionError(f"C[{i},{j}]: expected {value}, got {actual[i][j]}")
    return len(expected) * len(expected[0])


def load_manifest(path):
    """Check the selected bitstream file against its saved build manifest."""
    path = Path(path).resolve()
    manifest = json.loads(path.read_text(encoding="utf-8"))
    required = {"schema_version", "build_id", "bitstream", "bitstream_sha256",
                "source_commit", "core_hz", "baud", "part"}
    missing = required - manifest.keys()
    if missing:
        raise ValueError(f"build manifest missing {sorted(missing)}")
    if manifest["schema_version"] != 1:
        raise ValueError("unsupported manifest version")
    if not isinstance(manifest["build_id"], int) or not 0 <= manifest["build_id"] < (1 << 32):
        raise ValueError("invalid manifest build_id")
    if manifest["core_hz"] != 100_000_000 or manifest["baud"] not in (115200, 1_000_000):
        raise ValueError("unsupported preview clock or UART baud setting")
    bitstream = path.parent / manifest["bitstream"]
    digest = hashlib.sha256(bitstream.read_bytes()).hexdigest()
    if digest != manifest["bitstream_sha256"].lower():
        raise ValueError("bitstream SHA-256 does not match build manifest")
    return manifest


class Preview:
    def __init__(self, link):
        self.link = link
        self.identity = None
        self.descriptor = None
        self.loaded_words = set()
        self.completed_job = None
        self.active_job = None

    @classmethod
    def open(cls, port, baud=115200, timeout=2.0, retries=2):
        import serial  # Only hardware access requires pyserial.

        device = serial.Serial(port, baud, timeout=min(timeout, 0.05), write_timeout=timeout)
        device.reset_input_buffer()
        device.reset_output_buffer()
        return cls(Link(device, timeout=timeout, retries=retries))

    def close(self):
        self.link.serial.close()

    def _response(self, opcode, payload=b"", length=None):
        data = self.link.request(opcode, payload)
        if length is not None and len(data) != length:
            self.link.poisoned = True
            raise ProtocolError(f"opcode 0x{opcode:02x}: expected {length} bytes, got {len(data)}")
        return data

    def read_reg(self, address):
        if address & 3 or not 0 <= address < 65536:
            raise ValueError("register address must be an aligned u16")
        return struct.unpack("<I", self._response(2, struct.pack("<H", address), 4))[0]

    def write_reg(self, address, value):
        if address & 3 or not 0 <= address < 65536 or not 0 <= value < (1 << 32):
            raise ValueError("aligned u16 address and u32 value required")
        self._response(3, struct.pack("<HI", address, value), 0)

    def identify(self, manifest):
        """Begin a new software session only when hardware is idle.

        BUILD_ID checks the loaded design's source/configuration identity. The
        manifest hash separately verifies the selected local bitstream file;
        UART does not read back a hash of the FPGA configuration memory.
        """
        self.identity = None
        self.descriptor = None
        self.loaded_words.clear()
        self.completed_job = None
        self.active_job = None
        nonce = secrets.randbits(32)
        try:
            response = self._response(1, struct.pack("<I", nonce), 12)
        except DeviceError as exc:
            if exc.status != 6:
                raise
            # A fresh session may randomly select the previously cached sequence.
            response = self._response(1, struct.pack("<I", nonce), 12)
        echoed, device_id, version = struct.unpack("<III", response)
        if (echoed, device_id, version) != (nonce, ID, VERSION):
            raise RuntimeError("connected device is not the expected BRAM preview")
        geometry = self.read_reg(8)
        build_id = self.read_reg(0x50)
        core_hz = self.read_reg(0x48)
        status = self.read_reg(0x0C)
        if geometry != GEOMETRY or build_id != manifest["build_id"] or core_hz != manifest["core_hz"]:
            raise RuntimeError("hardware geometry, BUILD_ID or clock differs from build manifest")
        if status & (BUSY | DDR_READY | RESET_REQUIRED):
            raise RuntimeError(f"cannot begin preview session: STATUS=0x{status:08x}")
        for address, expected in FIXED_REGISTERS.items():
            if self.read_reg(address) != expected:
                raise RuntimeError(f"preview memory map differs at register 0x{address:02x}")
        self.write_reg(0x10, 2)
        status = self.read_reg(0x0C)
        if not status & READY or status & (BUSY | DONE | ERROR | DDR_READY | RESET_REQUIRED):
            raise RuntimeError(f"preview not ready after CLEAR_STATUS: 0x{status:08x}")
        self.identity = {"id": device_id, "version": version, "geometry": geometry,
                         "build_id": build_id, "core_hz": core_hz}
        return self.identity.copy()

    @staticmethod
    def _memory_bounds(address, length, writing):
        if address & 7 or length < 8 or length > 240 or length & 7:
            raise ValueError("memory commands require aligned 8..240 bytes, multiple of 8")
        if writing:
            if not A_BASE <= address < C_BASE or address + length > C_BASE:
                raise ValueError("MEM_WRITE is restricted to A/BT input memory")
            row_stride = INPUT_STRIDE
        else:
            if not C_BASE <= address < 0x5000 or address + length > 0x5000:
                raise ValueError("MEM_READ is restricted to C output memory")
            row_stride = OUTPUT_STRIDE
        if address // row_stride != (address + length - 1) // row_stride:
            raise ValueError("memory command crosses a local row")

    def write_memory(self, address, data):
        data = bytes(data)
        self._memory_bounds(address, len(data), True)
        self._response(5, struct.pack("<IH", address, len(data)) + data, 0)
        self.loaded_words.update(range(address, address + len(data), 8))

    def read_memory(self, address, length):
        self._memory_bounds(address, length, False)
        return self._response(4, struct.pack("<IH", address, length), length)

    def upload(self, packed):
        if self.identity is None:
            raise RuntimeError("identify the current device before uploading")
        self.loaded_words.clear()
        self.completed_job = None
        for base, rows in ((A_BASE, packed.a_rows), (BT_BASE, packed.bt_rows)):
            for row_index, row in enumerate(rows):
                for offset in range(0, len(row), 240):
                    self.write_memory(base + row_index * INPUT_STRIDE + offset, row[offset:offset + 240])

    def configure(self, descriptor):
        if self.identity is None:
            raise RuntimeError("identify the current device before configuring")
        # Do not keep a stale descriptor if a later register write fails.
        self.descriptor = None
        self.completed_job = None
        for address, value in ((0x18, descriptor.m), (0x1C, descriptor.n), (0x20, descriptor.k),
                               (0x3C, 0)):
            self.write_reg(address, value)
        self.descriptor = descriptor

    def start(self, job_id):
        if self.identity is None or self.descriptor is None:
            raise RuntimeError("identify, upload and configure before START")
        desc = self.descriptor
        required = {base + row * INPUT_STRIDE + k
                    for base, rows in ((A_BASE, desc.m), (BT_BASE, desc.n))
                    for row in range(rows) for k in range(0, desc.k, 8)}
        if not required <= self.loaded_words:
            raise RuntimeError("all required input words must be uploaded in this session")
        self.write_reg(0x14, job_id)
        self.completed_job = None
        self.active_job = None
        self.write_reg(0x10, 1)
        self.active_job = job_id

    def wait(self, job_id, timeout=5.0, poll_interval=0.001):
        if self.active_job != job_id:
            raise RuntimeError("job is not active in this session")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = self.read_reg(0x0C)
            if status & (ERROR | RESET_REQUIRED):
                raise DeviceError(self.read_reg(0x40) or 8, 3)
            if status & DONE:
                if status & BUSY or self.read_reg(0x44) != job_id:
                    raise RuntimeError("inconsistent completion or job ID")
                self.completed_job = job_id
                self.active_job = None
                return
            if not status & BUSY:
                raise RuntimeError("job disappeared without completion; reset may have occurred")
            time.sleep(poll_interval)
        raise TimeoutError("job did not complete before the host deadline")

    def counters(self):
        if self.completed_job is None:
            raise RuntimeError("counters are read only after successful completion")
        return {name: self.read_reg(address) | (self.read_reg(address + 4) << 32)
                for name, address in (("job_cycles", 0x80), ("compute_cycles", 0x88))}

    def read_output(self):
        if self.completed_job is None or self.descriptor is None:
            raise RuntimeError("wait for successful completion before reading C")
        desc = self.descriptor
        rows = []
        length = (desc.n * 4 + 7) & ~7
        for i in range(desc.m):
            data = self.read_memory(C_BASE + i * OUTPUT_STRIDE, length)
            if desc.n & 1 and data[-4:] != bytes(4):
                raise AssertionError("odd-column response padding was not zero")
            rows.append(list(struct.unpack(f"<{desc.n}i", data[:desc.n * 4])))
        return rows

    def benchmark(self, a, b, job_id, padding_seed=0, repeats=1, wait_timeout=5.0, on_result=None):
        """Upload once and fully compare every run; repeated jobs reuse BRAM inputs."""
        if repeats < 1:
            raise ValueError("repeats must be at least 1")
        started = time.perf_counter_ns()
        packed = pack_inputs(a, b, padding_seed)
        packed_at = time.perf_counter_ns()
        self.upload(packed)
        uploaded_at = time.perf_counter_ns()
        self.configure(packed.descriptor)
        configured_at = time.perf_counter_ns()
        measurements = []
        for repetition in range(repeats):
            current_job = job_id + repetition
            run_at = time.perf_counter_ns()
            self.start(current_job)
            self.wait(current_job, timeout=wait_timeout)
            completed_at = time.perf_counter_ns()
            counts = self.counters()
            counters_at = time.perf_counter_ns()
            output = self.read_output()
            downloaded_at = time.perf_counter_ns()
            checked = validate(output, packed.a, packed.b)
            validated_at = time.perf_counter_ns()
            desc = packed.descriptor
            tiles = ((desc.m + 3) // 4) * ((desc.n + 3) // 4)
            expected_job = tiles * (desc.k + 15)
            expected_compute = tiles * (desc.k + 11)
            if counts != {"job_cycles": expected_job, "compute_cycles": expected_compute}:
                raise AssertionError(f"cycle counters {counts} differ from the implemented cycle model")
            core_hz = self.identity["core_hz"]
            initial_ns = configured_at - started if repetition == 0 else 0
            measurement = {
                "job_id": current_job, "repetition": repetition, "m": desc.m,
                "n": desc.n, "k": desc.k, "input_residency": "uploaded" if repetition == 0 else "bram_reused",
                "packing_seconds": (packed_at - started) / 1e9 if repetition == 0 else 0.0,
                "upload_seconds": (uploaded_at - packed_at) / 1e9 if repetition == 0 else 0.0,
                "configure_seconds": (configured_at - uploaded_at) / 1e9 if repetition == 0 else 0.0,
                "command_and_wait_seconds": (completed_at - run_at) / 1e9,
                "counter_read_seconds": (counters_at - completed_at) / 1e9,
                "download_seconds": (downloaded_at - counters_at) / 1e9,
                "validation_seconds": (validated_at - downloaded_at) / 1e9,
                "host_inclusive_seconds": (initial_ns + downloaded_at - run_at) / 1e9,
                "core_seconds": counts["job_cycles"] / core_hz,
                "useful_gops": 2 * desc.m * desc.n * desc.k * core_hz / counts["job_cycles"] / 1e9,
                "useful_utilization": desc.m * desc.n * desc.k / (16 * counts["job_cycles"]),
                "expected_job_cycles": expected_job, "expected_compute_cycles": expected_compute,
                "compared_elements": checked, "passed": True, **counts,
            }
            measurements.append(measurement)
            if on_result is not None:
                on_result(measurement)
        return measurements
