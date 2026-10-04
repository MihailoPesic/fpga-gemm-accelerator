"""Serial DDR GEMM host API; reconnect/reset requires reloading matrix inputs."""
from dataclasses import asdict, dataclass
import hashlib
import json
import operator
from pathlib import Path
import secrets
import struct
import time

from host.preview.protocol import DeviceError, Link, ProtocolError, TransportError


ID, SERIAL_VERSION, OVERLAP_VERSION = 0x314d474e, 0x00000100, 0x00000200
DDR_BYTES = 128*1024*1024
READY, BUSY, DONE, ERROR, DDR_READY, RESET_REQUIRED = (1 << bit for bit in range(6))
COUNTERS = {'job_cycles': 0x80, 'compute_cycles': 0x88, 'read_beats': 0x90,
            'write_beats': 0x98, 'write_valid_bytes': 0xa0, 'input_wait_cycles': 0xa8,
            'read_stall_cycles': 0xb0, 'write_stall_cycles': 0xb8}
CONFIG = {'m': 0x18, 'n': 0x1c, 'k': 0x20, 'a_base': 0x24, 'bt_base': 0x28,
          'c_base': 0x2c, 'a_stride': 0x30, 'bt_stride': 0x34, 'c_stride': 0x38,
          'mode': 0x3c, 'watchdog': 0x4c}


def integer(value, name):
    try:
        if isinstance(value, bool):
            raise TypeError
        return operator.index(value)
    except TypeError as exc:
        raise ValueError(f'{name} must be an integer') from exc


def round_up(value, alignment):
    return (value+alignment-1)//alignment*alignment


@dataclass(frozen=True)
class Descriptor:
    m: int
    n: int
    k: int
    a_base: int
    bt_base: int
    c_base: int
    a_stride: int
    bt_stride: int
    c_stride: int
    mode: int = 0
    watchdog: int = 10000000

    def __post_init__(self):
        for name, value in asdict(self).items():
            value = integer(value, name)
            if not 0 <= value < 2**32:
                raise ValueError(f'{name} must fit unsigned 32 bits')
            object.__setattr__(self, name, value)
        if not (1 <= self.m <= 1024 and 1 <= self.n <= 1024 and 1 <= self.k <= 256):
            raise ValueError('M,N must be 1..1024 and K must be 1..256')
        if self.mode not in (0, 1) or self.watchdog < 1:
            raise ValueError('GEMM requires MODE=0 or 1 and WATCHDOG_LIMIT>=1')
        for name, _, stride, minimum in self.regions():
            if getattr(self, name+'_base') % 64 or stride % 64 or stride < minimum:
                raise ValueError(f'{name}: bases/strides require 64-byte alignment and sufficient row space')
        allocations = sorted((getattr(self, name+'_base'), getattr(self, name+'_base')+rows*stride)
                             for name, rows, stride, _ in self.regions())
        if any(end > DDR_BYTES for _, end in allocations):
            raise ValueError('full matrix allocations must fit the 128 MiB DDR window')
        if any(left[1] > right[0] for left, right in zip(allocations, allocations[1:])):
            raise ValueError('A, BT and C allocations must be pairwise disjoint')

    def regions(self):
        return (('a', self.m, self.a_stride, self.k),
                ('bt', self.n, self.bt_stride, self.k),
                ('c', self.m, self.c_stride, 4*self.n))

    @classmethod
    def layout(cls, m, n, k, base=0, guard_bytes=64, watchdog=10000000, mode=0):
        m, n, k = (integer(value, name) for name, value in (('m', m), ('n', n), ('k', k)))
        # Validate dimensions before using them in allocation arithmetic.
        if not (1 <= m <= 1024 and 1 <= n <= 1024 and 1 <= k <= 256):
            raise ValueError('M,N must be 1..1024 and K must be 1..256')
        base, guard_bytes = integer(base, 'base'), integer(guard_bytes, 'guard_bytes')
        if base < 0 or base % 64 or guard_bytes < 0 or guard_bytes % 64:
            raise ValueError('base and guard size must be nonnegative multiples of 64')
        a_stride, bt_stride, c_stride = round_up(k, 64), round_up(k, 64), round_up(4*n, 64)
        a_base = base+guard_bytes
        bt_base = a_base+m*a_stride+2*guard_bytes
        c_base = bt_base+n*bt_stride+2*guard_bytes
        result = cls(m, n, k, a_base, bt_base, c_base, a_stride, bt_stride, c_stride,
                     watchdog=watchdog, mode=mode)
        if c_base+m*c_stride+guard_bytes > DDR_BYTES:
            raise ValueError('output guard exceeds the DDR window')
        return result


