"""Verify T32 endurance or full-grid records without accessing hardware.

No raw C snapshots or UART stream were retained for these phases. Verification
checks original seals, metadata, counters and execution provenance; it does not
replay the original numerical comparison. All gates remain active under -O.
"""
import argparse
import csv
from datetime import datetime
import gzip
import hashlib
import io
import json
import math
from pathlib import Path, PurePosixPath
import re
import statistics
import tarfile

BUILD_ID = 0x9D4BEB4D
BITSTREAM = 'd187f51bac37c3682641243f1db112579ad14c8ac45a1b3df52e704bf58c8327'
COUNTERS = ('job_cycles', 'compute_cycles', 'read_beats', 'write_beats',
            'write_valid_bytes', 'input_wait_cycles', 'read_stall_cycles', 'write_stall_cycles')
METRICS = COUNTERS + ('core_seconds', 'useful_gops', 'useful_utilization')
HOST_TIMES = ('c_initialize_seconds', 'configure_seconds', 'job_wall_seconds',
              'allocation_download_seconds', 'validation_seconds', 'resident_host_seconds')
MIXED = ((1, 1, 1), (5, 3, 9), (31, 33, 17), (33, 35, 256),
         (65, 63, 255), (1, 64, 256), (64, 1, 256), (32, 32, 256))
GRID = tuple((m, m, k) for m in (32, 64, 128, 256) for k in (16, 64, 256)) + (
    (31, 33, 17), (65, 63, 255), (1, 64, 256), (64, 1, 256))
IDENTITY = dict(id=0x314D474E, version=0x200, geometry=(256 << 16) | (32 << 8) | 8,
                p=8, t=32, kmax=256, build_id=BUILD_ID, core_hz=100000000)


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def text_hash(raw):
    return sha(raw.decode('utf-8').replace('\r\n', '\n').replace('\r', '\n').encode())


def load(path):
    return json.loads(path.read_bytes())


def canonical(value):
    return sha(json.dumps(value, sort_keys=True, separators=(',', ':')).encode())


def safe_name(name):
    path = PurePosixPath(name)
    require(name and not path.is_absolute() and path.as_posix() == name and
            '\\' not in name and ':' not in name and all(x not in ('.', '..') for x in path.parts),
            'Unsafe archive path: ' + name)


def unpack(raw, inventory):
    tar_raw = gzip.decompress(raw)
    require(sha(raw) == inventory['archive_sha256_bytes'] and len(raw) == inventory['archive_bytes'] and
            sha(tar_raw) == inventory['tar_sha256_bytes'] and len(tar_raw) == inventory['tar_bytes'],
            'Archive stream identity differs')
    records = {}
    with tarfile.open(fileobj=io.BytesIO(tar_raw), mode='r:') as stream:
        for member in stream.getmembers():
            safe_name(member.name)
            require(member.isfile() and member.name not in records, 'Non-file or duplicate archive path')
            records[member.name] = stream.extractfile(member).read()
    require(set(records) == set(inventory['members']), 'Archive inventory differs')
    for name, raw in records.items():
        require((len(raw), sha(raw)) == (inventory['members'][name]['bytes'],
                inventory['members'][name]['sha256_bytes']), 'Archived original bytes differ: ' + name)
    require(inventory['original_files'] == len(records) and inventory['raw_bytes'] ==
            sum(map(len, records.values())), 'Archive count/size differs')
    return records


def distribution(values):
    return dict(minimum=min(values), median=statistics.median(values), maximum=max(values))


def distributions(runs, fields=METRICS):
    return {str(mode): dict(samples=sum(r['mode'] == mode for r in runs),
            metrics={key: distribution([r[key] for r in runs if r['mode'] == mode]) for key in fields})
            for mode in (0, 1)}


def traffic(m, n, k):
    reads = writes = useful = compute = tiles = microtiles = 0
    for i in range(0, m, 32):
        r = min(32, m - i)
        for j in range(0, n, 32):
            c = min(32, n - j)
            tiles += 1
            for _ in range(r + c):
                reads += len(range(0, k, 8))
            for _ in range(r):
                writes += len(range(0, c, 2))
            useful += 4 * r * c
            for _ in range(0, r, 8):
                for _ in range(0, c, 8):
                    compute += k + 23
                    microtiles += 1
    return dict(read_beats=reads, write_beats=writes, write_valid_bytes=useful,
                compute_cycles=compute), tiles, microtiles


