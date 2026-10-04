"""Attribute serial P4 DDR GEMM cycles against explicit behavioral AXI models."""
import argparse
from collections import Counter
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
RTL = tuple(ROOT/name for name in (
    'rtl/core/gemm_pe.sv', 'rtl/core/gemm_array.sv', 'rtl/core/gemm_microtile.sv',
    'rtl/memory/gemm_operand_banks.sv', 'rtl/memory/gemm_result_banks.sv',
    'rtl/memory/gemm_bram_microtile.sv', 'rtl/control/gemm_tile_engine.sv',
    'rtl/memory/gemm_dma_rows.sv', 'rtl/memory/gemm_axi_burst.sv',
    'rtl/memory/gemm_tile_dma.sv', 'rtl/memory/gemm_tile_dma_read_queue.sv',
    'rtl/control/gemm_ddr_job.sv',
    'rtl/control/gemm_ddr_registers.sv', 'rtl/control/gemm_packet_transport.sv',
    'rtl/control/gemm_ddr_control.sv', 'rtl/control/gemm_ddr_core.sv'))


def hashes(paths):
    return {path.relative_to(ROOT).as_posix(): hashlib.sha256(
        path.read_text(encoding='utf-8').encode('utf-8')).hexdigest() for path in paths}


def compare_models(configurations):
    """The serial schedule must expose every deliberately inserted delay."""
    comparisons = []
    by_key = {(item['t'], item['model']): item for item in configurations}
    for t in (8, 32):
        if (t, 'unstalled') not in by_key or (t, 'latency') not in by_key:
            continue
        fast = {job['name']: job for job in by_key[t, 'unstalled']['profile']['jobs']}
        slow = {job['name']: job for job in by_key[t, 'latency']['profile']['jobs']}
        if fast.keys() != slow.keys():
            raise RuntimeError('Memory models did not run identical workloads')
        for name, baseline in fast.items():
            delayed = slow[name]
            for key in ('shape', 'descriptor', 'seed', 'input_images_sha256'):
                if baseline[key] != delayed[key]:
                    raise RuntimeError(f'{t}/{name}: memory models changed the workload {key}')
            for channel in ('reads', 'writes'):
                def sequence(job):
                    return [(burst['address'], burst['beats']) for burst in job[channel]]
                if sequence(baseline) != sequence(delayed):
                    raise RuntimeError(f'{t}/{name}: memory models changed {channel} burst sequence')
                for first, second in zip(baseline[channel], delayed[channel], strict=True):
                    for interval, value in first['interval_cycles'].items():
                        expected_delta = (8 if channel == 'reads' and interval == 'ar_to_first_r'
                                          else 6 if channel == 'writes' and interval == 'b_response'
                                          else 0)
                        if second['interval_cycles'][interval]-value != expected_delta:
                            raise RuntimeError(f'{t}/{name}: wrong inserted delay in {channel}/{interval}')
            read_count, write_count = len(baseline['reads']), len(baseline['writes'])
            expected = 8*read_count + 6*write_count
            actual = delayed['counters']['job_cycles'] - baseline['counters']['job_cycles']
            for counter in ('compute_cycles', 'read_beats', 'write_beats', 'write_valid_bytes'):
                if baseline['counters'][counter] != delayed['counters'][counter]:
                    raise RuntimeError(f'{t}/{name}: inserted latency changed {counter}')
            if actual != expected:
                raise RuntimeError(f'{t}/{name}: latency sensitivity {actual} != {expected}')
            comparisons.append(dict(t=t, name=name, read_bursts=read_count,
                                    write_bursts=write_count, expected_added_cycles=expected,
                                    observed_added_cycles=actual, result='PASS'))
    return comparisons


def compare_reuse(configurations):
    comparisons = []
    by_key = {(item['t'], item['model']): item for item in configurations}
    for model in ('unstalled', 'latency'):
        if (8, model) not in by_key or (32, model) not in by_key:
            continue
        small = {job['name']: job for job in by_key[8, model]['profile']['jobs']}
        large = {job['name']: job for job in by_key[32, model]['profile']['jobs']}
        if small.keys() != large.keys():
            raise RuntimeError('Tile sizes did not run identical workloads')
        for name, baseline in small.items():
            reused = large[name]
            for key in ('shape', 'descriptor', 'seed', 'input_images_sha256'):
                if baseline[key] != reused[key]:
                    raise RuntimeError(f'{model}/{name}: tile sizes changed {key}')
            for counter in ('compute_cycles', 'write_beats', 'write_valid_bytes'):
                if baseline['counters'][counter] != reused['counters'][counter]:
                    raise RuntimeError(f'{model}/{name}: tile sizes changed {counter}')
            comparisons.append(dict(name=name, model=model, p=4, core_hz=100000000,
                                    t8_job_cycles=baseline['counters']['job_cycles'],
                                    t32_job_cycles=reused['counters']['job_cycles'],
                                    t8_read_bytes=8*baseline['counters']['read_beats'],
                                    t32_read_bytes=8*reused['counters']['read_beats'],
                                    result_write_bytes=8*baseline['counters']['write_beats'],
                                    scope='Controlled behavioral-model comparison; not a board speedup'))
    return comparisons


