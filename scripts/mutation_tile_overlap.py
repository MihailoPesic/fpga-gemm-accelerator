"""Check that concurrent-port tests detect broken result-buffer ownership."""
import argparse
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import sys
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', type=Path, default=ROOT / 'build/mutation_tile_overlap')
    args = parser.parse_args()
    out = args.build_dir.resolve()
    if out.exists():
        raise RuntimeError('Mutation output must be fresh')
    out.mkdir(parents=True)
    sources = [ROOT / name for name in (
        'rtl/core/gemm_pe.sv', 'rtl/core/gemm_array.sv', 'rtl/core/gemm_microtile.sv',
        'rtl/memory/gemm_operand_banks.sv', 'rtl/memory/gemm_result_banks.sv',
        'rtl/memory/gemm_bram_microtile.sv', 'rtl/control/gemm_tile_engine.sv')]
    recorded = sources + [ROOT / 'tb/test_tile_overlap.py', ROOT / 'tb/common.py',
                          ROOT / 'requirements-test.txt', Path(__file__).resolve()]
    def hashes():
        return {p.relative_to(ROOT).as_posix(): hashlib.sha256(
            p.read_text(encoding='utf-8').encode()).hexdigest() for p in recorded}
    before = hashes()
    original = sources[-1].read_text(encoding='utf-8')
    mutations = (
        ('response_owner_uses_live_request', 'output_buf != read_buf_q', 'output_buf != read_buf',
         ['start_response_ownership']),
        ('active_result_reads_allowed', '!rst && (!busy || read_buf != output_q) &&', '!rst &&',
         ['disjoint_load_compute_read']),
        ('response_owner_released_early', 'wire read_owned = read_pending || response_valid;',
         'wire read_owned = read_pending || (response_valid && !response_ready);',
         ['start_response_ownership']),
    )
    sys.path.insert(0, str(ROOT / 'tb'))
    os.environ['PYTHONPATH'] = str(ROOT / 'tb') + os.pathsep + os.environ.get('PYTHONPATH', '')
    from cocotb_tools.runner import get_runner
    results = []
    for name, old, new, expected_failures in mutations:
        if original.count(old) != 1:
            raise RuntimeError('Mutation anchor changed: ' + name)
        build = out / name
        build.mkdir()
        mutant = build / 'mutated_tile_engine.sv'
        mutant.write_text(original.replace(old, new), encoding='utf-8')
        runner = get_runner('icarus')
        runner.build(sources=[*sources[:-1], mutant], hdl_toplevel='gemm_tile_engine',
                     parameters={'P': 4, 'T': 8, 'CONCURRENT_PORTS': 1}, build_dir=build,
                     build_args=['-g2012', '-Wall'], always=True, timescale=('1ns', '1ps'))
        xml = Path(runner.test(hdl_toplevel='gemm_tile_engine', test_module='test_tile_overlap',
                              build_dir=build, test_dir=build,
                              extra_env={'GEMM_COVERAGE_DIR': str(build)}))
        tests = list(ET.parse(xml).iter('testcase'))
        failures = [test.attrib['name'] for test in tests if list(test.iter('failure'))]
        expected_cases = sorted(('disjoint_load_compute_read', 'start_response_ownership', 'start_edge_and_reset'))
        if (sorted(test.get('name') for test in tests) != expected_cases or failures != expected_failures or
                any(list(test.iter(tag)) for test in tests for tag in ('error', 'skipped'))):
            raise RuntimeError(f'{name}: unexpected detection result: {failures}')
        if hashes() != before:
            raise RuntimeError('Production inputs changed during mutation checks')
        results.append(dict(name=name, result='DETECTED', tests=len(tests),
                            expected_failures=failures, replacement=dict(old=old, new=new),
                            mutant_sha256_bytes=hashlib.sha256(mutant.read_bytes()).hexdigest(),
                            failing_xml_sha256_bytes=hashlib.sha256(xml.read_bytes()).hexdigest()))
    summary = dict(result='DETECTED', source_hashes_unchanged=True,
                   source_sha256_utf8_lf=before, mutations=results,
                   versions=dict(python=sys.version, cocotb=version('cocotb'),
                                 iverilog=subprocess.run(['iverilog', '-V'], check=True,
                                                        capture_output=True, text=True).stdout.splitlines()[0]),
                   scope='Three deliberately broken private tile-engine copies at P4/T8; no production edits or formal proof')
    (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    print('PASS: all three concurrent-port ownership defects detected')


if __name__ == '__main__':
    main()
