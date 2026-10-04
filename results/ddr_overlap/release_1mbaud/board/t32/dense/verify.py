"""Check the sealed dense benchmark records without accessing hardware.

The hardware runner compared all outputs and guarded allocations. Raw matrix
snapshots and UART bytes were not retained, so this validator checks evidence
consistency and provenance; it cannot replay those numerical comparisons.
"""

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


BUILD_ID = 0x9D4BEB4D
BITSTREAM = 'd187f51bac37c3682641243f1db112579ad14c8ac45a1b3df52e704bf58c8327'
UNIT = 'benchmark/case_11_256x256x256'
COUNTERS = ('job_cycles', 'compute_cycles', 'read_beats', 'write_beats',
            'write_valid_bytes', 'input_wait_cycles', 'read_stall_cycles',
            'write_stall_cycles')
HOST_TIMES = ('c_initialize_seconds', 'configure_seconds', 'job_wall_seconds',
              'allocation_download_seconds', 'validation_seconds', 'resident_host_seconds')


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
    require(name and not path.is_absolute() and '\\' not in name and
            path.as_posix() == name and ':' not in name and
            all(part not in ('.', '..') for part in path.parts), 'Unsafe member: ' + name)


def normalized(path):
    return sha(path.read_text(encoding='utf-8').encode('utf-8'))


def distribution(values):
    return dict(minimum=min(values), median=statistics.median(values), maximum=max(values))


def traffic():
    """Enumerate the fixed case's macrotiles and nonempty microtiles."""
    reads = writes = valid_bytes = compute = tiles = 0
    for i in range(0, 256, 32):
        for j in range(0, 256, 32):
            rows, cols = min(32, 256 - i), min(32, 256 - j)
            tiles += 1
            for _ in range(rows + cols):
                reads += len(range(0, 256, 8))
            for _ in range(rows):
                writes += len(range(0, cols, 2))
            valid_bytes += rows * cols * 4
            for _ in range(0, rows, 8):
                for _ in range(0, cols, 8):
                    compute += 256 + 3 * 8 - 1
    return dict(read_beats=reads, write_beats=writes,
                write_valid_bytes=valid_bytes, compute_cycles=compute), tiles


