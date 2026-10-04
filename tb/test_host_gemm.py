"""DDR GEMM host tests against an independent byte-addressed device.

The command model is a software fixture, not accelerator timing or board evidence.
Shared framing/retry tests remain in test_host_preview; these exercise this API
through the real Link and framed byte stream without opening a physical port.
"""
import contextlib
import csv
from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from host.gemm import Descriptor, GEMM, golden, load_manifest, pack_inputs, validate
from host.gemm.__main__ import host_source_hashes, main
from host.gemm.client import DDR_BYTES, ID, SERIAL_VERSION, OVERLAP_VERSION
from host.preview.protocol import DeviceError, Link, ProtocolError, TransportError
from tb.test_host_preview import FakeSerial


class MemoryDevice:
    """Sparse ordinary DDR memory and the public register/packet contracts."""

    def __init__(self, p=4, t=32, version=SERIAL_VERSION):
        self.regs = {0: 0x314d474e, 4: version, 8: (256 << 16) | (t << 8) | p,
                     0x0c: 0x11, 0x10: 0, 0x14: 0, 0x40: 0, 0x44: 0,
                     0x48: 100_000_000, 0x50: 0x19ab27cd, 0x4c: 10_000_000}
        self.config_addresses = set(range(0x18, 0x40, 4)) | {0x4c}
        self.regs.update({address: 0 for address in range(0x18, 0x40, 4)})
        self.regs.update({address: 0 for address in range(0x80, 0xc0, 4)})
        self.memory, self.calls, self.writes = {}, [], []
        self.starts, self.busy_polls, self.pending = 0, 0, False
        self.outcome, self.bad_read_address = 'success', None
        self.count_override = {}
        self.inject_error = None
        self.ping_id, self.ping_version = self.regs[0], self.regs[4]

    def read(self, address, length):
        return bytes(self.memory.get(address+i, 0xa5) for i in range(length))

    def write(self, address, data):
        self.memory.update((address+i, value) for i, value in enumerate(data))

    def complete(self):
        m, n, k = (self.regs[address] for address in (0x18, 0x1c, 0x20))
        a, bt, c = (self.regs[address] for address in (0x24, 0x28, 0x2c))
        sa, sb, sc = (self.regs[address] for address in (0x30, 0x34, 0x38))
        for i in range(m):
            for j in range(n):
                products = []
                for inner in range(k):
                    av = int.from_bytes(self.read(a+i*sa+inner, 1), 'little', signed=True)
                    bv = int.from_bytes(self.read(bt+j*sb+inner, 1), 'little', signed=True)
                    products.append(av*bv)
                self.write(c+i*sc+4*j, sum(products).to_bytes(4, 'little', signed=True))
        if self.outcome == 'wrong_output':
            self.memory[c] ^= 1
        if self.outcome == 'wrong_padding':
            self.memory[c+4*n] ^= 1
        p, t = self.regs[8] & 255, (self.regs[8] >> 8) & 255
        reads = writes = compute = 0
        for i0 in range(0, m, t):
            for j0 in range(0, n, t):
                rows, columns = min(t, m-i0), min(t, n-j0)
                reads += (rows+columns)*len(range(0, k, 8))
                writes += rows*len(range(0, columns, 2))
                for _ in range(0, rows, p):
                    for _ in range(0, columns, p):
                        compute += k+3*p-1
        # Fixture latency merely needs to be positive; it is never hardware data.
        counters = {0x80: compute+reads+writes+100, 0x88: compute, 0x90: reads,
                    0x98: writes, 0xa0: 4*m*n, 0xa8: reads+12, 0xb0: 7, 0xb8: 11}
        counters.update(self.count_override)
        for address, value in counters.items():
            self.regs[address], self.regs[address+4] = value & 0xffffffff, value >> 32
        self.regs[0x0c], self.pending = 0x15, False

    def handle(self, packet):
        opcode, data = packet.opcode, packet.payload
        self.calls.append((opcode, data))
        if self.inject_error is not None and opcode == self.inject_error[0]:
            _, status = self.inject_error
            self.inject_error = None
            return status, b''
        if opcode == 1:
            return 0, data+struct.pack('<II', self.ping_id, self.ping_version)
        if opcode == 2:
            address, = struct.unpack('<H', data)
            if address not in self.regs:
                return 2, b''
            if address == self.bad_read_address:
                return 0, b'bad'
            if address >= 0x80 and self.regs[0x0c] & 2 and not self.regs[0x0c] & 32:
                return 4, b''
            value = self.regs[address]
            if address == 0x0c and self.pending and self.outcome != 'hang':
                self.busy_polls -= 1
                if self.busy_polls == 0:
                    self.complete()
            return 0, struct.pack('<I', value)
        if opcode == 3:
            address, value = struct.unpack('<HI', data)
            if address not in self.regs:
                return 2, b''
            if address not in self.config_addresses | {0x10, 0x14}:
                return 1, b''
            if address == 0x10 and value not in (1, 2) or address == 0x4c and value == 0:
                return 1, b''
            if self.regs[0x0c] & 2:
                return 4, b''
            if address == 0x10 and self.regs[0x0c] & 32:
                return 5, b''
            self.writes.append((address, value))
            if address == 0x10:
                if value == 2:
                    self.regs[0x0c], self.regs[0x40] = 0x11, 0
                else:
                    self.starts += 1
                    self.regs[0x44] = self.regs[0x14]
                    self.regs[0x0c], self.pending, self.busy_polls = 0x12, True, 2
            else:
                self.regs[address] = value
            return 0, b''
        if opcode in (4, 5):
            address, length = struct.unpack_from('<IH', data)
            if self.regs[0x0c] & 32:
                return 8, b''
            if self.regs[0x0c] & 2:
                return 4, b''
            if address % 8 or not 8 <= length <= 240 or length % 8 or address+length > DDR_BYTES:
                return 2, b''
            if opcode == 5:
                if len(data) != 6+length:
                    return 1, b''
                self.write(address, data[6:])
                return 0, b''
            return 0, self.read(address, length)
        return 1, b''


