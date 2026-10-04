"""Check ordered row sequencing at one/four read-command depths."""
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
    parser.add_argument('--build-dir', type=Path, default=ROOT / 'build/test_dma_rows')
    parser.add_argument('--read-slots', type=int, choices=(1, 4),
                        help='run one read depth; default checks both')
    args = parser.parse_args()
    build_root = args.build_dir.resolve()
    build_root.mkdir(parents=True, exist_ok=True)
    (build_root/'summary.json').unlink(missing_ok=True)
    sources = [ROOT / 'rtl/memory/gemm_dma_rows.sv']
    recorded = sources + [ROOT / 'tb/test_dma_rows.py', ROOT / 'tb/common.py',
                          ROOT / 'requirements-test.txt', Path(__file__).resolve()]

    def hashes():
        return {path.relative_to(ROOT).as_posix(): hashlib.sha256(
            path.read_text(encoding='utf-8').encode('utf-8')).hexdigest() for path in recorded}

    before = hashes()
    sys.path.insert(0, str(ROOT / 'tb'))
    os.environ['PYTHONPATH'] = str(ROOT / 'tb') + os.pathsep + os.environ.get('PYTHONPATH', '')
    from cocotb_tools.runner import get_runner
    configurations = []
    for read_slots in ((args.read_slots,) if args.read_slots is not None else (1, 4)):
        build = build_root/f'read{read_slots}'
        build.mkdir(parents=True, exist_ok=True)
        for name in ('coverage.json', 'results.xml'):
            (build/name).unlink(missing_ok=True)
        runner = get_runner('icarus')
        runner.build(sources=sources, hdl_toplevel='gemm_dma_rows', build_dir=build,
                     parameters={'READ_SLOTS': read_slots},
                     build_args=['-g2012', '-Wall'], always=True, timescale=('1ns', '1ps'))
        result = runner.test(hdl_toplevel='gemm_dma_rows', test_module='test_dma_rows',
                             build_dir=build, test_dir=build,
                             extra_env={'GEMM_READ_SLOTS': str(read_slots),
                                        'GEMM_COVERAGE': str(build/'coverage.json')})
        tests = list(ET.parse(result).iter('testcase'))
        if not tests or any(list(case.iter(tag)) for case in tests for tag in ('failure', 'error', 'skipped')):
            raise RuntimeError(f'DMA row sequencer regression failed: {result}')
        if not (build/'coverage.json').is_file():
            raise RuntimeError('Missing DMA row sequencer coverage record')
        configurations.append({'read_slots': read_slots, 'tests': len(tests),
                               'directory': build.relative_to(build_root).as_posix(),
                               'result': 'PASS',
                               'coverage': json.loads((build/'coverage.json').read_text())})
    if before != hashes():
        raise RuntimeError('Sources changed during DMA row sequencing regression')
    summary = {'result': 'PASS', 'tests': sum(item['tests'] for item in configurations),
               'configurations': configurations,
               'scope': 'Row address/metadata sequencing and completion handshakes; no bank adapter, AXI fabric or physical DDR test',
               'versions': {'cocotb': version('cocotb')},
               'source_sha256_utf8_lf': before}
    (build_root / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
