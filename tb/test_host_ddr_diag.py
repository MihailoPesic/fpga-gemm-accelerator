"""DDR diagnostic host checks using an independent register-level device."""
import csv
import hashlib
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from host.ddr_diag import DDRDiagnostic, load_manifest
from host.ddr_diag.__main__ import main, software_hashes
from host.preview.protocol import Link, ProtocolError
from tb.test_host_preview import FakeSerial


class DiagnosticDevice:
    def __init__(self, outcome="success"):
        self.regs = {0: 0x3144474e, 4: 0x100, 0x0c: 0x11, 0x10: 0,
                     0x14: 0x12345678, 0x40: 0, 0x48: 81_250_000,
                     0x50: 0xabc123, 0x60: 0, 0x68: 0, 0x6c: 0,
                     0x70: 0, 0x74: 0, 0x80: 0, 0x84: 0, 0x90: 0,
                     0x94: 0, 0x98: 0, 0x9c: 0}
        self.outcome = outcome
        self.writes = []
        self.starts = 0
        self.busy_polls = 0

    def handle(self, packet):
        if packet.opcode == 1:
            return 0, packet.payload + struct.pack("<II", self.regs[0], self.regs[4])
        if packet.opcode == 2:
            address, = struct.unpack("<H", packet.payload)
            if address not in self.regs:
                return 2, b""
            if address >= 0x60 and self.regs[0x0c] & 2 and not self.regs[0x0c] & 8:
                return 4, b""
            value = self.regs[address]
            if address == 0x0c and self.busy_polls:
                self.busy_polls -= 1
                if not self.busy_polls:
                    self.complete()
            return 0, struct.pack("<I", value)
        if packet.opcode == 3:
            address, value = struct.unpack("<HI", packet.payload)
            if address not in self.regs:
                return 2, b""
            if address not in (0x10, 0x14) or address == 0x10 and value != 1:
                return 1, b""
            if self.regs[0x0c] & 8:
                return 8, b""
            if self.regs[0x0c] & 2:
                return 4, b""
            self.writes.append((address, value))
            if address == 0x10:
                self.starts += 1
                self.regs[0x0c] = 0x12
                self.busy_polls = 2
            else:
                self.regs[address] = value
            return 0, b""
        return 1, b""

    def complete(self):
        self.regs.update({0x0c: 0x15, 0x80: 0x12345, 0x84: 1,
                          0x90: 1024, 0x98: 648})
        if self.outcome == "wrong_counts":
            self.regs[0x98] = 647
        elif self.outcome == "zero_cycles":
            self.regs[0x80] = self.regs[0x84] = 0
        elif self.outcome == "fatal_busy":
            self.regs.update({0x0c: 0x3a, 0x40: 0x100, 0x60: 0x7fffff8,
                              0x68: 0x55667788, 0x6c: 0x11223344,
                              0x70: 0x55667789, 0x74: 0x11223344})