def counts(runs):
    return dict(completed_jobs=len(runs), compared_outputs=sum(r['m'] * r['n'] for r in runs),
                allocation_bytes_checked=sum(r['allocation_bytes_checked'] for r in runs),
                guard_input_bytes_checked=sum(r['guard_input_bytes_checked'] for r in runs))


def csv_matches(raw, runs):
    fields = list(dict.fromkeys(key for run in runs for key in run))
    reader = csv.DictReader(io.StringIO(raw.decode('utf-8')))
    rows = list(reader)
    require(reader.fieldnames == fields and len(rows) == len(runs) and
            all(row == {key: str(run[key]) for key in fields} for row, run in zip(rows, runs)),
            'CSV and original job records differ')


def check_plan(plan):
    require(plan['schema_version'] == 1 and plan['modes'] == [0, 1] and plan['samples'] == 30 and
            plan['seed'] == 20261004 and plan['oracle'] == 'numpy' and plan['guard_bytes'] == 64 and
            plan['minimum_continuous_seconds'] == 1800.0 and plan['maximum_shape'] == [1024, 1024, 256] and
            tuple(map(tuple, plan['mixed_shapes'])) == MIXED and plan['benchmark_cases'] ==
            [dict(index=index, m=m, n=n, k=k, seed=20261004 + index * 9973)
             for index, (m, n, k) in enumerate(GRID)], 'Exact release plan differs')


def unit_names(kind):
    if kind == 'endurance':
        return ['endurance']
    require(kind == 'benchmark', 'Unsupported evidence phase')
    return [f'benchmark/case_{index:02d}_{m}x{n}x{k}' for index, (m, n, k) in enumerate(GRID)]


