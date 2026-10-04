"""Replay a sealed two-mode maximum-shape record; never access hardware."""

import argparse
import csv
import gzip
import hashlib
import io
import json
import math
from pathlib import Path, PurePosixPath
import random
import tarfile

BUILD_ID = 0x9D4BEB4D
BITSTREAM = 'd187f51bac37c3682641243f1db112579ad14c8ac45a1b3df52e704bf58c8327'
COUNTERS = ('job_cycles', 'compute_cycles', 'read_beats', 'write_beats',
            'write_valid_bytes', 'input_wait_cycles', 'read_stall_cycles', 'write_stall_cycles')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return sha(json.dumps(value, sort_keys=True, separators=(',', ':')).encode())


def text_hash(raw):
    return sha(raw.decode('utf-8').replace('\r\n', '\n').replace('\r', '\n').encode())


def load(path):
    return json.loads(Path(path).read_bytes())


def safe_name(name):
    path = PurePosixPath(name)
    require(name and not path.is_absolute() and '\\' not in name and ':' not in name and path.as_posix() == name and
            all(part not in ('.', '..') for part in path.parts), 'Unsafe retained path')


def pattern(length, seed):
    return bytes(1 + (seed + 37 * index) % 255 for index in range(length))


def expected_input_images(case):
    m, n, k = case['shape']
    seed, desc = case['seed'], case['descriptor']
    rng = random.Random(seed)
    a = bytes(rng.randrange(-128, 128) & 255 for _ in range(m * k))
    b = bytes(rng.randrange(-128, 128) & 255 for _ in range(k * n))
    bt = bytes(b[inner * n + column] for column in range(n) for inner in range(k))
    require(sha(a) == case['input_a_sha256_bytes'] and sha(b) == case['raw_b_sha256_bytes'],
            'Seed/mathematical input hashes differ')
    a_rows = b''.join(a[row*k:(row+1)*k] + pattern(desc['a_stride']-k, seed+row*29) for row in range(m))
    bt_rows = b''.join(bt[col*k:(col+1)*k] + pattern(desc['bt_stride']-k, seed+97+col*29) for col in range(n))
    return (pattern(64, seed)+a_rows+pattern(64, seed+19),
            pattern(64, seed+71)+bt_rows+pattern(64, seed+90))