def connection(device=None, **serial_options):
    device = device or MemoryDevice()
    serial = FakeSerial(device.handle, **serial_options)
    return GEMM(Link(serial, timeout=0.5, retries=2, sequence=1)), device, serial


def completed(client, a=((-128, 7), (3, -5)), b=((2, -128, 9), (-4, 127, 0)), guards=True):
    packed = pack_inputs(a, b, padding_seed=37)
    client.upload(packed, guards)
    client.configure(packed.descriptor)
    client.start(19)
    client.wait(19, poll_interval=0)
    return packed


class PackingTests(unittest.TestCase):
    def test_known_binary_transpose_and_nonzero_padding(self):
        a, b = [[-128, -1, 127], [3, 4, 5]], [[1, 2], [3, -4], [-5, 6]]
        packed = pack_inputs(a, b, padding_seed=11)
        self.assertEqual(packed.a_rows[0][:3], b'\x80\xff\x7f')
        self.assertEqual(packed.bt_rows[0][:3], b'\x01\x03\xfb')
        self.assertEqual(packed.bt_rows[1][:3], b'\x02\xfc\x06')
        self.assertTrue(all(len(row) == 64 and all(row[3:]) for row in packed.a_rows+packed.bt_rows))
        self.assertEqual((packed.descriptor.a_base, packed.descriptor.bt_base, packed.descriptor.c_base), (64, 320, 576))
        self.assertEqual(golden(a, b), [[-766, 510], [-10, 20]])
        a[0][0], b[0][0] = 0, 0
        self.assertEqual(packed.a[0][0], -128)
        self.assertEqual(packed.b[0][0], 1)
        self.assertEqual(pack_inputs(packed.a, packed.b, 11), packed)

    def test_signed_extremes_wide_oracle(self):
        a = [[-128]*256]
        b = [[-128, 127] for _ in range(256)]
        self.assertEqual(golden(a, b), [[4194304, -4161536]])
        self.assertEqual(validate([[4194304, -4161536]], a, b), 2)
        self.assertEqual(struct.pack('<i', -4161536), b'\x00\x80\xc0\xff')
        with self.assertRaisesRegex(AssertionError, r'C\[0,1\]'):
            validate([[4194304, 0]], a, b)
        with self.assertRaisesRegex(AssertionError, 'shape'):
            validate([[0]], a, b)

    def test_reject_invalid_matrix_values_and_shapes(self):
        cases = [([], [[1]]), ([[]], [[1]]), ([[1], [1, 2]], [[1]]),
                 ([[1, 2]], [[1]]), ([[128]], [[1]]), ([[-129]], [[1]]),
                 ([[1.0]], [[1]]), ([[True]], [[1]]), ([1], [[1]])]
        for a, b in cases:
            with self.subTest(a=a, b=b), self.assertRaises(ValueError):
                pack_inputs(a, b)
        with self.assertRaises(ValueError):
            pack_inputs([[1]], [[1]], descriptor=Descriptor.layout(2, 1, 1))
        with self.assertRaises(ValueError):
            pack_inputs([[1]], [[1]], guard_bytes=8)

    def test_descriptor_limits_alignment_and_overlap(self):
        desc = Descriptor.layout(2, 3, 9)
        cases = dict(m=0, n=1025, k=257, mode=2, watchdog=0, a_base=1,
                     a_stride=8, bt_stride=0, c_stride=0, bt_base=desc.a_base,
                     c_base=desc.bt_base, a_base_float=1.0, huge_stride=0xffffffc0)
        for field, value in cases.items():
            if field == 'a_base_float':
                field = 'a_base'
            if field == 'huge_stride':
                field = 'a_stride'
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                replace(desc, **{field: value})
        with self.assertRaises(ValueError):
            Descriptor.layout(1, 1, 1, base=DDR_BYTES-128)
        with self.assertRaises(ValueError):
            Descriptor.layout(1025, 1, 1)
        self.assertEqual(Descriptor.layout(1024, 1024, 256).c_stride, 4096)

    def test_adjacent_allocations_exact_end_and_guard_validation(self):
        desc = Descriptor(1, 1, 1, 0, 64, DDR_BYTES-64, 64, 64, 64)
        packed = pack_inputs([[-128]], [[127]], descriptor=desc)
        self.assertEqual(packed.guard_bytes, 0)
        with self.assertRaises(ValueError):
            pack_inputs([[-128]], [[127]], descriptor=desc, guard_bytes=64)
        contiguous = Descriptor(1, 1, 1, 0, 64, 128, 64, 64, 64)
        self.assertEqual(contiguous.c_base, 128)
        with self.assertRaises(ValueError):
            replace(contiguous, c_base=DDR_BYTES)

    def test_mode_changes_only_descriptor_and_conflicts_are_rejected(self):
        a, b = [[-128, 127, -1]], [[1, 2], [-3, 4], [5, -6]]
        serial = pack_inputs(a, b, padding_seed=51, mode=0)
        overlap = pack_inputs(a, b, padding_seed=51, mode=1)
        self.assertEqual(serial.descriptor, replace(overlap.descriptor, mode=0))
        self.assertEqual(serial.a_rows, overlap.a_rows)
        self.assertEqual(serial.bt_rows, overlap.bt_rows)
        self.assertEqual(tuple(serial.images(True)), tuple(overlap.images(True)))
        self.assertEqual(pack_inputs(a, b, 51, descriptor=overlap.descriptor).descriptor.mode, 1)
        self.assertEqual(Descriptor.layout(1, 2, 3, mode=1), overlap.descriptor)
        for requested, descriptor in ((0, overlap.descriptor), (1, serial.descriptor)):
            with self.subTest(mode=requested), self.assertRaisesRegex(ValueError, 'mode differs'):
                pack_inputs(a, b, descriptor=descriptor, mode=requested)
        for mode in (-1, 2, True, 1.0):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                pack_inputs(a, b, mode=mode)


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.client, self.device, self.serial = connection()
        self.client.identify()

    def test_identity_is_read_only_and_distinct_from_full_v1(self):
        identity = self.client.identity
        self.assertEqual((identity['id'], identity['version']), (ID, SERIAL_VERSION))
        self.assertEqual(identity['version'], 0x100)
        self.assertEqual(self.device.writes, [])
        self.assertTrue(all(op in (1, 2) for op, _ in self.device.calls))
        self.device.regs[0x0c], self.device.regs[0x40] = 0x19, 3
        self.client.identify()
        self.assertEqual(self.device.regs[0x40], 3)
        self.client.clear_status()
        self.assertEqual(self.device.writes, [(0x10, 2)])
        self.assertEqual(self.device.regs[0x40], 0)

    def test_identity_rejects_mismatch_busy_and_fatal(self):
        for field, value in [('ping_id', 0), ('ping_version', 0x10000)]:
            device = MemoryDevice()
            setattr(device, field, value)
            client, _, _ = connection(device)
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                client.identify()
            self.assertIsNone(client.identity)
        for address, value in [(0, 0), (4, 0x10000), (8, 0x1002002), (0x48, 0),
                               (0x0c, 0x12), (0x0c, 0x38)]:
            device = MemoryDevice()
            device.regs[address] = value
            client, _, _ = connection(device)
            with self.subTest(address=address), self.assertRaises(RuntimeError):
                client.identify()
        manifest = {key: self.client.identity[key] for key in ('p', 't', 'kmax', 'build_id', 'core_hz')}
        for key in manifest:
            changed = dict(manifest, **{key: manifest[key]+1})
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                self.client.identify(changed)
        self.client.identify(manifest)

    def test_initial_sequence_conflict_then_new_request(self):
        self.device.inject_error = (1, 6)
        self.client.identify()
        packets = [packet for packet in self.serial.executed if packet.opcode == 1]
        self.assertEqual(len(packets), 3)
        self.assertNotEqual(packets[-1].sequence, packets[-2].sequence)
        self.assertEqual(packets[-1].payload, packets[-2].payload)

    def test_overlap_identity_requires_consistent_ping_registers_and_manifest(self):
        for p, t in ((4, 8), (4, 32), (8, 8), (8, 32)):
            with self.subTest(p=p, t=t):
                client, device, _ = connection(MemoryDevice(p, t, OVERLAP_VERSION))
                identity = client.identify()
                self.assertEqual((identity['id'], identity['version'], identity['p'], identity['t']),
                                 (ID, 0x200, p, t))
                self.assertTrue(all(op in (1, 2) for op, _ in device.calls))
                manifest = {key: identity[key] for key in ('id', 'version', 'p', 't', 'kmax', 'build_id', 'core_hz')}
                self.assertEqual(client.identify(manifest), identity)
                for key, value in (('id', 0), ('version', SERIAL_VERSION)):
                    with self.subTest(key=key), self.assertRaisesRegex(RuntimeError, 'ID/version'):
                        client.identify(dict(manifest, **{key: value}))
                    self.assertIsNone(client.identity)
                self.assertEqual(device.writes, [])
        for ping, register in ((SERIAL_VERSION, OVERLAP_VERSION), (OVERLAP_VERSION, SERIAL_VERSION)):
            device = MemoryDevice(version=register)
            device.ping_version = ping
            client, _, _ = connection(device)
            with self.subTest(ping=ping, register=register), self.assertRaisesRegex(RuntimeError, 'PING identity'):
                client.identify()
            self.assertIsNone(client.identity)
            self.assertEqual(device.writes, [])

    def test_serial_image_rejects_overlap_before_any_mutation(self):
        packed = pack_inputs([[-128, 7]], [[127], [-3]], mode=1)
        for operation in ('configure', 'upload', 'benchmark'):
            client, device, _ = connection()
            client.identify()
            with self.subTest(operation=operation), self.assertRaisesRegex(ValueError, 'MODE=1'):
                if operation == 'configure':
                    client.configure(packed.descriptor)
                elif operation == 'upload':
                    client.upload(packed, guards=True)
                else:
                    client.benchmark(packed.a, packed.b, mode=1)
            self.assertEqual(device.writes, [])
            self.assertFalse(any(op == 5 for op, _ in device.calls))
            self.assertEqual(device.memory, {})
            self.assertEqual(device.starts, 0)

    def test_selectable_image_runs_both_modes_with_identical_layout_and_counts(self):
        a = [[(i*7+k*11) % 256-128 for k in range(9)] for i in range(9)]
        b = [[(k*3-j*13) % 256-128 for j in range(11)] for k in range(9)]
        compared = []
        for mode in (0, 1):
            client, device, _ = connection(MemoryDevice(8, 8, OVERLAP_VERSION))
            client.identify()
            runs = client.benchmark(a, b, mode=mode, repeats=2, padding_seed=71)
            self.assertEqual([run['mode'] for run in runs], [mode, mode])
            self.assertEqual([run['input_residency'] for run in runs], ['uploaded', 'ddr_reused'])
            self.assertEqual(device.regs[0x3c], mode)
            self.assertEqual(client.read_output(), golden(a, b))
            self.assertTrue(all(run['compared_elements'] == 99 and run['guard_bytes_checked'] > 0 for run in runs))
            compared.append({key: runs[0][key] for key in ('compute_cycles', 'read_beats', 'write_beats', 'write_valid_bytes')})
        self.assertEqual(compared[0], compared[1])

    def test_memory_chunks_preserve_all_bytes_and_page_boundaries(self):
        address, raw = 0xff8, bytes(index % 256 for index in range(496))
        self.client.write_memory(address, raw)
        self.assertEqual(self.client.read_memory(address, len(raw)), raw)
        for opcode in (4, 5):
            commands = [struct.unpack_from('<IH', data) for op, data in self.device.calls if op == opcode]
            self.assertEqual(commands, [(0xff8, 8), (0x1000, 240), (0x10f0, 240), (0x11e0, 8)])
        self.client.write_memory(DDR_BYTES-8, b'lastword')
        self.assertEqual(self.client.read_memory(DDR_BYTES-8, 8), b'lastword')

    def test_invalid_memory_and_register_arguments_have_no_effect(self):
        before = len(self.device.calls)
        for address, length in [(-8, 8), (1, 8), (0, 0), (0, 7), (0, 9), (DDR_BYTES-8, 16)]:
            with self.subTest(address=address, length=length), self.assertRaises(ValueError):
                self.client.read_memory(address, length)
        for address in (-4, 1, 65536):
            with self.assertRaises(ValueError):
                self.client.read_reg(address)
        with self.assertRaises(ValueError):
            self.client.write_reg(0x14, 2**32)
        self.assertEqual(len(self.device.calls), before)

    def test_busy_fatal_and_not_ready_block_host_mutations(self):
        for status, code, exception in [(0x12, 0, RuntimeError), (0x38, 9, DeviceError),
                                        (0x19, 3, DeviceError), (0, 0, RuntimeError)]:
            self.device.regs[0x0c], self.device.regs[0x40] = status, code
            before = len([call for call in self.device.calls if call[0] in (3, 4, 5)])
            with self.subTest(status=status), self.assertRaises(exception):
                self.client.write_memory(0, b'12345678')
            self.assertEqual(len([call for call in self.device.calls if call[0] in (3, 4, 5)]), before)

    def test_full_non_square_job_padding_and_frozen_counters(self):
        self.device.count_override = {0x80: (1 << 40)+123, 0xb0: (1 << 35)+7}
        packed = completed(self.client)
        self.assertEqual(self.client.read_output(), [[-284, 17273, -1152], [26, -1019, 27]])
        self.assertEqual(validate(self.client.read_output(), packed.a, packed.b), 6)
        self.assertEqual(self.client.completed_counters['job_cycles'], (1 << 40)+123)
        self.assertEqual(self.client.completed_counters['read_stall_cycles'], (1 << 35)+7)
        desc = packed.descriptor
        self.assertTrue(all(self.device.read(desc.c_base+12, 4)))
        self.assertGreater(self.client.check_guards(packed), 0)
        self.assertEqual(set(address for address, _ in self.device.writes)-{0x10, 0x14}, self.device.config_addresses)

    def test_extreme_k256_result_bytes(self):
        packed = completed(self.client, [[-128]*256], [[-128, 127] for _ in range(256)])
        self.assertEqual(self.client.read_output(), [[4194304, -4161536]])
        self.assertEqual(self.device.read(packed.descriptor.c_base, 8), b'\x00\x00\x40\x00\x00\x80\xc0\xff')

    def test_benchmark_residency_counter_formulas_and_geometry(self):
        a = [[(i*7+j*3) % 256-128 for j in range(9)] for i in range(9)]
        b = [[(i*5-j*11) % 256-128 for j in range(11)] for i in range(9)]
        for p, t in ((4, 8), (8, 8), (4, 32), (8, 32)):
            client, device, _ = connection(MemoryDevice(p, t))
            client.identify()
            observed = []
            runs = client.benchmark(a, b, repeats=3, job_id=41, on_result=observed.append)
            self.assertEqual(observed, runs)
            self.assertEqual(device.starts, 3)
            self.assertEqual([run['input_residency'] for run in runs], ['uploaded', 'ddr_reused', 'ddr_reused'])
            self.assertEqual([run['job_id'] for run in runs], [41, 42, 43])
            self.assertTrue(all(run['compared_elements'] == 99 and run['guard_bytes_checked'] > 0 for run in runs))
            self.assertEqual(runs[1]['upload_seconds'], 0)
            self.assertEqual(runs[2]['packing_seconds'], 0)
            first_start = next(index for index, call in enumerate(device.calls)
                               if call == (3, struct.pack('<HI', 0x10, 1)))
            self.assertFalse(any(op == 5 for op, _ in device.calls[first_start:]))
            for run in runs:
                self.assertEqual(run['write_valid_bytes'], 396)
                self.assertEqual(run['write_beats'], 54)
                self.assertAlmostEqual(run['useful_gops'], 2*9*11*9*100_000_000/run['job_cycles']/1e9)
                self.assertTrue(all(value >= 0 for key, value in run.items() if key.endswith('_seconds')))

    def test_guard_checks_reject_wrong_upload_and_any_padding_corruption(self):
        packed = completed(self.client)
        other = pack_inputs(packed.a, packed.b, padding_seed=99, descriptor=packed.descriptor, guard_bytes=64)
        with self.assertRaisesRegex(RuntimeError, 'exact uploaded'):
            self.client.check_guards(other)
        for address in (packed.descriptor.a_base+3, packed.descriptor.bt_base-1,
                        packed.descriptor.c_base+4*packed.descriptor.n,
                        packed.descriptor.c_base+packed.descriptor.m*packed.descriptor.c_stride):
            self.device.memory[address] ^= 1
            with self.subTest(address=address), self.assertRaises(AssertionError):
                self.client.check_guards(packed)
            self.device.memory[address] ^= 1
        self.client.write_memory(0, b'12345678')
        self.assertIsNone(self.client.guard_images)
        with self.assertRaises(RuntimeError):
            self.client.read_output()

    def test_reset_and_external_configuration_changes_require_reload(self):
        packed = completed(self.client)
        self.client.identify()
        self.assertIsNone(self.client.descriptor)
        with self.assertRaises(RuntimeError):
            self.client.start(20)
        self.client.configure(packed.descriptor)
        self.device.regs[0x18] += 1
        before = self.device.starts
        with self.assertRaisesRegex(RuntimeError, 'configuration changed'):
            self.client.start(20)
        self.assertEqual(self.device.starts, before)

    def test_start_response_error_does_not_mark_accepted(self):
        packed = pack_inputs([[1]], [[2]])
        self.client.upload(packed)
        self.client.configure(packed.descriptor)
        original = self.device.handle

        def reject_start(packet):
            if packet.opcode == 3 and packet.payload == struct.pack('<HI', 0x10, 1):
                return 3, b''
            return original(packet)

        self.serial.handler = reject_start
        with self.assertRaises(DeviceError) as error:
            self.client.start(1)
        self.assertEqual(error.exception.status, 3)
        self.assertIsNone(self.client.active_job)
        self.assertEqual(self.device.starts, 0)

    def test_timeout_preserves_pending_job_and_never_restarts(self):
        self.device.outcome = 'hang'
        packed = pack_inputs([[1]], [[2]])
        self.client.upload(packed)
        self.client.configure(packed.descriptor)
        self.client.start(12)
        with self.assertRaises(TimeoutError):
            self.client.wait(12, timeout=0.001, poll_interval=0)
        self.assertEqual(self.client.active_job, 12)
        with self.assertRaises(RuntimeError):
            self.client.read_memory(0, 8)
        with self.assertRaises(RuntimeError):
            self.client.start(13)
        self.assertEqual(self.device.starts, 1)
        self.device.outcome = 'success'
        self.client.wait(12, poll_interval=0)
        self.assertEqual(self.client.read_output(), [[2]])

    def test_wait_rejects_wrong_identity_reset_and_fatal(self):
        for status, last, code, exception in [(0x12, 99, 0, RuntimeError), (0x11, 7, 0, RuntimeError),
                                             (0x02, 7, 0, RuntimeError), (0x3a, 7, 9, DeviceError),
                                             (0x17, 7, 0, ProtocolError)]:
            self.client.active_job = 7
            self.device.regs[0x0c], self.device.regs[0x44], self.device.regs[0x40] = status, last, code
            with self.subTest(status=status, last=last), self.assertRaises(exception):
                self.client.wait(7)
        self.assertEqual(self.device.starts, 0)

    def test_transport_retry_does_not_execute_start_twice(self):
        packed = pack_inputs([[3]], [[4]])
        self.client.upload(packed)
        self.client.configure(packed.descriptor)
        original = self.device.handle

        def drop_start_ack(packet):
            if packet.opcode == 3 and packet.payload == struct.pack('<HI', 0x10, 1):
                self.serial.drop_first = True
            return original(packet)

        self.serial.handler = drop_start_ack
        self.client.start(9)
        self.client.wait(9, poll_interval=0)
        starts = [packet for packet in self.serial.received
                  if packet.opcode == 3 and packet.payload == struct.pack('<HI', 0x10, 1)]
        self.assertEqual(len(starts), 2)
        self.assertEqual(starts[0], starts[1])
        self.assertEqual(self.device.starts, 1)
        self.assertEqual(self.client.link.retry_count, 1)

    def test_malformed_reply_or_lost_transport_invalidates_session(self):
        self.device.bad_read_address = 0x48
        with self.assertRaises(ProtocolError):
            self.client.read_reg(0x48)
        self.assertTrue(self.client.link.poisoned)
        self.assertIsNone(self.client.identity)
        with self.assertRaises(TransportError):
            self.client.identify()
        client, _, serial = connection()
        client.identify()
        with patch.object(serial, 'read', side_effect=OSError('unplugged')):
            with self.assertRaises(TransportError):
                client.read_reg(0)
        self.assertIsNone(client.identity)
        self.assertTrue(client.link.poisoned)

    def test_ready_wait_and_fatal_status_are_explicit(self):
        self.client.wait_ready()
        self.device.regs[0x0c] = 0
        with self.assertRaises(TimeoutError):
            self.client.wait_ready(0.001)
        self.device.regs[0x0c], self.device.regs[0x40] = 0x38, 10
        with self.assertRaises(DeviceError) as error:
            self.client.wait_ready()
        self.assertEqual(error.exception.status, 10)
        with self.assertRaises(DeviceError):
            self.client.clear_status()
        self.assertFalse(self.device.writes)


