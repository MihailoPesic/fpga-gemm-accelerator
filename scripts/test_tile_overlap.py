"""Verify concurrent ports on disjoint local buffers; no DDR overlap claim."""
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
CASES = ('disjoint_load_compute_read', 'start_response_ownership', 'start_edge_and_reset')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', type=Path, default=ROOT / 'build/test_tile_overlap')
    parser.add_argument('--only', choices=('p4_t8', 'p4_t32', 'p8_t8', 'p8_t32'))
    args = parser.parse_args()
    out = args.build_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    (out / 'summary.json').unlink(missing_ok=True)
    sources = [ROOT / name for name in (
        'rtl/core/gemm_pe.sv', 'rtl/core/gemm_array.sv', 'rtl/core/gemm_microtile.sv',
        'rtl/memory/gemm_operand_banks.sv', 'rtl/memory/gemm_result_banks.sv',
        'rtl/memory/gemm_bram_microtile.sv', 'rtl/control/gemm_tile_engine.sv')]
    recorded = sources + [ROOT / 'tb/test_tile_overlap.py', ROOT / 'tb/common.py',
                          ROOT / 'requirements-test.txt', Path(__file__).resolve()]
    def hashes():
        return {p.relative_to(ROOT).as_posix(): hashlib.sha256(
            p.read_text(encoding='utf-8').encode()).hexdigest() for p in recorded}
    before = hashes()
    from cocotb_tools.runner import get_runner
    sys.path.insert(0, str(ROOT / 'tb'))
    os.environ['PYTHONPATH'] = str(ROOT / 'tb') + os.pathsep + os.environ.get('PYTHONPATH', '')
    results = []
    for p, t in ((4, 8), (4, 32), (8, 8), (8, 32)):
        name = f'p{p}_t{t}'
        if args.only and args.only != name:
            continue
        build = out / name
        build.mkdir(parents=True, exist_ok=True)
        for stale in ('results.xml', *(case + '.json' for case in CASES)):
            (build / stale).unlink(missing_ok=True)
        runner = get_runner('icarus')
        runner.build(sources=sources, hdl_toplevel='gemm_tile_engine',
                     parameters={'P': p, 'T': t, 'CONCURRENT_PORTS': 1},
                     build_dir=build, build_args=['-g2012', '-Wall'], always=True,
                     timescale=('1ns', '1ps'))
        xml = runner.test(hdl_toplevel='gemm_tile_engine', test_module='test_tile_overlap',
                          build_dir=build, test_dir=build,
                          extra_env={'GEMM_COVERAGE_DIR': str(build)})
        tests = list(ET.parse(xml).iter('testcase'))
        if (sorted(test.get('name') for test in tests) != sorted(CASES) or
                any(list(test.iter(tag)) for test in tests for tag in ('failure', 'error', 'skipped'))):
            raise RuntimeError(f'{name}: concurrent port tests failed: {xml}')
        coverage = {case: json.loads((build / (case + '.json')).read_text()) for case in CASES}
        for case in coverage.values():
            if (case['p'], case['t'], case['seed']) != (p, t, 20261003 + p * 100 + t):
                raise RuntimeError(f'{name}: mismatched coverage configuration')
        results.append(dict(p=p, t=t, concurrent_ports=1, tests=len(tests), result='PASS',
                            directory=name, coverage=coverage))
        if hashes() != before:
            raise RuntimeError('Source inputs changed during test execution')
    counters = ('jobs', 'output_values', 'compute_load_edges', 'compute_read_edges',
                'compute_load_read_edges', 'active_load_blocks', 'active_read_blocks',
                'launch_gap_blocks', 'stalled_response_edges', 'disjoint_start_loads',
                'disjoint_start_reads', 'pending_disjoint_starts', 'held_disjoint_starts',
                'response_owned_start_blocks', 'resets')
    totals = {key: sum(case[key] for result in results for case in result['coverage'].values())
              for key in counters}
    summary = dict(result='PASS', configurations=results, totals=totals,
                   source_sha256_utf8_lf=before,
                   scope='Concurrent local bank ports and response ownership only; production DDR MODE=1 remains unsupported',
                   versions=dict(python=sys.version, cocotb=version('cocotb'),
                                 iverilog=subprocess.run(['iverilog', '-V'], check=True,
                                                        capture_output=True, text=True).stdout.splitlines()[0]))
    (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
