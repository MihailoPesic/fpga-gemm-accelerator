"""Simulate the complete BRAM preview through asynchronous 8N1 pins."""
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
    parser.add_argument('--build-dir', type=Path, default=ROOT / 'build/test_preview_uart')
    args = parser.parse_args()
    from cocotb_tools.runner import get_runner
    sys.path.insert(0, str(ROOT / 'tb'))
    os.environ['PYTHONPATH'] = str(ROOT / 'tb') + os.pathsep + os.environ.get('PYTHONPATH', '')
    sources = sorted((ROOT / 'rtl').rglob('*.sv'))
    legacy = ROOT / 'accelerator nexys.srcs/sources_1/new'
    sources += [legacy / 'uart_rx.sv', legacy / 'uart_tx.sv']
    build = args.build_dir.resolve()
    runner = get_runner('icarus')
    runner.build(sources=sources, hdl_toplevel='gemm_preview_uart', build_dir=build,
                 parameters={'CORE_HZ': 100_000_000, 'BAUD': 5_000_000, 'BUILD_ID': 0x7139a2c5},
                 build_args=['-g2012', '-Wall'], always=True, timescale=('1ns', '1ps'))
    result = runner.test(hdl_toplevel='gemm_preview_uart', test_module='test_preview_uart',
                         build_dir=build, test_dir=build,
                         extra_env={'GEMM_COVERAGE': str(build / 'coverage.json')})
    tests = list(ET.parse(result).iter('testcase'))
    if not tests or any(list(test.iter(tag)) for test in tests for tag in ('failure', 'error', 'skipped')):
        raise RuntimeError(f'UART integration failed: {result}')
    files = sources + [ROOT / 'tb/test_preview_uart.py', ROOT / 'tb/test_packet_transport.py',
                       ROOT / 'tb/common.py', Path(__file__).resolve()]
    summary = {'result': 'PASS', 'clock_hz': 100_000_000, 'simulated_baud': 5_000_000,
               'source_sha256_utf8_lf': {
                   str(path.relative_to(ROOT)): hashlib.sha256(path.read_text(encoding='utf-8').encode()).hexdigest()
                   for path in files}}
    (build / 'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
