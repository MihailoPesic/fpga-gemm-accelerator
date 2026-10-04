"""Compare one and four buffered reads at fixed P8/T32 against AXI RAM."""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
SOURCES = tuple(ROOT/name for name in (
    'rtl/core/gemm_pe.sv', 'rtl/core/gemm_array.sv', 'rtl/core/gemm_microtile.sv',
    'rtl/memory/gemm_operand_banks.sv', 'rtl/memory/gemm_result_banks.sv',
    'rtl/memory/gemm_bram_microtile.sv', 'rtl/control/gemm_tile_engine.sv',
    'rtl/memory/gemm_dma_rows.sv', 'rtl/memory/gemm_axi_burst.sv',
    'rtl/memory/gemm_tile_dma.sv', 'rtl/memory/gemm_tile_dma_read_queue.sv',
    'tb/sv/tile_dma_harness.sv'))
RECORDED = SOURCES + tuple(ROOT/name for name in (
    'tb/profile_read_dma.py', 'tb/test_tile_dma.py', 'tb/common.py',
    'requirements-test.txt', 'scripts/profile_read_dma.py'))
PHASES = ('a_load', 'bt_load', 'compute', 'c_store')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hashes():
    return {
        'source_sha256_bytes': {path.relative_to(ROOT).as_posix(): sha(path) for path in RECORDED},
        'source_sha256_utf8_lf': {
            path.relative_to(ROOT).as_posix(): hashlib.sha256(
                path.read_text(encoding='utf-8').encode('utf-8')).hexdigest() for path in RECORDED}}


def save(path, value):
    path.write_text(json.dumps(value, indent=2)+'\n', encoding='utf-8')


def compare(first, second):
    if [job['name'] for job in first['jobs']] != [job['name'] for job in second['jobs']]:
        raise RuntimeError('Read depths ran different workloads')
    result = []
    for old, new in zip(first['jobs'], second['jobs'], strict=True):
        name = old['name']
        for key in ('shape', 'seed', 'regions', 'golden_int32_sha256', 'compared_output_values',
                    'checked_memory_bytes', 'counters', 'reads', 'writes', 'write_strobes'):
            if old[key] != new[key]:
                raise RuntimeError(f'{name}: read depth changed workload, correctness or traffic: {key}')
        for phase in PHASES:
            if old['phases'][phase]['counters'] != new['phases'][phase]['counters']:
                raise RuntimeError(f'{name}: phase traffic changed: {phase}')
        for phase in ('compute', 'c_store'):
            if old['phases'][phase]['cycles'] != new['phases'][phase]['cycles']:
                raise RuntimeError(f'{name}: read depth changed {phase} timing')
        if old['phases']['compute']['tile_job_cycles'] != new['phases']['compute']['tile_job_cycles']:
            raise RuntimeError(f'{name}: compute counter changed')
        if old['max_axi_reads'] != 1 or not 2 <= new['max_axi_reads'] <= 4:
            raise RuntimeError(f'{name}: intended read concurrency was not exercised')
        phases = {phase: dict(read1_cycles=old['phases'][phase]['cycles'],
                              read4_cycles=new['phases'][phase]['cycles'],
                              saved_cycles=old['phases'][phase]['cycles']-new['phases'][phase]['cycles'])
                  for phase in PHASES}
        saved = old['helper_sequence_cycles']-new['helper_sequence_cycles']
        if saved != sum(phase['saved_cycles'] for phase in phases.values()):
            raise RuntimeError(f'{name}: phase attribution does not sum to total')
        result.append(dict(name=name, shape=old['shape'], seed=old['seed'], phases=phases,
                           read1_cycles=old['helper_sequence_cycles'], read4_cycles=new['helper_sequence_cycles'],
                           saved_cycles=saved,
                           model_cycle_ratio=old['helper_sequence_cycles']/new['helper_sequence_cycles'],
                           max_axi_reads={'read1': old['max_axi_reads'], 'read4': new['max_axi_reads']},
                           compared_output_values=old['compared_output_values'],
                           checked_memory_bytes=old['checked_memory_bytes'], counters=old['counters'],
                           golden_int32_sha256=old['golden_int32_sha256'],
                           result='PASS', scope='Same workload and traffic; behavioral-model cycle comparison only'))
    return result