class HostDiagnosticTests(unittest.TestCase):
    manifest = {"build_id": 0xabc123, "core_hz": 81_250_000}

    def client(self, outcome="success"):
        model = DiagnosticDevice(outcome)
        serial = FakeSerial(model.handle)
        client = DDRDiagnostic(Link(serial, timeout=0.01, sequence=1234))
        client.identify(self.manifest)
        return client, model

    def test_identify_and_repeated_complete_runs(self):
        client, model = self.client()
        for seed in (0, 0xffffffff, 0x12345678):
            result = client.run(seed)
            self.assertTrue(result["passed"])
            self.assertEqual(result["cycles"], 0x100012345)
            self.assertEqual((result["read_beats"], result["write_beats"]), (1024, 648))
            self.assertEqual(result["seed"], seed)
        self.assertEqual(model.starts, 3)
        self.assertEqual([address for address, value in model.writes], [0x14, 0x10]*3)
        client.close()
        self.assertTrue(client.link.serial.closed)

    def test_bad_success_counters_and_zero_cycles_fail(self):
        for outcome in ("wrong_counts", "zero_cycles"):
            with self.subTest(outcome=outcome):
                client, model = self.client(outcome)
                result = client.run(99)
                self.assertFalse(result["passed"])
                self.assertIn("host_error", result)
                self.assertEqual(model.starts, 1)

    def test_fatal_while_busy_exposes_frozen_word_and_never_restarts(self):
        client, model = self.client("fatal_busy")
        result = client.run(1)
        self.assertFalse(result["passed"])
        self.assertTrue(result["diagnostic_words_available"])
        self.assertEqual(result["first_fail_addr"], 0x7fffff8)
        self.assertEqual(result["expected"], 0x1122334455667788)
        self.assertEqual(result["actual"], 0x1122334455667789)
        self.assertEqual(result["error_code"], 0x100)
        with self.assertRaisesRegex(RuntimeError, "platform reset"):
            client.run(2)
        self.assertEqual(model.starts, 1)

    def test_live_counters_are_not_read(self):
        client, model = self.client()
        model.regs[0x0c] = 0x12
        result = client.snapshot()
        self.assertFalse(result["diagnostic_words_available"])
        self.assertNotIn("cycles", result)

    def test_identity_mismatch_and_unidentified_start(self):
        client, model = self.client()
        for address in (0, 4, 0x48, 0x50):
            old = model.regs[address]
            model.regs[address] ^= 1
            with self.assertRaises(RuntimeError):
                client.identify(self.manifest)
            with self.assertRaisesRegex(RuntimeError, "identify"):
                client.run(3)
            model.regs[address] = old
        self.assertEqual(model.starts, 0)

    def test_short_register_response_poisoned_and_no_start(self):
        serial = FakeSerial(lambda packet: (0, b"\x00"))
        client = DDRDiagnostic(Link(serial, timeout=0.01, sequence=1))
        with self.assertRaises(ProtocolError):
            client.read_reg(0)
        self.assertTrue(client.link.poisoned)

    def test_manifest_hash_and_cli_results(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            bitstream = directory / "ddr_diag.bit"
            bitstream.write_bytes(b"unit-test fixture, not a programmed image")
            manifest = {"schema_version": 1, **self.manifest, "baud": 115200,
                        "bitstream": bitstream.name,
                        "bitstream_sha256": hashlib.sha256(bitstream.read_bytes()).hexdigest()}
            manifest_path = directory / "build.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            self.assertEqual(load_manifest(manifest_path), manifest)
            for outcome, expected_exit, expected_runs in (("success", 0, 3), ("fatal_busy", 1, 1)):
                client, model = self.client(outcome)
                output = directory / outcome
                with patch("host.ddr_diag.__main__.DDRDiagnostic.open", return_value=client), \
                     patch("host.ddr_diag.__main__.version", return_value="fixture"), \
                     patch("sys.stdout", new=io.StringIO()):
                    result = main(["--port", "fixture", "--manifest", str(manifest_path),
                                   "--output", str(output)])
                self.assertEqual(result, expected_exit)
                report = json.loads((output / "results.json").read_text())
                self.assertEqual(len(report["runs"]), expected_runs)
                self.assertEqual(report["passed"], expected_exit == 0)
                with (output / "results.csv").open(newline="") as handle:
                    self.assertEqual(len(list(csv.DictReader(handle))), expected_runs)
                self.assertEqual(len(report["host"]["source_sha256_utf8_lf"]), 4)
                self.assertTrue(client.link.serial.closed)
            bitstream.write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                load_manifest(manifest_path)

    def test_software_identity_has_package_and_shared_transport(self):
        hashes = software_hashes()
        self.assertEqual(set(hashes), {"host/ddr_diag/__init__.py", "host/ddr_diag/__main__.py",
                                       "host/ddr_diag/client.py", "host/preview/protocol.py"})
        self.assertTrue(all(len(value) == 64 for value in hashes.values()))


if __name__ == "__main__":
    unittest.main()
