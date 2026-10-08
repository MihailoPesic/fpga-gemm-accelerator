"""Check the finished T32 CLI/API demo records without contacting hardware."""
import sys
sys.dont_write_bytecode = True

import argparse
import csv
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import statistics

BUILD_ID = 0x9D4BEB4D
BIT_SHA = 'd187f51bac37c3682641243f1db112579ad14c8ac45a1b3df52e704bf58c8327'
MANIFEST_SHA = 'b3843b33f232829180a5263150d72f1898cf42d56cb1da24f0590914e373706a'
HOST_SOURCES = {
    'host/gemm/__init__.py': '0c79bcae8988b56c29b06d79602eab132a295b2d18aa41b3991c39cef23d316a',
    'host/gemm/__main__.py': '61a8b5f4ec3b1cc3a5cfa34c0dba573290bea067f71503db1f7147ae2ec0b4e7',
    'host/gemm/client.py': '9ce195874259013b56e86b6522c44fb0bfa768f325341540dc3856e9a5bed600',
    'host/preview/protocol.py': 'c7ed1e64ea81a7c87276a4a7a99cca8e28b3e99308005121fe73a6f07ac208df',
}
RUNNER_SOURCES = dict(HOST_SOURCES, **{
    'scripts/qualify_release.py': 'fdfe9d020e3b89e565fbfb6e3246a0a93f4ba1184138c83a7c44d28dc5af7381',
    'scripts/program_ddr_gemm.py': '7549fe9e3f8a0b045d8bf0a9d1ae7655d36fe20bb3ab56aa365ce165b42bc7e7',
})
COUNTERS = ('job_cycles', 'compute_cycles', 'read_beats', 'write_beats',
            'write_valid_bytes', 'input_wait_cycles', 'read_stall_cycles', 'write_stall_cycles')
IDENTITY = dict(id=0x314D474E, version=0x200, geometry=(256 << 16) | (32 << 8) | 8,
                p=8, t=32, kmax=256, build_id=BUILD_ID, core_hz=100000000)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read_json(path):
    return json.loads(path.read_bytes())


def utc(value):
    result = datetime.fromisoformat(value)
    require(result.tzinfo is not None, 'Timestamp lacks timezone')
    return result


def same_fields(actual, expected, context):
    for field, wanted in expected.items():
        require(type(actual.get(field)) is type(wanted) and actual[field] == wanted,
                context + ': invalid ' + field)


