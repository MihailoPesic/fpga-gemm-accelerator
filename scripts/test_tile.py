"""Run result-bank and complete local-matrix regressions."""
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
    parser.add_argument('--build-dir', type=Path, default=ROOT / 'build/test_tile')
    parser.add_argument('--only', choices=('p4_t8', 'p4_t32', 'p8_t8', 'p8_t32'))
    args = parser.parse_args()
    from cocotb_tools.runner import get_runner
    sources = sorted((ROOT / 'rtl').rglob('*.sv'))
    sys.path.insert(0, str(ROOT / 'tb'))
    os.environ['PYTHONPATH'] = str(ROOT / 'tb') + os.pathsep + os.environ.get('PYTHONPATH', '')
    results = []
    for p, t in ((4, 8), (4, 32), (8, 8), (8, 32)):
        name = f'p{p}_t{t}'
        if args.only and args.only != name:
            continue
        for top, module in (('gemm_result_banks', 'test_result_banks'), ('gemm_tile_engine', 'test_tile_engine')):
            build = args.build_dir.resolve() / name / top
            runner = get_runner('icarus')
            runner.build(sources=sources, hdl_toplevel=top, parameters={'P': p, 'T': t},
                         build_dir=build, build_args=['-g2012', '-Wall'], always=True, timescale=('1ns', '1ps'))
            result = runner.test(hdl_toplevel=top, test_module=module, build_dir=build, test_dir=build,
                                 extra_env={'GEMM_COVERAGE': str(build / 'coverage.json')})
            tests = list(ET.parse(result).iter('testcase'))
            if not tests or any(list(x.iter(tag)) for x in tests for tag in ('failure', 'error', 'skipped')):
                raise RuntimeError(f'{name} {top} failed: {result}')
            results.append({'p': p, 't': t, 'module': top, 'result': 'PASS'})
    hashed = sources + [ROOT / 'tb/test_result_banks.py', ROOT / 'tb/test_tile_engine.py', ROOT / 'tb/common.py', Path(__file__).resolve()]
    summary = {'results': results, 'source_sha256_utf8_lf': {
        str(f.relative_to(ROOT)): hashlib.sha256(f.read_text(encoding='utf-8').encode()).hexdigest() for f in hashed}}
    (args.build_dir / 'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
