"""Run the bounded DDR diagnostic packet-backend regression."""
import hashlib
import json
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def main():
    build = ROOT / 'build/test_ddr_diag_control'
    build.mkdir(parents=True, exist_ok=True)
    for name in ('summary.json', 'coverage.json', 'results.xml'):
        (build / name).unlink(missing_ok=True)
    from cocotb_tools.runner import get_runner
    source = ROOT / 'rtl/control/gemm_ddr_diag_control.sv'
    files = [source, ROOT / 'tb/test_ddr_diag_control.py', ROOT / 'tb/common.py', Path(__file__).resolve()]
    def hashes():
        return {path.relative_to(ROOT).as_posix(): hashlib.sha256(
            path.read_text(encoding='utf-8').encode()).hexdigest() for path in files}
    inputs = hashes()
    sys.path.insert(0, str(ROOT / 'tb'))
    os.environ['PYTHONPATH'] = str(ROOT / 'tb') + os.pathsep + os.environ.get('PYTHONPATH', '')
    runner = get_runner('icarus')
    runner.build(sources=[source], hdl_toplevel='gemm_ddr_diag_control',
                 parameters={'BUILD_ID': 0x1234abcd}, build_dir=build,
                 build_args=['-g2012', '-Wall'], always=True, timescale=('1ns', '1ps'))
    result = runner.test(hdl_toplevel='gemm_ddr_diag_control', test_module='test_ddr_diag_control',
                         build_dir=build, test_dir=build, extra_env={'GEMM_COVERAGE': str(build / 'coverage.json')})
    tests = list(ET.parse(result).iter('testcase'))
    if not tests or any(list(test.iter(tag)) for test in tests for tag in ('failure', 'error', 'skipped')):
        raise RuntimeError(f'DDR diagnostic backend failed: {result}')
    if hashes() != inputs:
        raise RuntimeError('Sources changed during the DDR diagnostic backend test')
    summary = {'result': 'PASS', 'source_sha256_utf8_lf': inputs}
    (build / 'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