class ManifestAndCLITests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.bit = self.root/'gemm.bit'
        self.bit.write_bytes(b'unit-test fixture, not an FPGA bitstream')
        self.manifest = dict(schema_version=1, p=4, t=32, kmax=256, core_hz=100_000_000,
                             build_id=0x19ab27cd, baud=115200, bitstream='gemm.bit',
                             bitstream_sha256=hashlib.sha256(self.bit.read_bytes()).hexdigest(),
                             source_dirty=True, part='unit-test part')
        self.path = self.root/'build.json'
        self.write_manifest()

    def write_manifest(self):
        self.path.write_text(json.dumps(self.manifest), encoding='utf-8')

    def test_manifest_hash_schema_fields_and_geometry(self):
        self.assertEqual(load_manifest(self.path), self.manifest)
        self.bit.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            load_manifest(self.path)
        self.bit.write_bytes(b'unit-test fixture, not an FPGA bitstream')
        for key, value in [('schema_version', 2), ('p', 2), ('t', 16), ('kmax', 255),
                           ('build_id', -1), ('core_hz', 0), ('baud', 9600), ('bitstream', str(self.bit))]:
            original = self.manifest[key]
            self.manifest[key] = value
            self.write_manifest()
            with self.subTest(key=key), self.assertRaises(ValueError):
                load_manifest(self.path)
            self.manifest[key] = original
        del self.manifest['build_id']
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, 'missing'):
            load_manifest(self.path)

    def invoke(self, device=None, extra=()):
        client, device, serial = connection(device)
        with patch('host.gemm.__main__.GEMM.open', return_value=client) as opened, \
                patch('host.gemm.__main__.version', return_value='unit-test'), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            status = main(['--port', 'FAKE', '--m', '2', '--n', '3', '--k', '9', '--repeats', '2',
                           '--output', str(self.root/'results'), *extra])
        report = json.loads((self.root/'results/results.json').read_text(encoding='utf-8'))
        with (self.root/'results/results.csv').open(newline='', encoding='utf-8') as stream:
            rows = list(csv.DictReader(stream))
        return status, report, rows, device, serial, opened

    def test_cli_success_with_manifest_and_host_source_identity(self):
        status, report, rows, device, serial, opened = self.invoke(extra=('--manifest', str(self.path)))
        self.assertEqual(status, 0)
        self.assertTrue(report['passed'])
        self.assertEqual(report['summary']['jobs'], 2)
        self.assertEqual(len(rows), 2)
        self.assertEqual(device.starts, 2)
        self.assertEqual(report['build'], self.manifest)
        self.assertEqual(report['transport'], {'retries': 0, 'rejected_frames': 0})
        self.assertEqual(report['host']['source_sha256_utf8_lf'], host_source_hashes())
        self.assertEqual(set(host_source_hashes()), {'host/gemm/__init__.py', 'host/gemm/client.py',
                                                   'host/gemm/__main__.py', 'host/preview/protocol.py'})
        self.assertTrue(serial.closed)
        opened.assert_called_once_with('FAKE', 115200, 2.0, 2)

    def test_cli_mismatch_returns_failure_and_removes_stale_csv(self):
        self.invoke()
        device = MemoryDevice()
        device.outcome = 'wrong_output'
        status, report, rows, _, serial, _ = self.invoke(device)
        self.assertEqual(status, 1)
        self.assertFalse(report['passed'])
        self.assertIn('C[0,0]', report['error'])
        self.assertEqual(rows, [])
        self.assertTrue(serial.closed)

    def test_cli_padding_error_and_wrong_counters_fail(self):
        for kind in ('wrong_padding', 'wrong_counters'):
            device = MemoryDevice()
            if kind == 'wrong_counters':
                device.count_override[0xa0] = 1
            else:
                device.outcome = kind
            status, report, _, _, _, _ = self.invoke(device)
            with self.subTest(kind=kind):
                self.assertEqual(status, 1)
                self.assertFalse(report['passed'])
                self.assertIn('AssertionError', report['error'])

    def test_cli_identity_or_baud_mismatch_and_timeout_fail(self):
        device = MemoryDevice()
        device.regs[0x50] += 1
        status, report, _, _, _, _ = self.invoke(device, ('--manifest', str(self.path)))
        self.assertEqual(status, 1)
        self.assertIn('differs from the manifest', report['error'])
        status, report, _, _, _, opened = self.invoke(extra=('--manifest', str(self.path), '--baud', '1000000'))
        self.assertEqual(status, 1)
        opened.assert_not_called()
        device = MemoryDevice()
        device.outcome = 'hang'
        status, report, _, _, _, _ = self.invoke(device, ('--job-timeout', '0.001'))
        self.assertEqual(status, 1)
        self.assertIn('TimeoutError', report['error'])

    def test_cli_extremes_without_guards_and_parameter_errors(self):
        status, report, rows, _, _, _ = self.invoke(extra=('--extreme', '--no-guards'))
        self.assertEqual(status, 0)
        self.assertEqual(report['input_pattern'], 'all_minus128')
        self.assertFalse(report['guards_enabled'])
        self.assertTrue(all(int(row['guard_bytes_checked']) == 0 for row in rows))
        with patch('host.gemm.__main__.GEMM.open') as opened, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                main(['--port', 'FAKE', '--m', '1025'])
            self.assertEqual(error.exception.code, 2)
            opened.assert_not_called()

    def test_cli_mode_identity_and_version_mismatch_before_upload(self):
        status, report, rows, device, serial, _ = self.invoke(extra=('--mode', '1'))
        self.assertEqual(status, 1)
        self.assertIn('MODE=1', report['error'])
        self.assertEqual((rows, device.starts, device.writes, device.memory), ([], 0, [], {}))
        self.assertTrue(serial.closed)
        self.manifest.update(id=ID, version=OVERLAP_VERSION)
        self.write_manifest()
        for mode in (0, 1):
            device = MemoryDevice(version=OVERLAP_VERSION)
            status, report, rows, _, serial, _ = self.invoke(
                device, ('--manifest', str(self.path), '--mode', str(mode)))
            self.assertEqual(status, 0)
            self.assertEqual((report['kind'], report['mode']), ('overlap_ddr_gemm', mode))
            self.assertEqual([int(row['mode']) for row in rows], [mode, mode])
            self.assertEqual(report['identity']['version'], OVERLAP_VERSION)
            self.assertTrue(report['passed'] and serial.closed)
        status, report, rows, device, _, _ = self.invoke(extra=('--manifest', str(self.path)))
        self.assertEqual(status, 1)
        self.assertIn('ID/version differs', report['error'])
        self.assertEqual((rows, device.starts, device.writes, device.memory), ([], 0, [], {}))


if __name__ == '__main__':
    unittest.main()
