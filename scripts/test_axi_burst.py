"""Check one/four-slot AXI reads against RAM and adversarial responders."""
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
    parser.add_argument('--read-slots', type=int, choices=(1, 4),
                        help='Run one configuration; by default run both')
    args = parser.parse_args()
    build = args.build_dir.resolve()
    build.mkdir(parents=True, exist_ok=True)
    (build / 'summary.json').unlink(missing_ok=True)
    from cocotb_tools.runner import get_runner
    sys.path.insert(0, str(ROOT / 'tb'))
    os.environ['PYTHONPATH'] = str(ROOT / 'tb') + os.pathsep + os.environ.get('PYTHONPATH', '')
    source = ROOT / 'rtl/memory/gemm_axi_burst.sv'
    files = [source, ROOT / 'tb/test_axi_burst.py', ROOT / 'tb/test_axi_read_queue.py', ROOT / 'tb/common.py',
             ROOT / 'requirements-test.txt', Path(__file__).resolve()]
    def hashes():
        return {str(path.relative_to(ROOT)): hashlib.sha256(
            path.read_text(encoding='utf-8').encode()).hexdigest() for path in files}
    source_hashes = hashes()
    configs = []
    saved = {}
    for read_slots in (args.read_slots,) if args.read_slots else (1, 4):
        directory = build / f'read{read_slots}'
        directory.mkdir(exist_ok=True)
        for name in ('summary.json', 'coverage.json', 'queue_coverage.json', 'results.xml'):
            (directory / name).unlink(missing_ok=True)
        runner = get_runner('icarus')
        runner.build(sources=[source], hdl_toplevel='gemm_axi_burst', build_dir=directory,
                     parameters={'READ_SLOTS': read_slots}, build_args=['-g2012', '-Wall'],
                     always=True, timescale=('1ns', '1ps'))
        modules = 'test_axi_burst' if read_slots == 1 else 'test_axi_burst,test_axi_read_queue'
        result = runner.test(hdl_toplevel='gemm_axi_burst', test_module=modules,
                             build_dir=directory, test_dir=directory,
                             extra_env={'GEMM_READ_SLOTS': str(read_slots),
                                        'GEMM_COVERAGE': str(directory / 'coverage.json'),
                                        'GEMM_READ_QUEUE_COVERAGE': str(directory / 'queue_coverage.json')})
        tests = list(ET.parse(result).iter('testcase'))
        if not tests or any(list(test.iter(tag)) for test in tests for tag in ('failure', 'error', 'skipped')):
            raise RuntimeError(f'AXI burst regression failed: {result}')
        if hashes() != source_hashes:
            raise RuntimeError('Sources changed during AXI tests; rerun before recording evidence')
        artifacts = [directory / 'results.xml', directory / 'coverage.json']
        if read_slots == 4:
            artifacts.append(directory / 'queue_coverage.json')
        config = {'result': 'PASS', 'read_slots': read_slots, 'tests': len(tests),
                  'test_modules': modules.split(','),
                  'source_sha256_utf8_lf': source_hashes,
                  'saved_artifact_sha256_bytes': {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                                                  for path in artifacts}}
        (directory / 'summary.json').write_text(json.dumps(config, indent=2)+'\n')
        configs.append({'result': 'PASS', 'read_slots': read_slots, 'tests': len(tests),
                        'directory': directory.name})
        for path in [directory / 'summary.json', *artifacts]:
            saved[path.relative_to(build).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    if hashes() != source_hashes:
        raise RuntimeError('Sources changed during AXI tests; rerun before recording evidence')
    summary = {'result': 'PASS', 'tests': sum(config['tests'] for config in configs),
        'configurations': configs, 'versions': {
        name: version(name) for name in ('cocotb', 'cocotbext-axi', 'cocotb-bus', 'scapy')},
        'source_sha256_utf8_lf': source_hashes, 'saved_artifact_sha256_bytes': saved}
    (build / 'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