def verify(directory, extract=None, check_current=False, repo=None):
    manifest = load(directory, 'manifest.json')
    require(manifest['result'] == 'PASS' and manifest['kind'] == 't32_1mbaud_dense_board',
            'Curation is not the completed dense case')
    actual = {p.relative_to(directory).as_posix(): sha(p.read_bytes())
              for p in directory.rglob('*') if p.is_file() and p != directory / 'manifest.json'}
    require(actual == manifest['saved_artifact_sha256_bytes'], 'Public inventory/hash mismatch')
    archive = (directory / 'records.tar.gz').read_bytes()
    raw_tar = gzip.decompress(archive)
    inventory = load(directory, 'archive_inventory.json')
    require((sha(archive), len(archive), sha(raw_tar), len(raw_tar)) ==
            (inventory['archive_sha256_bytes'], inventory['archive_bytes'],
             inventory['tar_sha256_bytes'], inventory['tar_bytes']), 'Tar/gzip binding mismatch')
    records = {}
    with tarfile.open(fileobj=io.BytesIO(raw_tar), mode='r:') as stream:
        for member in stream.getmembers():
            safe_name(member.name)
            require(member.isfile() and member.name not in records,
                    'Non-file or duplicate archive member')
            records[member.name] = stream.extractfile(member).read()
    require(set(records) == set(inventory['members']) and len(records) == 61,
            'Missing or unexpected original records')
    for name, data in records.items():
        entry = inventory['members'][name]
        require((len(data), sha(data)) == (entry['bytes'], entry['sha256_bytes']),
                'Original record changed: ' + name)
    require(inventory['original_records'] == 61 and inventory['raw_record_bytes'] ==
            sum(map(len, records.values())), 'Archive totals mismatch')
    seal = load(directory, 'original_seal.json')
    original = {name: sha(data) for name, data in records.items()}
    original.update({name: sha((directory / name).read_bytes())
                     for name in ('results.json', 'results.csv')})
    require(seal['result'] == 'PASS' and original == seal['saved_artifact_sha256_bytes'] and
            len(original) == 63, 'Original case seal mismatch')

    report = load(directory, 'results.json')
    build = load(directory, 'build.json')
    plan_record = load(directory, 'plan.json')
    plan = plan_record['plan']
    binding = seal['binding']
    require(report['state'] == 'PASS' and report['passed'] is True and
            report['kind'] == 'gemm_release_benchmark' and report['source_hashes_unchanged'] is True,
            'Incomplete or unsuccessful benchmark')
    require(report['binding'] == binding and binding['unit'] == UNIT and
            canonical(plan) == binding['plan_sha256'], 'Plan/unit binding mismatch')
    for field in ('manifest_sha256_bytes', 'build_id', 'bitstream_sha256',
                  'host_source_sha256_utf8_lf'):
        require(plan_record[field] == binding[field], 'Plan identity mismatch: ' + field)
    require(sha((directory / 'build.json').read_bytes()) == binding['manifest_sha256_bytes'] and
            build == report['manifest'] and build['result'] == 'PASS', 'Build receipt mismatch')
    require((build['build_id'], build['bitstream_sha256'], build['p'], build['t'],
             build['kmax'], build['read_slots'], build['core_hz'], build['baud'],
             build['version'], build['enable_overlap']) ==
            (BUILD_ID, BITSTREAM, 8, 32, 256, 4, 100000000, 1000000, 0x200, True),
            'Wrong exact image or geometry')
    require(binding['build_id'] == BUILD_ID and binding['bitstream_sha256'] == BITSTREAM and
            manifest['build_id'] == BUILD_ID and manifest['bitstream_sha256_bytes'] == BITSTREAM,
            'Image identity mismatch')
    require(report['identity'] == dict(id=0x314D474E, version=0x200,
            geometry=(256 << 16) | (32 << 8) | 8, p=8, t=32, kmax=256,
            build_id=BUILD_ID, core_hz=100000000), 'Read-back identity mismatch')
    require(report['transport'] == dict(retries=0, rejected_frames=0, poisoned=False),
            'Transport failure/retry/rejected frame observed')

    execution = load(directory, 'completed_execution.json')
    snapshot = load(directory, 'execution_snapshot.json')
    names = ('program', 'smoke', 'dense_benchmark')
    selected = [stage for stage in snapshot['stages'] if stage['name'] in names]
    require(execution['result'] == 'PASS_COMPLETED_STAGES' and execution['stages'] == selected and
            [stage['name'] for stage in selected] == list(names) and
            execution['original_execution_sha256_bytes'] ==
            sha((directory / 'execution_snapshot.json').read_bytes()), 'Stage snapshot mismatch')
    require(execution['claimed_completed_stages'] == list(names) and
            execution['full_qualification_claimed_complete'] is False,
            'Unfinished qualification was promoted to a completed claim')
    for field in ('build_id', 'bitstream_sha256_bytes', 'manifest_sha256_bytes',
                  'review_record_sha256_bytes', 'helper_sha256_bytes', 'source_sha256_utf8_lf',
                  'operator_confirmation', 'operator_confirmation_source', 'startup_scope',
                  'port', 'baud', 'python', 'packages'):
        require(execution[field] == snapshot[field], 'Altered execution metadata: ' + field)
    previous_finish = snapshot['utc_started']
    for stage in selected:
        require(stage['actual_exit_code'] == 0 and stage['utc_finished'] is not None and
                previous_finish <= stage['utc_started'] <= stage['utc_finished'],
                'Selected stage lacks an actual successful completion')
        require(sha((directory / (stage['name'] + '_console.txt')).read_bytes()) ==
                stage['console_sha256_bytes'], 'Stage console hash mismatch')
        previous_finish = stage['utc_finished']
    require(selected[2]['utc_started'] <= report['utc_started'] <= report['utc_finished'] <=
            selected[2]['utc_finished'], 'Benchmark timestamps outside execution stage')
    command = selected[2]['command']
    for option, value in (('--phase', 'benchmark'), ('--cases', '11'), ('--modes', 'both'),
                          ('--samples', '30'), ('--oracle', 'numpy'), ('--port', 'COM11')):
        require(command.count(option) == 1 and command[command.index(option) + 1] == value,
                'Actual invocation mismatch: ' + option)
    require(snapshot['dense_benchmark_result_sha256_bytes'] == sha((directory / 'results.json').read_bytes()) and
            sha((directory / 'method.py').read_bytes()) == execution['helper_sha256_bytes'] and
            execution['build_id'] == BUILD_ID and execution['bitstream_sha256_bytes'] == BITSTREAM and
            execution['manifest_sha256_bytes'] == binding['manifest_sha256_bytes'],
            'Execution image/result/helper binding mismatch')
    operator = load(directory, 'operator_confirmation.json')
    require(operator == {field: execution[field] for field in
            ('operator_confirmation', 'operator_confirmation_source', 'startup_scope')} and
            operator['operator_confirmation'] == 'Fresh OFF/ON complete; powered on and connected',
            'Operator cold-start receipt mismatch')
    program = (directory / 'program_log.txt').read_text()
    require('DDR_GEMM_PROGRAMMED xc7a50t ' in program and
            '/build/gemm_release_p8_t32_1mbaud/gemm_ddr.bit' in program and
            'End of startup status: HIGH' in program and
            '0x9d4beb4d' in (directory / 'program_console.txt').read_text() and
            BITSTREAM in (directory / 'program_console.txt').read_text(), 'Programming marker mismatch')
    static = load(directory, 'static_archive_manifest.json')
    review = load(directory, 'static_review_record.json')
    require(static['result'] == 'PASS' and static['build_id'] == BUILD_ID and
            static['bitstream_sha256_bytes'] == BITSTREAM and
            static['source_sha256_utf8_lf'] == build['source_sha256_utf8_lf'] and
            review['actual_exit_code'] == 0 and
            static['checkpoint_sha256_bytes'] == review['checkpoint_sha256_bytes_before'] ==
            review['checkpoint_sha256_bytes_after'] and
            sha((directory / 'static_review_record.json').read_bytes()) ==
            execution['review_record_sha256_bytes'], 'Own static-review identity mismatch')
    require(manifest['static_archive_manifest_sha256_bytes'] ==
            sha((directory / 'static_archive_manifest.json').read_bytes()), 'Static archive receipt mismatch')
    sources = binding['host_source_sha256_utf8_lf']
    require(sources == execution['source_sha256_utf8_lf'] == report['host']['source_sha256_utf8_lf'],
            'Host source binding mismatch')
    checked = load(directory, 'source_verification.json')
    require(checked['result'] == 'PASS' and checked['build_source_sha256_utf8_lf'] ==
            build['source_sha256_utf8_lf'] and checked['host_source_sha256_utf8_lf'] == sources,
            'Curation source verification mismatch')
    for name, digest in sources.items():
        require(normalized(directory / 'inputs' / name) == digest, 'Host snapshot mismatch: ' + name)
    if check_current:
        if repo is None:
            repo = next((p for p in directory.parents if (p / 'rtl').is_dir() and
                         (p / 'host').is_dir()), None)
        require(repo is not None, 'Use --repo when checking current sources outside the checkout')
        for name, digest in {**build['source_sha256_utf8_lf'], **sources}.items():
            require(normalized(repo / name) == digest, 'Current source changed: ' + name)

    require(plan['modes'] == [0, 1] and plan['samples'] == 30 and plan['guard_bytes'] == 64 and
            plan['oracle'] == 'numpy' and plan['benchmark_cases'][11] ==
            dict(index=11, m=256, n=256, k=256, seed=20370707), 'Unexpected dense plan')
    case = json.loads(records['case_000000.json'])
    require(case['shape'] == [256, 256, 256] and case['seed'] == 20370707,
            'Case shape/seed mismatch')
    require(case['descriptor'] == dict(m=256, n=256, k=256, a_base=64, bt_base=65728,
            c_base=131392, a_stride=256, bt_stride=256, c_stride=1024, mode=0, watchdog=10000000),
            'Recorded descriptor layout mismatch')
    require([(x['name'], x['address'], x['bytes']) for x in case['images']] ==
            [('a', 0, 65664), ('bt', 65664, 65664), ('c', 131328, 262272)],
            'Recorded guarded allocation mismatch')
    expected_traffic, tiles = traffic()
    runs = report['runs']
    require(len(runs) == 60, 'Missing completed dense jobs')
    fields = list(dict.fromkeys(key for run in runs for key in run))
    reader = csv.DictReader(io.StringIO((directory / 'results.csv').read_text()))
    rows = list(reader)
    require(reader.fieldnames == fields and len(rows) == 60 and
            all(row == {key: str(run[key]) for key in fields} for row, run in zip(rows, runs)),
            'CSV disagrees with the original job records')
    console = (directory / 'dense_benchmark_console.txt').read_text().splitlines()
    require(len(console) == 61 and console[-1].startswith('PASS: '), 'Dense console incomplete')
    previous_finish = report['utc_started']
    for index, run in enumerate(runs):
        job = index + 1
        sample, offset = divmod(index, 2)
        mode = ((0, 1) if sample % 2 == 0 else (1, 0))[offset]
        c_before = sha(bytes(1 + (case['seed'] + (sample + 1) * 53 + 37 * q) % 255
                            for q in range(262272)))
        require(run == json.loads(records[f'job_{job:06}/job.json']) and
                run['state'] == run['stage'] == 'PASS' and run['passed'] is True,
                'Original job is failed or differs from aggregate')
        require((run['job_id'], run['sample'], run['mode'], run['seed'], run['m'], run['n'],
                 run['k'], run['p'], run['t'], run['build_id'], run['core_hz']) ==
                (job, sample, mode, 20370707, 256, 256, 256, 8, 32, BUILD_ID, 100000000),
                'Matched job order/configuration mismatch')
        require(run['descriptor'] == dict(case['descriptor'], mode=mode), 'Job descriptor mismatch')
        require(previous_finish <= run['utc_started'] <= run['utc_finished'] <= report['utc_finished'],
                'Job time order mismatch')
        previous_finish = run['utc_finished']
        for field in COUNTERS:
            require(type(run[field]) is int and 0 <= run[field] < 2**64, 'Invalid counter: ' + field)
        require(run['job_cycles'] >= run['compute_cycles'] > 0, 'Invalid completed job interval')
        for field, expected in expected_traffic.items():
            require(run[field] == expected, 'Tile traffic/schedule mismatch: ' + field)
        require((run['compared_elements'], run['allocation_bytes_checked'],
                 run['guard_input_bytes_checked']) == (65536, 393600, 131456),
                'Full output/input/padding/guard check count mismatch')
        for field in ('input_a_sha256_bytes', 'raw_b_sha256_bytes', 'oracle_sha256_bytes'):
            require(run[field] == case[field], 'Resident case digest mismatch')
        for image in case['images'][:2]:
            require(run['snapshot_sha256_bytes'][image['name']] == image['sha256_bytes'],
                    'Input changed during a job')
        require(run['c_before_sha256_bytes'] == c_before, 'Fresh C initialization digest mismatch')
        for field in HOST_TIMES:
            require(type(run[field]) in (int, float) and math.isfinite(run[field]) and run[field] >= 0,
                    'Invalid host timing: ' + field)
        derived = dict(core_seconds=run['job_cycles'] / 1e8,
                       useful_gops=2 * 256**3 * 1e8 / run['job_cycles'] / 1e9,
                       useful_utilization=256**3 / (64 * run['job_cycles']),
                       resident_host_seconds=sum(run[x] for x in
                        ('c_initialize_seconds', 'configure_seconds', 'job_wall_seconds',
                         'allocation_download_seconds')))
        for field, expected in derived.items():
            require(math.isclose(run[field], expected, rel_tol=1e-14, abs_tol=1e-15),
                    'Counter/host-derived timing mismatch: ' + field)
        require(console[index] == f"PASS case_11_256x256x256 job {job} MODE={mode} "
                f"256x256x256: {run['job_cycles']} cycles, {run['useful_gops']:.6f} useful GOPS",
                'Dense console job mismatch')
    ratios = []
    for sample in range(30):
        pair = runs[2 * sample:2 * sample + 2]
        for field in ('input_a_sha256_bytes', 'raw_b_sha256_bytes', 'oracle_sha256_bytes',
                      'c_before_sha256_bytes', 'snapshot_sha256_bytes'):
            require(pair[0][field] == pair[1][field], 'Matched modes used different data: ' + field)
        modes = {run['mode']: run for run in pair}
        ratios.append(modes[0]['job_cycles'] / modes[1]['job_cycles'])
    totals = dict(completed_jobs=60, compared_outputs=3932160,
                  allocation_bytes_checked=23616000, guard_input_bytes_checked=7887360)
    require(totals == report['checked_counts'] == manifest['checked_counts'], 'Aggregate count mismatch')
    summary = load(directory, 'summary.json')
    require(summary['result'] == 'PASS' and summary['completed_units'] == [UNIT] and
            summary['full_grid_benchmark_complete'] is False and
            summary['all_requested_release_phases_complete'] is False and
            summary['checked_counts'] == totals and summary['build_id'] == BUILD_ID and
            summary['bitstream_sha256'] == BITSTREAM and
            summary['plan_sha256'] == binding['plan_sha256'], 'Summary overclaims completed scope')
    for mode in (0, 1):
        selected_runs = [run for run in runs if run['mode'] == mode]
        distributions = dict(samples=30, metrics={field: distribution([r[field] for r in selected_runs])
            for field in COUNTERS + ('core_seconds', 'useful_gops', 'useful_utilization')})
        require(distributions == report['distributions'][str(mode)] ==
                summary['distributions'][str(mode)], 'Counter distribution mismatch')
        require(summary['host_time_distributions'][str(mode)] ==
                {field: distribution([r[field] for r in selected_runs]) for field in HOST_TIMES},
                'Host timing distribution mismatch')
    require(summary['paired_cycle_speedup'] == distribution(ratios) and
            summary['case_preparation'] == {field: case[field] for field in
             ('preparation_seconds', 'oracle_seconds', 'input_upload_seconds')},
            'Paired speedup/preparation mismatch')
    plotting = load(directory, 'plot_receipt.json')
    require(plotting['result'] == 'PASS' and plotting['backend'].lower() == 'agg' and
            plotting['build_id'] == BUILD_ID and plotting['input_summary_sha256_bytes'] ==
            sha((directory / 'summary.json').read_bytes()) and
            plotting['plotting_source_sha256_bytes'] == sha((directory / 'plot.py').read_bytes()) and
            plotting['outputs_sha256_bytes'] == {name: sha((directory / name).read_bytes())
             for name in ('performance.png', 'performance.pdf')}, 'Plot provenance mismatch')
    if extract is not None:
        require(not extract.exists(), 'Extraction destination already exists')
        extract.mkdir(parents=True)
        for name, data in records.items():
            target = extract / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    return dict(result='PASS', scope='Saved records, hashes, counters and completed-stage provenance; '
                'no numerical output-byte or UART replay', archived_records=61, paired_samples=30,
                jobs_per_mode=30, macrotiles_per_job=tiles, checked_counts=totals,
                paired_cycle_speedup=distribution(ratios), current_sources_checked=check_current)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--extract-records', type=Path)
    parser.add_argument('--check-current', action='store_true')
    parser.add_argument('--repo', type=Path)
    args = parser.parse_args()
    require(args.repo is None or args.check_current, '--repo requires --check-current')
    print(json.dumps(verify(Path(__file__).resolve().parent,
                           args.extract_records.resolve() if args.extract_records else None,
                           args.check_current, args.repo.resolve() if args.repo else None), indent=2))


if __name__ == '__main__':
    main()
