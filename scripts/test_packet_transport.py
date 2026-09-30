"""Run the framed UART transport regression without vendor libraries."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', type=Path, default=ROOT / 'build/test_packet_transport')
    args = parser.parse_args()
    from cocotb_tools.runner import get_runner
    sys.path.insert(0, str(ROOT / 'tb'))
    os.environ['PYTHONPATH'] = str(ROOT / 'tb') + os.pathsep + os.environ.get('PYTHONPATH', '')
    build = args.build_dir.resolve()
    source = ROOT / 'rtl/control/gemm_packet_transport.sv'
    runner = get_runner('icarus')
    runner.build(sources=[source], hdl_toplevel='gemm_packet_transport', build_dir=build,
                 build_args=['-g2012', '-Wall'], always=True, timescale=('1ns', '1ps'))
    result = runner.test(hdl_toplevel='gemm_packet_transport', test_module='test_packet_transport',
                         build_dir=build, test_dir=build,
                         extra_env={'GEMM_COVERAGE': str(build / 'coverage.json')})
    tests = list(ET.parse(result).iter('testcase'))
    if not tests or any(list(test.iter(tag)) for test in tests for tag in ('failure', 'error', 'skipped')):
        raise RuntimeError(f'Transport failed: {result}')
    files = [source, ROOT / 'tb/test_packet_transport.py', ROOT / 'tb/common.py', Path(__file__).resolve()]
    summary = {'result': 'PASS', 'source_sha256_utf8_lf': {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_text(encoding='utf-8').encode()).hexdigest()
        for path in files}}
    (build / 'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