def replay_unit(directory, plan_record, build):
    directory = Path(directory)
    seal = load(directory/'seal.json')
    actual = {p.relative_to(directory).as_posix(): sha(p.read_bytes())
              for p in directory.rglob('*') if p.is_file() and p != directory/'seal.json'}
    require(seal['result'] == 'PASS' and len(actual) == 15 and actual == seal['saved_artifact_sha256_bytes'],
            'Original maximum unit seal/inventory mismatch')
    report, case = load(directory/'results.json'), load(directory/'case_000000.json')
    plan = plan_record['plan']
    require(report['state'] == 'PASS' and report['passed'] is True and report['kind'] == 'gemm_release_maximum',
            'Maximum unit incomplete')
    expected_binding = dict(plan_sha256=canonical(plan), manifest_sha256_bytes=plan_record['manifest_sha256_bytes'],
        build_id=BUILD_ID, bitstream_sha256=BITSTREAM,
        host_source_sha256_utf8_lf=plan_record['host_source_sha256_utf8_lf'], unit='maximum')
    require(report['binding'] == seal['binding'] == expected_binding, 'Unit/plan binding differs')
    require(report['manifest'] == build and report['source_hashes_unchanged'] is True and
            report['host']['source_sha256_utf8_lf'] == expected_binding['host_source_sha256_utf8_lf'],
            'Manifest/host sources changed')
    require((build['build_id'], build['bitstream_sha256'], build['p'], build['t'], build['kmax'],
             build['baud'], build['core_hz'], build['version'], build['read_slots'], build['enable_overlap']) ==
            (BUILD_ID, BITSTREAM, 8, 32, 256, 1000000, 100000000, 0x200, 4, True), 'Unexpected image')
    require(plan['modes'] == [0, 1] and plan['seed'] == 20261004 and plan['guard_bytes'] == 64 and
            plan['maximum_shape'] == [1024, 1024, 256] and plan['oracle'] == 'numpy', 'Maximum plan differs')
    desc = dict(m=1024, n=1024, k=256, a_base=64, bt_base=262336, c_base=524608,
                a_stride=256, bt_stride=256, c_stride=4096, mode=0, watchdog=10000000)
    require(case['shape'] == plan['maximum_shape'] and case['seed'] == plan['seed'] and
            case['descriptor'] == desc, 'Case geometry/layout differs')
    require(report['identity'] == dict(id=0x314D474E, version=0x200, geometry=(256<<16)|(32<<8)|8,
            p=8, t=32, kmax=256, build_id=BUILD_ID, core_hz=100000000), 'UART identity differs')
    require(report['transport'] == dict(retries=0, rejected_frames=0, poisoned=False), 'Transport failure/retry')
    runs = report['runs']
    require(len(runs) == 2 and [(r['job_id'], r['mode'], r['sample']) for r in runs] == [(1,0,0),(2,1,0)],
            'Maximum mode order/job count differs')
    wanted_a, wanted_bt = expected_input_images(case)
    images = {entry['name']: entry for entry in case['images']}
    require(set(images) == {'a', 'bt', 'c'}, 'Case images missing')
    for name, address, length, raw in (('a',0,262272,wanted_a), ('bt',262272,262272,wanted_bt),
            ('c',524544,4194432,pattern(64,20261004+142)+pattern(4194304,20261004+179)+pattern(64,20261004+161))):
        require(images[name] == dict(name=name,address=address,bytes=length,sha256_bytes=sha(raw)),
                'Prepared image metadata differs: '+name)
    # Reconstruct the wide oracle from actual retained DDR inputs, not generated matrices.
    import numpy as np
    oracle = None
    raw_snapshots = {}
    replayed = []
    previous_finish = report['utc_started']
    for run in runs:
        path = directory/f"job_{run['job_id']:06d}"
        require(load(path/'job.json') == run, 'Summary/job record differs')
        require(run['state'] == 'PASS' and run['passed'] is True and run['stage'] == 'PASS' and
                (run['m'],run['n'],run['k'],run['seed'],run['p'],run['t'],run['build_id'],run['core_hz']) ==
                (1024,1024,256,20261004,8,32,BUILD_ID,100000000), 'Completed job identity differs')
        require(run['descriptor'] == dict(desc, mode=run['mode']), 'Job descriptor differs')
        require(previous_finish <= run['utc_started'] <= run['utc_finished'] <= report['utc_finished'],
                'Job timestamps outside unit')
        previous_finish = run['utc_finished']
        snapshots = {}
        require(set(run['saved_snapshots']) == {'a','bt','c','c_before','oracle'}, 'Missing complete snapshots')
        for name, entry in run['saved_snapshots'].items():
            safe_name(entry['file'])
            zipped = (path/entry['file']).read_bytes()
            raw = gzip.decompress(zipped)
            require(sha(zipped) == entry['gzip_sha256_bytes'] and len(raw) == entry['bytes'] and
                    sha(raw) == entry['sha256_bytes'], 'Compressed/raw snapshot differs: '+name)
            snapshots[name] = raw
            raw_snapshots[f"job_{run['job_id']:06d}/{entry['file']}"] = dict(bytes=len(raw),sha256_bytes=sha(raw))
        require(snapshots['a'] == wanted_a and snapshots['bt'] == wanted_bt, 'Input/padding/guard mutation')
        require(run['input_a_sha256_bytes'] == case['input_a_sha256_bytes'] and
                run['raw_b_sha256_bytes'] == case['raw_b_sha256_bytes'], 'Input identity differs')
        if oracle is None:
            a = np.frombuffer(snapshots['a'],dtype=np.int8,offset=64,count=1024*256).reshape(1024,256).astype(np.int64)
            bt = np.frombuffer(snapshots['bt'],dtype=np.int8,offset=64,count=1024*256).reshape(1024,256).astype(np.int64)
            wide = a @ bt.T
            require(bool(np.all(wide >= -(2**31)) and np.all(wide < 2**31)), 'Wide output outside INT32')
            oracle = wide.astype('<i4').tobytes(order='C')
        require(snapshots['oracle'] == oracle and sha(oracle) == run['oracle_sha256_bytes'] == case['oracle_sha256_bytes'],
                'Retained/recomputed wide oracle differs')
        initial = pattern(4194432, 20261004+53)
        require(snapshots['c_before'] == initial and sha(initial) == run['c_before_sha256_bytes'], 'Fresh C sentinel differs')
        require(snapshots['c'] == initial[:64]+oracle+initial[-64:], 'Useful C bytes or C guard mutation')
        require(run['snapshot_sha256_bytes'] == {name:sha(snapshots[name]) for name in ('a','bt','c')},
                'Snapshot identities differ')
        # Enumerate all row/column macrotiles independently of the runner formula.
        read_beats = write_beats = compute_cycles = 0
        for row in range(0,1024,32):
            for col in range(0,1024,32):
                read_beats += (min(32,1024-row)+min(32,1024-col))*len(range(0,256,8))
                write_beats += min(32,1024-row)*len(range(0,min(32,1024-col),2))
                compute_cycles += len(range(0,min(32,1024-row),8))*len(range(0,min(32,1024-col),8))*(256+23)
        require((run['read_beats'],run['write_beats'],run['compute_cycles'],run['write_valid_bytes']) ==
                (read_beats,write_beats,compute_cycles,4*1024*1024), 'Frozen traffic/schedule differs')
        require(all(type(run[k]) is int and 0 <= run[k] < 2**64 for k in COUNTERS) and
                run['job_cycles'] >= run['compute_cycles'] > 0, 'Invalid counters')
        for name, expected in dict(core_seconds=run['job_cycles']/1e8,
                useful_gops=2*1024*1024*256*1e8/run['job_cycles']/1e9,
                useful_utilization=1024*1024*256/(64*run['job_cycles'])).items():
            require(math.isclose(run[name],expected,rel_tol=1e-12,abs_tol=1e-12), 'Derived metric differs: '+name)
        times = ('c_initialize_seconds','configure_seconds','job_wall_seconds','allocation_download_seconds','validation_seconds','resident_host_seconds')
        require(all(type(run[key]) in (int,float) and math.isfinite(run[key]) and run[key] >= 0 for key in times),
                'Invalid host timing')
        require(math.isclose(run['resident_host_seconds'],sum(run[key] for key in times[:4]),rel_tol=1e-12,abs_tol=1e-12),
                'Host measurement boundaries differ')
        require((run['compared_elements'],run['allocation_bytes_checked'],run['guard_input_bytes_checked']) ==
                (1048576,4718976,524672), 'Comparison counts differ')
        selected = report['distributions'][str(run['mode'])]
        require(selected['samples'] == 1, 'Single maximum job misrepresented as repetitions')
        for key in COUNTERS+('core_seconds','useful_gops','useful_utilization'):
            require(selected['metrics'][key] == dict(minimum=run[key],median=run[key],maximum=run[key]),
                    'Distribution differs: '+key)
        replayed.append(dict(job_id=run['job_id'],mode=run['mode'],job_cycles=run['job_cycles'],useful_gops=run['useful_gops']))
    require(report['checked_counts'] == dict(completed_jobs=2,compared_outputs=2097152,
            allocation_bytes_checked=9437952,guard_input_bytes_checked=1049344), 'Aggregate counts differ')
    rows = list(csv.DictReader(io.StringIO((directory/'results.csv').read_text(encoding='utf-8'))))
    require(rows == [{key:str(value) for key,value in run.items()} for run in runs], 'CSV does not match exact run records')
    return dict(result='PASS',scope='Complete retained maximum-shape byte replay and independent INT64 A @ BT.T oracle; no UART replay',
                build_id=hex(BUILD_ID),numpy_version=np.__version__,jobs=2,outputs=2097152,
                allocation_bytes=9437952,guard_input_bytes=1049344,runs=replayed,raw_snapshots=raw_snapshots)


