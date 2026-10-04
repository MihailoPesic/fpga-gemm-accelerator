"""Board-runner plan and admission tests; no physical serial port is opened.

Counter checks use the software packet fixture, not FPGA performance data.
"""
from contextlib import redirect_stderr, redirect_stdout
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import hw_test_ddr_gemm as runner
from tb.test_host_gemm import MemoryDevice, connection
from host.gemm.client import OVERLAP_VERSION


P4_PLAN_SHA256 = '37d065012515680a1ae3cb395694a6c1e79d9ec8b5a06149fddcbf38a145f902'
P8_PLAN_SHA256 = 'cdec3f398fefd531cb6130e622fa13d73ccf8acd3bec4f2d0b9e5576645e621c'
# Original P4 inputs: flattened row-major A followed by B, signed bytes
# serialized as two's complement. Shapes and seeds are pinned by the plan hash.
MATRIX_SHA256 = {
    'signed_scalar': 'af472cf2977dbfccc45851e12525627fc9ecc03f274f108a865b18a672f38ba6',
    'signed_extremes': 'bc68e1fa7b0285da52544aeae4168e94cfdedc8eaa28f46b3022e84a90abf15a',
    'odd_tail': '1a6489fa9ed507f81b422516ada2f109141bdf136e2f47ae55d0c5d74c24bf0b',
    'column_tile_boundary': '576d75b6013b14d9a286eea189a4364cfad3c9196b2a15bc59abbdaaaf812d23',
    'multi_tile_kmax': '1f4cbcac83e78b297a1f793e83669953a55a450b749717e9cbe15fe6e7ddceed',
    'asymmetric_k255': '84d5c7cf36febc18f4f38566031dfdead5e6c4b7b8b3a377ebfd3463e0ea63a4',
    'resident_dense': 'd329e73c4152200b77f49bc1b66c8d5602992bd92e527cbb6dfc36795212759e',
}


