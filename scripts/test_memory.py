"""Verify synchronous operand banks and prefetch with the production compute core."""
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
    parser.add_argument('--build-dir', type=Path, default=ROOT / 'build/test_memory')
    args = parser.parse_args()
    from cocotb_tools.runner import get_runner

    sources = sorted((ROOT / 'rtl/core').glob('*.sv')) + sorted((ROOT / 'rtl/memory').glob('*.sv'))
    sys.path.insert(0, str(ROOT / 'tb'))
    os.environ['PYTHONPATH'] = str(ROOT / 'tb') + os.pathsep + os.environ.get('PYTHONPATH', '')
    results = []
    for p, t in ((4, 8), (4, 32), (8, 8), (8, 32)):
        build = args.build_dir.resolve() / f'p{p}_t{t}'
        runner = get_runner('icarus')
        runner.build(sources=sources, hdl_toplevel='gemm_bram_microtile',
                     parameters={'P': p, 'T': t}, build_dir=build,
                     build_args=['-g2012', '-Wall'], always=True, timescale=('1ns', '1ps'))
        result = runner.test(hdl_toplevel='gemm_bram_microtile', test_module='test_bram_microtile',
                             build_dir=build, test_dir=build,
                             extra_env={'GEMM_COVERAGE': str(build / 'coverage.json')})
        tests = list(ET.parse(result).iter('testcase'))
        if not tests or any(list(x.iter(tag)) for x in tests for tag in ('failure', 'error', 'skipped')):
            raise RuntimeError(f'P={p} T={t} failed: {result}')
        results.append({'p': p, 't': t, 'result': 'PASS'})
    hashed = sources + [ROOT / 'tb/test_bram_microtile.py', ROOT / 'tb/common.py', Path(__file__).resolve()]
    summary = {'results': results, 'source_sha256_utf8_lf': {
        str(f.relative_to(ROOT)): hashlib.sha256(f.read_text(encoding='utf-8').encode()).hexdigest()
        for f in hashed}}
    (args.build_dir / 'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
