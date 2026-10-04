"""Verify tagged tile scheduling from prevalidated descriptors against AXI RAM."""
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
CASES = (
    'complete_jobs_both_modes',
    'blocked_store_ownership_and_completion',
    'faults_drain_all_owners',
)


def tool_version(name, flag):
    executable = shutil.which(name)
    if executable is None:
        raise RuntimeError(f'{name} is not on PATH')
    result = subprocess.run([executable, flag], check=True, capture_output=True, text=True,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    lines = [line.strip() for line in (result.stdout + result.stderr).splitlines() if line.strip()]
    if not lines:
        raise RuntimeError(f'{name} did not report its version')
    return {'executable': executable, 'version': lines[0]}


def require_tests(xml, configuration):
    document = ET.parse(xml)
    tests = list(document.iter('testcase'))
    names = [test.get('name') for test in tests]
    if sorted(names, key=lambda name: name or '') != sorted(CASES):
        raise RuntimeError(f'{configuration}: expected exactly {CASES}, found {names}: {xml}')
    if any(list(document.iter(tag)) for tag in ('failure', 'error', 'skipped')):
        raise RuntimeError(f'{configuration}: scheduler regression failed or skipped tests: {xml}')
    for suite in document.iter():
        if suite.tag not in ('testsuites', 'testsuite'):
            continue
        for attribute in ('failures', 'errors', 'skipped', 'disabled'):
            if float(suite.get(attribute, '0')) != 0:
                raise RuntimeError(f'{configuration}: nonzero XML {attribute}: {xml}')
    return names


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', type=Path, default=ROOT / 'build/test_tile_scheduler')
    parser.add_argument('--only', choices=('p4_t8', 'p4_t32', 'p8_t8', 'p8_t32'))
    parser.add_argument('--read-slots', type=int, choices=(1, 4),
                        help='run one read depth; default checks both')
    args = parser.parse_args()
    build_root = args.build_dir.resolve()
    build_root.mkdir(parents=True, exist_ok=True)
    (build_root / 'summary.json').unlink(missing_ok=True)
    sources = [ROOT / name for name in (
        'rtl/core/gemm_pe.sv', 'rtl/core/gemm_array.sv', 'rtl/core/gemm_microtile.sv',
        'rtl/memory/gemm_operand_banks.sv', 'rtl/memory/gemm_result_banks.sv',
        'rtl/memory/gemm_bram_microtile.sv', 'rtl/control/gemm_tile_engine.sv',
        'rtl/memory/gemm_dma_rows.sv', 'rtl/memory/gemm_axi_burst.sv',
        'rtl/memory/gemm_tile_dma.sv', 'rtl/memory/gemm_tile_dma_read_queue.sv',
        'rtl/memory/gemm_tile_dma_duplex.sv', 'tb/sv/tile_dma_duplex_harness.sv',
        'rtl/control/gemm_tile_scheduler.sv', 'tb/sv/tile_scheduler_harness.sv')]
    recorded = sources + [ROOT / 'tb/test_tile_scheduler.py',
                          ROOT / 'tb/test_tile_dma_duplex.py', ROOT / 'tb/common.py',
                          ROOT / 'requirements-test.txt', Path(__file__).resolve()]

    def hashes():
        return {path.relative_to(ROOT).as_posix(): hashlib.sha256(
            path.read_text(encoding='utf-8').encode('utf-8')).hexdigest() for path in recorded}

    before = hashes()
    versions = {name: version(name) for name in ('cocotb', 'cocotbext-axi', 'cocotb-bus', 'scapy')}
    versions['python'] = sys.version
    versions['python_executable'] = sys.executable
    for tool in ('iverilog', 'vvp'):
        identity = tool_version(tool, '-V')
        versions[tool] = identity['version']
        versions[tool + '_executable'] = identity['executable']
    sys.path.insert(0, str(ROOT / 'tb'))
    os.environ['PYTHONPATH'] = str(ROOT / 'tb') + os.pathsep + os.environ.get('PYTHONPATH', '')
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
                raise RuntimeError('Source inputs changed before configuration execution')
            build = build_root / f'read{read_slots}' / name
            build.mkdir(parents=True, exist_ok=True)
            for stale in ('results.xml', 'coverage.json'):
                (build / stale).unlink(missing_ok=True)
            runner = get_runner('icarus')
            runner.build(sources=sources, hdl_toplevel='tile_scheduler_harness',
                         parameters={'P': p, 'T': t, 'READ_SLOTS': read_slots}, build_dir=build,
                         build_args=['-g2012', '-Wall'], always=True, timescale=('1ns', '1ps'))
            xml = runner.test(hdl_toplevel='tile_scheduler_harness', test_module='test_tile_scheduler',
                              build_dir=build, test_dir=build,
                              extra_env={'GEMM_P': str(p), 'GEMM_T': str(t),
                                         'GEMM_READ_SLOTS': str(read_slots),
                                         'GEMM_COVERAGE': str(build / 'coverage.json')})
            names = require_tests(xml, configuration)
            coverage_file = build / 'coverage.json'
            if not coverage_file.is_file():
                raise RuntimeError(f'{configuration}: missing coverage record')
            coverage = json.loads(coverage_file.read_text(encoding='utf-8'))
            if (coverage.get('p'), coverage.get('t'), coverage.get('read_slots')) != (p, t, read_slots):
                raise RuntimeError(f'{configuration}: mismatched coverage configuration')
            if hashes() != before:
                raise RuntimeError('Source inputs changed during configuration execution')
            results.append({'p': p, 't': t, 'read_slots': read_slots,
                            'directory': build.relative_to(build_root).as_posix(),
                            'tests': len(names), 'test_names': names, 'result': 'PASS',
                            'coverage': coverage})
    after = hashes()
    if after != before:
        raise RuntimeError('Source inputs changed during tile scheduler regression')
    totals = {}
    for counter in ('jobs', 'output_values', 'macrotiles'):
        provided = [result['coverage'].get(counter) for result in results]
        if any(value is not None for value in provided):
            if any(type(value) is not int or value < 0 for value in provided):
                raise RuntimeError(f'Missing or invalid coverage counter: {counter}')
            totals[counter] = sum(provided)
    summary = {
        'result': 'PASS', 'configurations': results, 'expected_test_names': list(CASES),
        'scope': 'Internal tagged scheduler with prevalidated descriptors, independent tile DMA contexts, local compute and behavioral AXI RAM; no UART, production MODE=1, public counters or platform qualification',
        'totals': totals, 'versions': versions, 'source_sha256_utf8_lf': before,
        'source_sha256_utf8_lf_after': after,
    }
    (build_root / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
