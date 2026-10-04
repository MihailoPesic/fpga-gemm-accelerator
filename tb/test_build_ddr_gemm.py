"""Build evidence gates with synthetic reports; never launches Vivado."""
from contextlib import ExitStack, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import build_ddr_gemm as builder
from host.gemm import load_manifest


def simulation_log(p=4, baud=10_000_000, t=32, overlap=False):
    compute1, compute2 = 1+3*p-1, ((5+p-1)//p)*(9+3*p-1)
    log = (f'DDR_GEMM_JOB_PASS job_id=1 m=1 n=1 k=1 outputs=1 job_cycles=100 compute_cycles={compute1} read_beats=2 write_beats=1 write_bytes=4\n'
           f'DDR_GEMM_JOB_PASS job_id=2 m=5 n=3 k=9 outputs=15 job_cycles=300 compute_cycles={compute2} read_beats=16 write_beats=10 write_bytes=60\n')
    jobs, outputs, reads, writes, responses = 2, 16, 120, 200, 50
    if overlap:
        # Two macrotiles, with one useful column in the last band. These
        # constants are independent expected traffic for the directed shapes.
        count = {(4, 8): (60, 22, 5, 9), (8, 8): (64, 22, 5, 9),
                 (4, 32): (180, 70, 17, 33), (8, 32): (160, 70, 17, 33)}[p, t]
        compute3, reads3, writes3, outputs3 = count
        log += (f'DDR_GEMM_JOB_PASS job_id=3 m=1 n={t+1} k=9 outputs={outputs3} '
                f'job_cycles={compute3+1000} compute_cycles={compute3} read_beats={reads3} '
                f'write_beats={writes3} write_bytes={4*outputs3}\n')
        jobs, outputs, reads, writes, responses = 3, 16+outputs3, 120+reads3, 200+writes3, 53
    return log+(f'DDR_GEMM_FINAL_PASS jobs={jobs} outputs={outputs} sim_baud={baud} '
                f'packets=100 read_beats={reads} write_beats={writes} write_responses={responses}\n')


def physical_fixture(out):
    for name in builder.REPORTS:
        (out/name).write_text('Synthetic report fixture, not implementation evidence\n', encoding='utf-8')
    (out/'timing.txt').write_text('All user specified timing constraints are met.\n', encoding='utf-8')
    (out/'check_timing.txt').write_text('\n'.join(
        f'{index}. checking {name} (0)' for index, name in enumerate(
            ('no_clock', 'constant_clock', 'unconstrained_internal_endpoints', 'loops', 'latch_loops'), 1)), encoding='utf-8')
    (out/'bus_skew.txt').write_text(''.join('    Slow 10.000 1.000 9.000\n' for _ in range(10)), encoding='utf-8')
    (out/'timing_pass.json').write_text(json.dumps(
        {'vivado': 'test fixture', 'wns_ns': 0.25, 'whs_ns': 0.04, 'core_hz': 100_000_000}), encoding='utf-8')
    (out/'gemm_ddr.bit').write_bytes(b'not a physical bitstream')
    return 'Bitgen Completed Successfully\nDDR_GEMM_BUILD_PASS WNS=0.25 WHS=0.04\n'


class GateTests(unittest.TestCase):
    def test_configuration_and_every_source_change_build_id(self):
        baseline = builder.make_identity({'rtl.sv': 'a'*64}, 4, 32, 115200, 10000000)
        self.assertEqual(baseline, builder.make_identity({'rtl.sv': 'a'*64}, 4, 32, 115200, 10000000))
        for args in (({'rtl.sv': 'b'*64}, 4, 32, 115200, 10000000),
                     ({'rtl.sv': 'a'*64}, 8, 32, 115200, 10000000),
                     ({'rtl.sv': 'a'*64}, 4, 8, 115200, 10000000),
                     ({'rtl.sv': 'a'*64}, 4, 32, 1000000, 10000000),
                     ({'rtl.sv': 'a'*64}, 4, 32, 115200, 5000000),
                     ({'rtl.sv': 'a'*64}, 4, 32, 115200, 10000000, 'typical'),
                     ({'rtl.sv': 'a'*64}, 4, 32, 115200, 10000000, 'off', 4)):
            with self.subTest(args=args):
                self.assertNotEqual(baseline['build_id'], builder.make_identity(*args)['build_id'])

    def test_exact_simulation_jobs_for_all_geometries(self):
        for p in (4, 8):
            for t in (8, 32):
                evidence = builder.parse_simulation(simulation_log(p), p, t, 10000000)
                self.assertEqual(len(evidence['jobs']), 2)
                self.assertEqual(evidence['traffic_including_host_transfers']['outputs'], 16)
        warning = 'WARNING: 200 us is required before CKE in the FAST calibration model\n'
        self.assertEqual(builder.parse_simulation(warning+simulation_log(), 4, 32, 10000000)['jobs'][1]['outputs'], 15)

    def test_incomplete_duplicate_or_wrong_simulation_results_fail(self):
        good = simulation_log()
        cases = [good.split('\n', 1)[1], good+good.splitlines()[0]+'\n',
                 good.replace('job_id=2', 'job_id=1'), good.replace('m=5', 'm=4'),
                 good.replace('outputs=15', 'outputs=14'), good.replace('compute_cycles=40', 'compute_cycles=39'),
                 good.replace('job_cycles=100', 'job_cycles=0'), good.replace('read_beats=16 ', 'read_beats=15 '),
                 good.replace('write_beats=10 ', 'write_beats=9 '), good.replace('write_bytes=60', 'write_bytes=64'),
                 good.replace('FINAL_PASS jobs=2', 'FINAL_PASS jobs=3'), good.replace('outputs=16 ', 'outputs=15 '),
                 good.replace('sim_baud=10000000', 'sim_baud=115200'),
                 good.replace('packets=100', 'packets=0'), good.replace('read_beats=120', 'read_beats=1'),
                 good.replace('write_responses=50', 'write_responses=5'),
                 good+good.splitlines()[-1]+'\n', '\n'.join(good.splitlines()[:-1])]
        for index, log in enumerate(cases):
            with self.subTest(index=index), self.assertRaises(ValueError):
                builder.parse_simulation(log, 4, 32, 10000000)

    def test_model_or_tool_failure_overrides_pass_markers(self):
        for failure in ('ERROR: tJIT(per) violation', 'Fatal: testbench assertion',
                        'FATAL: simulation watchdog', 'CRITICAL WARNING: clock constraint',
                        'DDR_GEMM_FAIL mismatch', 'TEST FAILED'):
            with self.subTest(failure=failure), self.assertRaises(RuntimeError):
                builder.parse_simulation(simulation_log()+failure, 4, 32, 10000000)
        with self.assertRaises(RuntimeError):
            builder.reject_failures(simulation_log(), 1)

    def test_snapshot_rejects_source_setting_generated_and_project_changes(self):
        identity = builder.make_identity({'rtl.sv': 'a'*64}, 4, 32, 115200, 10000000)
        snapshot = dict(identity, generated_sha256={'vendor.v': 'b'*64}, project_sha256='c'*64)
        builder.require_snapshot(snapshot, identity, {'vendor.v': 'b'*64}, 'c'*64)
        for changed, generated, project in ((dict(identity, baud=1000000), {'vendor.v': 'b'*64}, 'c'*64),
                                             (identity, {'vendor.v': 'd'*64}, 'c'*64),
                                             (identity, {'vendor.v': 'b'*64}, 'd'*64)):
            with self.assertRaises(ValueError):
                builder.require_snapshot(snapshot, changed, generated, project)

    def test_overlap_selection_changes_identity_and_cannot_reuse_serial_seal(self):
        arguments = ({'rtl.sv': 'a'*64}, 8, 32, 115200, 10000000)
        serial = builder.make_identity(*arguments)
        overlap = builder.make_identity(*arguments, enable_overlap=True)
        self.assertEqual((serial['kind'], serial['version'], serial['mode']), ('serial_ddr_gemm', 0x100, 'serial'))
        self.assertNotIn('enable_overlap', serial)
        self.assertEqual((overlap['kind'], overlap['version'], overlap['mode'], overlap['enable_overlap']),
                         ('overlap_ddr_gemm', 0x200, 'selectable', True))
        self.assertNotEqual(serial['build_id'], overlap['build_id'])
        self.assertEqual(overlap, builder.make_identity(*arguments, enable_overlap=True))
        generated, project = {'vendor.v': 'b'*64}, 'c'*64
        for old, new in ((serial, overlap), (overlap, serial)):
            with self.assertRaisesRegex(ValueError, 'identity differs'):
                builder.require_snapshot(dict(old, generated_sha256=generated, project_sha256=project),
                                         new, generated, project)
        for invalid in (0, 1, 'true', None):
            with self.subTest(selection=invalid), self.assertRaises(ValueError):
                builder.make_identity(*arguments, enable_overlap=invalid)

    def test_overlap_vendor_evidence_requires_exact_third_job_and_counters(self):
        for p, t in ((4, 8), (4, 32), (8, 8), (8, 32)):
            good = simulation_log(p, t=t, overlap=True)
            with self.subTest(p=p, t=t):
                evidence = builder.parse_simulation(good, p, t, 10000000, True)
                third = evidence['jobs'][2]
                self.assertEqual((third['job_id'], third['m'], third['n'], third['k']), (3, 1, t+1, 9))
                self.assertEqual(evidence['traffic_including_host_transfers']['outputs'], 16+t+1)
                with self.assertRaises(ValueError):
                    builder.parse_simulation(good, p, t, 10000000, False)
                with self.assertRaises(ValueError):
                    builder.parse_simulation(simulation_log(p), p, t, 10000000, True)
                lines = good.splitlines()
                for field in ('job_id', 'm', 'n', 'k', 'outputs', 'compute_cycles', 'read_beats', 'write_beats', 'write_bytes'):
                    changed = lines[2].replace(f'{field}={third[field]}', f'{field}={third[field]+1}')
                    with self.subTest(field=field), self.assertRaises(ValueError):
                        builder.parse_simulation('\n'.join([*lines[:2], changed, lines[3]])+'\n', p, t, 10000000, True)
                for bad in (good+lines[2]+'\n', '\n'.join([lines[1], lines[0], *lines[2:]])+'\n',
                            good.replace('FINAL_PASS jobs=3', 'FINAL_PASS jobs=2'),
                            good.replace(f'FINAL_PASS jobs=3 outputs={16+third["outputs"]}', 'FINAL_PASS jobs=3 outputs=16')):
                    with self.assertRaises(ValueError):
                        builder.parse_simulation(bad, p, t, 10000000, True)

    def test_physical_checks_require_reports_met_timing_and_bus_skew(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            log = physical_fixture(out)
            self.assertEqual(builder.physical_checks(out, log)['bus_skew_checks'], 10)
            for text in (log.replace('WNS=0.25', 'WNS=-0.25'), log.replace('WHS=0.04', 'WHS=-0.04'),
                         log.replace('Bitgen Completed Successfully', ''), log+log):
                with self.assertRaises(ValueError):
                    builder.physical_checks(out, text)
            for name, content in (('bus_skew.txt', '    Slow 10.000 1.000 -0.001\n'*10),
                                  ('bus_skew.txt', '    Slow 10.000 1.000 9.000\n'*9),
                                  ('timing.txt', 'Timing failed'),
                                  ('check_timing.txt', '1. checking no_clock (1)')):
                physical_fixture(out)
                (out/name).write_text(content, encoding='utf-8')
                with self.subTest(name=name), self.assertRaises(ValueError):
                    builder.physical_checks(out, log)
            physical_fixture(out)
            (out/'cdc.txt').unlink()
            with self.assertRaises(ValueError):
                builder.physical_checks(out, log)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.out = Path(self.temporary.name)
        self.sources = {'rtl.sv': 'a'*64}
        self.generated = {'project/vendor.v': 'b'*64}
        self.stages = []
        self.hook = lambda stage: None
        self.simulation = simulation_log()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(redirect_stdout(io.StringIO()))
        mocks = {'source_files': lambda: [], 'input_hashes': lambda: dict(self.sources),
                 'platform_configuration': lambda: ('<fixture/>\n', {}),
                 'validate_platform': lambda *args: None,
                 'generated_hashes': lambda out: dict(self.generated),
                 'generated_hash_metadata': lambda out: {'generated_hash_basis': 'fixture', 'generated_raw_mig_xdc_sha256': {}}}
        for name, function in mocks.items():
            self.stack.enter_context(patch.object(builder, name, side_effect=function))
        self.stack.enter_context(patch.object(builder.subprocess, 'run', side_effect=self.vivado_fixture))
        self.stack.enter_context(patch.object(builder.subprocess, 'check_output', return_value='fixture-commit\n'))

    def vivado_fixture(self, command, cwd, stdout, **kwargs):
        stage = Path(command[-1]).stem.removeprefix('config_')
        self.stages.append(stage)
        self.hook(stage)
        project = self.out/'project/axi_platform.xpr'
        if stage == 'prepare':
            project.parent.mkdir()
            project.write_bytes(b'prepared project fixture')
            stdout.write('DDR_GEMM_PREPARED\n')
        elif stage == 'sim':
            project.write_bytes(b'closed simulated project fixture')
            stdout.write(self.simulation)
            (self.out/'simulation_complete.txt').write_text('Vivado fixture version\n')
        elif stage == 'bitstream':
            stdout.write(physical_fixture(self.out))
        else:
            raise AssertionError(stage)
        return builder.subprocess.CompletedProcess(command, 0)

    def run_stage(self, stage, extra=()):
        builder.main(['--stage', stage, '--build-dir', str(self.out), '--vivado', 'fixture-only', *extra])

    def test_two_stage_pipeline_manifest_and_distinct_bauds(self):
        self.run_stage('sim')
        simulation = json.loads((self.out/'simulation.json').read_text())
        self.assertEqual(self.stages, ['prepare', 'sim'])
        self.assertFalse(simulation['physical_uart_baud_qualified'])
        self.assertFalse(simulation['uart_simulation_matches_build_baud'])
        self.assertEqual((simulation['baud'], simulation['simulation_baud']), (115200, 10000000))
        self.assertEqual(simulation['simulation_debug'], 'off')
        self.assertIn('set ddr_sim_debug off', (self.out/'config_prepare.tcl').read_text())
        self.assertEqual(simulation['project_sha256'], hashlib.sha256(b'closed simulated project fixture').hexdigest())
        self.run_stage('bitstream')
        self.assertEqual(self.stages, ['prepare', 'sim', 'bitstream'])
        manifest = load_manifest(self.out/'build.json')
        self.assertEqual(manifest['result'], 'PASS')
        self.assertEqual(manifest['build_id'], simulation['build_id'])
        self.assertEqual(manifest['bitstream'], 'gemm_ddr.bit')
        self.assertEqual(set(manifest['physical_report_sha256_bytes']), set(builder.REPORTS))

    def test_failed_simulation_cannot_authorize_bitstream(self):
        self.simulation += 'ERROR: DDR model timing violation\n'
        with self.assertRaises(RuntimeError):
            self.run_stage('sim')
        self.assertFalse((self.out/'simulation.json').exists())
        with self.assertRaises(ValueError):
            self.run_stage('bitstream')
        self.assertNotIn('bitstream', self.stages)

    def test_mutation_during_preparation_or_simulation_rejects_evidence(self):
        self.hook = lambda stage: self.sources.update({'rtl.sv': 'c'*64})
        with self.assertRaisesRegex(RuntimeError, 'inputs changed'):
            self.run_stage('sim')
        self.assertFalse((self.out/'simulation.json').exists())
        # Start another isolated fixture directory for a generated-file change.
        self.out = self.out/'generated_change'
        self.hook = lambda stage: self.generated.update({'project/vendor.v': 'c'*64}) if stage == 'sim' else None
        with self.assertRaisesRegex(RuntimeError, 'Generated RTL'):
            self.run_stage('sim')
        self.assertFalse((self.out/'simulation.json').exists())

    def test_stale_success_removed_when_settings_or_project_differ(self):
        self.run_stage('sim')
        (self.out/'build.json').write_text('{"result":"PASS"}')
        (self.out/'gemm_ddr.bit').write_bytes(b'stale image')
        with self.assertRaises(ValueError):
            self.run_stage('bitstream', ('--baud', '1000000'))
        self.assertFalse((self.out/'build.json').exists())
        self.assertFalse((self.out/'gemm_ddr.bit').exists())
        self.assertNotIn('bitstream', self.stages)
        (self.out/'project/axi_platform.xpr').write_bytes(b'GUI mutation')
        with self.assertRaises(ValueError):
            self.run_stage('bitstream')

    def test_simulation_log_mutation_blocks_bitstream(self):
        self.run_stage('sim')
        (self.out/'sim_console.txt').write_text(simulation_log()+'extra line\n')
        with self.assertRaisesRegex(ValueError, 'simulation evidence'):
            self.run_stage('bitstream')
        self.assertNotIn('bitstream', self.stages)

    def test_changed_simulation_debug_cannot_reuse_success(self):
        self.run_stage('sim')
        with self.assertRaises(ValueError):
            self.run_stage('bitstream', ('--sim-debug', 'typical'))
        self.assertNotIn('bitstream', self.stages)

    def test_read_depth_is_bound_to_both_stages_and_build_id(self):
        self.run_stage('sim', ('--read-slots', '4'))
        simulation = json.loads((self.out/'simulation.json').read_text())
        self.assertEqual(simulation['read_slots'], 4)
        self.assertIn('set ddr_read_slots 4', (self.out/'config_sim.tcl').read_text())
        with self.assertRaises(ValueError):
            self.run_stage('bitstream', ('--read-slots', '1'))
        self.assertNotIn('bitstream', self.stages)
        self.run_stage('bitstream', ('--read-slots', '4'))
        self.assertEqual(load_manifest(self.out/'build.json')['read_slots'], 4)

    def test_overlap_selection_bound_to_simulation_bitstream_and_tcl(self):
        self.simulation = simulation_log(overlap=True)
        self.run_stage('sim', ('--overlap', '--read-slots', '4'))
        simulation = json.loads((self.out/'simulation.json').read_text())
        self.assertEqual((simulation['kind'], simulation['version'], simulation['mode']),
                         ('overlap_ddr_gemm', 0x200, 'selectable'))
        self.assertIs(simulation['enable_overlap'], True)
        self.assertEqual(len(simulation['jobs']), 3)
        for stage in ('prepare', 'sim'):
            self.assertIn('set ddr_enable_overlap 1', (self.out/f'config_{stage}.tcl').read_text())
        with self.assertRaises(ValueError):
            self.run_stage('bitstream', ('--read-slots', '4'))
        self.assertNotIn('bitstream', self.stages)
        self.run_stage('bitstream', ('--overlap', '--read-slots', '4'))
        manifest = load_manifest(self.out/'build.json')
        self.assertEqual(manifest['build_id'], simulation['build_id'])
        self.assertEqual((manifest['kind'], manifest['version'], manifest['mode'], manifest['enable_overlap']),
                         ('overlap_ddr_gemm', 0x200, 'selectable', True))
        self.assertIn('set ddr_enable_overlap 1', (self.out/'config_bitstream.tcl').read_text())

    def test_serial_two_job_simulation_cannot_qualify_overlap_build(self):
        with self.assertRaises(ValueError):
            self.run_stage('sim', ('--overlap',))
        self.assertFalse((self.out/'simulation.json').exists())
        self.assertNotIn('bitstream', self.stages)


if __name__ == '__main__':
    unittest.main()
