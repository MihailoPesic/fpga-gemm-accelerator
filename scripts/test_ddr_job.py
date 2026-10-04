"""Verify full serial DDR GEMM descriptors, matrix results and completion counters."""
import argparse
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', type=Path, default=ROOT / 'build/test_ddr_job')
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
        'rtl/control/gemm_ddr_job.sv',
        'tb/sv/tile_dma_harness.sv', 'tb/sv/ddr_job_harness.sv')]
    recorded = sources + [ROOT / 'tb/test_ddr_job.py', ROOT / 'tb/common.py',
                          ROOT / 'requirements-test.txt', Path(__file__).resolve()]

    def hashes():
        return {p.relative_to(ROOT).as_posix(): hashlib.sha256(
            p.read_text(encoding='utf-8').encode('utf-8')).hexdigest() for p in recorded}

    before = hashes()
    sys.path.insert(0, str(ROOT / 'tb'))
    os.environ['PYTHONPATH'] = str(ROOT / 'tb') + os.pathsep + os.environ.get('PYTHONPATH', '')
    from cocotb_tools.runner import get_runner
    results = []
    configurations = ((depth, p, t) for depth in
                      ((args.read_slots,) if args.read_slots is not None else (1, 4))
                      for p, t in ((4, 8), (4, 32), (8, 8), (8, 32)))
    for read_slots, p, t in configurations:
        name = f'p{p}_t{t}'
        if args.only and args.only != name:
            continue
        build = build_root / f'read{read_slots}' / name
        build.mkdir(parents=True, exist_ok=True)
        for stale in ('results.xml', 'coverage.json'):
            (build / stale).unlink(missing_ok=True)
        runner = get_runner('icarus')
        runner.build(sources=sources, hdl_toplevel='ddr_job_harness',
                     parameters={'P': p, 'T': t, 'READ_SLOTS': read_slots}, build_dir=build,
                     build_args=['-g2012', '-Wall'], always=True, timescale=('1ns', '1ps'))
        result = runner.test(hdl_toplevel='ddr_job_harness', test_module='test_ddr_job',
                             build_dir=build, test_dir=build,
                             extra_env={'GEMM_P': str(p), 'GEMM_T': str(t),
                                        'GEMM_READ_SLOTS': str(read_slots),
                                        'GEMM_COVERAGE': str(build / 'coverage.json')})
        tests = list(ET.parse(result).iter('testcase'))
        if not tests or any(list(case.iter(tag)) for case in tests for tag in ('failure', 'error', 'skipped')):
            raise RuntimeError(f'{name}: DDR job regression failed: {result}')
        if not (build / 'coverage.json').is_file():
            raise RuntimeError(f'{name}: missing coverage record')
        results.append({'p': p, 't': t, 'read_slots': read_slots,
                        'directory': build.relative_to(build_root).as_posix(),
                        'tests': len(tests), 'result': 'PASS',
                        'coverage': json.loads((build / 'coverage.json').read_text())})
    if before != hashes():
        raise RuntimeError('Source files changed during DDR job regression')
    summary = {'result': 'PASS', 'configurations': results,
               'scope': 'Portable serial descriptor controller, row adapter, production tile engine and burst engine against behavioral AXI RAM; no UART, vendor DDR simulation or board measurement',
               'versions': {name: version(name) for name in ('cocotb', 'cocotbext-axi', 'cocotb-bus', 'scapy')},
               'source_sha256_utf8_lf': before}
    summary['versions']['python'] = sys.version
    summary['versions']['iverilog'] = subprocess.run(
        ['iverilog', '-V'], check=True, capture_output=True, text=True).stdout.splitlines()[0]
    counters = ('jobs', 'output_values', 'invalid_descriptors', 'macrotiles',
                'axi_read_beats', 'axi_write_beats', 'axi_write_responses')
    summary['totals'] = {name: sum(item['coverage'][name] for item in results) for name in counters}
    (build_root / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