@dataclass(frozen=True)
class PackedInputs:
    descriptor: Descriptor
    a: tuple
    b: tuple
    a_rows: tuple
    bt_rows: tuple
    padding_seed: int
    guard_bytes: int

    def images(self, guards=False):
        desc, guard = self.descriptor, self.guard_bytes if guards else 0
        for index, (name, rows, stride, _) in enumerate(desc.regions()):
            if name == 'c' and not guards:
                continue
            before = padding(guard, self.padding_seed+index*71)
            after = padding(guard, self.padding_seed+index*71+19)
            data = (b''.join(self.a_rows) if name == 'a' else b''.join(self.bt_rows) if name == 'bt'
                    else padding(rows*stride, self.padding_seed+index*71+37))
            yield name, getattr(desc, name+'_base')-guard, before+data+after


def padding(length, seed):
    return bytes(1+(seed+37*index) % 255 for index in range(length))


def _matrix(matrix, name):
    try:
        rows = tuple(tuple(integer(value, name) for value in row) for row in matrix)
    except TypeError as exc:
        raise ValueError(f'{name} must be a rectangular matrix') from exc
    if not rows or not rows[0] or any(len(row) != len(rows[0]) for row in rows):
        raise ValueError(f'{name} must be nonempty and rectangular')
    if any(not -128 <= value <= 127 for row in rows for value in row):
        raise ValueError(f'{name} values must fit signed INT8')
    return rows


def pack_inputs(a, b, padding_seed=0, descriptor=None, guard_bytes=None, mode=None):
    """Pack mathematical A[M][K] and B[K][N]; DDR stores BT[j][k]=B[k][j]."""
    a, b = _matrix(a, 'A'), _matrix(b, 'B')
    if len(a[0]) != len(b):
        raise ValueError('A columns must equal B rows')
    padding_seed = integer(padding_seed, 'padding_seed')
    guard = (64 if descriptor is None else 0) if guard_bytes is None else integer(guard_bytes, 'guard_bytes')
    if guard < 0 or guard % 64:
        raise ValueError('guard_bytes must be a nonnegative multiple of 64')
    if mode is not None:
        mode = integer(mode, 'mode')
        if mode not in (0, 1):
            raise ValueError('mode must be 0 or 1')
        if descriptor is not None and descriptor.mode != mode:
            raise ValueError('mode differs from the supplied descriptor')
    desc = descriptor or Descriptor.layout(len(a), len(b[0]), len(b), guard_bytes=guard,
                                          mode=0 if mode is None else mode)
    if not isinstance(desc, Descriptor) or (desc.m, desc.n, desc.k) != (len(a), len(b[0]), len(b)):
        raise ValueError('descriptor dimensions differ from the input matrices')
    regions = sorted((getattr(desc, name+'_base')-guard,
                      getattr(desc, name+'_base')+rows*stride+guard)
                     for name, rows, stride, _ in desc.regions())
    if regions[0][0] < 0 or regions[-1][1] > DDR_BYTES or any(a[1] > b[0] for a, b in zip(regions, regions[1:])):
        raise ValueError('guard regions must fit DDR and must not overlap')

    def row_bytes(values, stride, seed):
        return bytes(value & 255 for value in values)+padding(stride-len(values), seed)

    a_rows = tuple(row_bytes(row, desc.a_stride, padding_seed+i*29) for i, row in enumerate(a))
    bt_rows = tuple(row_bytes(tuple(b[k][j] for k in range(desc.k)), desc.bt_stride,
                              padding_seed+97+j*29) for j in range(desc.n))
    return PackedInputs(desc, a, b, a_rows, bt_rows, padding_seed, guard)


def golden(a, b):
    a, b = _matrix(a, 'A'), _matrix(b, 'B')
    if len(a[0]) != len(b):
        raise ValueError('A columns must equal B rows')
    return [[sum(int(a[i][k])*int(b[k][j]) for k in range(len(b)))
             for j in range(len(b[0]))] for i in range(len(a))]


