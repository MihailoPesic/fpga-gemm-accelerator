"""Simulate and build the DDR GEMM system for Nexys A7-50T."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from build_ddr_diag import (generated_hashes, generated_hash_metadata,
                            platform_configuration, validate_platform)
from test_mig import byte_hash, tcl_path, text_hash

ROOT = Path(__file__).resolve().parents[1]
CORE_HZ = 100_000_000
PART = 'xc7a50ticsg324-1L'
SIM_CASES = ((1, 1, 1, 1), (2, 5, 3, 9))  # job_id, M, N, K
REPORTS = ('timing.txt', 'check_timing.txt', 'utilization.txt', 'clocks.txt',
           'clock_interaction.txt', 'clock_utilization.txt', 'route.txt', 'drc.txt',
           'methodology.txt', 'cdc.txt', 'pulse_width.txt',
           'pulse_width_violations.txt', 'bus_skew.txt')


def source_files():
    return [ROOT/name for name in (
        'rtl/core/gemm_pe.sv', 'rtl/core/gemm_array.sv', 'rtl/core/gemm_microtile.sv',
        'rtl/memory/gemm_operand_banks.sv', 'rtl/memory/gemm_result_banks.sv',
        'rtl/memory/gemm_bram_microtile.sv', 'rtl/control/gemm_tile_engine.sv',
        'rtl/memory/gemm_dma_rows.sv', 'rtl/memory/gemm_tile_dma.sv',
        'rtl/memory/gemm_tile_dma_read_queue.sv',
        'rtl/memory/gemm_tile_dma_duplex.sv',
        'rtl/control/gemm_tile_scheduler.sv', 'rtl/control/gemm_ddr_overlap_job.sv',
        'rtl/memory/gemm_axi_burst.sv', 'rtl/control/gemm_ddr_job.sv',
        'rtl/control/gemm_ddr_registers.sv', 'rtl/control/gemm_packet_transport.sv',
        'rtl/control/gemm_ddr_control.sv', 'rtl/control/gemm_ddr_core.sv',
        'accelerator nexys.srcs/sources_1/new/uart_rx.sv',
        'accelerator nexys.srcs/sources_1/new/uart_tx.sv',
        'platform/nexys_a7/axi_ddr_platform.sv', 'platform/nexys_a7/gemm_ddr_top.sv')]


def input_files():
    # Imported helpers are build inputs: their changes invalidate the same seal
    # as RTL, constraints, fixture and generator changes.
    return source_files()+[ROOT/name for name in (
        'scripts/build_ddr_gemm.py', 'scripts/build_ddr_gemm.tcl',
        'scripts/build_ddr_diag.py', 'scripts/create_axi_platform.tcl', 'scripts/test_mig.py',
        'tb/vendor/tb_ddr_gemm.sv', 'platform/nexys_a7/axi_ddr.xdc',
        'platform/nexys_a7/gemm_ddr.xdc',
        'accelerator nexys.srcs/sources_1/ip/mig_7series_0/mig_a.prj')]


def input_hashes():
    return {path.relative_to(ROOT).as_posix(): text_hash(path) for path in input_files()}


def make_identity(hashes, p, t, baud, sim_baud, sim_debug='off', read_slots=1, enable_overlap=False):
    if type(read_slots) is not int or read_slots not in (1, 4):
        raise ValueError('Read-command depth must be 1 or 4')
    identity = dict(kind='serial_ddr_gemm', source_sha256_utf8_lf=hashes,
                    part=PART, core_hz=CORE_HZ, p=p, t=t, kmax=256,
                    baud=baud, simulation_baud=sim_baud, simulation_debug=sim_debug,
                    id=0x314d474e, version=0x100, mode='serial', read_slots=read_slots)
    if type(enable_overlap) is not bool:
        raise ValueError('Overlap build selection must be boolean')
    if enable_overlap:
        identity.update(kind='overlap_ddr_gemm', version=0x200, mode='selectable', enable_overlap=True)
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).digest()
    return dict(identity, build_id=int.from_bytes(digest[:4], 'little'))


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2)+'\n', encoding='utf-8', newline='\n')


def reject_failures(log, exit_code=0):
    if exit_code or re.search(r'\b(?:ERROR|FATAL|CRITICAL WARNING)\s*:|DDR_GEMM_FAIL|TEST FAILED', log, re.I):
        raise RuntimeError('Vivado or its simulation reported a failure; inspect the preserved console')


def parse_simulation(log, p, t, sim_baud, enable_overlap=False):
    reject_failures(log)
    pattern = (r'^DDR_GEMM_JOB_PASS job_id=(\d+) m=(\d+) n=(\d+) k=(\d+) outputs=(\d+) '
               r'job_cycles=(\d+) compute_cycles=(\d+) read_beats=(\d+) write_beats=(\d+) write_bytes=(\d+)\s*$')
    matches = re.findall(pattern, log, re.M)
    cases = SIM_CASES + ((3, 1, t+1, 9),) if enable_overlap else SIM_CASES
    if len(matches) != len(cases):
        raise ValueError(f'Expected exactly {len(cases)} complete UART GEMM job markers')
    jobs = []
    names = ('job_id', 'm', 'n', 'k', 'outputs', 'job_cycles', 'compute_cycles',
             'read_beats', 'write_beats', 'write_bytes')
    for values, expected in zip(matches, cases):
        job = dict(zip(names, map(int, values)))
        job_id, m, n, k = expected
        counts = {'outputs': m*n, 'compute_cycles': ((m+p-1)//p)*((n+p-1)//p)*(k+3*p-1),
                  'read_beats': ((k+7)//8)*(m*((n+t-1)//t)+n*((m+t-1)//t)),
                  'write_beats': m*((n+1)//2), 'write_bytes': 4*m*n}
        if (tuple(job[key] for key in names[:4]) != (job_id, m, n, k) or
                any(job[key] != value for key, value in counts.items()) or
                job['job_cycles'] < job['compute_cycles']):
            raise ValueError('Vendor job dimensions, comparisons or counters differ from the fixture contract')
        jobs.append(job)
    final = re.findall(
        r'^DDR_GEMM_FINAL_PASS jobs=(\d+) outputs=(\d+) sim_baud=(\d+) packets=(\d+) '
        r'read_beats=(\d+) write_beats=(\d+) write_responses=(\d+)\s*$', log, re.M)
    if len(final) != 1:
        raise ValueError('Missing or repeated final UART/DDR GEMM marker')
    traffic = dict(zip(('jobs', 'outputs', 'sim_baud', 'packets', 'read_beats', 'write_beats',
                        'write_responses'), map(int, final[0])))
    if (traffic['jobs'] != len(jobs) or traffic['outputs'] != sum(job['outputs'] for job in jobs) or
            traffic['sim_baud'] != sim_baud or traffic['packets'] < 1 or
            traffic['read_beats'] < sum(job['read_beats'] for job in jobs) or
            traffic['write_beats'] < sum(job['write_beats'] for job in jobs) or
            traffic['write_responses'] < sum(job['m'] for job in jobs)):
        raise ValueError('Final UART/DDR GEMM totals differ from the completed jobs')
    return {'jobs': jobs, 'traffic_including_host_transfers': traffic}


def require_snapshot(snapshot, identity, generated, project_hash):
    if any(snapshot.get(key) != value for key, value in identity.items()):
        raise ValueError('Saved project identity differs from these sources or settings; use a fresh --build-dir')
    if snapshot.get('generated_sha256') != generated or snapshot.get('project_sha256') != project_hash:
        raise ValueError('Generated platform or project changed; use a fresh --build-dir')


def physical_checks(out, log):
    reject_failures(log)
    checks = json.loads((out/'timing_pass.json').read_text(encoding='utf-8'))
    matches = re.findall(r'^DDR_GEMM_BUILD_PASS WNS=(-?[\d.]+) WHS=(-?[\d.]+)\s*$', log, re.M)
    if (len(matches) != 1 or 'Bitgen Completed Successfully' not in log or
            tuple(map(float, matches[0])) != (checks['wns_ns'], checks['whs_ns']) or
            min(checks['wns_ns'], checks['whs_ns']) < 0 or checks['core_hz'] != CORE_HZ):
        raise ValueError('Missing successful routed timing and bitstream gates')
    for name in REPORTS:
        if not (out/name).is_file() or not (out/name).stat().st_size:
            raise ValueError(f'Missing physical report: {name}')
    if 'All user specified timing constraints are met.' not in (out/'timing.txt').read_text():
        raise ValueError('Timing summary does not report all constraints met')
    timing = (out/'check_timing.txt').read_text()
    for category in ('no_clock', 'constant_clock', 'unconstrained_internal_endpoints', 'loops', 'latch_loops'):
        if not re.search(rf'^\d+\. checking {category} \(0\)\s*$', timing, re.M):
            raise ValueError(f'check_timing did not pass {category}')
    skew = (out/'bus_skew.txt').read_text().split(
        '\n2. Bus Skew Report Per Constraint\n---------------------------------', 1)[0]
    slacks = re.findall(r'^\s+(?:Slow|Fast)\s+[\d.]+\s+[\d.]+\s+(-?[\d.]+)\s*$', skew, re.M)
    if len(slacks) != 10 or min(map(float, slacks)) < 0:
        raise ValueError('All ten generated SmartConnect bus-skew checks must pass')
    return dict(checks, bus_skew_checks=len(slacks), bus_skew_worst_slack_ns=min(map(float, slacks)))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=('sim', 'bitstream'), default='sim')
    parser.add_argument('--p', type=int, choices=(4, 8), default=4)
    parser.add_argument('--t', type=int, choices=(8, 32), default=32)
    parser.add_argument('--read-slots', type=int, choices=(1, 4), default=1,
                        help='outstanding read-command depth')
    parser.add_argument('--overlap', action='store_true',
                        help='build the selectable serial/overlap scheduler (development version 0x200)')
    parser.add_argument('--baud', type=int, choices=(115200, 1000000), default=115200,
                        help='physical build UART baud; independent of accelerated vendor simulation')
    parser.add_argument('--sim-baud', type=int, choices=(115200, 1000000, 5000000, 10000000), default=10000000)
    parser.add_argument('--sim-debug', choices=('off', 'typical'), default='off',
                        help='off for batch regression; typical enables waveform inspection in a separate build')
    parser.add_argument('--build-dir', type=Path, default=ROOT/'build/gemm')
    parser.add_argument('--vivado', default=shutil.which('vivado') or 'C:/AMDDesignTools/2026.1/Vivado/bin/vivado.bat')
    args = parser.parse_args(argv)
    out = args.build_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    stale = ('simulation.json', 'simulation_complete.txt', 'build.json') if args.stage == 'sim' else (
        'build.json', 'timing_pass.json', 'gemm_ddr.bit')
    for name in stale:
        (out/name).unlink(missing_ok=True)
    sources, hashes = source_files(), input_hashes()
    identity = make_identity(hashes, args.p, args.t, args.baud, args.sim_baud,
                             args.sim_debug, args.read_slots, args.overlap)
    xml, pins = platform_configuration()
    prj, project = out/'mig_axi.prj', out/'project/axi_platform.xpr'
    seal, simulation = out/'generated_state.json', out/'simulation.json'
    kwargs = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}

    def unchanged():
        if input_hashes() != hashes:
            raise RuntimeError('Build inputs changed during the run; use a fresh --build-dir')

    def snapshot():
        return dict(identity, generated_sha256=generated_hashes(out),
                    **generated_hash_metadata(out), project_sha256=byte_hash(project))

    def run(stage):
        config = out/f'config_{stage}.tcl'
        config.write_text('\n'.join([
            f'set ddr_root {tcl_path(ROOT)}', f'set ddr_out {tcl_path(out)}', f'set ddr_stage {stage}',
            f'set ddr_build_id {identity["build_id"]}', f'set ddr_p {args.p}', f'set ddr_t {args.t}',
            f'set ddr_read_slots {args.read_slots}',
            f'set ddr_enable_overlap {int(args.overlap)}',
            f'set ddr_baud {args.baud}', f'set ddr_sim_baud {args.sim_baud}',
            f'set ddr_sim_debug {args.sim_debug}',
            f'set ddr_sources [list {" ".join(tcl_path(path) for path in sources)}]', '']), encoding='utf-8')
        console = out/f'{stage}_console.txt'
        command = [args.vivado, '-mode', 'batch', '-notrace', '-source',
                   str(ROOT/'scripts/build_ddr_gemm.tcl'), '-log', str(out/f'{stage}.log'),
                   '-journal', str(out/f'{stage}.jou'), '-tclargs', str(config)]
        config_hash = byte_hash(config)
        print(f'DDR GEMM {stage}; P{args.p}/T{args.t}; build ID 0x{identity["build_id"]:08x}; {console}', flush=True)
        with console.open('w', encoding='utf-8') as stream:
            result = subprocess.run(command, cwd=out, stdout=stream, stderr=subprocess.STDOUT, **kwargs)
        (out/f'{stage}_vivado_exit.txt').write_text(str(result.returncode)+'\n', encoding='utf-8')
        unchanged()
        if byte_hash(config) != config_hash:
            raise RuntimeError('Build configuration changed during Vivado execution')
        log = console.read_text(encoding='utf-8', errors='replace')
        reject_failures(log, result.returncode)
        return log, console

    if prj.exists() and prj.read_text(encoding='utf-8') != xml:
        raise ValueError('MIG configuration changed; use a fresh --build-dir')
    if project.exists():
        if not seal.exists():
            raise ValueError('Unsealed existing project; use a fresh --build-dir')
        require_snapshot(json.loads(seal.read_text()), identity, generated_hashes(out), byte_hash(project))
    elif args.stage == 'bitstream':
        raise ValueError('Run --stage sim successfully before building a bitstream')
    if args.stage == 'bitstream':
        if not simulation.is_file():
            raise ValueError('Run --stage sim successfully before building a bitstream')
        passed = json.loads(simulation.read_text())
        require_snapshot(passed, identity, generated_hashes(out), byte_hash(project))
        if (passed.get('result') != 'PASS' or passed.get('schema_version') != 1 or
                passed.get('console_sha256_bytes') != byte_hash(out/'sim_console.txt')):
            raise ValueError('Missing matching successful vendor simulation evidence')
        parse_simulation((out/'sim_console.txt').read_text(), args.p, args.t, args.sim_baud, args.overlap)
    prj.write_text(xml, encoding='utf-8')
    write_json(out/'inputs.json', identity)
    git = lambda *cmd: subprocess.check_output(['git', '-C', str(ROOT), *cmd], text=True, **kwargs).strip()
    provenance = dict(source_commit=git('rev-parse', 'HEAD'), source_dirty=bool(git('status', '--porcelain')))
    if not project.exists():
        preparation, _ = run('prepare')
        if not re.search(r'^DDR_GEMM_PREPARED\s*$', preparation, re.M):
            raise ValueError('Missing completed project preparation marker')
        validate_platform(out, xml, pins)
        write_json(seal, snapshot())
    before_generated = generated_hashes(out)
    log, console = run(args.stage)
    if generated_hashes(out) != before_generated:
        raise RuntimeError('Generated RTL, memory models, constraints or configuration changed during the run')
    validate_platform(out, xml, pins)
    current = snapshot()
    # Closing a project may change its bookkeeping. Bind implementation to the
    # exact final project bytes after successful simulation, never the old seal.
    write_json(seal, current)
    common = dict(current, **provenance, schema_version=1, result='PASS',
                  ddr_pins_preserved=len(pins), source_hashes_unchanged=True,
                  vivado_exit_code=0, physical_board_validated=False, warm_reset_qualified=False,
                  configuration_sha256_utf8_lf=text_hash(prj), console_sha256_bytes=byte_hash(console),
                  console_sha256_utf8_lf=text_hash(console),
                  warnings=[line for line in log.splitlines() if 'WARNING:' in line])
    if args.stage == 'sim':
        evidence = parse_simulation(log, args.p, args.t, args.sim_baud, args.overlap)
        completion = out/'simulation_complete.txt'
        if not completion.is_file() or not completion.read_text().strip():
            raise ValueError('Vivado did not close the completed simulation normally')
        summary = dict(common, **evidence, vivado=completion.read_text().strip(),
                       scope='Complete framed-UART jobs through GEMM, SmartConnect, MIG and unchanged DDR2 model; selectable scheduler includes a multi-tile MODE=1 job; no physical-board claim' if args.overlap else 'Two complete framed-UART jobs through production GEMM, SmartConnect, MIG and unchanged DDR2 model; no physical-board claim',
                       calibration='FAST', cold_start_only=True, physical_pcb_delays=False,
                       uart_simulation_matches_build_baud=args.sim_baud == args.baud,
                       physical_uart_baud_qualified=False)
        write_json(simulation, summary)
        print(f'PASS: {simulation}', flush=True)
    else:
        checks = physical_checks(out, log)
        bitstream = out/'gemm_ddr.bit'
        if not bitstream.is_file() or not bitstream.stat().st_size:
            raise ValueError('Missing nonempty GEMM bitstream')
        manifest = dict(common, bitstream=bitstream.name, bitstream_sha256=byte_hash(bitstream),
                        physical_checks=checks, physical_report_sha256_bytes={name: byte_hash(out/name) for name in REPORTS},
                        simulation_sha256_bytes=byte_hash(simulation),
                        scope='Selectable serial/overlap DDR-backed INT8 GEMM implementation; physical-board matrix validation pending' if args.overlap else 'Serial DDR-backed INT8 GEMM implementation; physical-board matrix validation pending')
        write_json(out/'build.json', manifest)
        print(f'PASS: {out/"build.json"}', flush=True)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