def verify_unit(files, unit, plan_record, build):
    """Check one immutable complete unit independently of parent publication."""
    get = lambda name: json.loads(files[name])
    seal, report, plan = get('seal.json'), get('results.json'), plan_record['plan']
    check_plan(plan)
    require(seal['result'] == 'PASS' and {name: sha(raw) for name, raw in files.items() if name != 'seal.json'} ==
            seal['saved_artifact_sha256_bytes'], 'Original unit seal differs: ' + unit)
    require(report['state'] == 'PASS' and report['passed'] is True and report['source_hashes_unchanged'] is True and
            report['kind'] == 'gemm_release_' + unit.split('/')[0], 'Unit is not complete: ' + unit)
    binding = seal['binding']
    require(binding == report['binding'] and binding['unit'] == unit and
            binding['plan_sha256'] == canonical(plan), 'Unit/plan binding differs')
    for key in ('manifest_sha256_bytes', 'build_id', 'bitstream_sha256', 'host_source_sha256_utf8_lf'):
        require(binding[key] == plan_record[key], 'Plan identity differs: ' + key)
    require(report['manifest'] == build and report['identity'] == IDENTITY and
            report['transport'] == dict(retries=0, rejected_frames=0, poisoned=False) and
            report['host']['source_sha256_utf8_lf'] == binding['host_source_sha256_utf8_lf'],
            'Observed identity/transport/source binding differs')
    runs = report['runs']
    require(runs and type(runs[0]['job_id']) is int and 1 <= runs[0]['job_id'] < 2**32,
            'Missing or invalid first job ID')
    first_id = runs[0]['job_id']
    if unit == 'endurance':
        cycles = report['completed_mixed_cycles']
        require(type(cycles) is int and cycles >= 1 and len(runs) == cycles * len(MIXED) * 2,
                'Endurance omitted a complete mixed cycle')
        require(math.isfinite(report['continuous_exercise_seconds']) and report['continuous_exercise_seconds'] >= 1800.0 and
                report['elapsed_seconds'] >= report['continuous_exercise_seconds'], 'Endurance duration too short/invalid')
        actual_interval = (datetime.fromisoformat(report['utc_finished']) -
                           datetime.fromisoformat(report['utc_started'])).total_seconds()
        require(math.isfinite(report['elapsed_seconds']) and
                1800 <= report['continuous_exercise_seconds'] <= report['elapsed_seconds'] <= actual_interval + 0.1,
                'Endurance persisted duration exceeds its actual recorded UTC interval')
        cases = [(cycle * 16 + index * 2, shape, 20261004 + cycle * 104729 + index * 9973,
                  1, (cycle + index) % 2) for cycle in range(cycles) for index, shape in enumerate(MIXED)]
    else:
        index = unit_names('benchmark').index(unit)
        require(len(runs) == 60 and report['continuous_exercise_seconds'] == 0,
                'Benchmark is not 30 completed samples in each mode')
        require(index != 11 or first_id == 1, 'Historical dense unit first ID differs')
        cases = [(0, GRID[index], 20261004 + index * 9973, 30, 0)]
        cycles = None
    expected_names = {'seal.json', 'results.json', 'results.csv'}
    previous_finish = report['utc_started']
    ratios, preparations, total_tiles, total_microtiles = [], [], 0, 0
    for offset, shape, seed, samples, mode_phase in cases:
        case_name = f'case_{offset:06d}.json'
        expected_names.add(case_name)
        case = get(case_name)
        require(case['shape'] == list(shape) and case['seed'] == seed, 'Case shape/seed/order differs')
        m, n, k = shape
        descriptor = case['descriptor']
        require((descriptor['m'], descriptor['n'], descriptor['k'], descriptor['mode'], descriptor['watchdog']) ==
                (m, n, k, 0, 10000000), 'Recorded case descriptor differs')
        require(all(type(descriptor[key]) is int and descriptor[key] % 64 == 0 for key in
                ('a_base', 'bt_base', 'c_base', 'a_stride', 'bt_stride', 'c_stride')) and
                descriptor['a_stride'] >= k and descriptor['bt_stride'] >= k and descriptor['c_stride'] >= 4 * n,
                'Invalid allocation alignment/stride')
        lengths = [m * descriptor['a_stride'], n * descriptor['bt_stride'], m * descriptor['c_stride']]
        expected_images = [(name, descriptor[name + '_base'] - 64, length + 128)
                           for name, length in zip(('a', 'bt', 'c'), lengths)]
        require([(x['name'], x['address'], x['bytes']) for x in case['images']] == expected_images and
                all(0 <= start < start + length <= 128 * 2**20 for _, start, length in expected_images),
                'Guarded allocation extent differs')
        regions = [(start, start + length) for _, start, length in expected_images]
        require(all(end <= other or other_end <= start for position, (start, end) in enumerate(regions)
                    for other, other_end in regions[position + 1:]), 'Guarded allocations overlap')
        c_length = lengths[2] + 128
        allocation_bytes = sum(lengths) + 384
        expected_traffic, tiles, microtiles = traffic(m, n, k)
        preparations.append({key: case[key] for key in
                             ('preparation_seconds', 'oracle_seconds', 'input_upload_seconds')})
        for sample in range(samples):
            modes = (0, 1) if (sample + mode_phase) % 2 == 0 else (1, 0)
            pair = runs[offset + sample * 2:offset + sample * 2 + 2]
            before_digest = sha(bytes(1 + (seed + (sample + 1) * 53 + 37 * q) % 255 for q in range(c_length)))
            for which, run in enumerate(pair):
                ordinal = offset + sample * 2 + which
                job_id = first_id + ordinal
                name = f'job_{job_id:06d}/job.json'
                expected_names.add(name)
                require(run == get(name) and run['state'] == run['stage'] == 'PASS' and run['passed'] is True,
                        'Original job differs/failed')
                require((run['job_id'], run['sample'], run['mode'], run['seed'], run['m'], run['n'], run['k'],
                         run['p'], run['t'], run['build_id'], run['core_hz']) ==
                        (job_id, sample, modes[which], seed, m, n, k, 8, 32, BUILD_ID, 100000000),
                        'Job order/configuration differs')
                require(run['descriptor'] == dict(descriptor, mode=run['mode']), 'Job descriptor snapshot differs')
                require(previous_finish <= run['utc_started'] <= run['utc_finished'] <= report['utc_finished'],
                        'Job timestamps overlap or leave the unit interval')
                previous_finish = run['utc_finished']
                require(all(type(run[key]) is int and 0 <= run[key] < 2**64 for key in COUNTERS) and
                        run['job_cycles'] >= run['compute_cycles'] > 0, 'Invalid frozen counters')
                for key, expected in expected_traffic.items():
                    require(run[key] == expected, 'Independent tile traffic/schedule differs: ' + key)
                require((run['compared_elements'], run['allocation_bytes_checked'], run['guard_input_bytes_checked']) ==
                        (m * n, allocation_bytes, allocation_bytes - 4 * m * n), 'Full check counts differ')
                for key in ('input_a_sha256_bytes', 'raw_b_sha256_bytes', 'oracle_sha256_bytes'):
                    require(run[key] == case[key] and re.fullmatch('[0-9a-f]{64}', run[key]), 'Resident input/oracle digest differs')
                require(set(run['snapshot_sha256_bytes']) == {'a', 'bt', 'c'} and
                        all(re.fullmatch('[0-9a-f]{64}', value) for value in run['snapshot_sha256_bytes'].values()),
                        'Snapshot digest layout differs')
                for image in case['images'][:2]:
                    require(run['snapshot_sha256_bytes'][image['name']] == image['sha256_bytes'], 'Input bytes changed during job')
                require(run['c_before_sha256_bytes'] == before_digest, 'Fresh C sentinel digest differs')
                require(all(type(run[key]) in (float, int) and math.isfinite(run[key]) and run[key] >= 0
                            for key in HOST_TIMES), 'Invalid host time')
                derived = dict(core_seconds=run['job_cycles'] / 1e8,
                               useful_gops=2 * m * n * k * 1e8 / run['job_cycles'] / 1e9,
                               useful_utilization=m * n * k / (64 * run['job_cycles']),
                               resident_host_seconds=sum(run[key] for key in
                                ('c_initialize_seconds', 'configure_seconds', 'job_wall_seconds', 'allocation_download_seconds')))
                for key, expected in derived.items():
                    require(math.isclose(run[key], expected, rel_tol=1e-14, abs_tol=1e-15), 'Derived timing differs: ' + key)
                total_tiles += tiles
                total_microtiles += microtiles
            for key in ('input_a_sha256_bytes', 'raw_b_sha256_bytes', 'oracle_sha256_bytes',
                        'c_before_sha256_bytes', 'snapshot_sha256_bytes'):
                require(pair[0][key] == pair[1][key], 'Matched modes used different bytes: ' + key)
            modes = {run['mode']: run for run in pair}
            ratios.append(modes[0]['job_cycles'] / modes[1]['job_cycles'])
    require(set(files) == expected_names, 'Missing or unexpected original unit files')
    csv_matches(files['results.csv'], runs)
    require(report['checked_counts'] == counts(runs) and report['distributions'] == distributions(runs),
            'Original aggregate counts/distributions differ')
    computed = dict(unit=unit, checked_counts=counts(runs), macrotiles=total_tiles,
                    microtiles=total_microtiles, distributions=distributions(runs),
                    host_distributions=distributions(runs, HOST_TIMES),
                    paired_cycle_speedup=distribution(ratios), case_preparations=preparations,
                    original_files=len(files), original_sealed_files=len(files) - 1)
    if cycles is not None:
        computed.update(completed_mixed_cycles=cycles, continuous_exercise_seconds=report['continuous_exercise_seconds'])
    return computed, runs, report


