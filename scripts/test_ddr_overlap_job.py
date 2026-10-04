"""Verify validated serial/overlap jobs, status and counters against behavioral AXI RAM."""
import argparse
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
CASES = ('matched_jobs_both_modes', 'validation_and_start_contract',
         'final_b_and_counter_snapshots', 'fault_counters_and_drain',
         'calibration_watchdog_and_clear', 'seeded_mixed_jobs')


def tool_version(name):
    executable = shutil.which(name)
    if executable is None:
        raise RuntimeError(f'{name} is not on PATH')
    result = subprocess.run([executable, '-V'], check=True, capture_output=True, text=True,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    lines = [line.strip() for line in (result.stdout+result.stderr).splitlines() if line.strip()]
    if not lines:
        raise RuntimeError(f'{name} did not report its version')
    return executable, lines[0]


def require_tests(xml, configuration):
    document = ET.parse(xml)
    names = [test.get('name') for test in document.iter('testcase')]
    if sorted(names, key=lambda name: name or '') != sorted(CASES):
        raise RuntimeError(f'{configuration}: expected exactly {CASES}, found {names}: {xml}')
    if any(list(document.iter(tag)) for tag in ('failure', 'error', 'skipped')):
        raise RuntimeError(f'{configuration}: overlap job regression failed or skipped: {xml}')
    for suite in document.iter():
        if suite.tag in ('testsuites', 'testsuite'):
            for attribute in ('failures', 'errors', 'skipped', 'disabled'):
                if float(suite.get(attribute, '0')) != 0:
                    raise RuntimeError(f'{configuration}: nonzero XML {attribute}: {xml}')
    return names


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', type=Path, default=ROOT/'build/test_ddr_overlap_job')
    parser.add_argument('--only', choices=('p4_t8', 'p4_t32', 'p8_t8', 'p8_t32'))
    parser.add_argument('--read-slots', type=int, choices=(1, 4))
    parser.add_argument('--random-jobs', type=int, default=100,
                        help='seeded full-output jobs per configuration; use a smaller smoke count explicitly')
    args = parser.parse_args()
    if not 0 <= args.random_jobs <= 1000:
        parser.error('--random-jobs must be in 0..1000')
    build_root = args.build_dir.resolve()
    build_root.mkdir(parents=True, exist_ok=True)
    (build_root/'summary.json').unlink(missing_ok=True)
    sources = [ROOT/name for name in (
        'rtl/core/gemm_pe.sv', 'rtl/core/gemm_array.sv', 'rtl/core/gemm_microtile.sv',
        'rtl/memory/gemm_operand_banks.sv', 'rtl/memory/gemm_result_banks.sv',
        'rtl/memory/gemm_bram_microtile.sv', 'rtl/control/gemm_tile_engine.sv',
        'rtl/memory/gemm_dma_rows.sv', 'rtl/memory/gemm_axi_burst.sv',
        'rtl/memory/gemm_tile_dma.sv', 'rtl/memory/gemm_tile_dma_read_queue.sv',
        'rtl/memory/gemm_tile_dma_duplex.sv', 'tb/sv/tile_dma_duplex_harness.sv',
        'rtl/control/gemm_tile_scheduler.sv', 'rtl/control/gemm_ddr_overlap_job.sv',
        'tb/sv/ddr_overlap_job_harness.sv')]
    recorded = sources+[ROOT/'tb/test_ddr_overlap_job.py', ROOT/'tb/test_tile_scheduler.py',
                        ROOT/'tb/test_tile_dma_duplex.py', ROOT/'tb/common.py',
                        ROOT/'requirements-test.txt', Path(__file__).resolve()]

    def hashes():
        return {path.relative_to(ROOT).as_posix(): hashlib.sha256(
            path.read_text(encoding='utf-8').encode('utf-8')).hexdigest() for path in recorded}

    before = hashes()
    versions = {name: version(name) for name in ('cocotb', 'cocotbext-axi', 'cocotb-bus', 'scapy')}
    versions.update(python=sys.version, python_executable=sys.executable)
    for tool in ('iverilog', 'vvp'):
        versions[tool+'_executable'], versions[tool] = tool_version(tool)
    sys.path.insert(0, str(ROOT/'tb'))
    os.environ['PYTHONPATH'] = str(ROOT/'tb')+os.pathsep+os.environ.get('PYTHONPATH', '')
    from cocotb_tools.runner import get_runner
    results = []
    depths = (args.read_slots,) if args.read_slots is not None else (1, 4)
    for read_slots in depths:
        for p, t in ((4, 8), (4, 32), (8, 8), (8, 32)):
            name = f'p{p}_t{t}'
            if args.only and args.only != name:
                continue
            configuration = f'read{read_slots}/{name}'
            if hashes() != before:
                raise RuntimeError('Source inputs changed before overlap-job execution')
            build = build_root/f'read{read_slots}'/name
            build.mkdir(parents=True, exist_ok=True)
            for stale in ('results.xml', 'coverage.json'):
                (build/stale).unlink(missing_ok=True)
            runner = get_runner('icarus')
            runner.build(sources=sources, hdl_toplevel='ddr_overlap_job_harness',
                         parameters={'P': p, 'T': t, 'READ_SLOTS': read_slots}, build_dir=build,
                         build_args=['-g2012', '-Wall'], always=True, timescale=('1ns', '1ps'))
            xml = runner.test(hdl_toplevel='ddr_overlap_job_harness', test_module='test_ddr_overlap_job',
                              build_dir=build, test_dir=build,
                              extra_env={'GEMM_P': str(p), 'GEMM_T': str(t),
                                         'GEMM_READ_SLOTS': str(read_slots),
                                         'GEMM_RANDOM_JOBS': str(args.random_jobs),
                                         'GEMM_COVERAGE': str(build/'coverage.json')})
            names = require_tests(xml, configuration)
            coverage_file = build/'coverage.json'
            if not coverage_file.is_file():
                raise RuntimeError(f'{configuration}: missing coverage record')
            coverage = json.loads(coverage_file.read_text(encoding='utf-8'))
            if (coverage.get('p'), coverage.get('t'), coverage.get('read_slots')) != (p, t, read_slots):
                raise RuntimeError(f'{configuration}: mismatched coverage geometry/depth')
            if coverage.get('requested_random_jobs') != args.random_jobs or coverage.get('seeded_jobs') != args.random_jobs:
                raise RuntimeError(f'{configuration}: seeded job count is incomplete')
            if coverage.get('shell_seed') != 20261005:
                raise RuntimeError(f'{configuration}: unexpected shell workload seed')
            if hashes() != before:
                raise RuntimeError('Source inputs changed during overlap-job execution')
            results.append(dict(p=p, t=t, read_slots=read_slots,
                                directory=build.relative_to(build_root).as_posix(),
                                tests=len(names), test_names=names, result='PASS', coverage=coverage))
    after = hashes()
    if after != before:
        raise RuntimeError('Source inputs changed during overlap-job regression')
    totals = {}
    for counter in ('jobs', 'seeded_jobs', 'output_values', 'macrotiles', 'invalid_descriptors'):
        counts = [result['coverage'].get(counter) for result in results]
        if any(type(count) is not int or count < 0 for count in counts):
            raise RuntimeError(f'Missing or invalid coverage counter: {counter}')
        totals[counter] = sum(counts)
    summary = dict(result='PASS', configurations=results, expected_test_names=list(CASES),
                   scope='Validated job shell with tagged scheduler, independent tile DMA contexts, real local compute and behavioral AXI RAM; no packet/UART, vendor, routed or board qualification',
                   random_jobs_per_configuration=args.random_jobs, totals=totals, versions=versions,
                   source_sha256_utf8_lf=before, source_sha256_utf8_lf_after=after)
    (build_root/'summary.json').write_text(json.dumps(summary, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
