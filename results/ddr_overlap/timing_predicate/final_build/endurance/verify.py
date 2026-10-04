"""Verify the saved endurance evidence without connecting to hardware."""

import argparse
import csv
import gzip
import hashlib
import io
import json
import math
from pathlib import Path, PurePosixPath
import statistics
import tarfile


COUNTERS = ('job_cycles', 'compute_cycles', 'read_beats', 'write_beats',
            'write_valid_bytes', 'input_wait_cycles', 'read_stall_cycles',
            'write_stall_cycles')
MIXED = ((1, 1, 1), (5, 3, 9), (31, 33, 17), (33, 35, 256),
         (65, 63, 255), (1, 64, 256), (64, 1, 256), (32, 32, 256))
BUILD_ID = 0x2C680AF7
BITSTREAM = '5124adc4348ed1f42b6420d2776d7d1f50a44df7e340a6789b3a9954373e4cf5'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def load(directory, name):
    return json.loads((directory / name).read_bytes())


def canonical(value):
    return sha(json.dumps(value, sort_keys=True, separators=(',', ':')).encode())


def safe_name(name):
    path = PurePosixPath(name)
    require(not path.is_absolute() and name and '\\' not in name and
            all(part not in ('.', '..') for part in path.parts), 'Unsafe member: ' + name)


def traffic(m, n, k, p, t):
    """Enumerate actual macrotiles and nonempty microtiles independently."""
    reads = writes = valid_bytes = compute = 0
    for i in range(0, m, t):
        rows = min(t, m - i)
        for j in range(0, n, t):
            cols = min(t, n - j)
            for _ in range(rows + cols):
                reads += len(range(0, k, 8))
            for _ in range(rows):
                writes += len(range(0, cols, 2))
            valid_bytes += rows * cols * 4
            for _ in range(0, rows, p):
                for _ in range(0, cols, p):
                    compute += k + 3 * p - 1
    return dict(read_beats=reads, write_beats=writes,
                write_valid_bytes=valid_bytes, compute_cycles=compute)


