"""Check preserved failed endurance evidence; never qualify it as a release.

The saved prefix contains full host comparisons, but no raw C or UART bytes.
An opcode-2 timeout leaves job 384 without final output/identity validation.
This validates records and provenance, not numerical or electrical replay.
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
import statistics
import tarfile

BUILD_ID = 0x9D4BEB4D
BITSTREAM = 'd187f51bac37c3682641243f1db112579ad14c8ac45a1b3df52e704bf58c8327'
COUNTERS = ('job_cycles', 'compute_cycles', 'read_beats', 'write_beats',
            'write_valid_bytes', 'input_wait_cycles', 'read_stall_cycles', 'write_stall_cycles')
MIXED = ((1, 1, 1), (5, 3, 9), (31, 33, 17), (33, 35, 256),
         (65, 63, 255), (1, 64, 256), (64, 1, 256), (32, 32, 256))
ERROR = dict(type='TransportError', message='opcode 0x02 timed out; outcome uncertain, reconnect and reload')


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def normalized(raw):
    return sha(raw.decode('utf-8').replace('\r\n', '\n').replace('\r', '\n').encode())


def load(path):
    return json.loads(path.read_bytes())


def unpack(raw, inventory):
    plain = gzip.decompress(raw)
    require(sha(raw) == inventory['archive_sha256_bytes'] and len(raw) == inventory['archive_bytes'] and
            sha(plain) == inventory['tar_sha256_bytes'] and len(plain) == inventory['tar_bytes'], 'Archive stream differs')
    files = {}
    with tarfile.open(fileobj=io.BytesIO(plain), mode='r:') as stream:
        for member in stream.getmembers():
            p = PurePosixPath(member.name)
            require(member.name and not p.is_absolute() and p.as_posix() == member.name and
                    ':' not in member.name and '\\' not in member.name and
                    all(x not in ('.', '..') for x in p.parts) and member.isfile() and member.name not in files,
                    'Unsafe/non-file/duplicate archive member')
            files[member.name] = stream.extractfile(member).read()
    require(set(files) == set(inventory['members']) and len(files) == inventory['original_files'] and
            sum(map(len, files.values())) == inventory['raw_bytes'], 'Archive inventory differs')
    for name, raw in files.items():
        require(inventory['members'][name] == dict(bytes=len(raw), sha256_bytes=sha(raw)), 'Original file bytes differ: ' + name)
    return files


def traffic(m, n, k):
    reads = writes = valid = compute = 0
    for i in range(0, m, 32):
        for j in range(0, n, 32):
            r, c = min(32, m - i), min(32, n - j)
            reads += (r + c) * len(range(0, k, 8))
            writes += r * len(range(0, c, 2))
            valid += r * c * 4
            compute += len(range(0, r, 8)) * len(range(0, c, 8)) * (k + 23)
    return dict(read_beats=reads, write_beats=writes, write_valid_bytes=valid, compute_cycles=compute)


def check_records(files, plan_record, build):
    """Validate the exact stopped dataset, including the unvalidated last job."""
    require('seal.json' not in files, 'Failed unit has been promoted to a PASS seal')
    report = json.loads(files['results.json'])
    require(report['state'] == 'FAIL' and report['passed'] is False and report['error'] == ERROR and
            report['kind'] == 'gemm_release_endurance' and report['completed_mixed_cycles'] == 23,
            'Actual failure outcome differs')
    binding = report['binding']
    plan = plan_record['plan']
    require(plan['modes'] == [0, 1] and plan['seed'] == 20261004 and plan['guard_bytes'] == 64 and
            plan['minimum_continuous_seconds'] == 1800.0 and tuple(map(tuple, plan['mixed_shapes'])) == MIXED and
            binding['unit'] == 'endurance' and binding['plan_sha256'] ==
            sha(json.dumps(plan, sort_keys=True, separators=(',', ':')).encode()), 'Failed unit plan differs')
    for key in ('manifest_sha256_bytes', 'build_id', 'bitstream_sha256', 'host_source_sha256_utf8_lf'):
        require(binding[key] == plan_record[key], 'Plan identity differs: ' + key)
    require(report['manifest'] == build and report['identity'] == dict(id=0x314D474E, version=0x200,
            geometry=(256 << 16) | (32 << 8) | 8, p=8, t=32, kmax=256, build_id=BUILD_ID, core_hz=100000000) and
            report['host']['source_sha256_utf8_lf'] == binding['host_source_sha256_utf8_lf'], 'Failed unit observed identity differs')
    require(0 <= report['continuous_exercise_seconds'] < 1800.0 and
            report['elapsed_seconds'] >= report['continuous_exercise_seconds'], 'Persisted duration incorrectly qualifies endurance')
    runs = report['runs']
    require(len(runs) == 381, 'Successful prefix length differs')
    failed = json.loads(files['job_000384/job.json'])
    require(failed['state'] == 'FAIL' and failed['passed'] is False and failed['error'] == ERROR and
            failed['stage'] == 'DOWNLOAD' and failed['snapshot_sha256_bytes'] == {} and
            not any(key in failed for key in ('compared_elements', 'allocation_bytes_checked', 'guard_input_bytes_checked')),
            'Unvalidated last job promoted to completed output validation')
    expected_files = {'results.json', 'results.csv', 'job_000384/job.json'}
    totals = dict(completed_jobs=381, compared_outputs=0, allocation_bytes_checked=0, guard_input_bytes_checked=0)
    previous_finish = report['utc_started']
    c_digests = {}
    for ordinal, run in enumerate(runs + [failed]):
        job_id = ordinal + 3
        cycle, position = divmod(ordinal, 16)
        index, lane = divmod(position, 2)
        m, n, k = MIXED[index]
        mode = ((0, 1) if (cycle + index) % 2 == 0 else (1, 0))[lane]
        seed = 20261004 + cycle * 104729 + index * 9973
        case_name = f'case_{ordinal - lane:06d}.json'
        job_name = f'job_{job_id:06d}/job.json'
        expected_files.update((case_name, job_name))
        case = json.loads(files[case_name])
        require(run == json.loads(files[job_name]) and case['shape'] == [m, n, k] and case['seed'] == seed,
                'Original prefix job/case differs')
        require((run['job_id'], run['mode'], run['sample'], run['seed'], run['m'], run['n'], run['k']) ==
                (job_id, mode, 0, seed, m, n, k), 'Prefix/failed job order differs')
        descriptor = case['descriptor']
        require(run['descriptor'] == dict(descriptor, mode=mode) and
                (descriptor['m'], descriptor['n'], descriptor['k'], descriptor['watchdog'], descriptor['mode']) ==
                (m, n, k, 10000000, 0) and all(descriptor[key] % 64 == 0 for key in
                ('a_base', 'bt_base', 'c_base', 'a_stride', 'bt_stride', 'c_stride')) and
                descriptor['a_stride'] >= k and descriptor['bt_stride'] >= k and descriptor['c_stride'] >= 4 * n,
                'Descriptor layout differs')
        lengths = (m * descriptor['a_stride'], n * descriptor['bt_stride'], m * descriptor['c_stride'])
        images = [(name, descriptor[name + '_base'] - 64, length + 128)
                  for name, length in zip(('a', 'bt', 'c'), lengths)]
        require([(x['name'], x['address'], x['bytes']) for x in case['images']] == images and
                all(0 <= start < start + length <= 128 * 2**20 for _, start, length in images), 'Guarded allocation differs')
        regions = [(start, start + length) for _, start, length in images]
        require(all(end <= other or other_end <= start for number, (start, end) in enumerate(regions)
                    for other, other_end in regions[number + 1:]), 'Guarded allocations overlap')
        for key, expected in traffic(m, n, k).items():
            require(run[key] == expected, 'Independent traffic/schedule differs: ' + key)
        require(all(type(run[key]) is int and 0 <= run[key] < 2**64 for key in COUNTERS) and
                run['job_cycles'] >= run['compute_cycles'] > 0, 'Recorded frozen counters invalid')
        before = sha(bytes(1 + (seed + 53 + 37 * q) % 255 for q in range(lengths[2] + 128)))
        require(run['c_before_sha256_bytes'] == before, 'Fresh C sentinel differs')
        for key in ('input_a_sha256_bytes', 'raw_b_sha256_bytes', 'oracle_sha256_bytes'):
            require(run[key] == case[key], 'Resident input/oracle digest differs')
        require(previous_finish <= run['utc_started'] <= run['utc_finished'] <= report['utc_finished'], 'Prefix/failed job times differ')
        previous_finish = run['utc_finished']
        if ordinal == 381:
            continue
        require(run['state'] == run['stage'] == 'PASS' and run['passed'] is True and
                (run['p'], run['t'], run['core_hz'], run['build_id']) == (8, 32, 100000000, BUILD_ID), 'Prefix job was not fully validated')
        allocation = sum(lengths) + 384
        require((run['compared_elements'], run['allocation_bytes_checked'], run['guard_input_bytes_checked']) ==
                (m * n, allocation, allocation - 4 * m * n), 'Successful-prefix validation totals differ')
        for image in case['images'][:2]:
            require(run['snapshot_sha256_bytes'][image['name']] == image['sha256_bytes'], 'Successful-prefix inputs changed')
        for key, expected in dict(core_seconds=run['job_cycles'] / 1e8,
                useful_gops=2 * m * n * k * 1e8 / run['job_cycles'] / 1e9,
                useful_utilization=m * n * k / (64 * run['job_cycles'])).items():
            require(math.isclose(run[key], expected, rel_tol=1e-14, abs_tol=1e-15), 'Counter-derived metric differs')
        if lane == 1:
            prior = runs[ordinal - 1]
            require(all(prior[key] == run[key] for key in ('input_a_sha256_bytes', 'raw_b_sha256_bytes',
                    'oracle_sha256_bytes', 'c_before_sha256_bytes', 'snapshot_sha256_bytes')), 'Completed matched pair differs')
        totals['compared_outputs'] += m * n
        totals['allocation_bytes_checked'] += allocation
        totals['guard_input_bytes_checked'] += allocation - 4 * m * n
    require(set(files) == expected_files and len(files) == 575, 'Failed original file inventory differs')
    require(totals == report['checked_counts'], 'Successful-prefix aggregate totals differ')
    fields = list(dict.fromkeys(key for run in runs for key in run))
    reader = csv.DictReader(io.StringIO(files['results.csv'].decode()))
    rows = list(reader)
    require(reader.fieldnames == fields and len(rows) == 381 and
            all(row == {key: str(run[key]) for key in fields} for row, run in zip(rows, runs)), 'Failed-unit CSV differs')
    for mode in (0, 1):
        selected = [run for run in runs if run['mode'] == mode]
        metrics = {key: dict(minimum=min(r[key] for r in selected), median=statistics.median(r[key] for r in selected),
                   maximum=max(r[key] for r in selected)) for key in COUNTERS + ('core_seconds', 'useful_gops', 'useful_utilization')}
        require(report['distributions'][str(mode)] == dict(samples=len(selected), metrics=metrics), 'Successful-prefix distributions differ')
    return dict(recorded_outcome='FAIL', checked_counts=totals, jobs_per_mode={str(mode): sum(r['mode'] == mode for r in runs)
                for mode in (0, 1)}, failed_job_id=384, failed_job_stage='DOWNLOAD', completed_mixed_cycles=23,
                persisted_progress_seconds=report['continuous_exercise_seconds'],
                endurance_qualified=False, full_grid_qualified=False, original_files=575)


def verify_recovery(directory, execution, records, plan):
    """Replay only retained job 384; never promote the interrupted unit."""
    area = directory / 'recovery'
    record_raw = (area / 'record.json').read_bytes()
    record, native = json.loads(record_raw), load(area / 'native_receipt.json')
    failed = json.loads(records['job_000384/job.json'])
    case = json.loads(records['case_000380.json'])
    require(native['actual_exit_code'] == 0 and native['tool_chunk_id'] == '39a5da' and
            native['record_sha256_bytes'] == sha(record_raw) and
            datetime.fromisoformat(record['utc_finished']) <= datetime.fromisoformat(native['observed_receipt_saved_utc']),
            'Actual read-only recovery completion differs')
    require(record['result'] == 'PASS' and record['hardware_mutated'] is False and
            record['reset_or_reprogrammed'] is False and record['replayed_start'] is False and
            record['temporary_system_sleep_inhibited'] is True and record['status'] == 21 and
            record['last_job_id'] == 384 and record['identity'] == dict(id=0x314D474E, version=0x200,
            geometry=(256 << 16) | (32 << 8) | 8, p=8, t=32, kmax=256, build_id=BUILD_ID, core_hz=100000000) and
            record['source_sha256_utf8_lf'] == plan['host_source_sha256_utf8_lf'] and
            record['failed_execution_sha256_bytes'] == sha((directory / 'execution.json').read_bytes()) and
            record['failed_job_sha256_bytes'] == sha(records['job_000384/job.json']) and
            record['helper_sha256_bytes'] == sha((area / 'method.py').read_bytes()) and
            datetime.fromisoformat(execution['utc_finished']) <= datetime.fromisoformat(record['utc_started']) <=
            datetime.fromisoformat(record['utc_finished']), 'Separate retained recovery identity/history differs')
    require((area / 'failed_job.json').read_bytes() == records['job_000384/job.json'] and
            (area / 'case.json').read_bytes() == records['case_000380.json'] and
            record['counters'] == {key: failed[key] for key in COUNTERS}, 'Retained recovery replaced original failed bytes/counters')
    events_raw = (area / 'standby_events.json').read_bytes()
    require(sha(events_raw) == record['standby_events_sha256_bytes'] ==
            '89a7c7dd5c59aa57ebe6ae4b85b589fdf0852b5807c161a863e54115b64dae6f',
            'Actual Modern Standby event bytes differ')
    events = json.loads(events_raw)
    wakes = [event for event in events if event['Id'] == 507 and
             event['ProviderName'] == 'Microsoft-Windows-Kernel-Power' and 'exiting Modern Standby' in event['Message']]
    require(len(wakes) == 1 and
            datetime.fromisoformat(failed['utc_started']) < datetime.fromisoformat(wakes[0]['utc']) <
            datetime.fromisoformat(failed['utc_finished']), 'Recorded wake event does not bracket the interrupted job')
    snapshots = record['saved_snapshots']
    require(set(snapshots) == {'a', 'bt', 'c'} and record['transport'] ==
            dict(retries=0, rejected_frames=0, poisoned=False), 'Retained recovery memory/transport set differs')
    raw_images = {}
    for name, length in (('a', 16512), ('bt', 384), ('c', 4224)):
        saved = snapshots[name]
        require(saved['file'] == name + '_after.bin.gz', 'Retained recovery snapshot path differs')
        compressed = (area / saved['file']).read_bytes()
        raw = gzip.decompress(compressed)
        require(sha(compressed) == saved['gzip_sha256_bytes'] and sha(raw) == saved['sha256_bytes'] and
                len(raw) == saved['bytes'] == length, 'Retained recovery snapshot differs: ' + name)
        raw_images[name] = raw
    require(case['shape'] == [64, 1, 256] and case['seed'] == failed['seed'] == 22729609 and
            failed['descriptor'] == case['descriptor'], 'Retained recovery descriptor differs')
    for image in case['images'][:2]:
        require(sha(raw_images[image['name']]) == image['sha256_bytes'], 'Retained input/guard bytes changed')
    initial = bytes(1 + (failed['seed'] + 53 + 37 * q) % 255 for q in range(4224))
    require(sha(initial) == failed['c_before_sha256_bytes'], 'Retained recovery C initialization differs')
    expected_c, outputs = bytearray(initial), bytearray()
    for row in range(64):
        total = 0
        for k in range(256):
            a, b = raw_images['a'][64 + row * 256 + k], raw_images['bt'][64 + k]
            total += (a if a < 128 else a - 256) * (b if b < 128 else b - 256)
        word = total.to_bytes(4, 'little', signed=True)
        outputs.extend(word)
        expected_c[64 + row * 64:68 + row * 64] = word
    require(bytes(expected_c) == raw_images['c'] and sha(outputs) == failed['oracle_sha256_bytes'] and
            record['compared_outputs'] == 64 and record['allocation_bytes_checked'] == 21120 and
            record['guard_input_bytes_checked'] == 20864, 'Independent retained recovery output/guard replay differs')
    return dict(result='PASS_READ_ONLY_RECOVERY_REPLAY', job_id=384, compared_outputs=64,
                allocation_bytes_checked=21120, guard_input_bytes_checked=20864,
                original_endurance_outcome='FAIL', included_in_endurance_counts=False,
                standby_wake_utc=wakes[0]['utc'], timeout_site_identified=False,
                scope='Retained DDR bytes after read-only reconnect; no reset, reprogramming or START replay')


def verify(directory, check_current=False, repo=None, extract=None):
    directory = Path(directory)
    manifest = load(directory / 'manifest.json')
    actual = {p.relative_to(directory).as_posix(): sha(p.read_bytes()) for p in directory.rglob('*')
              if p.is_file() and p != directory / 'manifest.json'}
    require(manifest['result'] == 'PRESERVED_FAILURE' and manifest['kind'] == 't32_1mbaud_endurance_failed' and
            actual == manifest['saved_artifact_sha256_bytes'], 'Failed-evidence package seal differs')
    execution, native = load(directory / 'execution.json'), load(directory / 'native_receipt.json')
    require(native['actual_exit_code'] == 1 and native['session_id'] == 17347 and native['tool_chunk_id'] == '679880' and
            native['execution_sha256_bytes'] == sha((directory / 'execution.json').read_bytes()) and
            execution['state'] == 'FAIL' and execution['utc_finished'] <= native['observed_utc'], 'Actual failed native completion differs')
    require([s['name'] for s in execution['stages']] == ['program', 'smoke', 'dense_benchmark', 'full_qualification'] and
            [s['actual_exit_code'] for s in execution['stages']] == [0, 0, 0, 1], 'Actual failed stage sequence differs')
    build, plan = load(directory / 'build.json'), load(directory / 'plan.json')
    require((build['build_id'], build['bitstream_sha256'], build['p'], build['t'], build['read_slots'], build['core_hz'],
             build['baud'], build['version'], build['enable_overlap']) ==
            (BUILD_ID, BITSTREAM, 8, 32, 4, 100000000, 1000000, 0x200, True) and build['result'] == 'PASS' and
            execution['build_id'] == BUILD_ID == plan['build_id'] == manifest['build_id'] and
            execution['bitstream_sha256_bytes'] == plan['bitstream_sha256'] == manifest['bitstream_sha256_bytes'] == BITSTREAM and
            execution['manifest_sha256_bytes'] == plan['manifest_sha256_bytes'] == sha((directory / 'build.json').read_bytes()) and
            execution['helper_sha256_bytes'] == sha((directory / 'board_method.py').read_bytes()), 'Failed-run exact image/method differs')
    require(execution['operator_confirmation'] == 'Fresh OFF/ON complete; powered on and connected' and
            execution['operator_confirmation_source'] == 'Actual user reply to the new-image cold-cycle request in this session',
            'Cold-power receipt differs')
    logs, previous = {}, execution['utc_started']
    for stage in execution['stages']:
        raw = gzip.decompress((directory / 'execution_logs' / (stage['name'] + '_console.txt.gz')).read_bytes())
        require(sha(raw) == stage['console_sha256_bytes'] and previous <= stage['utc_started'] <= stage['utc_finished'] <=
                execution['utc_finished'], 'Stage console/time differs')
        previous = stage['utc_finished']
        logs[stage['name']] = raw.decode().splitlines()
    for option, value in (('--phase', 'all'), ('--port', 'COM11'), ('--samples', '30'), ('--duration', '1800'),
                          ('--seed', '20261004'), ('--modes', 'both'), ('--oracle', 'numpy')):
        command = execution['stages'][-1]['command']
        require(command.count(option) == 1 and command[command.index(option) + 1] == value, 'Failed invocation option differs')
    require(command.count('--resume') == 1, 'Failed full invocation lacks expected resume')
    require(logs['full_qualification'][-1] == 'FAIL: TransportError: ' + ERROR['message'], 'Actual timeout console differs')
    program = (directory / 'program_log.txt').read_text()
    require('DDR_GEMM_PROGRAMMED xc7a50t ' in program and '/build/gemm_release_p8_t32_1mbaud/gemm_ddr.bit' in program and
            'End of startup status: HIGH' in program and '0x9d4beb4d' in '\n'.join(logs['program']) and
            BITSTREAM in '\n'.join(logs['program']), 'Program marker differs')
    static, review = load(directory / 'static_archive_manifest.json'), load(directory / 'static_review_record.json')
    require(static['result'] == 'PASS' and static['build_id'] == BUILD_ID and static['bitstream_sha256_bytes'] == BITSTREAM and
            static['source_sha256_utf8_lf'] == build['source_sha256_utf8_lf'] and review['actual_exit_code'] == 0 and
            static['checkpoint_sha256_bytes'] == review['checkpoint_sha256_bytes_before'] == review['checkpoint_sha256_bytes_after'] and
            sha((directory / 'static_review_record.json').read_bytes()) == execution['review_record_sha256_bytes'] and
            sha((directory / 'static_archive_manifest.json').read_bytes()) == manifest['static_archive_manifest_sha256_bytes'],
            'Own static/checkpoint binding differs')
    sources = unpack((directory / 'sources.tar.gz').read_bytes(), load(directory / 'source_inventory.json'))
    expected = dict(build['source_sha256_utf8_lf'], **plan['host_source_sha256_utf8_lf'])
    require(set(sources) == set(expected) and len(sources) == 38 and
            execution['source_sha256_utf8_lf'] == plan['host_source_sha256_utf8_lf'], 'Frozen source set differs')
    if check_current and repo is None:
        repo = next((p for p in directory.parents if (p / 'rtl').is_dir() and (p / 'host').is_dir()), None)
        require(repo is not None, 'Use --repo for current checks outside the checkout')
    for name, digest in expected.items():
        require(normalized(sources[name]) == digest, 'Frozen source differs: ' + name)
        if check_current:
            require(normalized((repo / name).read_bytes()) == digest, 'Current source differs: ' + name)
    records = unpack((directory / 'records.tar.gz').read_bytes(), load(directory / 'archive_inventory.json'))
    require((directory / 'original_results.json').read_bytes() == records['results.json'] and
            (directory / 'original_results.csv').read_bytes() == records['results.csv'],
            'Readable original aggregates differ from archived original bytes')
    result = check_records(records, plan, build)
    require(result == load(directory / 'summary.json'), 'Derived failure summary differs')
    recovery = verify_recovery(directory, execution, records, plan)
    require(recovery == load(directory / 'recovery_summary.json'), 'Derived retained recovery summary differs')
    report = json.loads(records['results.json'])
    stage = execution['stages'][-1]
    require(stage['utc_started'] <= report['utc_started'] <= report['utc_finished'] <= stage['utc_finished'], 'Failed unit outside actual stage')
    for run in report['runs']:
        line = f"PASS endurance job {run['job_id']} MODE={run['mode']} {run['m']}x{run['n']}x{run['k']}: " + \
               f"{run['job_cycles']} cycles, {run['useful_gops']:.6f} useful GOPS"
        require(logs['full_qualification'].count(line) == 1, 'Successful-prefix console differs')
    require(manifest['endurance_qualified'] is False and manifest['full_grid_qualified'] is False,
            'Failed run incorrectly qualified')
    if extract is not None:
        require(not extract.exists(), 'Extraction target already exists')
        extract.mkdir(parents=True)
        for name, raw in records.items():
            target = extract / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
    return dict(result='PASS_EVIDENCE_CHECK', **result,
                recovery=recovery, current_sources_checked=check_current,
                scope='Failed endurance records/digests only; separate retained job 384 output/guard byte replay, no UART replay')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-current', action='store_true')
    parser.add_argument('--repo', type=Path)
    parser.add_argument('--extract-records', type=Path)
    args = parser.parse_args()
    require(args.repo is None or args.check_current, '--repo requires --check-current')
    print(json.dumps(verify(Path(__file__).resolve().parent, args.check_current,
                           args.repo.resolve() if args.repo else None,
                           args.extract_records.resolve() if args.extract_records else None), indent=2))


if __name__ == '__main__':
    main()
