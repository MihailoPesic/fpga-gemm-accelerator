"""Verify packet commands and DDR GEMM against AXI RAM."""
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
    parser.add_argument('--build-dir', type=Path, default=ROOT/'build/test_ddr_core')
    parser.add_argument('--only', choices=('p4_t8', 'p4_t32', 'p8_t8', 'p8_t32'))
    parser.add_argument('--overlap', action='store_true', help='test the selectable serial/overlap build')
    parser.add_argument('--suite', choices=('compatibility', 'overlap'), default='compatibility')
    parser.add_argument('--read-slots', type=int, choices=(1, 4),
                        help='run one read depth; default checks both')
    args = parser.parse_args()
    if args.suite == 'overlap' and not args.overlap:
        parser.error('--suite overlap requires --overlap')
    build_root = args.build_dir.resolve()
    build_root.mkdir(parents=True, exist_ok=True)
    (build_root/'summary.json').unlink(missing_ok=True)
    sources = [ROOT/name for name in (
        'rtl/core/gemm_pe.sv', 'rtl/core/gemm_array.sv', 'rtl/core/gemm_microtile.sv',
        'rtl/memory/gemm_operand_banks.sv', 'rtl/memory/gemm_result_banks.sv',
        'rtl/memory/gemm_bram_microtile.sv', 'rtl/control/gemm_tile_engine.sv',
        'rtl/memory/gemm_dma_rows.sv', 'rtl/memory/gemm_axi_burst.sv',
        'rtl/memory/gemm_tile_dma.sv', 'rtl/memory/gemm_tile_dma_read_queue.sv',
        'rtl/memory/gemm_tile_dma_duplex.sv', 'rtl/control/gemm_tile_scheduler.sv',
        'rtl/control/gemm_ddr_overlap_job.sv',
        'rtl/control/gemm_ddr_job.sv',
        'rtl/control/gemm_ddr_registers.sv', 'rtl/control/gemm_packet_transport.sv',
        'rtl/control/gemm_ddr_control.sv', 'rtl/control/gemm_ddr_core.sv')]
    recorded = sources+[ROOT/'tb/test_ddr_core.py', ROOT/'tb/test_ddr_core_overlap.py', ROOT/'tb/common.py',
                        ROOT/'requirements-test.txt', Path(__file__).resolve()]

    def hashes():
        return {path.relative_to(ROOT).as_posix(): hashlib.sha256(
            path.read_text(encoding='utf-8').encode('utf-8')).hexdigest() for path in recorded}

    before = hashes()
    sys.path.insert(0, str(ROOT/'tb'))
    os.environ['PYTHONPATH'] = str(ROOT/'tb')+os.pathsep+os.environ.get('PYTHONPATH', '')
    from cocotb_tools.runner import get_runner
    results = []
    configurations = ((depth, p, t) for depth in
                      ((args.read_slots,) if args.read_slots is not None else (1, 4))
                      for p, t in ((4, 8), (4, 32), (8, 8), (8, 32)))
    for read_slots, p, t in configurations:
        name = f'p{p}_t{t}'
        if args.only and name != args.only:
            continue
        build = build_root/f'read{read_slots}'/name
        build.mkdir(parents=True, exist_ok=True)
        for stale in ('results.xml', 'coverage.json'):
            (build/stale).unlink(missing_ok=True)
        identity = 0xd0000000+p*256+t+((read_slots-1)<<16)
        runner = get_runner('icarus')
        runner.build(sources=sources, hdl_toplevel='gemm_ddr_core',
                     parameters={'P': p, 'T': t, 'BUILD_ID': identity,
                                 'READ_SLOTS': read_slots, 'ENABLE_OVERLAP': int(args.overlap)}, build_dir=build,
                     build_args=['-g2012', '-Wall'], always=True, timescale=('1ns', '1ps'))
        result = runner.test(hdl_toplevel='gemm_ddr_core',
                             test_module='test_ddr_core_overlap' if args.suite == 'overlap' else 'test_ddr_core',
                             build_dir=build, test_dir=build,
                             extra_env={'GEMM_P': str(p), 'GEMM_T': str(t), 'GEMM_BUILD_ID': str(identity),
                                        'GEMM_READ_SLOTS': str(read_slots),
                                        'GEMM_VERSION': str(0x200 if args.overlap else 0x100),
                                        'GEMM_COVERAGE': str(build/'coverage.json')})
        tests = list(ET.parse(result).iter('testcase'))
        if len(tests) != (2 if args.suite == 'overlap' else 10) or any(
                list(test.iter(tag)) for test in tests for tag in ('failure', 'error', 'skipped')):
            raise RuntimeError(f'{name}: DDR core regression failed: {result}')
        if not (build/'coverage.json').is_file():
            raise RuntimeError(f'{name}: missing coverage')
        results.append({'p': p, 't': t, 'read_slots': read_slots, 'enable_overlap': args.overlap, 'suite': args.suite,
                        'directory': build.relative_to(build_root).as_posix(),
                        'build_id': identity, 'tests': len(tests), 'result': 'PASS',
                        'coverage': json.loads((build/'coverage.json').read_text())})
    if hashes() != before:
        raise RuntimeError('Sources changed during DDR core regression')
    summary = {'result': 'PASS', 'configurations': results, 'source_sha256_utf8_lf': before,
               'scope': 'Byte-framed transport, production control/register/job/DMA/core/burst and behavioral AXI RAM; no UART pin timing, vendor DDR or board measurement',
               'versions': {name: version(name) for name in ('cocotb', 'cocotbext-axi', 'cocotb-bus')}}
    summary['versions']['python'] = sys.version
    summary['versions']['iverilog'] = subprocess.run(
        ['iverilog', '-V'], check=True, capture_output=True, text=True).stdout.splitlines()[0]
    counters = ('jobs', 'output_values', 'requests', 'responses', 'replays', 'sequence_conflicts',
                'malformed_frames', 'invalid_memory_commands', 'axi_read_bursts', 'axi_write_bursts',
                'axi_read_beats', 'axi_write_beats', 'axi_write_responses')
    summary['totals'] = {name: sum(item['coverage'][name] for item in results) for name in counters}
    (build_root/'summary.json').write_text(json.dumps(summary, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