def save_compact(build, profile):
    """Keep reviewable tables beside the complete local edge traces."""
    compact = {key: value for key, value in profile.items() if key != 'jobs'}
    compact['original_profile_sha256_bytes'] = hashlib.sha256((build/'profile.json').read_bytes()).hexdigest()
    compact['trace_scope'] = ('Job summaries and per-burst CSV contain event endpoints, counts and gap histograms; '
                             'full ordered edge lists remain in the local profile.json')
    compact['jobs'] = [{key: value for key, value in job.items() if key not in ('reads', 'writes')}
                       for job in profile['jobs']]
    (build/'compact.json').write_text(json.dumps(compact, indent=2)+'\n', encoding='utf-8')
    rows = []
    for job in profile['jobs']:
        for burst in sorted(job['reads']+job['writes'], key=lambda item: item['index']):
            row = dict(name=job['name'], job_id=job['descriptor']['job_id'],
                       index=burst['index'], tile=burst['tile'], kind=burst['kind'],
                       row=burst['row'], word=burst['word'], address=burst['address'], beats=burst['beats'])
            for event in ('command_offer', 'command', 'ar', 'aw', 'gather_complete', 'b', 'local_done'):
                row[event] = burst.get(event, '')
            for channel in ('r', 'local_reads', 'loads', 'result_requests', 'result_responses', 'local_writes', 'w'):
                edges = burst[channel]
                row[channel+'_first'] = edges[0] if edges else ''
                row[channel+'_last'] = edges[-1] if edges else ''
                row[channel+'_count'] = len(edges)
                gaps = Counter(second-first for first, second in zip(edges, edges[1:]))
                row[channel+'_gap_histogram'] = ';'.join(f'{gap}:{count}' for gap, count in sorted(gaps.items()))
            for interval in ('command_to_ar', 'ar_to_first_r', 'remaining_r_span',
                             'last_r_to_last_bank_load', 'last_bank_load_to_local_done',
                             'c_gather', 'aw_w_issue', 'b_response', 'b_to_local_done'):
                row[interval] = burst['interval_cycles'].get(interval, '')
            rows.append(row)
    with (build/'bursts.csv').open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', type=Path, default=ROOT/'build/profile_ddr')
    parser.add_argument('--only', choices=('p4_t8', 'p4_t32'))
    parser.add_argument('--model', choices=('unstalled', 'latency'))
    args = parser.parse_args()
    out = args.build_dir.resolve()
    if out.exists() and any(out.iterdir()):
        parser.error('build directory is not empty; select a new directory to preserve earlier profiles')
    out.mkdir(parents=True, exist_ok=True)
    recorded = (*RTL, ROOT/'tb/profile_ddr_core.py', ROOT/'tb/test_ddr_core.py',
                ROOT/'tb/common.py', ROOT/'requirements-test.txt', Path(__file__).resolve())
    before = hashes(recorded)
    record = dict(schema_version=1, result='RUNNING',
                  utc_started=datetime.now(timezone.utc).isoformat(),
                  requested_geometry=args.only or 'P4/T8 and P4/T32',
                  requested_model=args.model or 'unstalled and latency',
                  source_sha256_utf8_lf=before)
    run_record = out/'run.json'
    run_record.write_text(json.dumps(record, indent=2)+'\n', encoding='utf-8')
    try:
        run_profiles(args, out, recorded, before)
    except BaseException as exc:
        record.update(result='FAIL', utc_finished=datetime.now(timezone.utc).isoformat(),
                      error=dict(type=type(exc).__name__, message=str(exc)),
                      saved_artifact_sha256_bytes={
                          path.relative_to(out).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in out.rglob('*') if path.is_file() and
                          path.name in ('profile.json', 'results.xml')})
        run_record.write_text(json.dumps(record, indent=2)+'\n', encoding='utf-8')
        raise
    record.update(result='PASS', utc_finished=datetime.now(timezone.utc).isoformat(),
                  summary_sha256_bytes=hashlib.sha256((out/'summary.json').read_bytes()).hexdigest())
    run_record.write_text(json.dumps(record, indent=2)+'\n', encoding='utf-8')


