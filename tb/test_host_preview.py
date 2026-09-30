"""Host serialization, retry and local-preview API tests; no simulator required."""

import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
import zlib

from host.preview import Descriptor, Preview, golden, load_manifest, pack_inputs, validate
from host.preview.client import BUSY, DONE, GEOMETRY, ID, READY, VERSION
from host.preview.__main__ import host_source_hashes
from host.preview.protocol import (
    DeviceError, FrameDecoder, Link, Packet, ProtocolError, TransportError,
    cobs_decode, cobs_encode, decode_packet, encode_packet,
)


class FakeSerial:
    """Byte-stream fake with the device's last-exchange retry cache."""

    def __init__(self, handler, drop_first=False, read_limit=270, write_limit=4096):
        self.handler = handler
        self.drop_first = drop_first
        self.read_limit = read_limit
        self.write_limit = write_limit
        self.decoder = FrameDecoder()
        self.received = []
        self.executed = []
        self.cache_request = None
        self.cache_response = None
        self.output = bytearray()
        self.closed = False

    def write(self, data):
        data = data[:self.write_limit]
        for packet in self.decoder.feed(data):
            self.received.append(packet)
            if packet == self.cache_request:
                response = self.cache_response
            elif self.cache_request is not None and packet.sequence == self.cache_request.sequence:
                response = encode_packet(packet.opcode | 0x80, packet.sequence, struct.pack("<H", 6))
            else:
                status, payload = self.handler(packet)
                response = encode_packet(packet.opcode | 0x80, packet.sequence,
                                         struct.pack("<H", status) + payload)
                self.cache_request, self.cache_response = packet, response
                self.executed.append(packet)
            if self.drop_first:
                self.drop_first = False
            else:
                self.output.extend(response)
        return len(data)

    def read(self, size):
        count = min(size, len(self.output), self.read_limit)
        data = bytes(self.output[:count])
        del self.output[:count]
        return data

    def close(self):
        self.closed = True