def validate(actual, a, b):
    expected = golden(a, b)
    if len(actual) != len(expected) or any(len(row) != len(expected[0]) for row in actual):
        raise AssertionError('output shape mismatch')
    for i, row in enumerate(expected):
        for j, value in enumerate(row):
            if not -(2**31) <= value < 2**31:
                raise AssertionError('oracle output exceeds signed INT32')
            if actual[i][j] != value:
                raise AssertionError(f'C[{i},{j}]: expected {value}, got {actual[i][j]}')
    return len(expected)*len(expected[0])


def load_manifest(path):
    """Validate a selected local bitstream; UART exposes BUILD_ID, not its hash."""
    path = Path(path).resolve()
    data = json.loads(path.read_text(encoding='utf-8'))
    required = {'schema_version', 'build_id', 'bitstream', 'bitstream_sha256', 'core_hz', 'baud', 'p', 't', 'kmax'}
    if missing := required-data.keys():
        raise ValueError(f'build manifest missing {sorted(missing)}')
    if data['schema_version'] != 1:
        raise ValueError('unsupported build manifest schema')
    for name in ('build_id', 'core_hz'):
        value = integer(data[name], name)
        if not (0 <= value < 2**32) or name == 'core_hz' and value == 0:
            raise ValueError(f'invalid manifest {name}')
    if data['p'] not in (4, 8) or data['t'] not in (8, 32) or data['kmax'] != 256:
        raise ValueError('unsupported manifest geometry')
    if data['baud'] not in (115200, 1000000):
        raise ValueError('unsupported manifest UART baud')
    selected = Path(data['bitstream'])
    if selected.is_absolute():
        raise ValueError('manifest bitstream path must be relative to the manifest')
    digest = hashlib.sha256((path.parent/selected).read_bytes()).hexdigest()
    if digest != str(data['bitstream_sha256']).lower():
        raise ValueError('bitstream SHA-256 differs from the manifest')
    return data


