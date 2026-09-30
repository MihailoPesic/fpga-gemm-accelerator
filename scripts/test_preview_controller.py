"""Run the framed-command backend regression without a UART timing dependency."""
import hashlib
import json
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def main():
    from cocotb_tools.runner import get_runner
    build = ROOT / 'build/test_preview_controller'
    sources = [*(ROOT / 'rtl/core').glob('*.sv'), *(ROOT / 'rtl/memory').glob('*.sv'),
               ROOT / 'rtl/control/gemm_tile_engine.sv', ROOT / 'rtl/control/gemm_preview_controller.sv']
    sources = sorted(sources)
    sys.path.insert(0, str(ROOT / 'tb'))
    os.environ['PYTHONPATH'] = str(ROOT / 'tb') + os.pathsep + os.environ.get('PYTHONPATH', '')
    runner = get_runner('icarus')
    runner.build(sources=sources, hdl_toplevel='gemm_preview_controller', parameters={'BUILD_ID': 0x12345678},
                 build_dir=build, build_args=['-g2012', '-Wall'], always=True, timescale=('1ns', '1ps'))
    result = runner.test(hdl_toplevel='gemm_preview_controller', test_module='test_preview_controller',
                         build_dir=build, test_dir=build, extra_env={'GEMM_COVERAGE': str(build / 'coverage.json')})
    tests = list(ET.parse(result).iter('testcase'))
    if not tests or any(list(test.iter(tag)) for test in tests for tag in ('failure', 'error', 'skipped')):
        raise RuntimeError(f'Preview backend regression failed: {result}')
    hashed = sources + [ROOT / 'tb/test_preview_controller.py', ROOT / 'tb/common.py', Path(__file__).resolve()]
    summary = {'result': 'PASS', 'source_sha256_utf8_lf': {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_text(encoding='utf-8').encode()).hexdigest()
        for path in hashed}}
    (build / 'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
