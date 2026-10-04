"""Check the DDR register map and deferred command responses with a job-interface model."""
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
    parser.add_argument('--build-dir', type=Path, default=ROOT / 'build/test_ddr_registers')
    parser.add_argument('--only', choices=('p4_t8', 'p4_t32', 'p8_t8', 'p8_t32'))
    args = parser.parse_args()
    build_root = args.build_dir.resolve()
    build_root.mkdir(parents=True, exist_ok=True)
    (build_root / 'summary.json').unlink(missing_ok=True)
    source = ROOT / 'rtl/control/gemm_ddr_registers.sv'
    recorded = [source, ROOT / 'tb/test_ddr_registers.py', ROOT / 'tb/common.py',
                ROOT / 'requirements-test.txt', Path(__file__).resolve()]

    def hashes():
        return {p.relative_to(ROOT).as_posix(): hashlib.sha256(
            p.read_text(encoding='utf-8').encode('utf-8')).hexdigest() for p in recorded}

    before = hashes()
    sys.path.insert(0, str(ROOT / 'tb'))
    os.environ['PYTHONPATH'] = str(ROOT / 'tb') + os.pathsep + os.environ.get('PYTHONPATH', '')
    from cocotb_tools.runner import get_runner
    results = []
    for p, t in ((4, 8), (4, 32), (8, 8), (8, 32)):
        name = f'p{p}_t{t}'
        if args.only and args.only != name:
            continue
        build = build_root / name
        build.mkdir(parents=True, exist_ok=True)
        for stale in ('results.xml', 'coverage.json'):
            (build / stale).unlink(missing_ok=True)
        params = {'P': p, 'T': t, 'BUILD_ID': 0xa1000000+(p << 8)+t}
        identity = {'ID': 0x314d474e, 'VERSION': 0x00010000, 'CORE_HZ': 100000000}
        if (p, t) == (8, 32):
            identity = {'ID': 0x78563412, 'VERSION': 0x00020304, 'CORE_HZ': 125000000}
            params.update(identity)
        runner = get_runner('icarus')
        runner.build(sources=[source], hdl_toplevel='gemm_ddr_registers',
                     parameters=params, build_dir=build,
                     build_args=['-g2012', '-Wall'], always=True, timescale=('1ns', '1ps'))
        result = runner.test(hdl_toplevel='gemm_ddr_registers', test_module='test_ddr_registers',
                             build_dir=build, test_dir=build,
                             extra_env={'GEMM_PARAMETERS': json.dumps(dict(identity, **params)),
                                        'GEMM_COVERAGE': str(build / 'coverage.json')})
        tests = list(ET.parse(result).iter('testcase'))
        if not tests or any(list(case.iter(tag)) for case in tests for tag in ('failure', 'error', 'skipped')):
            raise RuntimeError(f'{name}: DDR register regression failed: {result}')
        coverage = build / 'coverage.json'
        if not coverage.is_file():
            raise RuntimeError(f'{name}: missing coverage record')
        results.append({'p': p, 't': t, 'parameters': dict(identity, **params),
                        'tests': len(tests), 'result': 'PASS', 'coverage': json.loads(coverage.read_text())})
    if before != hashes():
        raise RuntimeError('Source files changed during DDR register regression')
    summary = {'result': 'PASS', 'configurations': results,
               'scope': 'Local register request/response interface with independent job status/handshake model; no UART or DDR integration',
               'versions': {'cocotb': version('cocotb'), 'python': sys.version,
                            'iverilog': subprocess.run(['iverilog', '-V'], check=True,
                                capture_output=True, text=True).stdout.splitlines()[0]},
               'source_sha256_utf8_lf': before}
    (build_root / 'summary.json').write_text(json.dumps(summary, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
