"""Programmer preflight with synthetic evidence and a mocked Vivado process."""
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import program_ddr_gemm as programmer


REPORT_NAMES = ('timing.txt', 'check_timing.txt', 'utilization.txt', 'clocks.txt',
                'clock_interaction.txt', 'clock_utilization.txt', 'route.txt', 'drc.txt',
                'methodology.txt', 'cdc.txt', 'pulse_width.txt',
                'pulse_width_violations.txt', 'bus_skew.txt')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


class ProgrammerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='gemm evidence ')
        self.addCleanup(self.temporary.cleanup)
        self.out = Path(self.temporary.name)
        self.manifest_path = self.out/'build.json'
        self.fixture()

    def fixture(self, p=4, t=32, baud=115200, overlap=False):
        bitstream = b'Synthetic test fixture; never sent to an FPGA.\x00\xff'
        (self.out/'gemm_ddr.bit').write_bytes(bitstream)
        identity = dict(schema_version=1, result='PASS', kind='serial_ddr_gemm',
                        part='xc7a50ticsg324-1L', id=0x314d474e, version=0x100,
                        mode='serial', build_id=0x51a71e01, p=p, t=t, kmax=256,
                        core_hz=100_000_000, baud=baud, simulation_baud=10_000_000,
                        simulation_debug='off', vivado_exit_code=0,
                        source_hashes_unchanged=True,
                        source_sha256_utf8_lf={'rtl/archived_only.sv': 'a'*64},
                        configuration_sha256_utf8_lf='b'*64,
                        generated_sha256={'project/archived_platform.v': 'c'*64})
        if overlap:
            identity.update(kind='overlap_ddr_gemm', version=0x200, mode='selectable', enable_overlap=True)
        self.simulation = identity.copy()
        self.manifest = dict(identity, bitstream='gemm_ddr.bit',
                             bitstream_sha256=digest(bitstream),
                             physical_checks=dict(core_hz=100_000_000, wns_ns=0.25,
                                                  whs_ns=0.025, bus_skew_checks=10,
                                                  bus_skew_worst_slack_ns=8.25),
                             physical_report_sha256_bytes={})
        for name in REPORT_NAMES:
            raw = f'Synthetic {name}; not an implementation result.\r\n'.encode()
            (self.out/name).write_bytes(raw)
            self.manifest['physical_report_sha256_bytes'][name] = digest(raw)
        self.write_simulation()

    def write_manifest(self):
        self.manifest_path.write_text(json.dumps(self.manifest)+'\n', encoding='utf-8')

    def write_simulation(self):
        raw = (json.dumps(self.simulation)+'\n').encode()
        (self.out/'simulation.json').write_bytes(raw)
        self.manifest['simulation_sha256_bytes'] = digest(raw)
        self.write_manifest()

    def invoke(self):
        with redirect_stdout(io.StringIO()):
            programmer.main(['--manifest', str(self.manifest_path), '--vivado', 'fixture-vivado'])

    def rejected(self):
        with patch.object(programmer.subprocess, 'run') as launch:
            with self.assertRaises((ValueError, OSError)):
                self.invoke()
            launch.assert_not_called()

    def test_clean_archived_evidence_all_geometries_and_physical_bauds(self):
        for p in (4, 8):
            for t in (8, 32):
                for baud in (115200, 1_000_000):
                    with self.subTest(p=p, t=t, baud=baud):
                        self.fixture(p, t, baud)
                        # The qualified image need not have its source checkout
                        # present, and the live repository need not match it.
                        self.assertFalse((programmer.ROOT/'rtl/archived_only.sv').exists())
                        with patch.object(programmer.subprocess, 'run') as launch:
                            self.invoke()
                        launch.assert_called_once()
                        args, kwargs = launch.call_args
                        command = args[0]
                        self.assertEqual(command[0], 'fixture-vivado')
                        self.assertEqual(command[-2:], ['-tclargs', str(self.out/'gemm_ddr.bit')])
                        self.assertEqual(kwargs['cwd'], self.out)
                        self.assertTrue(kwargs['check'])
                        if programmer.os.name == 'nt':
                            self.assertEqual(kwargs['creationflags'], subprocess.CREATE_NO_WINDOW)

    def test_unqualified_manifest_never_launches(self):
        for name, value in (('result', 'FAIL'), ('kind', 'ddr_diagnostic'), ('mode', 'overlap'),
                            ('part', 'xc7a35tcpg236-1'), ('id', 0), ('version', 0x10000),
                            ('core_hz', 50_000_000), ('vivado_exit_code', 1),
                            ('source_hashes_unchanged', False)):
            with self.subTest(name=name):
                self.fixture()
                self.manifest[name] = value
                self.write_manifest()
                self.rejected()

    def test_missing_or_changed_bitstream_never_launches(self):
        for missing in (False, True):
            with self.subTest(missing=missing):
                self.fixture()
                path = self.out/'gemm_ddr.bit'
                if missing:
                    path.unlink()
                else:
                    path.write_bytes(path.read_bytes()+b'changed')
                self.rejected()

    def test_missing_or_changed_simulation_never_launches(self):
        for missing in (False, True):
            with self.subTest(missing=missing):
                self.fixture()
                path = self.out/'simulation.json'
                if missing:
                    path.unlink()
                else:
                    path.write_bytes(path.read_bytes()+b' ')
                self.rejected()

    def test_resealed_simulation_must_still_match_pass_and_identity(self):
        mutations = (('schema_version', 2), ('result', 'FAIL'), ('vivado_exit_code', 1),
                     ('source_hashes_unchanged', False), ('kind', 'ddr_diagnostic'),
                     ('part', 'other'), ('id', 0), ('version', 0x10000), ('mode', 'overlap'),
                     ('build_id', 0x51a71e02), ('p', 8), ('t', 8), ('kmax', 128),
                     ('core_hz', 50_000_000), ('baud', 1_000_000), ('simulation_baud', 5_000_000),
                     ('simulation_debug', 'typical'),
                     ('configuration_sha256_utf8_lf', 'd'*64),
                     ('generated_sha256', {'project/archived_platform.v': 'd'*64}),
                     ('source_sha256_utf8_lf', {'rtl/archived_only.sv': 'b'*64}))
        for name, value in mutations:
            with self.subTest(name=name):
                self.fixture()
                self.simulation[name] = value
                # Recompute the container hash, so this exercises the semantic
                # identity check rather than merely byte-integrity rejection.
                self.write_simulation()
                self.rejected()

    def test_invalid_or_nonobject_simulation_never_launches(self):
        for raw in (b'not JSON', b'[]', b'null', b'{}'):
            with self.subTest(raw=raw):
                self.fixture()
                (self.out/'simulation.json').write_bytes(raw)
                self.manifest['simulation_sha256_bytes'] = digest(raw)
                self.write_manifest()
                self.rejected()

    def test_source_identity_and_simulation_hash_required(self):
        for field in ('source_sha256_utf8_lf', 'generated_sha256'):
            for hashes in ({}, [], {'rtl.sv': 'not a SHA'}, {'': 'a'*64}):
                with self.subTest(field=field, hashes=hashes):
                    self.fixture()
                    self.manifest[field] = hashes
                    self.write_manifest()
                    self.rejected()
        for configuration in ('', 'g'*64, None):
            with self.subTest(configuration=configuration):
                self.fixture()
                self.manifest['configuration_sha256_utf8_lf'] = configuration
                self.write_manifest()
                self.rejected()
        for hash_value in ('', 'a'*63, 'g'*64, None):
            with self.subTest(hash=hash_value):
                self.fixture()
                self.manifest['simulation_sha256_bytes'] = hash_value
                self.write_manifest()
                self.rejected()

    def test_complete_report_hash_set_required(self):
        for mutation in ('missing', 'extra', 'bad_digest', 'wrong_type'):
            with self.subTest(mutation=mutation):
                self.fixture()
                hashes = self.manifest['physical_report_sha256_bytes']
                if mutation == 'missing':
                    del hashes['cdc.txt']
                elif mutation == 'extra':
                    hashes['unexpected.txt'] = 'a'*64
                elif mutation == 'bad_digest':
                    hashes['cdc.txt'] = 'bad'
                else:
                    self.manifest['physical_report_sha256_bytes'] = []
                self.write_manifest()
                self.rejected()

    def test_every_missing_or_changed_report_never_launches(self):
        for name in REPORT_NAMES:
            for missing in (False, True):
                with self.subTest(report=name, missing=missing):
                    self.fixture()
                    path = self.out/name
                    if missing:
                        path.unlink()
                    else:
                        # Even a newline-only change must invalidate a raw hash.
                        path.write_bytes(path.read_bytes().replace(b'\r\n', b'\n'))
                    self.rejected()

    def test_empty_report_rejected_even_with_matching_hash(self):
        (self.out/'cdc.txt').write_bytes(b'')
        self.manifest['physical_report_sha256_bytes']['cdc.txt'] = digest(b'')
        self.write_manifest()
        self.rejected()

    def test_physical_checks_must_be_finite_nonnegative_and_complete(self):
        for name in ('wns_ns', 'whs_ns', 'bus_skew_worst_slack_ns'):
            for value in (-0.001, float('nan'), float('inf'), '0.25', None, True):
                with self.subTest(name=name, value=value):
                    self.fixture()
                    self.manifest['physical_checks'][name] = value
                    self.write_manifest()
                    self.rejected()
        for checks in (None, {}, {'wns_ns': 1, 'whs_ns': 1}):
            with self.subTest(checks=checks):
                self.fixture()
                self.manifest['physical_checks'] = checks
                self.write_manifest()
                self.rejected()
        for name, value in (('bus_skew_checks', 9), ('core_hz', 50_000_000)):
            with self.subTest(name=name):
                self.fixture()
                self.manifest['physical_checks'][name] = value
                self.write_manifest()
                self.rejected()

    def test_vivado_failure_propagates_after_valid_preflight(self):
        with patch.object(programmer.subprocess, 'run',
                          side_effect=subprocess.CalledProcessError(1, 'fixture-vivado')) as launch:
            with self.assertRaises(subprocess.CalledProcessError):
                self.invoke()
        launch.assert_called_once()

    def test_read_depth_matches_simulation_and_preserves_older_archives(self):
        for depth in (1, 4):
            self.fixture()
            self.manifest['read_slots'] = depth
            self.simulation['read_slots'] = depth
            self.write_simulation()
            with patch.object(programmer.subprocess, 'run') as launch:
                self.invoke()
            launch.assert_called_once()
        for manifest_depth, simulation_depth in ((4, None), (4, 1), (1, 4), (True, 1), (2, 2)):
            with self.subTest(manifest=manifest_depth, simulation=simulation_depth):
                self.fixture()
                self.manifest['read_slots'] = manifest_depth
                if simulation_depth is not None:
                    self.simulation['read_slots'] = simulation_depth
                self.write_simulation()
                self.rejected()

    def test_qualified_overlap_images_require_matching_selected_identity(self):
        for p, t, depth in ((4, 8, 1), (4, 32, 4), (8, 8, 4), (8, 32, 1)):
            with self.subTest(p=p, t=t, depth=depth):
                self.fixture(p, t, overlap=True)
                self.manifest['read_slots'] = self.simulation['read_slots'] = depth
                self.write_simulation()
                qualified = programmer.qualified_manifest(self.manifest_path)
                self.assertEqual((qualified['kind'], qualified['version'], qualified['mode']),
                                 ('overlap_ddr_gemm', 0x200, 'selectable'))
                self.assertIs(qualified['enable_overlap'], True)
                with patch.object(programmer.subprocess, 'run') as launch:
                    self.invoke()
                launch.assert_called_once()
        for target in ('manifest', 'simulation'):
            for invalid in (None, False, 0, 1, 'true'):
                with self.subTest(target=target, flag=invalid):
                    self.fixture(overlap=True)
                    evidence = self.manifest if target == 'manifest' else self.simulation
                    if invalid is None:
                        del evidence['enable_overlap']
                    else:
                        evidence['enable_overlap'] = invalid
                    self.write_simulation()
                    self.rejected()
        for target in ('manifest', 'simulation'):
            for field, value in (('kind', 'serial_ddr_gemm'), ('version', 0x100), ('mode', 'overlap')):
                with self.subTest(target=target, field=field):
                    self.fixture(overlap=True)
                    evidence = self.manifest if target == 'manifest' else self.simulation
                    evidence[field] = value
                    self.write_simulation()
                    self.rejected()

    def test_serial_selection_cannot_claim_enabled_overlap(self):
        # Absent flags remain accepted for archived serial images. An explicit
        # false selection is also valid when both archived identities agree.
        self.manifest['enable_overlap'] = self.simulation['enable_overlap'] = False
        self.write_simulation()
        with patch.object(programmer.subprocess, 'run') as launch:
            self.invoke()
        launch.assert_called_once()
        for target in ('manifest', 'simulation', 'both'):
            self.fixture()
            if target in ('manifest', 'both'):
                self.manifest['enable_overlap'] = True
            if target in ('simulation', 'both'):
                self.simulation['enable_overlap'] = True
            self.write_simulation()
            with self.subTest(target=target):
                self.rejected()


if __name__ == '__main__':
    unittest.main()