def verify_provenance(directory, build, plan_record, manifest):
    """Keep the interrupted and recovered process histories separate."""
    original = load(directory / 'original_execution.json')
    old_native = load(directory / 'original_native_receipt.json')
    current = load(directory / 'execution.json')
    native = load(directory / 'native_receipt.json')
    recovery = load(directory / 'recovery/record.json')
    recovery_native = load(directory / 'recovery/native_receipt.json')
    require(old_native['actual_exit_code'] == 1 and old_native['session_id'] == 17347 and
            old_native['tool_chunk_id'] == '679880' and old_native['execution_sha256_bytes'] ==
            sha((directory / 'original_execution.json').read_bytes()) and original['state'] == 'FAIL',
            'Original failed process history differs')
    require(native['actual_exit_code'] == 0 and native['session_id'] == 78344 and native['tool_chunk_id'] and
            native['execution_sha256_bytes'] == sha((directory / 'execution.json').read_bytes()) and
            current['state'] == 'PASS' and current['utc_finished'] is not None and
            current['utc_finished'] <= native['observed_utc'], 'Recovered process lacks actual successful completion')
    require(current['original_execution_sha256_bytes'] == sha((directory / 'original_execution.json').read_bytes()) and
            current['original_native_receipt_sha256_bytes'] == sha((directory / 'original_native_receipt.json').read_bytes()) and
            current['recovery_record_sha256_bytes'] == sha((directory / 'recovery/record.json').read_bytes()),
            'Recovery chain hashes differ')
    require(original['build_id'] == current['build_id'] == BUILD_ID and
            original['bitstream_sha256_bytes'] == current['bitstream_sha256_bytes'] == BITSTREAM and
            original['manifest_sha256_bytes'] == current['manifest_sha256_bytes'] == plan_record['manifest_sha256_bytes'] ==
            sha((directory / 'build.json').read_bytes()) and
            original['source_sha256_utf8_lf'] == current['source_sha256_utf8_lf'] == plan_record['host_source_sha256_utf8_lf'] and
            original['helper_sha256_bytes'] == sha((directory / 'original_board_method.py').read_bytes()) and
            current['helper_sha256_bytes'] == sha((directory / 'board_method.py').read_bytes()) and
            current['keep_awake_source_sha256_bytes'] == sha((directory / 'keep_awake.py').read_bytes()),
            'Exact recovered image/source/wrapper binding differs')
    require(original['operator_confirmation'] == 'Fresh OFF/ON complete; powered on and connected' and
            original['operator_confirmation_source'] == 'Actual user reply to the new-image cold-cycle request in this session',
            'Actual original cold-power receipt differs')
    require(recovery_native['actual_exit_code'] == 0 and recovery_native['tool_chunk_id'] == '39a5da' and
            recovery_native['record_sha256_bytes'] == sha((directory / 'recovery/record.json').read_bytes()) and
            datetime.fromisoformat(recovery['utc_finished']) <=
            datetime.fromisoformat(recovery_native['observed_receipt_saved_utc']) and
            recovery['result'] == 'PASS' and recovery['hardware_mutated'] is False and recovery['reset_or_reprogrammed'] is False and
            recovery['replayed_start'] is False and recovery['last_job_id'] == 384 and recovery['status'] == 21 and
            recovery['identity'] == IDENTITY and recovery['source_sha256_utf8_lf'] == plan_record['host_source_sha256_utf8_lf'] and
            recovery['failed_execution_sha256_bytes'] == sha((directory / 'original_execution.json').read_bytes()) and
            recovery['failed_job_sha256_bytes'] == sha((directory / 'recovery/failed_job.json').read_bytes()) and
            recovery['helper_sha256_bytes'] == sha((directory / 'recovery/method.py').read_bytes()) and
            recovery['standby_events_sha256_bytes'] == sha((directory / 'recovery/standby_events.json').read_bytes()) and
            original['utc_finished'] <= recovery['utc_started'] <= recovery['utc_finished'] <= current['utc_started'],
            'Read-only retained-completion recovery differs')
    failed = load(directory / 'recovery/failed_job.json')
    require(failed['state'] == 'FAIL' and failed['passed'] is False and failed['job_id'] == 384 and
            recovery['counters'] == {key: failed[key] for key in COUNTERS}, 'Recovery counters replaced the failed job')
    recovered_bytes = {}
    for name, saved in recovery['saved_snapshots'].items():
        compressed = (directory / 'recovery' / saved['file']).read_bytes()
        raw = gzip.decompress(compressed)
        require(sha(compressed) == saved['gzip_sha256_bytes'] and sha(raw) == saved['sha256_bytes'] and
                len(raw) == saved['bytes'], 'Retained recovery snapshot differs: ' + name)
        recovered_bytes[name] = raw
    require(set(recovered_bytes) == {'a', 'bt', 'c'} and recovery['transport'] ==
            dict(retries=0, rejected_frames=0, poisoned=False), 'Recovery memory/transport set differs')
    case = load(directory / 'recovery/case.json')
    require(case['shape'] == [64, 1, 256] and case['seed'] == failed['seed'] and
            failed['descriptor'] == case['descriptor'], 'Retained recovery descriptor differs')
    for image in case['images'][:2]:
        require(sha(recovered_bytes[image['name']]) == image['sha256_bytes'], 'Retained recovery inputs changed')
    expected_c = bytearray(1 + (failed['seed'] + 53 + 37 * q) % 255 for q in range(len(recovered_bytes['c'])))
    outputs = bytearray()
    for row in range(64):
        total = 0
        for k in range(256):
            a = recovered_bytes['a'][64 + row * 256 + k]
            b = recovered_bytes['bt'][64 + k]
            total += (a if a < 128 else a - 256) * (b if b < 128 else b - 256)
        word = total.to_bytes(4, 'little', signed=True)
        outputs.extend(word)
        expected_c[64 + row * 64:68 + row * 64] = word
    require(bytes(expected_c) == recovered_bytes['c'] and sha(outputs) == failed['oracle_sha256_bytes'] and
            recovery['compared_outputs'] == 64 and recovery['allocation_bytes_checked'] == 21120 and
            recovery['guard_input_bytes_checked'] == 20864, 'Independent retained recovery comparison differs')
    old_stages, new_stages = original['stages'], current['stages']
    require([s['name'] for s in old_stages] == ['program', 'smoke', 'dense_benchmark', 'full_qualification'] and
            [s['actual_exit_code'] for s in old_stages] == [0, 0, 0, 1] and len(new_stages) == 1 and
            new_stages[0]['name'] == 'full_qualification' and new_stages[0]['actual_exit_code'] == 0,
            'Failed versus recovered stage sequence differs')
    logs = {}
    for prefix, history, stages in (('original_', original, old_stages), ('', current, new_stages)):
        previous = history['utc_started']
        for stage in stages:
            raw = gzip.decompress((directory / (prefix + 'execution_logs') / (stage['name'] + '_console.txt.gz')).read_bytes())
            require(sha(raw) == stage['console_sha256_bytes'] and previous <= stage['utc_started'] <=
                    stage['utc_finished'] <= history['utc_finished'], 'Stage console/time differs')
            previous = stage['utc_finished']
            logs[prefix + stage['name']] = raw.decode().splitlines()
            command = stage['command']
            require(command.count('--manifest') == 1 and command[command.index('--manifest') + 1].replace('\\', '/').endswith(
                    '/build/gemm_release_p8_t32_1mbaud/build.json'), 'Executed manifest differs')
    for stage, phase in ((old_stages[2], 'benchmark'), (old_stages[3], 'all'), (new_stages[0], 'all')):
        command = stage['command']
        for key, value in (('--phase', phase), ('--modes', 'both'), ('--samples', '30'), ('--oracle', 'numpy'),
                           ('--duration', '1800'), ('--seed', '20261004'), ('--port', 'COM11')):
            require(command.count(key) == 1 and command[command.index(key) + 1] == value, 'Executed option differs: ' + key)
    command = new_stages[0]['command']
    require(command.count('--resume') == 1 and command.count('--') == 1 and
            command[1].replace('\\', '/').endswith('/scripts/keep_awake.py') and
            old_stages[2]['command'].count('--cases') == 1 and
            old_stages[2]['command'][old_stages[2]['command'].index('--cases') + 1] == '11',
            'Temporary sleep-inhibited command/resume differs')
    program = (directory / 'program_log.txt').read_text()
    require('DDR_GEMM_PROGRAMMED xc7a50t ' in program and
            '/build/gemm_release_p8_t32_1mbaud/gemm_ddr.bit' in program and 'End of startup status: HIGH' in program and
            '0x9d4beb4d' in '\n'.join(logs['original_program']) and BITSTREAM in '\n'.join(logs['original_program']),
            'Original programming marker differs')
    static, review = load(directory / 'static_archive_manifest.json'), load(directory / 'static_review_record.json')
    require(static['result'] == 'PASS' and static['build_id'] == BUILD_ID and static['bitstream_sha256_bytes'] == BITSTREAM and
            static['source_sha256_utf8_lf'] == build['source_sha256_utf8_lf'] and
            review['result'] == 'PASS_PROVISIONAL_REVIEW' and review['actual_exit_code'] == 0 and
            static['checkpoint_sha256_bytes'] == review['checkpoint_sha256_bytes_before'] == review['checkpoint_sha256_bytes_after'] and
            original['review_record_sha256_bytes'] == sha((directory / 'static_review_record.json').read_bytes()) and
            manifest['static_archive_manifest_sha256_bytes'] == sha((directory / 'static_archive_manifest.json').read_bytes()),
            'Own static/checkpoint review binding differs')
    parent_summary = load(directory / 'parent_summary.json')
    expected_units = ['maximum', 'endurance'] + unit_names('benchmark')
    require(sha((directory / 'parent_summary.json').read_bytes()) == current['summary_sha256_bytes'] and
            parent_summary['result'] == 'PASS' and parent_summary['source_hashes_unchanged'] is True and
            parent_summary['all_requested_release_phases_complete'] is True and parent_summary['full_grid_benchmark_complete'] is True and
            parent_summary['available_completed_units'] == current['available_completed_units'] == expected_units and
            parent_summary['build_id'] == BUILD_ID and parent_summary['bitstream_sha256'] == BITSTREAM and
            parent_summary['plan_sha256'] == canonical(plan_record['plan']), 'Completed recovered parent summary differs')
    require([r['unit'] for r in current['reused_completed_units']] == ['maximum', 'benchmark/case_11_256x256x256'] and
            all(r['original_seal_sha256_bytes'] == r['copied_seal_sha256_bytes'] for r in current['reused_completed_units']),
            'Historical unit reuse differs')
    for item in current['reused_completed_units']:
        label = 'maximum' if item['unit'] == 'maximum' else 'dense'
        original_seal_raw = (directory / 'reused_units' / (label + '_seal.json')).read_bytes()
        original_seal = json.loads(original_seal_raw)
        require(original_seal['result'] == 'PASS' and sha(original_seal_raw) ==
                item['original_seal_sha256_bytes'] == item['copied_seal_sha256_bytes'] and
                original_seal['binding']['unit'] == item['unit'] and
                original_seal['binding']['build_id'] == BUILD_ID and
                original_seal['binding']['manifest_sha256_bytes'] == plan_record['manifest_sha256_bytes'] and
                original_seal['binding']['plan_sha256'] == canonical(plan_record['plan']),
                'Previously completed unit dependency seal differs')
    return logs, old_stages[2], new_stages[0]