def verify_package(directory, check_current=False):
    directory = Path(directory)
    manifest = load(directory/'manifest.json')
    actual = {p.relative_to(directory).as_posix():sha(p.read_bytes()) for p in directory.rglob('*')
              if p.is_file() and p != directory/'manifest.json'}
    require(manifest['result'] == 'PASS' and manifest['kind'] == 't32_1mbaud_maximum_board' and
            manifest['build_id'] == BUILD_ID and manifest['bitstream_sha256_bytes'] == BITSTREAM and
            actual == manifest['saved_artifact_sha256_bytes'], 'Publication seal differs')
    build, plan = load(directory/'build.json'), load(directory/'plan.json')
    execution, receipt = load(directory/'execution.json'),load(directory/'native_receipt.json')
    require(receipt['actual_exit_code'] == 1 and receipt['session_id'] == 17347 and receipt['tool_chunk_id'] == '679880' and
            receipt['execution_sha256_bytes'] == sha((directory/'execution.json').read_bytes()),
            'Actual native completion receipt differs')
    require(execution['state'] == 'FAIL' and execution['utc_finished'] is not None and
            execution['build_id'] == BUILD_ID and execution['baud'] == 1000000 and
            execution['manifest_sha256_bytes'] == sha((directory/'build.json').read_bytes()) == plan['manifest_sha256_bytes'] and
            execution['bitstream_sha256_bytes'] == BITSTREAM and
            execution['helper_sha256_bytes'] == sha((directory/'board_method.py').read_bytes()), 'Cold/program execution binding differs')
    require(execution['operator_confirmation'] == 'Fresh OFF/ON complete; powered on and connected' and
            execution['operator_confirmation_source'] == 'Actual user reply to the new-image cold-cycle request in this session',
            'Missing operator cold-power receipt')
    stages = execution['stages']
    require([s['name'] for s in stages] == ['program','smoke','dense_benchmark','full_qualification'], 'Stage order differs')
    require([s['actual_exit_code'] for s in stages] == [0,0,0,1] and
            manifest['parent_result'] == 'FAIL' and manifest['parent_actual_exit_code'] == 1 and
            manifest['full_qualification_claimed_complete'] is False,
            'Failed parent was promoted to completed full qualification')
    previous_finish = execution['utc_started']
    for stage in stages:
        name = stage['name']
        raw = gzip.decompress((directory/'execution_logs'/(name+'_console.txt.gz')).read_bytes())
        require(previous_finish <= stage['utc_started'] <= stage['utc_finished'] <= execution['utc_finished'] and
                sha(raw) == stage['console_sha256_bytes'], 'Actual stage/log differs: '+name)
        previous_finish = stage['utc_finished']
        command = stage['command']
        require(command.count('--manifest') == 1 and 'gemm_release_p8_t32_1mbaud' in command[command.index('--manifest')+1],
                'Wrong programmed/tested manifest')
    command = stages[-1]['command']
    for flag, value in (('--phase','all'), ('--modes','both'), ('--oracle','numpy'), ('--samples','30'), ('--seed','20261004')):
        require(command.count(flag) == 1 and command[command.index(flag)+1] == value, 'Full qualification command differs: '+flag)
    require(command.count('--resume') == 1 and stages[0]['command'][1].replace('\\','/').rsplit('/',1)[-1] == 'program_ddr_gemm.py',
            'Unexpected programming/qualification stage')
    program = (directory/'program_log.txt').read_text(encoding='utf-8')
    require('DDR_GEMM_PROGRAMMED xc7a50t ' in program and '/build/gemm_release_p8_t32_1mbaud/gemm_ddr.bit' in program and
            'End of startup status: HIGH' in program, 'Programming log marker differs')
    program_console = gzip.decompress((directory/'execution_logs/program_console.txt.gz').read_bytes()).decode('utf-8')
    require('0x9d4beb4d' in program_console and BITSTREAM in program_console, 'Programmed identity differs')
    qualification_console = gzip.decompress((directory/'execution_logs/full_qualification_console.txt.gz').read_bytes()).decode('utf-8').splitlines()
    require(qualification_console[-1] == 'FAIL: TransportError: opcode 0x02 timed out; outcome uncertain, reconnect and reload',
            'Later endurance failure was concealed or altered')
    for run in load(directory/'unit/results.json')['runs']:
        line = f"PASS maximum job {run['job_id']} MODE={run['mode']} 1024x1024x256: {run['job_cycles']} cycles, {run['useful_gops']:.6f} useful GOPS"
        require(qualification_console.count(line) == 1, 'Actual maximum console differs')
    static = load(directory/'static_archive_manifest.json')
    review = load(directory/'static_review_record.json')
    require(static['result'] == 'PASS' and static['build_id'] == BUILD_ID and static['bitstream_sha256_bytes'] == BITSTREAM and
            static['source_sha256_utf8_lf'] == build['source_sha256_utf8_lf'] and
            review['result'] == 'PASS_PROVISIONAL_REVIEW' and review['actual_exit_code'] == 0 and
            static['checkpoint_sha256_bytes'] == review['checkpoint_sha256_bytes_before'] == review['checkpoint_sha256_bytes_after'] and
            execution['review_record_sha256_bytes'] == sha((directory/'static_review_record.json').read_bytes()) and
            manifest['static_archive_manifest_sha256_bytes'] == sha((directory/'static_archive_manifest.json').read_bytes()),
            'Own implementation/static review binding differs')
    require(stages[0]['utc_finished'] <= stages[-1]['utc_started'] and
            stages[-1]['utc_started'] <= load(directory/'unit/results.json')['utc_started'] and
            load(directory/'unit/results.json')['utc_finished'] <= stages[-1]['utc_finished'], 'Maximum outside cold/program run')
    sources = load(directory/'source_inventory.json')
    expected = dict(build['source_sha256_utf8_lf'],**plan['host_source_sha256_utf8_lf'])
    require(set(sources) == set(expected), 'Source inventory differs')
    with tarfile.open(directory/'sources.tar.gz','r:gz') as archive:
        members = archive.getmembers()
        require(len(members) == len(expected) and len({m.name for m in members}) == len(members), 'Source snapshot count differs')
        for member in members:
            safe_name(member.name)
            require(member.isfile() and member.name in expected, 'Unexpected source snapshot')
            raw = archive.extractfile(member).read()
            require(sources[member.name] == dict(bytes=len(raw),sha256_bytes=sha(raw),sha256_utf8_lf=text_hash(raw)) and
                    text_hash(raw) == expected[member.name], 'Frozen source differs: '+member.name)
            if check_current:
                root = Path(__file__).resolve().parent
                while not (root/'rtl').is_dir() and root != root.parent:
                    root = root.parent
                require(text_hash((root/member.name).read_bytes()) == expected[member.name], 'Current source differs: '+member.name)
    require(execution['source_sha256_utf8_lf'] == plan['host_source_sha256_utf8_lf'], 'Execution host source binding differs')
    copied = load(directory/'resume_copy.json')
    original_files = {p.relative_to(directory/'unit').as_posix():sha(p.read_bytes())
                      for p in (directory/'unit').rglob('*') if p.is_file()}
    require(copied['result'] == 'PASS_BYTE_IDENTICAL_COPY' and copied['unit_was_rerun'] is False and
            copied['recovered_parent_completion_claimed'] is False and
            copied['original_artifact_sha256_bytes'] == copied['copied_artifact_sha256_bytes'] == original_files,
            'Copied maximum unit was presented as a new run')
    result = replay_unit(directory/'unit',plan,build)
    saved = load(directory/'replay.json')
    require({k:v for k,v in result.items() if k != 'numpy_version'} == {k:v for k,v in saved.items() if k != 'numpy_version'},
            'Saved independent replay differs')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--unit',type=Path,help='Private original sealed unit; receipt qualification not implied')
    parser.add_argument('--plan',type=Path)
    parser.add_argument('--build',type=Path)
    parser.add_argument('--check-current',action='store_true')
    parser.add_argument('--record',type=Path)
    args = parser.parse_args()
    if args.unit:
        require(args.plan and args.build,'Original replay needs explicit plan and build')
        result = replay_unit(args.unit,load(args.plan),load(args.build))
    else:
        result = verify_package(Path(__file__).resolve().parent,args.check_current)
    if args.record:
        require(not args.record.exists(),'Refuse existing replay record')
        args.record.parent.mkdir(parents=True,exist_ok=True)
        args.record.write_bytes((json.dumps(result,indent=2)+'\n').encode())
    print(json.dumps({k:v for k,v in result.items() if k != 'raw_snapshots'},indent=2))


if __name__ == '__main__':
    main()