class GEMM:
    """One identified connection and one active job. Reset/reconnect invalidates residency.

    identify() is read-only. The caller explicitly clears recoverable status and
    reloads inputs before work in a new session; no timed-out START is replayed
    across sessions. Link alone retries byte-identical packets within a session.
    """
    def __init__(self, link):
        self.link = link
        self.identity = self.descriptor = self.active_job = self.completed_job = None
        self.completed_descriptor = self.completed_counters = None
        self.guard_images = self.guard_packed = None

    @classmethod
    def open(cls, port, baud=115200, timeout=2.0, retries=2):
        import serial
        device = serial.Serial(port, baud, timeout=min(timeout, 0.05), write_timeout=timeout)
        device.reset_input_buffer()
        device.reset_output_buffer()
        return cls(Link(device, timeout=timeout, retries=retries))

    def close(self):
        self.link.serial.close()

    def _request(self, opcode, payload=b'', length=0):
        try:
            data = self.link.request(opcode, payload)
            if len(data) != length:
                raise ProtocolError(f'opcode 0x{opcode:02x}: expected {length} response bytes, got {len(data)}')
            return data
        except (ProtocolError, TransportError, OSError):
            self.link.poisoned = True
            self.identity = self.descriptor = self.active_job = self.completed_job = None
            self.completed_descriptor = self.completed_counters = self.guard_images = self.guard_packed = None
            raise

    def read_reg(self, address):
        address = integer(address, 'address')
        if address % 4 or not 0 <= address < 65536:
            raise ValueError('register address must be an aligned u16')
        return struct.unpack('<I', self._request(2, struct.pack('<H', address), 4))[0]

    def write_reg(self, address, value):
        address, value = integer(address, 'address'), integer(value, 'value')
        if address % 4 or not 0 <= address < 65536 or not 0 <= value < 2**32:
            raise ValueError('register writes require an aligned u16 address and a u32 value')
        self._request(3, struct.pack('<HI', address, value))

    def identify(self, manifest=None):
        self.identity = self.descriptor = self.active_job = self.completed_job = None
        self.completed_descriptor = self.completed_counters = self.guard_images = self.guard_packed = None
        nonce = secrets.randbits(32)
        try:
            reply = self._request(1, struct.pack('<I', nonce), 12)
        except DeviceError as exc:
            if exc.status != 6:
                raise
            reply = self._request(1, struct.pack('<I', nonce), 12)
        returned_nonce, returned_id, returned_version = struct.unpack('<III', reply)
        if (returned_nonce != nonce or returned_id != ID or
                returned_version not in (SERIAL_VERSION, OVERLAP_VERSION)):
            raise RuntimeError('connected device is not a supported DDR GEMM interface')
        if (self.read_reg(0), self.read_reg(4)) != (ID, returned_version):
            raise RuntimeError('register ID/version differs from the PING identity')
        geometry = self.read_reg(8)
        p, t, kmax = geometry & 255, (geometry >> 8) & 255, geometry >> 16
        build_id, core_hz = self.read_reg(0x50), self.read_reg(0x48)
        if p not in (4, 8) or t not in (8, 32) or kmax != 256 or not core_hz:
            raise RuntimeError('unsupported hardware geometry or clock')
        identity = dict(id=ID, version=returned_version, geometry=geometry, p=p, t=t, kmax=kmax,
                        build_id=build_id, core_hz=core_hz)
        if manifest is not None and any(identity[key] != manifest[key] for key in ('p', 't', 'kmax', 'build_id', 'core_hz')):
            raise RuntimeError('hardware geometry, BUILD_ID or clock differs from the manifest')
        if manifest is not None and any(key in manifest and identity[key] != manifest[key] for key in ('id', 'version')):
            raise RuntimeError('hardware ID/version differs from the manifest')
        status = self.read_reg(0x0c)
        if status & (BUSY | RESET_REQUIRED):
            raise RuntimeError(f'cannot begin session: STATUS=0x{status:08x}')
        self.identity = identity
        return identity.copy()

    def wait_ready(self, timeout=20.0):
        if self.identity is None or timeout <= 0:
            raise ValueError('identify first and supply a positive readiness timeout')
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            status = self.read_reg(0x0c)
            if status & (ERROR | RESET_REQUIRED):
                raise DeviceError(self.read_reg(0x40) or 8, 3)
            if status & READY and status & DDR_READY and not status & BUSY:
                return
            time.sleep(0.01)
        raise TimeoutError('DDR calibration/idle readiness was not reached')

    def _idle(self, recoverable_error=False):
        if self.identity is None:
            raise RuntimeError('identify this connection and reload inputs after any reset/reconnect')
        if self.active_job is not None:
            raise RuntimeError('wait for the active job before accessing host memory or configuration')
        status = self.read_reg(0x0c)
        if status & RESET_REQUIRED or status & ERROR and not recoverable_error:
            raise DeviceError(self.read_reg(0x40) or 8, 3)
        if status & BUSY:
            raise RuntimeError('accelerator is busy')
        if not status & DDR_READY or not status & READY:
            raise RuntimeError(f'DDR GEMM is not ready: STATUS=0x{status:08x}')
        return status

    def clear_status(self):
        self._idle(recoverable_error=True)
        self.write_reg(0x10, 2)
        self.completed_job = self.completed_descriptor = self.completed_counters = None

    @staticmethod
    def _memory_range(address, length):
        address, length = integer(address, 'address'), integer(length, 'length')
        if address < 0 or address % 8 or length < 8 or length % 8 or address+length > DDR_BYTES:
            raise ValueError('memory access requires aligned complete 8-byte words inside DDR')
        return address, length

    @staticmethod
    def _chunks(address, length):
        offset = 0
        while offset < length:
            count = min(240, length-offset, 4096-((address+offset) % 4096))
            yield address+offset, offset, count
            offset += count

    def write_memory(self, address, data):
        data = bytes(data)
        address, length = self._memory_range(address, len(data))
        self._idle()
        self.completed_job = self.completed_descriptor = self.completed_counters = None
        self.guard_images = self.guard_packed = None
        for current, offset, count in self._chunks(address, length):
            self._request(5, struct.pack('<IH', current, count)+data[offset:offset+count])

    def read_memory(self, address, length):
        address, length = self._memory_range(address, length)
        self._idle()
        return b''.join(self._request(4, struct.pack('<IH', current, count), count)
                        for current, _, count in self._chunks(address, length))

    def upload(self, packed, guards=False):
        if not isinstance(packed, PackedInputs):
            raise ValueError('upload expects pack_inputs() output')
        self._idle()
        if packed.descriptor.mode and self.identity['version'] != OVERLAP_VERSION:
            raise ValueError('the identified serial image does not support MODE=1')
        self.guard_images = self.guard_packed = None
        images = tuple(packed.images(guards))
        for _, address, data in images:
            self.write_memory(address, data)
        self.guard_images = images if guards else None
        self.guard_packed = packed if guards else None

    def configure(self, descriptor):
        if not isinstance(descriptor, Descriptor):
            raise ValueError('configure expects a validated Descriptor')
        self._idle()
        if descriptor.mode and self.identity['version'] != OVERLAP_VERSION:
            raise ValueError('the identified serial image does not support MODE=1')
        self.descriptor = None
        self.completed_job = self.completed_descriptor = self.completed_counters = None
        for name, address in CONFIG.items():
            self.write_reg(address, getattr(descriptor, name))
        self.descriptor = descriptor

    def start(self, job_id):
        job_id = integer(job_id, 'job_id')
        if not 0 <= job_id < 2**32:
            raise ValueError('job_id must fit unsigned 32 bits')
        self._idle()
        if self.descriptor is None:
            raise RuntimeError('configure a descriptor before START')
        # Detect stale configuration after a reset or an external register writer.
        if any(self.read_reg(address) != getattr(self.descriptor, name) for name, address in CONFIG.items()):
            raise RuntimeError('device configuration changed; identify and reload after reset')
        self.write_reg(0x14, job_id)
        self.completed_job = self.completed_descriptor = self.completed_counters = None
        self.write_reg(0x10, 1)
        self.active_job = job_id

    def wait(self, job_id, timeout=30.0, poll_interval=0.001):
        if timeout <= 0 or poll_interval < 0 or self.active_job != job_id:
            raise ValueError('wait requires the active job ID and a positive timeout')
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            status, last = self.read_reg(0x0c), self.read_reg(0x44)
            if status & (ERROR | RESET_REQUIRED):
                raise DeviceError(self.read_reg(0x40) or 8, 3)
            if not status & DDR_READY or last != job_id:
                raise RuntimeError('job identity/calibration changed; reset may have occurred')
            if status & DONE:
                if status & BUSY or not status & READY:
                    raise ProtocolError('inconsistent successful job status')
                counts = {name: self.read_reg(address) | (self.read_reg(address+4) << 32)
                          for name, address in COUNTERS.items()}
                final = self.read_reg(0x0c)
                if self.read_reg(0x44) != job_id or final != status:
                    raise RuntimeError('completion changed while reading frozen counters')
                if not counts['job_cycles']:
                    raise ProtocolError('completed job has a zero cycle count')
                self.active_job = None
                self.completed_job, self.completed_descriptor = job_id, self.descriptor
                self.completed_counters = counts
                return counts.copy()
            if not status & BUSY:
                raise RuntimeError('job disappeared without completion; reset may have occurred')
            time.sleep(poll_interval)
        raise TimeoutError('GEMM job did not complete; its outcome remains pending')

    def read_output(self, descriptor=None):
        desc = descriptor or self.completed_descriptor
        if self.completed_job is None or desc != self.completed_descriptor:
            raise RuntimeError('wait for this descriptor to complete before reading C')
        status = self._idle()
        if not status & DONE or self.read_reg(0x44) != self.completed_job:
            raise RuntimeError('completed output identity changed; reload after reset')
        rows = []
        for row in range(desc.m):
            raw = self.read_memory(desc.c_base+row*desc.c_stride, round_up(4*desc.n, 8))
            # An odd final INT32 shares a read beat with arbitrary row padding.
            rows.append(list(struct.unpack(f'<{desc.n}i', raw[:4*desc.n])))
        return rows

    def check_guards(self, packed):
        if self.guard_images is None:
            raise RuntimeError('guard checking was not enabled during upload')
        if packed != self.guard_packed:
            raise RuntimeError('guard checking requires the exact uploaded matrix data and layout')
        if self.completed_job is None or packed.descriptor != self.completed_descriptor:
            raise RuntimeError('guard checking requires this descriptor to have completed')
        desc, checked = packed.descriptor, 0
        for name, address, expected in self.guard_images:
            actual = self.read_memory(address, len(expected))
            if name != 'c':
                if actual != expected:
                    raise AssertionError(f'{name.upper()} allocation or guards changed')
                checked += len(expected)
                continue
            guard = desc.c_base-address
            spans = [(0, guard), (guard+desc.m*desc.c_stride, len(expected))]
            spans += [(guard+row*desc.c_stride+4*desc.n, guard+(row+1)*desc.c_stride)
                      for row in range(desc.m)]
            for begin, end in spans:
                if actual[begin:end] != expected[begin:end]:
                    raise AssertionError(f'C padding/guard modified at DDR address 0x{address+begin:08x}')
                checked += end-begin
        return checked

    def benchmark(self, a, b, job_id=1, repeats=1, padding_seed=0, guards=True,
                  timeout=30.0, on_result=None, mode=0):
        repeats, job_id = integer(repeats, 'repeats'), integer(job_id, 'job_id')
        if repeats < 1 or job_id < 0 or job_id+repeats > 2**32:
            raise ValueError('positive repeats and non-wrapping unsigned job IDs required')
        self._idle()
        began = time.perf_counter_ns()
        packed = pack_inputs(a, b, padding_seed, guard_bytes=64 if guards else 0, mode=mode)
        if packed.descriptor.mode and self.identity['version'] != OVERLAP_VERSION:
            raise ValueError('the identified serial image does not support MODE=1')
        packed_at = time.perf_counter_ns()
        self.upload(packed, guards)
        uploaded_at = time.perf_counter_ns()
        self.configure(packed.descriptor)
        configured_at = time.perf_counter_ns()
        measurements, desc = [], packed.descriptor
        for repetition in range(repeats):
            started = time.perf_counter_ns()
            self.start(job_id+repetition)
            counts = self.wait(job_id+repetition, timeout)
            completed_at = time.perf_counter_ns()
            output = self.read_output(desc)
            downloaded_at = time.perf_counter_ns()
            compared = validate(output, packed.a, packed.b)
            validated_at = time.perf_counter_ns()
            guard_bytes = self.check_guards(packed) if guards else 0
            guarded_at = time.perf_counter_ns()
            p, t, hz = (self.identity[name] for name in ('p', 't', 'core_hz'))
            expected_counts = {
                'read_beats': ((desc.k+7)//8)*(desc.m*((desc.n+t-1)//t)+desc.n*((desc.m+t-1)//t)),
                'write_beats': desc.m*((desc.n+1)//2), 'write_valid_bytes': 4*desc.m*desc.n,
                'compute_cycles': ((desc.m+p-1)//p)*((desc.n+p-1)//p)*(desc.k+3*p-1)}
            if any(counts[name] != expected for name, expected in expected_counts.items()):
                raise AssertionError(f'counter contract mismatch: {counts}, expected {expected_counts}')
            initial = configured_at-began if repetition == 0 else 0
            result = dict(job_id=job_id+repetition, repetition=repetition, m=desc.m, n=desc.n, k=desc.k,
                          p=p, t=t, mode=desc.mode, core_hz=hz, build_id=self.identity['build_id'],
                          input_residency='uploaded' if repetition == 0 else 'ddr_reused',
                          packing_seconds=(packed_at-began)/1e9 if repetition == 0 else 0.0,
                          upload_seconds=(uploaded_at-packed_at)/1e9 if repetition == 0 else 0.0,
                          configure_seconds=(configured_at-uploaded_at)/1e9 if repetition == 0 else 0.0,
                          job_wall_seconds=(completed_at-started)/1e9,
                          download_seconds=(downloaded_at-completed_at)/1e9,
                          validation_seconds=(validated_at-downloaded_at)/1e9,
                          guard_read_check_seconds=(guarded_at-validated_at)/1e9,
                          host_inclusive_seconds=(initial+downloaded_at-started)/1e9,
                          core_seconds=counts['job_cycles']/hz,
                          useful_gops=2*desc.m*desc.n*desc.k*hz/counts['job_cycles']/1e9,
                          useful_utilization=desc.m*desc.n*desc.k/(p*p*counts['job_cycles']),
                          compared_elements=compared, guard_bytes_checked=guard_bytes,
                          passed=True, **counts)
            measurements.append(result)
            if on_result is not None:
                on_result(result)
        return measurements
