"""Run the DDR diagnostic through its real burst engine and behavioral AXI RAM."""
import argparse
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', type=Path, default=ROOT / 'build/test_ddr_diag')
    args = parser.parse_args()
    build = args.build_dir.resolve()
    build.mkdir(parents=True, exist_ok=True)
    for name in ('summary.json', 'coverage.json', 'results.xml'):
        (build / name).unlink(missing_ok=True)
    sources = [ROOT/'rtl/control/gemm_ddr_diag.sv', ROOT/'rtl/memory/gemm_axi_burst.sv',
               ROOT/'tb/sv/ddr_diag_harness.sv']
    recorded = sources + [ROOT/'tb/test_ddr_diag.py', ROOT/'tb/common.py',
                           ROOT/'requirements-test.txt', Path(__file__).resolve()]
    def hashes():
        return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_text(encoding='utf-8').encode()).hexdigest()
                for p in recorded}
    before = hashes()
    sys.path.insert(0, str(ROOT/'tb'))
    os.environ['PYTHONPATH'] = str(ROOT/'tb') + os.pathsep + os.environ.get('PYTHONPATH', '')
    from cocotb_tools.runner import get_runner
    runner = get_runner('icarus')
    runner.build(sources=sources, hdl_toplevel='ddr_diag_harness', build_dir=build,
                 build_args=['-g2012', '-Wall'], always=True, timescale=('1ns', '1ps'))
    result = runner.test(hdl_toplevel='ddr_diag_harness', test_module='test_ddr_diag', build_dir=build,
                         test_dir=build, extra_env={'GEMM_COVERAGE': str(build/'coverage.json')})
    tests = list(ET.parse(result).iter('testcase'))
    if not tests or any(list(case.iter(tag)) for case in tests for tag in ('failure', 'error', 'skipped')):
        raise RuntimeError(f'DDR diagnostic test failed: {result}')
    if before != hashes():
        raise RuntimeError('Sources changed during DDR diagnostic test')
    summary = {'result': 'PASS', 'tests': len(tests), 'scope': 'Diagnostic plus production AXI burst engine, behavioral RAM; no vendor bridge or physical DDR',
               'watchdog_limit': 64, 'versions': {name: version(name) for name in ('cocotb', 'cocotbext-axi', 'cocotb-bus')},
               'source_sha256_utf8_lf': before}
    (build/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