def run_profiles(args, out, recorded, before):
    sys.path.insert(0, str(ROOT/'tb'))
    os.environ['PYTHONPATH'] = str(ROOT/'tb')+os.pathsep+os.environ.get('PYTHONPATH', '')
    from cocotb_tools.runner import get_runner
    configurations = []
    for t in (8, 32):
        if args.only and args.only != f'p4_t{t}':
            continue
        for model in ('unstalled', 'latency'):
            if args.model and args.model != model:
                continue
            build = out/f'p4_t{t}_{model}'
            identity = 0xd1000000+4*256+t
            runner = get_runner('icarus')
            runner.build(sources=RTL, hdl_toplevel='gemm_ddr_core',
                         parameters={'P': 4, 'T': t, 'BUILD_ID': identity, 'CORE_HZ': 100000000},
                         build_dir=build, build_args=['-g2012', '-Wall'],
                         always=True, timescale=('1ns', '1ps'))
            result = runner.test(hdl_toplevel='gemm_ddr_core', test_module='profile_ddr_core',
                                 build_dir=build, test_dir=build,
                                 extra_env={'GEMM_P': '4', 'GEMM_T': str(t),
                                            'GEMM_BUILD_ID': str(identity),
                                            'GEMM_PROFILE_MODEL': model,
                                            'GEMM_PROFILE': str(build/'profile.json')})
            tests = list(ET.parse(result).iter('testcase'))
            if len(tests) != 1 or any(list(test.iter(tag)) for test in tests
                                      for tag in ('failure', 'error', 'skipped')):
                raise RuntimeError(f'P4/T{t}/{model}: profiler failed: {result}')
            profile = json.loads((build/'profile.json').read_text(encoding='utf-8'))
            if profile['result'] != 'PASS' or len(profile['jobs']) != 4:
                raise RuntimeError('Profiler did not complete all four workloads')
            save_compact(build, profile)
            configurations.append(dict(p=4, t=t, model=model, tests=1, result='PASS',
                                       directory=build.name, profile=profile))
    comparisons = compare_models(configurations)
    reuse_comparisons = compare_reuse(configurations)
    if hashes(recorded) != before:
        raise RuntimeError('Sources changed during profiling')
    raw_hashes = {}
    for item in configurations:
        for filename in ('profile.json', 'results.xml'):
            path = out/item['directory']/filename
            raw_hashes[path.relative_to(out).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    summary = dict(schema_version=1, result='PASS', utc_finished=datetime.now(timezone.utc).isoformat(),
                   scope='Production portable RTL with behavioral AXI RAM; no MIG, electrical timing or physical DDR latency claim',
                   source_sha256_utf8_lf=before, saved_artifact_sha256_bytes=raw_hashes,
                   versions={name: version(name) for name in ('cocotb', 'cocotbext-axi', 'cocotb-bus')},
                   configurations=[{key: value for key, value in item.items() if key != 'profile'}
                                   for item in configurations],
                   latency_sensitivity=comparisons,
                   reuse_comparisons=reuse_comparisons,
                   total_jobs=sum(len(item['profile']['jobs']) for item in configurations),
                   total_outputs=sum(job['compared_outputs'] for item in configurations
                                     for job in item['profile']['jobs']))
    summary['versions']['python'] = sys.version
    summary['versions']['iverilog'] = subprocess.run(
        ['iverilog', '-V'], check=True, capture_output=True, text=True).stdout.splitlines()[0]
    rows = []
    for item in configurations:
        for job in item['profile']['jobs']:
            rows.append(dict(p=4, t=item['t'], model=item['model'], name=job['name'],
                             **job['shape'], **job['counters'],
                             **{f'phase_{key}': value for key, value in job['phases'].items()},
                             read_bursts=len(job['reads']), write_bursts=len(job['writes'])))
    with (out/'cycles.csv').open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summary['saved_artifact_sha256_bytes']['cycles.csv'] = hashlib.sha256((out/'cycles.csv').read_bytes()).hexdigest()
    (out/'summary.json').write_text(json.dumps(summary, indent=2)+'\n', encoding='utf-8')
    compact_summary = {key: value for key, value in summary.items() if key != 'saved_artifact_sha256_bytes'}
    compact_summary['original_artifact_sha256_bytes'] = summary['saved_artifact_sha256_bytes']
    compact_summary['artifact_scope'] = 'Compact summaries, burst CSVs and cycle table are derived from the complete local profiles; XML is copied unchanged'
    saved = {}
    for item in configurations:
        for filename in ('compact.json', 'bursts.csv', 'results.xml'):
            path = out/item['directory']/filename
            saved[path.relative_to(out).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    saved['cycles.csv'] = summary['saved_artifact_sha256_bytes']['cycles.csv']
    compact_summary['saved_artifact_sha256_bytes'] = saved
    (out/'compact_summary.json').write_text(json.dumps(compact_summary, indent=2)+'\n', encoding='utf-8')
    print(f"PASS: {summary['total_jobs']} profiled jobs, {summary['total_outputs']} compared outputs; {out/'summary.json'}")


if __name__ == '__main__':
    main()