def integer_counters(counts, m, n, k):
    require(all(type(counts.get(key)) is int and 0 <= counts[key] < 2**64 for key in COUNTERS),
            'Frozen counters must all be unsigned 64-bit integers')
    expected = dict(read_beats=((k+7)//8)*(m*((n+31)//32)+n*((m+31)//32)),
                    write_beats=m*((n+1)//2), write_valid_bytes=4*m*n,
                    compute_cycles=((m+7)//8)*((n+7)//8)*(k+23))
    same_fields(counts, expected, 'Counter contract')
    require(counts['job_cycles'] >= counts['compute_cycles'] > 0, 'Invalid job/compute interval')
    require(all(counts[key] <= counts['job_cycles'] for key in
                ('input_wait_cycles', 'read_stall_cycles', 'write_stall_cycles')),
            'Wait/stall counter exceeds job interval')


def check(area, repo):
    sealed = (area/'record.json').is_file()
    if sealed:
        record = read_json(area/'record.json')
        actual = {path.relative_to(area).as_posix():sha(path.read_bytes())
                  for path in sorted(area.rglob('*')) if path.is_file() and path != area/'record.json'}
        require(record.get('result') == 'PASS' and record.get('saved_artifact_sha256_bytes') == actual,
                'Public archive byte seal or exact file inventory differs')
    execution_bytes = (area/'execution.json').read_bytes()
    execution = json.loads(execution_bytes)
    receipt = read_json(area/'native_receipt.json')
    same_fields(execution, dict(state='PASS', build_id=BUILD_ID,
                bitstream_sha256_bytes=BIT_SHA, manifest_sha256_bytes=MANIFEST_SHA,
                operator_confirmation='Fresh OFF/ON complete; powered on and connected',
                operator_confirmation_source='Actual separate post-T8 user reply'), 'Execution')
    same_fields(receipt, dict(actual_exit_code=0, execution_sha256_bytes=sha(execution_bytes)), 'Native receipt')
    require(receipt.get('session_id') is None or type(receipt['session_id']) is int,
            'Native session ID is invalid')
    require(isinstance(receipt.get('tool_chunk_id'), str) and receipt['tool_chunk_id'],
            'Native completion lacks its tool chunk')
    began, finished = utc(execution['utc_started']), utc(execution['utc_finished'])
    require(began <= finished <= utc(receipt['observed_utc']), 'Execution/receipt timestamp order differs')
    require(not any(key in execution for key in ('error', 'close_error')), 'Execution records an error')
    require(execution.get('source_sha256_utf8_lf') == RUNNER_SOURCES, 'Original method source hashes differ')
    for name, digest in RUNNER_SOURCES.items():
        require(sha((repo/name).read_text(encoding='utf-8').encode('utf-8')) == digest,
                'Current method source differs: ' + name)

    stages = execution['stages']
    require(len(stages) == 2 and [stage['name'] for stage in stages] == ['program', 'cli'],
            'Expected exactly one program and one CLI stage')
    previous = began
    for stage in stages:
        same_fields(stage, dict(actual_exit_code=0), 'Child stage')
        start, end = utc(stage['utc_started']), utc(stage['utc_finished'])
        require(previous <= start <= end <= finished, 'Child stages overlap or lie outside execution')
        previous = end
        for stream in ('stdout', 'stderr'):
            raw = (area/(stage['name']+'.'+stream+'.txt')).read_bytes()
            require(sha(raw) == stage[stream+'_sha256_bytes'], 'Child log hash differs: '+stage['name']+'/'+stream)
        require(isinstance(stage.get('command'), list) and '-B' in stage['command'],
                'Child command lacks explicit no-bytecode setting')
    program_text = (area/'program.stdout.txt').read_text(encoding='utf-8')
    require('0x9d4beb4d' in program_text and BIT_SHA in program_text, 'Programming log image identity differs')
    cli_command = stages[1]['command']
    for key, wanted in {'--port':'COM11', '--mode':'1', '--m':'5', '--n':'3', '--k':'9',
                        '--repeats':'3', '--retries':'0'}.items():
        require(cli_command.count(key) == 1 and cli_command[cli_command.index(key)+1] == wanted,
                'CLI command option differs: '+key)
    require('-m' in cli_command and cli_command[cli_command.index('-m')+1] == 'host.gemm',
            'CLI did not execute host.gemm')

    cli = read_json(area/'cli/results.json')
    same_fields(cli, dict(schema_version=1, kind='overlap_ddr_gemm', passed=True, port='COM11',
                mode=1, baud=1000000, seed=20261002, guards_enabled=True,
                input_pattern='seeded_signed_int8'), 'CLI record')
    require(cli.get('shape') == dict(m=5, n=3, k=9), 'CLI shape differs')
    require(cli.get('identity') == IDENTITY == execution.get('identity'), 'Observed hardware identities differ')
    require(cli['host']['source_sha256_utf8_lf'] == HOST_SOURCES, 'CLI host source hashes differ')
    require(cli.get('transport') == dict(retries=0, rejected_frames=0), 'CLI transport was not error-free')
    require(execution.get('transport') == dict(retries=0, rejected_frames=0, poisoned=False),
            'API transport was not error-free')
    require(not any(key in cli for key in ('error', 'close_error')), 'CLI records an error')

    manifest = cli['build']
    same_fields(manifest, dict(build_id=BUILD_ID, bitstream_sha256=BIT_SHA, kind='overlap_ddr_gemm',
                part='xc7a50ticsg324-1L', p=8, t=32, kmax=256, core_hz=100000000,
                baud=1000000, read_slots=4, id=0x314D474E, version=0x200,
                mode='selectable', enable_overlap=True, result='PASS',
                vivado_exit_code=0, source_hashes_unchanged=True), 'Qualified build')
    local_manifest = area/'build.json' if (area/'build.json').is_file() else repo/'build/gemm_release_p8_t32_1mbaud/build.json'
    require(sha(local_manifest.read_bytes()) == MANIFEST_SHA and read_json(local_manifest) == manifest,
            'Original selected manifest bytes or CLI manifest copy differ')
    selected_bitstream = local_manifest.parent/manifest['bitstream']
    bitstream_checked = selected_bitstream.is_file()
    if bitstream_checked:
        require(sha(selected_bitstream.read_bytes()) == BIT_SHA, 'Selected local bitstream hash differs')
    require(len(manifest['source_sha256_utf8_lf']) == 32, 'Expected 32 build/core source inputs')
    for name, digest in manifest['source_sha256_utf8_lf'].items():
        require(sha((repo/name).read_text(encoding='utf-8').encode('utf-8')) == digest,
                'Current core/build source differs: ' + name)

    runs = cli['runs']
    require(len(runs) == 3, 'CLI did not save exactly three jobs')
    for index, run in enumerate(runs):
        same_fields(run, dict(job_id=index+1, repetition=index, m=5, n=3, k=9, p=8, t=32,
                    mode=1, core_hz=100000000, build_id=BUILD_ID, compared_elements=15,
                    guard_bytes_checked=1156, passed=True,
                    input_residency='uploaded' if index == 0 else 'ddr_reused'), 'CLI run')
        integer_counters(run, 5, 3, 9)
        for name, expected in dict(core_seconds=run['job_cycles']/100000000,
                                   useful_gops=2*5*3*9*100000000/run['job_cycles']/1e9,
                                   useful_utilization=5*3*9/(64*run['job_cycles'])).items():
            require(type(run[name]) in (int, float) and math.isfinite(run[name]) and
                    math.isclose(run[name], expected, rel_tol=1e-12, abs_tol=1e-15),
                    'Derived CLI metric differs: ' + name)
        for name, value in run.items():
            if name.endswith('_seconds'):
                require(type(value) in (int, float) and math.isfinite(value) and value >= 0,
                        'Invalid host timing: ' + name)
    cycles = [run['job_cycles'] for run in runs]
    require(cli.get('summary') == dict(jobs=3, job_cycles_min=min(cycles),
                job_cycles_median=statistics.median(cycles), job_cycles_max=max(cycles)), 'CLI cycle summary differs')
    with (area/'cli/results.csv').open(encoding='utf-8', newline='') as stream:
        csv_runs = list(csv.DictReader(stream))
    require(len(csv_runs) == 3, 'CLI CSV sample count differs')
    for run, csv_run in zip(runs, csv_runs):
        require(csv_run == {key:str(value) for key, value in run.items()}, 'CLI original JSON/CSV differ')
    stdout = (area/'cli.stdout.txt').read_text(encoding='utf-8').splitlines()
    require(stdout == [f"PASS job {run['job_id']}: {run['job_cycles']} cycles, {run['useful_gops']:.6f} useful GOPS"
                       for run in runs], 'CLI success output differs from saved measurements')

    api_jobs = execution['api_jobs']
    require(len(api_jobs) == 2, 'API did not save exactly two jobs')
    a = [[1, -2, 3], [4, 5, -6]]
    b = [[7, 8], [-9, 10], [11, -12]]
    oracle = [[sum(a[row][kk]*b[kk][column] for kk in range(3)) for column in range(2)] for row in range(2)]
    require(oracle == [[58, -48], [-83, 154]], 'Independent Python integer oracle differs')
    for index, job in enumerate(api_jobs):
        same_fields(job, dict(job_id=index+1, mode=index, compared_elements=4, guard_bytes_checked=752), 'API job')
        require(job['a'] == a and job['b'] == b and job['expected'] == oracle and job['c'] == oracle,
                'Saved API matrices differ from independently calculated signed result')
        require(all(type(value) is int and -128 <= value <= 127 for matrix in (job['a'], job['b'])
                    for row in matrix for value in row), 'Saved API operands are not signed INT8')
        require(all(type(value) is int and -(2**31) <= value < 2**31 for row in job['c'] for value in row),
                'Saved API result is not signed INT32')
        require(job['descriptor'] == dict(m=2, n=2, k=3, a_base=64, bt_base=320, c_base=576,
                    a_stride=64, bt_stride=64, c_stride=64, mode=index, watchdog=10000000),
                'API descriptor/layout differs')
        integer_counters(job['counters'], 2, 2, 3)

    return dict(result='PASS', software_only=True, image=f'0x{BUILD_ID:08x}',
                exact_public_inventory_checked=sealed, selected_local_bitstream_checked=bitstream_checked,
                native_execution_sha256_bytes=sha(execution_bytes), checked_current_sources=38,
                cli_jobs=3, cli_reported_compared_outputs=45, cli_reported_guard_bytes=3468,
                api_jobs=2, api_saved_outputs_independently_replayed=8, api_reported_guard_bytes=1504,
                cli_cycles=cycles, api_cycles=[job['counters']['job_cycles'] for job in api_jobs],
                replay_limits=[
                    'CLI matrices and raw memory/UART were not saved: checks metadata, reported comparison counts, counters, logs and source/image binding; no independent CLI numerical replay.',
                    'Saved API A/B/C permit independent integer numerical replay for eight outputs; guard counts are recorded live checks, without saved raw DDR guard bytes.',
                    'Native/child receipts are checked for binding and consistency; software replay cannot reobserve programming, physical AXI completion, warm reset or electrical timing.',
                ])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--area', type=Path, required=True)
    parser.add_argument('--repo', type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(check(args.area.resolve(), args.repo.resolve()), indent=2))
        return 0
    except (Exception, KeyboardInterrupt) as error:
        print('FAIL: '+type(error).__name__+': '+str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
