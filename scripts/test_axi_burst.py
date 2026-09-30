"""Check the serial AXI burst primitive against RAM and adversarial responders."""
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
    parser.add_argument('--build-dir', type=Path, default=ROOT / 'build/test_axi_burst')
    args = parser.parse_args()
    build = args.build_dir.resolve()
    build.mkdir(parents=True, exist_ok=True)
    for name in ('summary.json', 'coverage.json', 'results.xml'):
        (build / name).unlink(missing_ok=True)
    from cocotb_tools.runner import get_runner
    sys.path.insert(0, str(ROOT / 'tb'))
    os.environ['PYTHONPATH'] = str(ROOT / 'tb') + os.pathsep + os.environ.get('PYTHONPATH', '')
    source = ROOT / 'rtl/memory/gemm_axi_burst.sv'
    files = [source, ROOT / 'tb/test_axi_burst.py', ROOT / 'tb/common.py',
             ROOT / 'requirements-test.txt', Path(__file__).resolve()]
    def hashes():
        return {str(path.relative_to(ROOT)): hashlib.sha256(
            path.read_text(encoding='utf-8').encode()).hexdigest() for path in files}
    source_hashes = hashes()
    runner = get_runner('icarus')
    runner.build(sources=[source], hdl_toplevel='gemm_axi_burst', build_dir=build,
                 build_args=['-g2012', '-Wall'], always=True, timescale=('1ns', '1ps'))
    result = runner.test(hdl_toplevel='gemm_axi_burst', test_module='test_axi_burst',
                         build_dir=build, test_dir=build,
                         extra_env={'GEMM_COVERAGE': str(build / 'coverage.json')})
    tests = list(ET.parse(result).iter('testcase'))
    if not tests or any(list(test.iter(tag)) for test in tests for tag in ('failure', 'error', 'skipped')):
        raise RuntimeError(f'AXI burst regression failed: {result}')
    if hashes() != source_hashes:
        raise RuntimeError('Sources changed during AXI tests; rerun before recording evidence')
    summary = {'result': 'PASS', 'tests': len(tests), 'versions': {
        name: version(name) for name in ('cocotb', 'cocotbext-axi', 'cocotb-bus', 'scapy')},
        'source_sha256_utf8_lf': source_hashes}
    (build / 'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