def execute(out, identity):
    sys.path.insert(0, str(ROOT/'tb'))
    os.environ['PYTHONPATH'] = str(ROOT/'tb')+os.pathsep+os.environ.get('PYTHONPATH', '')
    from cocotb_tools.runner import get_runner
    configurations = []
    artifacts = []
    for depth in (1, 4):
        build = out/f'read{depth}'
        runner = get_runner('icarus')
        runner.build(sources=SOURCES, hdl_toplevel='tile_dma_harness',
                     parameters={'P': 8, 'T': 32, 'READ_SLOTS': depth},
                     build_dir=build, build_args=['-g2012', '-Wall'], always=True,
                     timescale=('1ns', '1ps'))
        result = Path(runner.test(
            hdl_toplevel='tile_dma_harness', test_module='profile_read_dma',
            build_dir=build, test_dir=build,
            extra_env={'GEMM_P': '8', 'GEMM_T': '32', 'GEMM_READ_SLOTS': str(depth),
                       'GEMM_READ_PROFILE': str(build/'profile.json')})).resolve()
        tests = list(ET.parse(result).iter('testcase'))
        if len(tests) != 1 or any(list(test.iter(tag)) for test in tests for tag in ('failure', 'error', 'skipped')):
            raise RuntimeError(f'Read depth {depth}: profiler simulation did not pass: {result}')
        profile_path = build/'profile.json'
        profile = json.loads(profile_path.read_text(encoding='utf-8'))
        if (profile.get('result'), profile.get('p'), profile.get('t'), profile.get('read_slots')) != ('PASS', 8, 32, depth):
            raise RuntimeError(f'Read depth {depth}: invalid profile identity/result')
        if [(job['name'], job['shape']) for job in profile['jobs']] != [
                ('dense', [32,32,256]), ('odd', [31,29,17])]:
            raise RuntimeError(f'Read depth {depth}: incomplete case set')
        artifacts.extend((result, profile_path))
        configurations.append(profile)
        print(f'READ_DMA_PROFILE depth={depth} PASS jobs={len(profile["jobs"])}', flush=True)
    comparisons = compare(*configurations)
    if source_hashes() != identity:
        raise RuntimeError('Profile inputs changed during execution')
    versions = {name: version(name) for name in ('cocotb', 'cocotbext-axi', 'cocotb-bus', 'scapy')}
    versions['python'] = sys.version
    versions['iverilog'] = subprocess.run(['iverilog', '-V'], check=True, capture_output=True,
                                         text=True).stdout.splitlines()[0]
    summary = dict(schema_version=1, result='PASS', p=8, t=32, read_slots=[1,4],
                   scope=configurations[0]['cycle_scope'], versions=versions, **identity,
                   cases=comparisons,
                   configurations=[dict(read_slots=item['read_slots'], result=item['result'],
                                        profile=f'read{item["read_slots"]}/profile.json') for item in configurations])
    save(out/'summary.json', summary)
    rows = [dict(name=case['name'], phase=phase, **case['phases'][phase])
            for case in comparisons for phase in PHASES]
    rows.extend(dict(name=case['name'], phase='helper_sequence',
                     read1_cycles=case['read1_cycles'], read4_cycles=case['read4_cycles'],
                     saved_cycles=case['saved_cycles']) for case in comparisons)
    with (out/'comparison.csv').open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=('name', 'phase', 'read1_cycles', 'read4_cycles', 'saved_cycles'))
        writer.writeheader()
        writer.writerows(rows)
    artifacts.extend((out/'summary.json', out/'comparison.csv'))
    save(out/'manifest.json', dict(result='PASS', sha256_bytes={
        path.relative_to(out).as_posix(): sha(path) for path in artifacts}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', type=Path, default=ROOT/'build/profile_read_dma')
    args = parser.parse_args()
    out = args.build_dir.resolve()
    if out.exists():
        parser.error('select a new build directory; existing evidence is never overwritten')
    out.mkdir(parents=True)
    identity = source_hashes()
    record = dict(schema_version=1, result='RUNNING', utc_started=datetime.now(timezone.utc).isoformat(),
                  configuration='P8/T32; READ_SLOTS=1 and4; unstalled AXI RAM', **identity)
    save(out/'run.json', record)
    try:
        execute(out, identity)
    except BaseException as exc:
        record.update(result='FAIL', utc_finished=datetime.now(timezone.utc).isoformat(),
                      error=dict(type=type(exc).__name__, message=str(exc)),
                      saved_artifact_sha256_bytes={path.relative_to(out).as_posix(): sha(path)
                          for path in out.rglob('*') if path.is_file() and
                          (path.suffix == '.xml' or path.name == 'profile.json')})
        save(out/'run.json', record)
        raise
    record.update(result='PASS', utc_finished=datetime.now(timezone.utc).isoformat(),
                  summary_sha256_bytes=sha(out/'summary.json'), manifest_sha256_bytes=sha(out/'manifest.json'))
    save(out/'run.json', record)
    print('READ_DMA_PROFILE PASS: '+str(out/'summary.json'), flush=True)


if __name__ == '__main__':
    main()