def verify(directory, extract=None, check_current=False):
    manifest = load(directory, 'manifest.json')
    require(manifest['result'] == 'PASS', 'Curation is not PASS')
    saved = manifest['saved_artifact_sha256_bytes']
    actual = {p.relative_to(directory).as_posix(): sha(p.read_bytes())
              for p in directory.rglob('*') if p.is_file() and p != directory / 'manifest.json'}
    require(actual == saved, 'Public file inventory/hash mismatch')
    archive = (directory / 'records.tar.gz').read_bytes()
    raw_tar = gzip.decompress(archive)
    inventory = load(directory, 'archive_inventory.json')
    require(sha(archive) == inventory['archive_sha256_bytes'] and
            len(archive) == inventory['archive_bytes'] and
            sha(raw_tar) == inventory['tar_sha256_bytes'] and
            len(raw_tar) == inventory['tar_bytes'], 'Decompressed tar mismatch')
    records = {}
    with tarfile.open(fileobj=io.BytesIO(raw_tar), mode='r:') as stream:
        for member in stream.getmembers():
            safe_name(member.name)
            require(member.isfile() and member.name not in records,
                    'Non-file or duplicate archive member')
            records[member.name] = stream.extractfile(member).read()
    require(set(records) == set(inventory['members']), 'Archive member inventory mismatch')
    for name, data in records.items():
        entry = inventory['members'][name]
        require(len(data) == entry['bytes'] and sha(data) == entry['sha256_bytes'],
                'Archived raw bytes changed: ' + name)
    require(len(records) == inventory['original_records'] and
            sum(map(len, records.values())) == inventory['raw_record_bytes'],
            'Archive raw counts mismatch')
    original = {name: sha(data) for name, data in records.items()}
    original.update({name: sha((directory / name).read_bytes())
                     for name in ('results.json', 'results.csv')})
    seal = load(directory, 'original_seal.json')
    require(seal['result'] == 'PASS' and original == seal['saved_artifact_sha256_bytes'],
            'Original 554-file seal mismatch')

    plan_record = load(directory, 'plan.json')
    plan = plan_record['plan']
    report = load(directory, 'results.json')
    summary = load(directory, 'summary.json')
    build = load(directory, 'build.json')
    execution = load(directory, 'execution.json')
    native = load(directory, 'native_receipt.json')
    predecessor = load(directory, 'maximum_execution.json')
    binding = seal['binding']
    require(report['state'] == 'PASS' and report['passed'] is True and
            report['source_hashes_unchanged'] is True and report['kind'] == 'gemm_release_endurance',
            'Incomplete or unsuccessful endurance record')
    require(binding == report['binding'] and binding['unit'] == 'endurance' and
            canonical(plan) == binding['plan_sha256'], 'Plan/unit binding mismatch')
    for field in ('manifest_sha256_bytes', 'build_id', 'bitstream_sha256',
                  'host_source_sha256_utf8_lf'):
        require(plan_record[field] == binding[field], 'Plan identity mismatch: ' + field)
    require(sha((directory / 'build.json').read_bytes()) == binding['manifest_sha256_bytes'] and
            build == report['manifest'], 'Build manifest mismatch')
    require(build['build_id'] == BUILD_ID == binding['build_id'] == execution['build_id'] and
            build['bitstream_sha256'] == BITSTREAM == binding['bitstream_sha256'] ==
            execution['bitstream_sha256_bytes'], 'Unexpected exact image')
    require((build['p'], build['t'], build['kmax'], build['read_slots'],
             build['core_hz'], build['baud'], build['version'], build['enable_overlap']) ==
            (8, 32, 256, 4, 100000000, 115200, 0x200, True), 'Build geometry mismatch')
    identity = report['identity']
    require(identity == dict(id=0x314D474E, version=0x200, geometry=(256 << 16) | (32 << 8) | 8,
                             p=8, t=32, kmax=256, build_id=BUILD_ID, core_hz=100000000),
            'Read-back identity mismatch')
    require(execution['state'] == 'PASS' and execution['actual_exit_code'] == 0 and
            native['actual_exit_code'] == 0 and native['session_id'] == 76281 and
            native['chunk_id'] == 'f5e084' and native['execution_sha256_bytes'] ==
            sha((directory / 'execution.json').read_bytes()), 'Actual exit receipt mismatch')
    command = execution['command']
    for option, value in (('--phase', 'endurance'), ('--modes', 'both'),
                          ('--oracle', 'numpy'), ('--duration', '1800'), ('--port', 'COM11')):
        require(command.count(option) == 1 and command[command.index(option) + 1] == value,
                'Actual invocation mismatch: ' + option)
    require(sha((directory / 'console.txt').read_bytes()) == execution['console_sha256_bytes'] and
            sha((directory / 'method.py').read_bytes()) == execution['helper_sha256_bytes'] and
            sha((directory / 'build.json').read_bytes()) == execution['manifest_sha256_bytes'] and
            sha((directory / 'original_seal.json').read_bytes()) == execution['seal_sha256_bytes'] and
            sha((directory / 'maximum_execution.json').read_bytes()) ==
            execution['maximum_execution_sha256_bytes'] and
            predecessor['state'] == 'PASS' and predecessor['actual_exit_code'] == 0,
            'Execution dependency/hash mismatch')
    require(predecessor['build_id'] == BUILD_ID and
            predecessor['bitstream_sha256_bytes'] == BITSTREAM, 'Preceding image mismatch')
    require(report['transport'] == dict(retries=0, rejected_frames=0, poisoned=False),
            'Transport retry/rejection/failure observed')
    hashes = binding['host_source_sha256_utf8_lf']
    require(hashes == execution['source_sha256_utf8_lf'] == report['host']['source_sha256_utf8_lf'] ==
            plan_record['host_source_sha256_utf8_lf'], 'Host source binding mismatch')
    checked = load(directory, 'source_verification.json')
    require(checked['result'] == 'PASS' and checked['build_source_sha256_utf8_lf'] ==
            build['source_sha256_utf8_lf'] and checked['host_source_sha256_utf8_lf'] == hashes,
            'Recorded current-source verification mismatch')
    for name, digest in hashes.items():
        data = (directory / 'inputs' / name).read_text(encoding='utf-8').encode('utf-8')
        require(sha(data) == digest, 'Frozen host/runner snapshot mismatch: ' + name)
    if check_current:
        root = next(p for p in directory.parents if (p / 'rtl').is_dir() and (p / 'host').is_dir())
        for name, digest in {**build['source_sha256_utf8_lf'], **hashes}.items():
            require(sha((root / name).read_text(encoding='utf-8').encode('utf-8')) == digest,
                    'Current source changed: ' + name)

    require(plan['modes'] == [0, 1] and tuple(map(tuple, plan['mixed_shapes'])) == MIXED and
            plan['minimum_continuous_seconds'] == 1800.0 and plan['guard_bytes'] == 64,
            'Unexpected endurance plan')
    require(report['completed_mixed_cycles'] == 23 and
            report['continuous_exercise_seconds'] >= plan['minimum_continuous_seconds'] and
            report['continuous_exercise_seconds'] == execution['continuous_exercise_seconds'],
            'Duration or complete-cycle count mismatch')
    runs = report['runs']
    require(len(runs) == 368 and len(records) == 552, 'Missing jobs/cases')
    fields = list(dict.fromkeys(key for run in runs for key in run))
    reader = csv.DictReader(io.StringIO((directory / 'results.csv').read_text()))
    rows = list(reader)
    require(reader.fieldnames == fields and len(rows) == len(runs) and
            all(row == {key: str(run[key]) for key in fields} for row, run in zip(rows, runs)),
            'CSV and aggregate records disagree')
    console = (directory / 'console.txt').read_text().splitlines()
    require(len(console) == len(runs) + 1 and console[-1].startswith('PASS: '),
            'Execution console is incomplete')
    totals = dict(completed_jobs=0, compared_outputs=0, allocation_bytes_checked=0,
                  guard_input_bytes_checked=0)
    previous_finish = report['utc_started']
    for cycle in range(23):
        for index, shape in enumerate(MIXED):
            first = (cycle * len(MIXED) + index) * 2
            seed = plan['seed'] + cycle * 104729 + index * 9973
            case = json.loads(records[f'case_{first:06}.json'])
            modes = (0, 1) if (cycle + index) % 2 == 0 else (1, 0)
            require(case['shape'] == list(shape) and case['seed'] == seed,
                    'Case order/seed mismatch')
            pair = runs[first:first + 2]
            for offset, run in enumerate(pair):
                job = first + offset + 1
                m, n, k = shape
                require(run == json.loads(records[f'job_{job:06}/job.json']) and
                        run['state'] == 'PASS' and run['stage'] == 'PASS' and run['passed'] is True,
                        'Individual job differs or failed')
                require((run['job_id'], run['sample'], run['mode'], run['seed'],
                         run['m'], run['n'], run['k'], run['p'], run['t'], run['build_id'],
                         run['core_hz']) == (job, 0, modes[offset], seed, m, n, k, 8, 32,
                                            BUILD_ID, 100000000), 'Job order/configuration mismatch')
                descriptor = dict(case['descriptor'], mode=run['mode'])
                require(descriptor == run['descriptor'], 'Descriptor snapshot mismatch')
                require(all(descriptor[name] % 64 == 0 for name in
                            ('a_base', 'bt_base', 'c_base', 'a_stride', 'bt_stride', 'c_stride')) and
                        descriptor['a_stride'] >= k and descriptor['bt_stride'] >= k and
                        descriptor['c_stride'] >= 4 * n, 'Invalid recorded alignment/stride')
                lengths = (m * descriptor['a_stride'], n * descriptor['bt_stride'],
                           m * descriptor['c_stride'])
                expected_images = [(name, descriptor[name + '_base'] - plan['guard_bytes'],
                                    length + 2 * plan['guard_bytes'])
                                   for name, length in zip(('a', 'bt', 'c'), lengths)]
                require([(image['name'], image['address'], image['bytes']) for image in case['images']]
                        == expected_images and all(0 <= address < address + length <= 128 * 2**20
                                                   for _, address, length in expected_images),
                        'Recorded guarded allocation layout mismatch')
                regions = [(address, address + length) for _, address, length in expected_images]
                require(all(end <= other or other_end <= start for pos, (start, end) in enumerate(regions)
                            for other, other_end in regions[pos + 1:]), 'Recorded allocation overlap')
                require(previous_finish <= run['utc_started'] <= run['utc_finished'] <=
                        report['utc_finished'], 'Job time order mismatch')
                previous_finish = run['utc_finished']
                for field in COUNTERS:
                    require(type(run[field]) is int and 0 <= run[field] < 2**64,
                            'Invalid frozen counter: ' + field)
                require(run['job_cycles'] >= run['compute_cycles'] > 0,
                        'Invalid completed job interval')
                for field, expected in traffic(m, n, k, 8, 32).items():
                    require(run[field] == expected, 'Tile traffic/schedule mismatch: ' + field)
                allocations = (m * descriptor['a_stride'] + n * descriptor['bt_stride'] +
                               m * descriptor['c_stride'] + 6 * plan['guard_bytes'])
                require(sum(image['bytes'] for image in case['images']) == allocations and
                        run['compared_elements'] == m * n and
                        run['allocation_bytes_checked'] == allocations and
                        run['guard_input_bytes_checked'] == allocations - 4 * m * n,
                        'Output/input/padding/guard count mismatch')
                for field in ('input_a_sha256_bytes', 'raw_b_sha256_bytes', 'oracle_sha256_bytes'):
                    require(run[field] == case[field], 'Case data digest mismatch')
                for image in case['images']:
                    if image['name'] in ('a', 'bt'):
                        require(run['snapshot_sha256_bytes'][image['name']] == image['sha256_bytes'],
                                'Input image digest mismatch')
                c_length = next(image['bytes'] for image in case['images'] if image['name'] == 'c')
                sentinel = bytes(1 + (seed + 53 + 37 * q) % 255 for q in range(c_length))
                require(sha(sentinel) == run['c_before_sha256_bytes'], 'C initialization digest mismatch')
                for field, value in dict(core_seconds=run['job_cycles'] / 100000000,
                        useful_gops=2 * m * n * k * 100000000 / run['job_cycles'] / 1e9,
                        useful_utilization=m * n * k / (64 * run['job_cycles'])).items():
                    require(math.isclose(run[field], value, rel_tol=1e-14, abs_tol=1e-15),
                            'Counter-derived performance mismatch: ' + field)
                require(console[job - 1] == f"PASS endurance job {job} MODE={run['mode']} "
                        f"{m}x{n}x{k}: {run['job_cycles']} cycles, {run['useful_gops']:.6f} useful GOPS",
                        'Console job record mismatch')
                totals['completed_jobs'] += 1
                totals['compared_outputs'] += m * n
                totals['allocation_bytes_checked'] += allocations
                totals['guard_input_bytes_checked'] += allocations - 4 * m * n
            for field in ('input_a_sha256_bytes', 'raw_b_sha256_bytes', 'oracle_sha256_bytes',
                          'c_before_sha256_bytes', 'snapshot_sha256_bytes'):
                require(pair[0][field] == pair[1][field], 'Matched modes used different data: ' + field)
    require(totals == report['checked_counts'] == summary['checked_counts'] ==
            manifest['checked_counts'], 'Aggregate counts mismatch')
    require(totals['completed_jobs'] == execution['completed_jobs'] and
            totals['compared_outputs'] == execution['compared_outputs'], 'Exit receipt counts mismatch')
    require(summary['result'] == 'PASS' and summary['completed_units'] == ['endurance'] and
            summary['source_hashes_unchanged'] is True and
            summary['full_grid_benchmark_complete'] is False and
            summary['all_requested_release_phases_complete'] is False and
            summary['build_id'] == BUILD_ID and summary['bitstream_sha256'] == BITSTREAM and
            summary['plan_sha256'] == binding['plan_sha256'], 'Summary scope mismatch')
    for mode in (0, 1):
        selected = [run for run in runs if run['mode'] == mode]
        distributions = dict(samples=len(selected), metrics={})
        for field in COUNTERS + ('core_seconds', 'useful_gops', 'useful_utilization'):
            values = [run[field] for run in selected]
            distributions['metrics'][field] = dict(minimum=min(values),
                median=statistics.median(values), maximum=max(values))
        require(distributions == report['distributions'][str(mode)], 'Distribution mismatch')
    if extract is not None:
        require(not extract.exists(), 'Extraction destination already exists')
        extract.mkdir(parents=True)
        for name, data in records.items():
            target = extract / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    return dict(result='PASS', scope='Saved metadata, hashes, plan, counters and execution consistency; '
                'no output-byte or UART replay', archived_records=len(records), mixed_cycles=23,
                jobs_per_mode=184, continuous_exercise_seconds=report['continuous_exercise_seconds'],
                checked_counts=totals, current_sources_checked=check_current)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--extract-records', type=Path)
    parser.add_argument('--check-current', action='store_true')
    args = parser.parse_args()
    directory = Path(__file__).resolve().parent
    extract = args.extract_records.resolve() if args.extract_records else None
    if extract is not None:
        root = next(p for p in directory.parents if (p / 'build').is_dir() and (p / 'rtl').is_dir())
        require(extract.is_relative_to(root / 'build'), 'Extract only into a fresh workspace build directory')
    print(json.dumps(verify(directory, extract, args.check_current), indent=2))


if __name__ == '__main__':
    main()