class PreviewDevice:
    """Command-level model, independent of RTL port/bank implementation."""

    def __init__(self):
        self.regs = {0: ID, 4: VERSION, 8: GEOMETRY, 0x0C: READY,
                     0x10: 0, 0x14: 0, 0x18: 0, 0x1C: 0, 0x20: 0,
                     0x24: 0, 0x28: 0x2000, 0x2C: 0x4000,
                     0x30: 256, 0x34: 256, 0x38: 128, 0x3C: 0,
                     0x40: 0, 0x44: 0, 0x48: 100_000_000, 0x50: 0x12345678,
                     0x80: 0, 0x84: 0, 0x88: 0, 0x8C: 0}
        self.memory = bytearray(0x5000)
        self.starts = 0
        self.writes = []

    def handle(self, packet):
        data = packet.payload
        if packet.opcode == 1:
            return 0, data + struct.pack("<II", ID, VERSION)
        if packet.opcode == 2:
            address, = struct.unpack("<H", data)
            if address not in self.regs:
                return 2, b""
            return 0, struct.pack("<I", self.regs[address])
        if packet.opcode == 3:
            address, value = struct.unpack("<HI", data)
            if address not in self.regs:
                return 2, b""
            if address not in (0x10, 0x14, 0x18, 0x1C, 0x20, 0x3C):
                return 1, b""
            if address == 0x3C and value != 0:
                return 1, b""
            if address == 0x10 and value not in (1, 2):
                return 1, b""
            if self.regs[0x0C] & BUSY:
                return 4, b""
            if self.regs[0x0C] & (1 << 5):
                return 8, b""
            if address == 0x10:
                if value == 2:
                    self.regs[0x0C] = READY
                elif value == 1:
                    self.starts += 1
                    m, n, k = (self.regs[address] for address in (0x18, 0x1C, 0x20))
                    for i in range(m):
                        for j in range(n):
                            total = 0
                            for inner in range(k):
                                av = struct.unpack_from("b", self.memory, i * 256 + inner)[0]
                                bv = struct.unpack_from("b", self.memory, 0x2000 + j * 256 + inner)[0]
                                total += av * bv
                            struct.pack_into("<i", self.memory, 0x4000 + i * 128 + j * 4, total)
                        if n & 1:
                            self.memory[0x4000 + i * 128 + n * 4:0x4000 + i * 128 + n * 4 + 4] = bytes(4)
                    tiles = ((m + 3) // 4) * ((n + 3) // 4)
                    self.regs[0x80], self.regs[0x88] = tiles * (k + 15), tiles * (k + 11)
                    self.regs[0x44] = self.regs[0x14]
                    self.regs[0x0C] = READY | DONE
            else:
                self.regs[address] = value
            return 0, b""
        if packet.opcode in (4, 5):
            address, length = struct.unpack_from("<IH", data)
            if packet.opcode == 5:
                self.memory[address:address + length] = data[6:]
                self.writes.append((address, length))
                return 0, b""
            return 0, bytes(self.memory[address:address + length])
        return 1, b""


class ProtocolTests(unittest.TestCase):
    def test_crc_ieee_vector(self):
        self.assertEqual(zlib.crc32(b"123456789"), 0xCBF43926)

    def test_cobs_known_vectors_and_boundary(self):
        vectors = [(b"", b"\x01"), (b"\x00", b"\x01\x01"),
                   (b"\x11\x22\x00\x33", b"\x03\x11\x22\x02\x33"),
                   (b"\x11\x22\x33", b"\x04\x11\x22\x33"),
                   (bytes(3), bytes([1, 1, 1, 1]))]
        for raw, encoded in vectors:
            self.assertEqual(cobs_encode(raw), encoded)
            self.assertEqual(cobs_decode(encoded), raw)
        run = bytes(range(1, 255))
        self.assertEqual(cobs_encode(run), b"\xff" + run + b"\x01")
        self.assertEqual(cobs_decode(b"\xff" + run), run)
        for raw in (run + b"\x00", run * 2, bytes(range(256)), bytes(256)):
            self.assertEqual(cobs_decode(cobs_encode(raw)), raw)

    def test_packet_layout_and_maximum_payload(self):
        payload = struct.pack("<I", 0x12345678)
        wire = encode_packet(1, 0x5678, payload)
        self.assertEqual(wire[-1:], b"\x00")
        self.assertNotIn(0, wire[:-1])
        raw = cobs_decode(wire[:-1])
        self.assertEqual(raw[:10], bytes.fromhex("01 01 78 56 04 00 78 56 34 12"))
        self.assertEqual(decode_packet(wire[:-1]), Packet(1, 0x5678, payload))
        payload = bytes(range(256))
        wire = encode_packet(5, 65535, payload)
        self.assertLessEqual(len(wire), 270)
        self.assertEqual(decode_packet(wire[:-1]).payload, payload)
        with self.assertRaises(ValueError):
            encode_packet(5, 0, bytes(257))

    def test_reject_malformed_frames_and_resynchronize(self):
        valid = encode_packet(0x81, 17, b"\x00\x00payload")
        damaged_raw = bytearray(cobs_decode(valid[:-1]))
        damaged_raw[-1] ^= 1
        crc_bad = cobs_encode(damaged_raw) + b"\x00"
        invalid = [b"\x03\x01\x00", crc_bad, b"\x01" * 300 + b"\x00"]
        decoder = FrameDecoder()
        packets = []
        stream = b"\x00" + b"".join(invalid) + valid
        for value in stream:
            packets.extend(decoder.feed(bytes([value])))
        self.assertEqual(packets, [decode_packet(valid[:-1])])
        self.assertEqual(decoder.rejected, 3)
        self.assertLessEqual(len(decoder.buffer), 269)
        for field_index, value in ((0, 2), (4, 250)):
            raw = bytearray(cobs_decode(valid[:-1]))
            raw[field_index] = value
            raw[-4:] = struct.pack("<I", zlib.crc32(raw[:-4]))
            with self.assertRaises(ProtocolError):
                decode_packet(cobs_encode(raw))

    def test_identical_retry_does_not_execute_twice(self):
        serial = FakeSerial(lambda packet: (0, b""), drop_first=True,
                            read_limit=2, write_limit=3)
        link = Link(serial, timeout=0.005, retries=1, sequence=42)
        self.assertEqual(link.request(3, struct.pack("<HI", 0x10, 1)), b"")
        self.assertEqual(len(serial.received), 2)
        self.assertEqual(serial.received[0], serial.received[1])
        self.assertEqual(len(serial.executed), 1)
        self.assertEqual(link.retry_count, 1)
        self.assertEqual(link.sequence, 43)

    def test_sequence_conflict_is_reported_and_next_request_uses_new_sequence(self):
        serial = FakeSerial(lambda packet: (0, b""))
        link = Link(serial, sequence=65535)
        link.request(3, b"first")
        link.sequence = 65535
        with self.assertRaises(DeviceError) as caught:
            link.request(3, b"different")
        self.assertEqual(caught.exception.status, 6)
        self.assertFalse(link.poisoned)
        link.request(3, b"next")
        self.assertEqual(serial.received[-1].sequence, 0)

    def test_timeout_poisoning_prevents_accidental_start_replay(self):
        serial = FakeSerial(lambda packet: (0, b""))
        serial.read = lambda size: b""
        link = Link(serial, timeout=0.002, retries=1, sequence=20)
        with self.assertRaises(TransportError):
            link.request(3, b"start")
        self.assertTrue(link.poisoned)
        self.assertEqual(len(serial.executed), 1)
        with self.assertRaises(TransportError):
            link.request(3, b"start")
        self.assertEqual(len(serial.received), 2)

    def test_stale_and_bad_crc_responses_do_not_hide_following_valid_response(self):
        serial = FakeSerial(lambda packet: (0, b"answer"), read_limit=3)
        serial.output.extend(encode_packet(0x81, 90, b"\x00\x00stale"))
        bad = bytearray(cobs_decode(encode_packet(0x81, 7, b"\x00\x00bad")[:-1]))
        bad[-1] ^= 0x80
        serial.output.extend(cobs_encode(bad) + b"\x00")
        link = Link(serial, sequence=7)
        self.assertEqual(link.request(1, b"nonce"), b"answer")
        self.assertEqual(link.decoder.rejected, 1)


class HostTests(unittest.TestCase):
    def setUp(self):
        self.device = PreviewDevice()
        self.serial = FakeSerial(self.device.handle, read_limit=11)
        self.client = Preview(Link(self.serial, sequence=0))
        self.manifest = {"build_id": 0x12345678, "core_hz": 100_000_000}
        self.client.identify(self.manifest)

    def test_independent_non_square_packing_and_signed_oracle(self):
        a = [[-128, 2, -3], [4, -5, 127]]
        b = [[-128, 1], [6, -7], [8, 9]]
        packed = pack_inputs(a, b, padding_seed=9)
        self.assertEqual(packed.descriptor, Descriptor(2, 2, 3))
        self.assertEqual(packed.a_rows[0][:3], b"\x80\x02\xfd")
        self.assertEqual(packed.bt_rows[0][:3], b"\x80\x06\x08")
        self.assertEqual(packed.bt_rows[1][:3], b"\x01\xf9\x09")
        self.assertTrue(all(packed.a_rows[0][3:]))
        self.assertEqual(golden(a, b), [[16372, -169], [474, 1182]])
        self.assertEqual(validate([[16372, -169], [474, 1182]], a, b), 4)
        with self.assertRaisesRegex(AssertionError, r"C\[1,1\]"):
            validate([[16372, -169], [474, 1181]], a, b)

    def test_invalid_shapes_values_and_memory_ranges(self):
        for a, b in (([], [[1]]), ([[1, 2]], [[3]]), ([[128]], [[1]]),
                     ([[1], [2, 3]], [[4]])):
            with self.assertRaises(ValueError):
                pack_inputs(a, b)
        with self.assertRaises(ValueError):
            pack_inputs([[1]] * 33, [[1]])
        for address, length, writing in ((1, 8, True), (0, 7, True), (0, 248, True),
                                         (248, 16, True), (0x4000, 8, True),
                                         (0x2000, 8, False), (0x4078, 16, False)):
            with self.assertRaises(ValueError):
                self.client._memory_bounds(address, length, writing)

    def test_upload_split_256_and_full_extreme_comparison(self):
        a, b = [[-128] * 256], [[-128, 127, 0] for _ in range(256)]
        measurements = self.client.benchmark(a, b, 100, repeats=2)
        self.assertEqual(self.client.read_output(), [[4194304, -4161536, 0]])
        self.assertEqual(self.device.starts, 2)
        self.assertEqual(self.device.writes,
                         [(0, 240), (240, 16), (0x2000, 240), (0x20F0, 16),
                          (0x2100, 240), (0x21F0, 16), (0x2200, 240), (0x22F0, 16)])
        self.assertEqual(measurements[0]["job_cycles"], 271)
        self.assertEqual(measurements[0]["compute_cycles"], 267)
        self.assertEqual(measurements[0]["compared_elements"], 3)
        self.assertEqual(measurements[1]["input_residency"], "bram_reused")
        self.assertEqual(measurements[1]["upload_seconds"], 0)

    def test_reidentify_clears_input_coverage_and_requires_reload(self):
        packed = pack_inputs([[2]], [[3]])
        self.client.upload(packed)
        self.client.configure(packed.descriptor)
        self.client.start(1)
        self.client.wait(1)
        self.client.identify(self.manifest)
        self.client.configure(packed.descriptor)
        with self.assertRaisesRegex(RuntimeError, "uploaded"):
            self.client.start(2)
        self.assertEqual(self.device.starts, 1)

    def test_identity_mismatch_and_busy_reconnect_are_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "BUILD_ID"):
            self.client.identify({**self.manifest, "build_id": 0})
        self.device.regs[0x0C] = BUSY
        with self.assertRaisesRegex(RuntimeError, "session"):
            self.client.identify(self.manifest)

    def test_initial_sequence_conflict_uses_fresh_sequence(self):
        self.serial.cache_request = Packet(1, 100, b"old!")
        self.client.link.sequence = 100
        before = len(self.serial.received)
        self.client.identify(self.manifest)
        self.assertIsNotNone(self.client.identity)
        self.assertEqual(self.serial.received[before].sequence, 100)

    def test_configure_only_writes_writable_preview_registers(self):
        self.serial.received.clear()
        self.client.configure(Descriptor(3, 5, 7))
        writes = [struct.unpack("<HI", packet.payload) for packet in self.serial.received
                  if packet.opcode == 3]
        self.assertEqual(writes, [(0x18, 3), (0x1C, 5), (0x20, 7), (0x3C, 0)])
        # The command model must reject even a same-value write to fixed fields.
        for address in (0x24, 0x28, 0x2C, 0x30, 0x34, 0x38):
            with self.assertRaises(DeviceError) as caught:
                self.client.write_reg(address, self.device.regs[address])
            self.assertEqual(caught.exception.status, 1)

    def test_identity_checks_fixed_preview_memory_layout(self):
        self.device.regs[0x30] = 128
        with self.assertRaisesRegex(RuntimeError, "memory map"):
            self.client.identify(self.manifest)

    def test_fatal_job_error_never_returns_success(self):
        packed = pack_inputs([[2]], [[3]])
        self.client.upload(packed)
        self.client.configure(packed.descriptor)
        self.client.start(1)
        self.device.regs[0x0C] = (1 << 3) | (1 << 5)
        self.device.regs[0x40] = 8
        with self.assertRaises(DeviceError) as caught:
            self.client.wait(1)
        self.assertEqual(caught.exception.status, 8)
        with self.assertRaises(RuntimeError):
            self.client.read_output()

    def test_reconfiguration_invalidates_software_completion(self):
        self.client.benchmark([[2]], [[3]], 1)
        self.client.configure(Descriptor(2, 2, 2))
        with self.assertRaises(RuntimeError):
            self.client.read_output()

    def test_manifest_hash_is_checked(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "preview.bit").write_bytes(b"test bitstream")
            manifest = {"schema_version": 1, "build_id": 10, "bitstream": "preview.bit",
                        "bitstream_sha256": hashlib.sha256(b"test bitstream").hexdigest(),
                        "source_commit": "a" * 40, "core_hz": 100_000_000,
                        "baud": 115200, "part": "xc7a50ticsg324-1L"}
            path = root / "build.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            self.assertEqual(load_manifest(path), manifest)
            (root / "preview.bit").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                load_manifest(path)

    def test_host_identity_names_each_source_with_normalized_lf_hash(self):
        hashes = host_source_hashes()
        self.assertEqual(set(hashes), {f"host/preview/{name}" for name in
                                      ("__init__.py", "__main__.py", "client.py", "protocol.py")})
        root = Path(__file__).resolve().parents[1]
        for name, digest in hashes.items():
            contents = (root / name).read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
            self.assertEqual(digest, hashlib.sha256(contents).hexdigest())


if __name__ == "__main__":
    unittest.main()