class BoardQualificationTests(unittest.TestCase):
    def test_original_p4_plan_is_unchanged(self):
        plan = runner.case_plan()
        self.assertEqual(plan, runner.case_plan(4))
        self.assertEqual(plan, runner.case_plan(4, mode=0))
        self.assertEqual(runner.plan_hash(plan), P4_PLAN_SHA256)
        self.assertEqual(sum(case['repeats'] for case in plan['cases']), 48)
        self.assertEqual(sum(case['m']*case['n']*case['repeats'] for case in plan['cases']), 49593)

    def test_p8_preserves_workloads_and_input_bytes(self):
        p4, p8 = runner.case_plan(4), runner.case_plan(8)
        expected = copy.deepcopy(p4)
        expected['p'] = 8
        self.assertEqual(p8, expected)
        self.assertEqual(runner.plan_hash(p8), P8_PLAN_SHA256)
        for old, new in zip(p4['cases'], p8['cases']):
            with self.subTest(case=old['name']):
                self.assertEqual(runner.matrices(old), runner.matrices(new))
                raw = bytes(value & 255 for matrix in runner.matrices(new)
                            for row in matrix for value in row)
                self.assertEqual(hashlib.sha256(raw).hexdigest(), MATRIX_SHA256[new['name']])

    def test_overlap_plan_changes_only_mode_and_preserves_exact_inputs(self):
        for p, serial_hash in ((4, P4_PLAN_SHA256), (8, P8_PLAN_SHA256)):
            serial, overlap = runner.case_plan(p, 0), runner.case_plan(p, 1)
            expected = copy.deepcopy(serial)
            expected.update(mode='overlap', job_mode=1)
            self.assertEqual(overlap, expected)
            self.assertEqual(runner.plan_hash(serial), serial_hash)
            self.assertNotEqual(runner.plan_hash(overlap), serial_hash)
            self.assertEqual((overlap['total_jobs'], overlap['total_compared_outputs']), (48, 49593))
            for case in overlap['cases']:
                raw = bytes(value & 255 for matrix in runner.matrices(case) for row in matrix for value in row)
                self.assertEqual(hashlib.sha256(raw).hexdigest(), MATRIX_SHA256[case['name']])
        for mode in (-1, 2, True, 0.0, 1.0, '1', None):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                runner.case_plan(8, mode)

    def test_only_explicit_p4_p8_plans_are_supported(self):
        for p in (0, 1, 2, 16, 4.0, 8.0, True, '8', None):
            with self.subTest(p=p), self.assertRaises(ValueError):
                runner.case_plan(p)

    def test_manifest_matching_and_evidence_gates_are_retained(self):
        for p in (4, 8):
            plan = runner.case_plan(p)
            manifest = {key: plan[key] for key in ('p', 't', 'kmax', 'core_hz', 'baud')}
            manifest.update(source_commit='test-only', source_dirty=True)
            with patch.object(runner, 'qualified_manifest', return_value=manifest) as gate:
                self.assertEqual(runner.qualification_manifest('unused', plan), manifest)
                gate.assert_called_once_with('unused')
            for key, wrong in (('p', 12-p), ('t', 8), ('kmax', 128),
                               ('core_hz', 99000000), ('baud', 1000000)):
                changed = dict(manifest, **{key: wrong})
                with self.subTest(p=p, key=key), patch.object(runner, 'qualified_manifest', return_value=changed):
                    with self.assertRaises(ValueError):
                        runner.qualification_manifest('unused', plan)
            for field, wrong in (('source_commit', None), ('source_dirty', 1)):
                with self.subTest(p=p, field=field):
                    changed = dict(manifest, **{field: wrong})
                    with patch.object(runner, 'qualified_manifest', return_value=changed), self.assertRaises(ValueError):
                        runner.qualification_manifest('unused', plan)
        with patch.object(runner, 'qualified_manifest', side_effect=ValueError('evidence changed')):
            with self.assertRaisesRegex(ValueError, 'evidence changed'):
                runner.qualification_manifest('unused', runner.case_plan(8))

    def test_unsupported_plan_cannot_relax_fixed_platform(self):
        for key, wrong in (('p', 16), ('t', 8), ('kmax', 128),
                           ('core_hz', 99000000), ('baud', 1000000)):
            bad = dict(runner.case_plan(8), **{key: wrong})
            with self.subTest(key=key), patch.object(runner, 'qualified_manifest') as gate:
                with self.assertRaises(ValueError):
                    runner.qualification_manifest('unused', bad)
                gate.assert_not_called()

    def test_overlap_qualification_requires_exact_capability_before_com(self):
        plan = runner.case_plan(8, 1)
        manifest = {key: plan[key] for key in ('p', 't', 'kmax', 'core_hz', 'baud')}
        manifest.update(source_commit='test-only', source_dirty=True, kind='overlap_ddr_gemm',
                        version=OVERLAP_VERSION, enable_overlap=True)
        with patch.object(runner, 'qualified_manifest', return_value=manifest):
            self.assertEqual(runner.qualification_manifest('unused', plan), manifest)
        mutations = (('kind', 'serial_ddr_gemm'), ('version', 0x100), ('version', None),
                     ('enable_overlap', False), ('enable_overlap', None), ('enable_overlap', 1))
        for field, wrong in mutations:
            with self.subTest(field=field, value=wrong), tempfile.TemporaryDirectory() as temporary:
                changed = dict(manifest, **{field: wrong})
                with patch.object(runner, 'qualified_manifest', return_value=changed), \
                        patch.object(runner.GEMM, 'open') as open_com, \
                        redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    code = runner.main(['--port', 'UNUSED', '--manifest', 'unused.json', '--p', '8',
                                        '--mode', '1', '--output', str(Path(temporary)/'results')])
                self.assertEqual(code, 1)
                open_com.assert_not_called()
                report = json.loads((Path(temporary)/'results/summary.json').read_text())
                self.assertEqual((report['state'], report['mode'], report['checked_counts']['completed_jobs']),
                                 ('FAIL', 1, 0))
                self.assertIn('overlap-capable image', report['error']['message'])
        for changes in ({'job_mode': 0}, {'job_mode': True}, {'mode': 'serial'}, {'job_mode': 2}):
            with self.subTest(changes=changes), patch.object(runner, 'qualified_manifest') as gate:
                with self.assertRaises(ValueError):
                    runner.qualification_manifest('unused', dict(plan, **changes))
                gate.assert_not_called()

    def test_host_checks_literal_p4_p8_active_cycles(self):
        # Literal counts from the microtile schedule, including masked tail PEs.
        for p, counts in ((4, (12, 40, 267)), (8, (24, 32, 279))):
            for shape, expected in zip(((1, 1, 1), (5, 3, 9), (1, 2, 256)), counts):
                m, n, k = shape
                with self.subTest(p=p, shape=shape):
                    client, _, _ = connection(MemoryDevice(p, 32))
                    client.identify()
                    a = [[-128]*k for _ in range(m)]
                    b = [[-128 if col % 2 == 0 else 127 for col in range(n)] for _ in range(k)]
                    run, = client.benchmark(a, b, repeats=1, guards=True)
                    self.assertEqual(run['compute_cycles'], expected)
                    self.assertEqual(run['compared_elements'], m*n)

    def test_p8_wrong_active_cycle_counts_are_rejected(self):
        for wrong in (30, 40):
            with self.subTest(wrong=wrong):
                client, device, _ = connection(MemoryDevice(8, 32))
                client.identify()
                device.count_override = {0x88: wrong}
                with self.assertRaisesRegex(AssertionError, 'counter contract mismatch'):
                    client.benchmark([[-128]*9 for _ in range(5)], [[-128]*3 for _ in range(9)])

    def test_cli_rejects_p16_before_opening_com(self):
        with patch.object(runner.GEMM, 'open') as open_com, patch.object(runner, 'qualified_manifest') as gate:
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
                runner.main(['--port', 'UNUSED', '--manifest', 'unused.json', '--p', '16'])
            self.assertEqual(exc.exception.code, 2)
            gate.assert_not_called()
            open_com.assert_not_called()

    def test_cli_qualification_failure_is_nonzero_before_opening_com(self):
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary)/'results'
            with patch.object(runner.GEMM, 'open') as open_com:
                with patch.object(runner, 'qualified_manifest', side_effect=ValueError('evidence changed')):
                    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                        code = runner.main(['--port', 'UNUSED', '--manifest', 'unused.json',
                                            '--p', '8', '--output', str(out)])
                self.assertEqual(code, 1)
                open_com.assert_not_called()
            report = json.loads((out/'summary.json').read_text(encoding='utf-8'))
            self.assertEqual(report['state'], 'FAIL')
            self.assertFalse(report['passed'])
            self.assertEqual(report['error']['message'], 'evidence changed')
            self.assertEqual(report['checked_counts']['completed_jobs'], 0)

    def test_full_overlap_48_job_fixture_preserves_checks_and_binds_mode(self):
        class RepeatedInputsDevice(MemoryDevice):
            """Cache only identical fixture arithmetic; every host check still runs."""
            def __init__(self):
                super().__init__(8, 32, OVERLAP_VERSION)
                self.cached = None

            def complete(self):
                m, n, k = (self.regs[address] for address in (0x18, 0x1c, 0x20))
                a, bt, c = (self.regs[address] for address in (0x24, 0x28, 0x2c))
                sa, sb, sc = (self.regs[address] for address in (0x30, 0x34, 0x38))
                key = (m, n, k, c, sc, tuple(self.read(a+i*sa, k) for i in range(m)),
                       tuple(self.read(bt+j*sb, k) for j in range(n)))
                if self.cached is not None and self.cached[0] == key:
                    _, rows, counts = self.cached
                    for i, raw in enumerate(rows):
                        self.write(c+i*sc, raw)
                    self.regs.update(counts)
                    self.regs[0x0c], self.pending = 0x15, False
                else:
                    super().complete()
                    self.cached = (key, tuple(self.read(c+i*sc, 4*n) for i in range(m)),
                                   {address: self.regs[address] for address in range(0x80, 0xc0, 4)})

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bitstream = b'Unit-test fixture, never programmed to hardware'
            (root/'gemm_ddr.bit').write_bytes(bitstream)
            manifest = dict(schema_version=1, result='PASS', p=8, t=32, kmax=256,
                            core_hz=100000000, baud=115200, source_commit='unit-test', source_dirty=True,
                            source_sha256_utf8_lf={'fixture': 'a'*64}, part='xc7a50ticsg324-1L',
                            build_id=0x19ab27cd, id=0x314d474e, version=OVERLAP_VERSION,
                            kind='overlap_ddr_gemm', mode='selectable', enable_overlap=True,
                            read_slots=4, bitstream='gemm_ddr.bit', bitstream_sha256=hashlib.sha256(bitstream).hexdigest())
            path = root/'build.json'
            path.write_text(json.dumps(manifest))
            client, device, serial = connection(RepeatedInputsDevice())
            # This synchronous software fixture calculates the first output
            # before returning a UART response; its wall time is not hardware.
            client.link.timeout = 30
            with patch.object(runner, 'qualified_manifest', return_value=manifest), \
                    patch.object(runner.GEMM, 'open', return_value=client) as open_com, \
                    patch.object(runner, 'version', return_value='unit-test'), \
                    redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                code = runner.main(['--port', 'FAKE', '--manifest', str(path), '--p', '8', '--mode', '1',
                                    '--timeout', '30', '--output', str(root/'results')])
            report = json.loads((root/'results/results.json').read_text())
            self.assertEqual(code, 0, report.get('error'))
            self.assertEqual(device.starts, 48)
            self.assertEqual(device.regs[0x3c], 1)
            self.assertTrue(serial.closed)
            open_com.assert_called_once_with('FAKE', 115200, 30.0, 2)
            summary = json.loads((root/'results/summary.json').read_text())
            self.assertEqual((report['kind'], report['mode'], report['state']),
                             ('overlap_ddr_gemm_board_qualification', 1, 'PASS'))
            self.assertEqual(report['case_plan'], runner.case_plan(8, 1))
            self.assertEqual(report['case_plan_sha256'], runner.plan_hash(runner.case_plan(8, 1)))
            self.assertEqual([run['job_id'] for run in report['runs']], list(range(1, 49)))
            self.assertTrue(all(run['mode'] == 1 and run['passed'] and run['guard_bytes_checked'] > 0
                                for run in report['runs']))
            self.assertEqual(sum(run['compared_elements'] for run in report['runs']), 49593)
            self.assertEqual(summary['checked_counts']['completed_jobs'], 48)
            self.assertEqual(summary['checked_counts']['compared_outputs'], 49593)
            self.assertEqual((summary['build_identity']['version'], summary['build_identity']['mode'],
                              summary['build_identity']['enable_overlap']), (0x200, 'selectable', True))
            self.assertEqual(summary['results_sha256'], hashlib.sha256((root/'results/results.json').read_bytes()).hexdigest())
            for case in report['cases']:
                detail = json.loads((root/'results'/case['directory']/'results.json').read_text())
                self.assertEqual((detail['kind'], detail['mode']), ('overlap_ddr_gemm_board_case', 1))
                self.assertEqual(detail['identity']['version'], OVERLAP_VERSION)
                self.assertTrue(detail['passed'])


if __name__ == '__main__':
    unittest.main()
