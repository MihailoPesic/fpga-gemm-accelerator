"""Verify a completed T8 MODE0 grid without serial or hardware access.

The original comparisons checked outputs, inputs, padding and guards. Only
metadata/digests/counters are retained here; no per-job C/UART byte replay.
All validation uses explicit checks and remains active with Python -O.
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

BUILD_ID = 0xEED7B111
BITSTREAM = '21845beeb677bc8c0445231646cefb6e3633bdcf58a2d8c0566bcceaf44d6f37'
COUNTERS = ('job_cycles', 'compute_cycles', 'read_beats', 'write_beats',
            'write_valid_bytes', 'input_wait_cycles', 'read_stall_cycles', 'write_stall_cycles')
METRICS = COUNTERS + ('core_seconds', 'useful_gops', 'useful_utilization')
HOST_TIMES = ('c_initialize_seconds', 'configure_seconds', 'job_wall_seconds',
              'allocation_download_seconds', 'validation_seconds', 'resident_host_seconds')
GRID = tuple((m, m, k) for m in (32, 64, 128, 256) for k in (16, 64, 256)) + (
    (31, 33, 17), (65, 63, 255), (1, 64, 256), (64, 1, 256))
MIXED = ((1, 1, 1), (5, 3, 9), (31, 33, 17), (33, 35, 256),
         (65, 63, 255), (1, 64, 256), (64, 1, 256), (32, 32, 256))
IDENTITY = dict(id=0x314D474E, version=0x200, geometry=(256 << 16) | (8 << 8) | 8,
                p=8, t=8, kmax=256, build_id=BUILD_ID, core_hz=100000000)


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


def unpack(raw, inventory):
    plain = gzip.decompress(raw)
    require(sha(raw) == inventory['archive_sha256_bytes'] and len(raw) == inventory['archive_bytes'] and
            sha(plain) == inventory['tar_sha256_bytes'] and len(plain) == inventory['tar_bytes'], 'Archive bytes differ')
    files = {}
    with tarfile.open(fileobj=io.BytesIO(plain), mode='r:') as stream:
        for member in stream.getmembers():
            path = PurePosixPath(member.name)
            require(member.name and not path.is_absolute() and path.as_posix() == member.name and
                    ':' not in member.name and '\\' not in member.name and
                    all(part not in ('.', '..') for part in path.parts) and member.isfile() and member.name not in files,
                    'Unsafe/non-file/duplicate archive member')
            files[member.name] = stream.extractfile(member).read()
    require(set(files) == set(inventory['members']) and len(files) == inventory['original_files'] and
            sum(map(len, files.values())) == inventory['raw_bytes'], 'Archive inventory differs')
    for name, data in files.items():
        require(inventory['members'][name] == dict(bytes=len(data), sha256_bytes=sha(data)), 'Archived original differs: ' + name)
    return files


def distribution(values):
    return dict(minimum=min(values), median=statistics.median(values), maximum=max(values))


def distributions(runs, fields=METRICS):
    return {'0': dict(samples=len(runs), metrics={name: distribution([run[name] for run in runs]) for name in fields})}


def counts(runs):
    return dict(completed_jobs=len(runs), compared_outputs=sum(run['m'] * run['n'] for run in runs),
                allocation_bytes_checked=sum(run['allocation_bytes_checked'] for run in runs),
                guard_input_bytes_checked=sum(run['guard_input_bytes_checked'] for run in runs))


def csv_matches(raw, runs):
    fields = list(dict.fromkeys(key for run in runs for key in run))
    reader = csv.DictReader(io.StringIO(raw.decode()))
    rows = list(reader)
    require(reader.fieldnames == fields and len(rows) == len(runs) and
            all(row == {key: str(run[key]) for key in fields} for row, run in zip(rows, runs)), 'CSV/job records differ')


def traffic(m, n, k):
    reads = writes = valid = compute = tiles = 0
    for i in range(0, m, 8):
        for j in range(0, n, 8):
            r, c = min(8, m - i), min(8, n - j)
            tiles += 1
            reads += (r + c) * len(range(0, k, 8))
            writes += r * len(range(0, c, 2))
            valid += 4 * r * c
            compute += k + 23
    return dict(read_beats=reads, write_beats=writes, write_valid_bytes=valid, compute_cycles=compute), tiles


def unit_names():
    return [f'benchmark/case_{index:02d}_{m}x{n}x{k}' for index, (m, n, k) in enumerate(GRID)]


def check_plan(plan):
    require(plan['schema_version'] == 1 and plan['modes'] == [0] and plan['samples'] == 30 and
            plan['seed'] == 20261004 and plan['oracle'] == 'numpy' and plan['guard_bytes'] == 64 and
            plan['minimum_continuous_seconds'] == 1800.0 and plan['maximum_shape'] == [1024, 1024, 256] and
            tuple(map(tuple, plan['mixed_shapes'])) == MIXED and plan['benchmark_cases'] ==
            [dict(index=index, m=m, n=n, k=k, seed=20261004 + index * 9973)
             for index, (m, n, k) in enumerate(GRID)], 'Exact T8 MODE0 plan differs')


def verify_unit(files, unit, plan_record, build):
    report, seal = json.loads(files['results.json']), json.loads(files['seal.json'])
    index = unit_names().index(unit)
    m, n, k = GRID[index]
    seed = 20261004 + index * 9973
    check_plan(plan_record['plan'])
    require(seal['result'] == 'PASS' and seal['saved_artifact_sha256_bytes'] ==
            {name: sha(raw) for name, raw in files.items() if name != 'seal.json'}, 'Original T8 unit seal differs')
    binding = dict(plan_sha256=canonical(plan_record['plan']), unit=unit,
                   **{key: plan_record[key] for key in ('manifest_sha256_bytes', 'build_id',
                       'bitstream_sha256', 'host_source_sha256_utf8_lf')})
    require(report['state'] == 'PASS' and report['passed'] is True and report['source_hashes_unchanged'] is True and
            report['kind'] == 'gemm_release_benchmark' and report['binding'] == seal['binding'] == binding and
            report['manifest'] == build and report['identity'] == IDENTITY and report['transport'] ==
            dict(retries=0, rejected_frames=0, poisoned=False) and report['continuous_exercise_seconds'] == 0 and
            report['host']['source_sha256_utf8_lf'] == binding['host_source_sha256_utf8_lf'], 'T8 unit identity/outcome differs')
    runs = report['runs']
    require(len(runs) == 30 and type(runs[0]['job_id']) is int and 1 <= runs[0]['job_id'] < 2**32,
            'T8 unit does not contain 30 samples')
    case = json.loads(files['case_000000.json'])
    require(case['shape'] == [m,n,k] and case['seed'] == seed, 'T8 case shape/seed differs')
    descriptor = case['descriptor']
    require((descriptor['m'], descriptor['n'], descriptor['k'], descriptor['mode'], descriptor['watchdog']) ==
            (m, n, k, 0, 10000000) and all(type(descriptor[key]) is int and descriptor[key] % 64 == 0 for key in
            ('a_base','bt_base','c_base','a_stride','bt_stride','c_stride')) and descriptor['a_stride'] >= k and
            descriptor['bt_stride'] >= k and descriptor['c_stride'] >= 4*n, 'T8 descriptor differs')
    lengths = (m*descriptor['a_stride'], n*descriptor['bt_stride'], m*descriptor['c_stride'])
    images = [(name,descriptor[name+'_base']-64,length+128) for name,length in zip(('a','bt','c'),lengths)]
    require([(image['name'],image['address'],image['bytes']) for image in case['images']] == images and
            all(0 <= start < start+length <= 128*2**20 for _,start,length in images), 'T8 guarded image extent differs')
    regions = [(start,start+length) for _,start,length in images]
    require(all(end <= other or other_end <= start for number,(start,end) in enumerate(regions)
                for other,other_end in regions[number+1:]), 'T8 guarded images overlap')
    expected_traffic, tiles = traffic(m,n,k)
    expected_names = {'seal.json','results.json','results.csv','case_000000.json'}
    previous = report['utc_started']
    first_id = runs[0]['job_id']
    resident = None
    for sample, run in enumerate(runs):
        name = f'job_{first_id+sample:06d}/job.json'
        expected_names.add(name)
        require(name in files, 'T8 job sequence/configuration differs: missing original job file')
        require(run == json.loads(files[name]) and run['state'] == run['stage'] == 'PASS' and run['passed'] is True and
                (run['job_id'],run['sample'],run['mode'],run['seed'],run['m'],run['n'],run['k'],run['p'],run['t'],
                 run['build_id'],run['core_hz']) ==
                (first_id+sample,sample,0,seed,m,n,k,8,8,BUILD_ID,100000000) and run['descriptor'] == descriptor,
                'T8 job sequence/configuration differs')
        require(previous <= run['utc_started'] <= run['utc_finished'] <= report['utc_finished'], 'T8 job timestamps differ')
        previous = run['utc_finished']
        require(all(type(run[name]) is int and 0 <= run[name] < 2**64 for name in COUNTERS) and
                run['job_cycles'] >= run['compute_cycles'] > 0 and
                all(run[name] == value for name,value in expected_traffic.items()), 'Independent T8 traffic/schedule differs')
        allocation = sum(lengths)+384
        require((run['compared_elements'],run['allocation_bytes_checked'],run['guard_input_bytes_checked']) ==
                (m*n,allocation,allocation-4*m*n), 'T8 complete comparison counts differ')
        identity = tuple(run[name] for name in ('input_a_sha256_bytes','raw_b_sha256_bytes','oracle_sha256_bytes'))
        require(identity == tuple(case[name] for name in ('input_a_sha256_bytes','raw_b_sha256_bytes','oracle_sha256_bytes')) and
                all(re.fullmatch('[0-9a-f]{64}',value) for value in identity), 'T8 input/oracle digest differs')
        resident = identity if resident is None else resident
        require(identity == resident and set(run['snapshot_sha256_bytes']) == {'a','bt','c'} and
                all(re.fullmatch('[0-9a-f]{64}',value) for value in run['snapshot_sha256_bytes'].values()) and
                all(run['snapshot_sha256_bytes'][image['name']] == image['sha256_bytes'] for image in case['images'][:2]),
                'T8 resident input/snapshot digests differ')
        require(run['c_before_sha256_bytes'] == sha(bytes(1+(seed+(sample+1)*53+37*q)%255
                for q in range(lengths[2]+128))), 'T8 fresh C sentinel differs')
        require(all(type(run[name]) in (float,int) and math.isfinite(run[name]) and run[name] >= 0 for name in HOST_TIMES),
                'T8 host time invalid')
        for name,value in dict(core_seconds=run['job_cycles']/1e8,
                useful_gops=2*m*n*k*1e8/run['job_cycles']/1e9,
                useful_utilization=m*n*k/(64*run['job_cycles']),
                resident_host_seconds=sum(run[name] for name in
                 ('c_initialize_seconds','configure_seconds','job_wall_seconds','allocation_download_seconds'))).items():
            require(math.isclose(run[name],value,rel_tol=1e-14,abs_tol=1e-15), 'T8 derived metric differs')
    require(set(files) == expected_names and len(files) == 34, 'Missing/unexpected T8 original unit file')
    csv_matches(files['results.csv'],runs)
    require(report['checked_counts'] == counts(runs) and report['distributions'] == distributions(runs),
            'T8 original counts/distributions differ')
    return dict(unit=unit,checked_counts=counts(runs),distributions=distributions(runs),
        host_distributions=distributions(runs,HOST_TIMES),macrotiles=tiles*30,microtiles=tiles*30,
        case_preparation={name:case[name] for name in ('preparation_seconds','oracle_seconds','input_upload_seconds')},
        first_job_id=first_id,last_job_id=first_id+29,original_files=34,original_sealed_files=33),runs,report


def verify_provenance(directory, build, plan_record, manifest):
    execution, native = load(directory/'execution.json'), load(directory/'native_receipt.json')
    prior, prior_native = load(directory/'t32_execution.json'), load(directory/'t32_native_receipt.json')
    require(execution['state'] == 'PASS' and native['actual_exit_code'] == 0 and
            type(native['session_id']) is int and native['session_id'] > 0 and native['tool_chunk_id'] and
            native['execution_sha256_bytes'] == sha((directory/'execution.json').read_bytes()) and
            execution['utc_finished'] <= native['observed_utc'], 'T8 actual native completion missing')
    require(prior['state'] == 'PASS' and prior_native['actual_exit_code'] == 0 and prior_native['session_id'] == 78344 and
            prior_native['tool_chunk_id'] and prior_native['execution_sha256_bytes'] ==
            execution['t32_execution_sha256_bytes'] == sha((directory/'t32_execution.json').read_bytes()) and
            len(prior['stages']) == 1 and prior['stages'][0]['actual_exit_code'] == 0 and
            prior['build_id'] == 0x9D4BEB4D and prior['utc_finished'] <= prior_native['observed_utc'] and
            datetime.fromisoformat(prior_native['observed_utc']) <= datetime.fromisoformat(execution['utc_started']),
            'T32 actual completion/released-port precondition differs')
    require(execution['operator_confirmation'] == 'Fresh OFF/ON complete; powered on and connected' and
            execution['operator_confirmation_source'] == 'Separate actual user reply after T32 qualification completed' and
            (execution['build_id'],execution['bitstream_sha256_bytes'],execution['baud'],execution['port']) ==
            (BUILD_ID,BITSTREAM,1000000,'COM11') and execution['manifest_sha256_bytes'] ==
            plan_record['manifest_sha256_bytes'] == sha((directory/'build.json').read_bytes()) and
            execution['source_sha256_utf8_lf'] == plan_record['host_source_sha256_utf8_lf'] and
            execution['helper_sha256_bytes'] == sha((directory/'board_method.py').read_bytes()) and
            execution['keep_awake_source_sha256_bytes'] == sha((directory/'keep_awake.py').read_bytes()),
            'T8 cold/image/source/wrapper binding differs')
    stages = execution['stages']
    require([stage['name'] for stage in stages] == ['program','smoke','dense_benchmark','full_benchmark'] and
            [stage['actual_exit_code'] for stage in stages] == [0,0,0,0], 'T8 actual stage sequence differs')
    logs = {}
    previous = execution['utc_started']
    for stage in stages:
        raw = gzip.decompress((directory/'execution_logs'/(stage['name']+'_console.txt.gz')).read_bytes())
        require(sha(raw) == stage['console_sha256_bytes'] and previous <= stage['utc_started'] <=
                stage['utc_finished'] <= execution['utc_finished'], 'T8 actual stage console/time differs')
        previous = stage['utc_finished']
        logs[stage['name']] = raw.decode().splitlines()
        command = stage['command']
        require(command.count('--manifest') == 1 and command[command.index('--manifest')+1].replace('\\','/').endswith(
                '/build/gemm_release_p8_t8_1mbaud/build.json'), 'T8 stage selected manifest differs')
    for stage in stages[2:]:
        command = stage['command']
        require(command[1].replace('\\','/').endswith('/scripts/keep_awake.py') and command.count('--') == 1,
                'T8 temporary sleep inhibitor invocation differs')
        for option,value in (('--port','COM11'),('--modes','0'),('--oracle','numpy'),('--samples','30'),
                             ('--duration','1800'),('--seed','20261004'),('--phase','benchmark')):
            require(command.count(option) == 1 and command[command.index(option)+1] == value,
                    'T8 invocation option differs: '+option)
    require(stages[2]['command'].count('--cases') == 1 and stages[2]['command'][stages[2]['command'].index('--cases')+1] == '11' and
            '--resume' not in stages[2]['command'] and stages[3]['command'].count('--resume') == 1 and
            '--cases' not in stages[3]['command'], 'T8 dense/full child epochs differ')
    program = (directory/'program_log.txt').read_text()
    require('DDR_GEMM_PROGRAMMED xc7a50t ' in program and '/build/gemm_release_p8_t8_1mbaud/gemm_ddr.bit' in program and
            'End of startup status: HIGH' in program and '0xeed7b111' in '\n'.join(logs['program']) and
            BITSTREAM in '\n'.join(logs['program']), 'T8 programming marker differs')
    static, review = load(directory/'static_archive_manifest.json'), load(directory/'static_review_record.json')
    require(static['result'] == 'PASS' and static['build_id'] == BUILD_ID and static['bitstream_sha256_bytes'] == BITSTREAM and
            static['source_sha256_utf8_lf'] == build['source_sha256_utf8_lf'] and review['actual_exit_code'] == 0 and
            review['result'] == 'PASS_PROVISIONAL_REVIEW' and static['checkpoint_sha256_bytes'] ==
            review['checkpoint_sha256_bytes_before'] == review['checkpoint_sha256_bytes_after'] and
            execution['review_record_sha256_bytes'] == sha((directory/'static_review_record.json').read_bytes()) and
            manifest['static_archive_manifest_sha256_bytes'] == sha((directory/'static_archive_manifest.json').read_bytes()),
            'T8 own static review binding differs')
    parent = load(directory/'parent_summary.json')
    require(sha((directory/'parent_summary.json').read_bytes()) == execution['summary_sha256_bytes'] and
            parent['result'] == 'PASS' and parent['source_hashes_unchanged'] is True and parent['requested_phase'] == 'benchmark' and
            parent['full_grid_benchmark_complete'] is True and parent['all_requested_release_phases_complete'] is False and
            parent['available_completed_units'] == execution['available_completed_units'] == unit_names() and
            parent['build_id'] == BUILD_ID and parent['bitstream_sha256'] == BITSTREAM and
            parent['plan_sha256'] == canonical(plan_record['plan']), 'T8 full-grid parent summary differs')
    return execution, logs, stages


def verify(directory, check_current=False, repo=None, extract=None):
    directory = Path(directory)
    manifest = load(directory/'manifest.json')
    require(manifest['result'] == 'PASS' and manifest['kind'] == 't8_1mbaud_mode0_benchmark_board' and
            manifest['saved_artifact_sha256_bytes'] == {path.relative_to(directory).as_posix():sha(path.read_bytes())
             for path in directory.rglob('*') if path.is_file() and path != directory/'manifest.json'}, 'T8 package seal differs')
    build, plan_record = load(directory/'build.json'), load(directory/'plan.json')
    check_plan(plan_record['plan'])
    require(build['result'] == 'PASS' and (build['build_id'],build['bitstream_sha256'],build['p'],build['t'],build['kmax'],
            build['read_slots'],build['core_hz'],build['baud'],build['version'],build['enable_overlap']) ==
            (BUILD_ID,BITSTREAM,8,8,256,4,100000000,1000000,0x200,True) and
            plan_record['build_id'] == manifest['build_id'] == BUILD_ID and
            plan_record['bitstream_sha256'] == manifest['bitstream_sha256_bytes'] == BITSTREAM, 'T8 exact build differs')
    execution, logs, stages = verify_provenance(directory,build,plan_record,manifest)
    sources = unpack((directory/'sources.tar.gz').read_bytes(),load(directory/'source_inventory.json'))
    expected_sources = dict(build['source_sha256_utf8_lf'],**plan_record['host_source_sha256_utf8_lf'])
    require(set(sources) == set(expected_sources) and len(sources) == 38, 'T8 frozen source set differs')
    if check_current and repo is None:
        repo = next((p for p in directory.parents if (p/'rtl').is_dir() and (p/'host').is_dir()),None)
        require(repo is not None, 'Use --repo for current checks outside checkout')
    for name,digest in expected_sources.items():
        require(text_hash(sources[name]) == digest, 'T8 frozen source differs: '+name)
        if check_current:
            require(text_hash((repo/name).read_bytes()) == digest, 'T8 current source differs: '+name)
    if check_current:
        require((repo/'scripts/keep_awake.py').read_bytes() == (directory/'keep_awake.py').read_bytes(), 'Current sleep wrapper differs')
    records = unpack((directory/'records.tar.gz').read_bytes(),load(directory/'archive_inventory.json'))
    units = unit_names()
    grouped = {unit:{name[len(unit)+1:]:raw for name,raw in records.items() if name.startswith(unit+'/')} for unit in units}
    require({unit+'/'+name for unit,files in grouped.items() for name in files} == set(records), 'Unexpected T8 archived unit')
    metrics, combined = {},[]
    full_index = 0
    for index,unit in enumerate(units):
        metric,runs,report = verify_unit(grouped[unit],unit,plan_record,build)
        stage = stages[2] if index == 11 else stages[3]
        require(stage['utc_started'] <= report['utc_started'] <= report['utc_finished'] <= stage['utc_finished'],
                'T8 original unit lies outside its actual child epoch')
        first_id = 1 if index == 11 else 1+30*full_index
        if index != 11:
            full_index += 1
        require(runs[0]['job_id'] == first_id, 'T8 child-epoch job IDs differ')
        if index == 11:
            require(sha(grouped[unit]['results.json']) == execution['dense_benchmark_result_sha256_bytes'],
                    'T8 reused dense results changed')
        for run in runs:
            line = f"PASS {unit.split('/')[-1]} job {run['job_id']} MODE=0 {run['m']}x{run['n']}x{run['k']}: " + \
                   f"{run['job_cycles']} cycles, {run['useful_gops']:.6f} useful GOPS"
            require(logs[stage['name']].count(line) == 1, 'T8 executed job console differs')
        metrics[unit] = metric
        combined.extend(dict(unit=unit,**run) for run in runs)
    totals = counts(combined)
    require(len(combined) == 480 and len(records) == 544 and load(directory/'results.json') ==
            dict(schema_version=1,result='PASS',phase='benchmark',units=units,build_id=BUILD_ID,runs=combined,checked_counts=totals),
            'T8 derived full-grid aggregate differs')
    csv_matches((directory/'results.csv').read_bytes(),combined)
    summary = load(directory/'summary.json')
    require(summary['result'] == 'PASS' and summary['unit_metrics'] == metrics and
            summary['checked_counts'] == manifest['checked_counts'] == totals and summary['full_grid_benchmark_complete'] is True and
            summary['raw_output_byte_replay'] is False and summary['maximum_qualified'] is False and
            summary['endurance_qualified'] is False and summary['physical_mode1_qualified'] is False,
            'T8 summary scope/counts differ')
    if extract is not None:
        require(not extract.exists(), 'Extraction target already exists')
        extract.mkdir(parents=True)
        for name,raw in records.items():
            destination = extract/name
            destination.parent.mkdir(parents=True,exist_ok=True)
            destination.write_bytes(raw)
    return dict(result='PASS',phase='benchmark',units=16,modes=[0],checked_counts=totals,
        original_files=544,original_sealed_files=528,current_sources_checked=check_current,
        scope='T8 MODE0 original metadata/digests/counters; no output-byte or UART replay')


verify_package = verify


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-current',action='store_true')
    parser.add_argument('--repo',type=Path)
    parser.add_argument('--extract-records',type=Path)
    args = parser.parse_args()
    require(args.repo is None or args.check_current, '--repo requires --check-current')
    print(json.dumps(verify(Path(__file__).resolve().parent,args.check_current,
        args.repo.resolve() if args.repo else None,args.extract_records.resolve() if args.extract_records else None),indent=2))


if __name__ == '__main__':
    main()