def verify_package(directory, check_current=False, repo=None, extract=None):
    directory = Path(directory)
    manifest = load(directory / 'manifest.json')
    kind = manifest['phase']
    units = unit_names(kind)
    actual = {p.relative_to(directory).as_posix(): sha(p.read_bytes()) for p in directory.rglob('*')
              if p.is_file() and p != directory / 'manifest.json'}
    require(manifest['result'] == 'PASS' and manifest['kind'] == 't32_1mbaud_' + kind + '_board' and
            actual == manifest['saved_artifact_sha256_bytes'], 'Package seal differs')
    build, plan_record = load(directory / 'build.json'), load(directory / 'plan.json')
    check_plan(plan_record['plan'])
    require(build['result'] == 'PASS' and (build['build_id'], build['bitstream_sha256'], build['p'], build['t'],
            build['kmax'], build['read_slots'], build['core_hz'], build['baud'], build['version'], build['enable_overlap']) ==
            (BUILD_ID, BITSTREAM, 8, 32, 256, 4, 100000000, 1000000, 0x200, True), 'Exact final image differs')
    require(plan_record['manifest_sha256_bytes'] == sha((directory / 'build.json').read_bytes()) and
            plan_record['build_id'] == BUILD_ID and plan_record['bitstream_sha256'] == BITSTREAM and
            manifest['build_id'] == BUILD_ID and manifest['bitstream_sha256_bytes'] == BITSTREAM,
            'Plan/build identity differs')
    logs, dense_stage, current_stage = verify_provenance(directory, build, plan_record, manifest)
    sources = unpack((directory / 'sources.tar.gz').read_bytes(), load(directory / 'source_inventory.json'))
    expected_sources = dict(build['source_sha256_utf8_lf'], **plan_record['host_source_sha256_utf8_lf'])
    require(set(sources) == set(expected_sources) and len(sources) == 38, 'Source snapshot set differs')
    for name, digest in expected_sources.items():
        require(text_hash(sources[name]) == digest, 'Frozen source identity differs: ' + name)
    if check_current:
        if repo is None:
            repo = next((p for p in directory.parents if (p / 'rtl').is_dir() and (p / 'host').is_dir()), None)
        require(repo is not None, 'Use --repo to check current sources outside the checkout')
        for name, digest in expected_sources.items():
            require(text_hash((repo / name).read_bytes()) == digest, 'Current source differs: ' + name)
        require((repo / 'scripts/keep_awake.py').read_bytes() == (directory / 'keep_awake.py').read_bytes(),
                'Current temporary sleep-inhibitor source differs')

    records = unpack((directory / 'records.tar.gz').read_bytes(), load(directory / 'archive_inventory.json'))
    grouped = {unit: {name[len(unit) + 1:]: raw for name, raw in records.items() if name.startswith(unit + '/')} for unit in units}
    require({unit + '/' + name for unit, files in grouped.items() for name in files} == set(records),
            'Unexpected/missing archived unit')
    metrics, combined, reports = {}, [], {}
    for unit in units:
        metric, runs, report = verify_unit(grouped[unit], unit, plan_record, build)
        if unit == 'endurance':
            require(runs[0]['job_id'] == 1, 'Fresh recovered endurance job sequence differs')
        if unit == 'benchmark/case_11_256x256x256':
            reused = next(r for r in load(directory / 'execution.json')['reused_completed_units'] if r['unit'] == unit)
            require(sha(grouped[unit]['seal.json']) == reused['original_seal_sha256_bytes'] == reused['copied_seal_sha256_bytes'] and
                    report['utc_finished'] == reused['original_utc_finished'] and report['checked_counts'] == reused['checked_counts'],
                    'Previously sealed dense unit changed during reuse')
        metrics[unit], reports[unit] = metric, report
        combined.extend(dict(unit=unit, **run) for run in runs)
        stage_name = 'original_dense_benchmark' if unit == 'benchmark/case_11_256x256x256' else 'full_qualification'
        stage = dense_stage if unit == 'benchmark/case_11_256x256x256' else current_stage
        require(stage['utc_started'] <= report['utc_started'] <= report['utc_finished'] <= stage['utc_finished'],
                'Unit execution lies outside its actual completed stage')
        for run in runs:
            line = f"PASS {unit.split('/')[-1]} job {run['job_id']} MODE={run['mode']} " + \
                   f"{run['m']}x{run['n']}x{run['k']}: {run['job_cycles']} cycles, {run['useful_gops']:.6f} useful GOPS"
            require(logs[stage_name].count(line) == 1, 'Executed job console differs')
    totals = counts(combined)
    aggregate = load(directory / 'results.json')
    summary = load(directory / 'summary.json')
    require(aggregate == dict(schema_version=1, result='PASS', phase=kind, units=units,
                             build_id=BUILD_ID, runs=combined, checked_counts=totals), 'Derived aggregate differs')
    csv_matches((directory / 'results.csv').read_bytes(), combined)
    require(summary['result'] == 'PASS' and summary['phase'] == kind and summary['units'] == units and
            summary['checked_counts'] == totals == manifest['checked_counts'] and
            summary['unit_metrics'] == metrics and summary['full_grid_benchmark_complete'] == (kind == 'benchmark') and
            summary['parent_full_qualification_complete'] is True and summary['raw_output_byte_replay'] is False,
            'Derived summary scope/counts/distributions differ')
    if kind == 'benchmark':
        require(len(combined) == 960 and len(records) == 1024, 'Full grid omitted original samples/files')
    if extract is not None:
        require(not extract.exists(), 'Extraction destination already exists')
        extract.mkdir(parents=True)
        for name, raw in records.items():
            target = extract / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
    return dict(result='PASS', phase=kind, units=len(units), checked_counts=totals,
                original_files=len(records), unit_metrics=metrics,
                scope='Original records, counters and actual completed execution; no output-byte or UART replay',
                current_sources_checked=check_current)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-current', action='store_true')
    parser.add_argument('--repo', type=Path)
    parser.add_argument('--extract-records', type=Path)
    args = parser.parse_args()
    require(args.repo is None or args.check_current, '--repo requires --check-current')
    result = verify_package(Path(__file__).resolve().parent, args.check_current,
                            args.repo.resolve() if args.repo else None,
                            args.extract_records.resolve() if args.extract_records else None)
    print(json.dumps({key: value for key, value in result.items() if key != 'unit_metrics'}, indent=2))


if __name__ == '__main__':
    main()
