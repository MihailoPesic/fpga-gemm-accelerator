"""Validate an already programmed GEMM image and record release measurements.

maximum checks the full 1024x1024x256 boundary. endurance runs complete mixed
cycles for at least 30 minutes. benchmark records the fixed square/tail grid,
with at least 30 samples per selected mode. Nothing programs or resets the FPGA.
Completed sealed units may be resumed; an interrupted unit requires a fresh
output directory because an accepted START may have an unknown outcome.
"""
import argparse
import csv
from dataclasses import asdict, replace
from datetime import datetime, timezone
import gzip
import hashlib
from importlib.metadata import version
import json
import math
from pathlib import Path
import platform
import random
import statistics
import struct
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from host.gemm import Descriptor, GEMM, pack_inputs
from host.gemm.__main__ import host_source_hashes
from host.gemm.client import COUNTERS, DONE, ERROR, RESET_REQUIRED, BUSY, OVERLAP_VERSION
from scripts.program_ddr_gemm import qualified_manifest

GRID = tuple((size, size, reduction) for size in (32, 64, 128, 256) for reduction in (16, 64, 256))
TAILS = ((31, 33, 17), (65, 63, 255), (1, 64, 256), (64, 1, 256))
MIXED = ((1, 1, 1), (5, 3, 9), (31, 33, 17), (33, 35, 256),
         (65, 63, 255), (1, 64, 256), (64, 1, 256), (32, 32, 256))
TIMING = dict(
    job_cycles='Frozen FPGA counter: validated START through final successful result B response',
    job_wall_seconds='START, configuration readback, JOB_ID, polling and frozen counter reads',
    preparation_seconds='Input generation, host B transpose, padding and guarded packing',
    oracle_seconds='Independent wide-integer reference prepared once per resident input set',
    input_upload_seconds='A and BT allocations uploaded once per case in this connection',
    c_initialize_seconds='Complete C allocation and guards reinitialized before every job',
    allocation_download_seconds='Each complete A/BT/C allocation read once; useful C and padding checked from that same snapshot',
    validation_seconds='Every output byte and every input/padding/guard byte compared',
    resident_host_seconds='C initialization, configuration, job command/counters and complete allocation download; excludes validation',
    continuous_exercise_seconds='Continuous mixed host/FPGA work in one connection; includes transfers, preparation, validation and persistence, with no inter-session gaps')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def utc():
    return datetime.now(timezone.utc).isoformat()


def sha_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def text_hash(path):
    return sha_bytes(Path(path).read_text(encoding='utf-8').encode('utf-8'))


def source_hashes():
    result = host_source_hashes()
    for name in ('scripts/qualify_release.py', 'scripts/program_ddr_gemm.py'):
        result[name] = text_hash(ROOT/name)
    return result


def canonical_hash(value):
    return sha_bytes(json.dumps(value,sort_keys=True,separators=(',',':')).encode('utf-8'))


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name+'.tmp')
    with temporary.open('w',encoding='utf-8',newline='\n') as stream:
        json.dump(value,stream,indent=2)
        stream.write('\n')
    temporary.replace(path)


def snapshots(path, raw):
    with Path(path).open('xb') as output:
        with gzip.GzipFile(fileobj=output,mode='wb',filename='',mtime=0) as stream:
            stream.write(raw)
    return dict(file=Path(path).name,bytes=len(raw),sha256_bytes=sha_bytes(raw),gzip_sha256_bytes=sha(path))


def benchmark_shapes():
    return GRID+TAILS


def plan(modes, samples=30, duration=1800, seed=20261004, oracle='python', cases=None):
    require(tuple(modes) in ((0,), (1,), (0,1)), 'Choose MODE=0, MODE=1, or both')
    require(type(samples) is int and samples >= 30, 'Release benchmarks require at least 30 samples')
    require(type(duration) in (int,float) and math.isfinite(duration) and duration >= 1800,
            'Release endurance requires at least 1800 continuous seconds')
    require(oracle in ('python','numpy'), 'Unknown wide-integer oracle')
    indexes = tuple(range(len(benchmark_shapes()))) if cases is None else tuple(cases)
    require(indexes and len(set(indexes)) == len(indexes) and all(type(i) is int and 0 <= i < len(benchmark_shapes()) for i in indexes),
            'Benchmark case indexes must be unique and in 0..15')
    return dict(schema_version=1,modes=list(modes),samples=samples,minimum_continuous_seconds=duration,
                seed=seed,oracle=oracle,guard_bytes=64,
                maximum_shape=[1024,1024,256],mixed_shapes=[list(shape) for shape in MIXED],
                benchmark_cases=[dict(index=index,m=benchmark_shapes()[index][0],n=benchmark_shapes()[index][1],
                    k=benchmark_shapes()[index][2],seed=seed+index*9973) for index in indexes],
                c_initialization='Fresh full C allocation and guards for every job; both modes in a pair start with identical bytes',
                pair_order='MODE order alternates for each benchmark sample',
                residency='A/BT uploaded once per case; reconnect always reloads pending input sets')


def selected_modes(value):
    return {'0':(0,), '1':(1,), 'both':(0,1)}[value]


def release_manifest(path, modes):
    manifest = qualified_manifest(path)
    require(manifest.get('p') == 8 and manifest.get('t') in (8,32) and manifest.get('kmax') == 256 and
            manifest.get('core_hz') == 100000000, 'Release comparisons require P8, T8/T32, signed INT8 and a routed 100 MHz core')
    require(isinstance(manifest.get('source_commit'),str) and type(manifest.get('source_dirty')) is bool,
            'Qualified image lacks complete source identity')
    if 1 in modes:
        require(manifest.get('kind') == 'overlap_ddr_gemm' and manifest.get('version') == OVERLAP_VERSION and
                manifest.get('enable_overlap') is True, 'MODE=1 requires a qualified overlap-capable image')
    return manifest


def wide_output(a, b, backend='python'):
    if backend == 'numpy':
        import numpy as np
        # Convert before multiplication; INT8 matmul is not a valid oracle.
        product = np.asarray(a,dtype=np.int64) @ np.asarray(b,dtype=np.int64)
        require(np.all(product >= -(2**31)) and np.all(product < 2**31), 'Wide oracle exceeded signed INT32')
        return product.astype('<i4').tobytes(order='C')
    m,n,k = len(a),len(b[0]),len(b)
    columns = tuple(tuple(b[inner][column] for inner in range(k)) for column in range(n))
    output = bytearray()
    for row in a:
        values = [sum(left*right for left,right in zip(row,column)) for column in columns]
        require(all(-(2**31) <= value < 2**31 for value in values), 'Wide oracle exceeded signed INT32')
        output.extend(struct.pack(f'<{n}i',*values))
    require(len(output) == 4*m*n, 'Wide oracle byte count differs')
    return bytes(output)


def prepare(shape, seed, backend):
    began = time.perf_counter()
    m,n,k = shape
    rng = random.Random(seed)
    a = [[rng.randrange(-128,128) for _ in range(k)] for _ in range(m)]
    b = [[rng.randrange(-128,128) for _ in range(n)] for _ in range(k)]
    packed = pack_inputs(a,b,seed,guard_bytes=64)
    images = tuple(packed.images(guards=True))
    prepared = time.perf_counter()
    expected = wide_output(a,b,backend)
    oracle_done = time.perf_counter()
    return dict(packed=packed,images=images,expected=expected,
                preparation_seconds=prepared-began,oracle_seconds=oracle_done-prepared,
                input_a_sha256_bytes=sha_bytes(bytes(value & 255 for row in a for value in row)),
                raw_b_sha256_bytes=sha_bytes(bytes(value & 255 for row in b for value in row)),
                oracle_sha256_bytes=sha_bytes(expected))


def c_sentinel(length, seed, sample):
    salt = seed + (sample+1)*53
    return bytes(1+(salt+37*index)%255 for index in range(length))


def expected_c(initial, expected, desc):
    require(len(initial) == desc.m*desc.c_stride+128 and len(expected) == 4*desc.m*desc.n,
            'Expected C dimensions/allocation differ')
    result = bytearray(initial)
    for row in range(desc.m):
        result[64+row*desc.c_stride:64+row*desc.c_stride+4*desc.n] = expected[row*4*desc.n:(row+1)*4*desc.n]
    return bytes(result)


def traffic(desc,p,t):
    return dict(compute_cycles=((desc.m+p-1)//p)*((desc.n+p-1)//p)*(desc.k+3*p-1),
                read_beats=((desc.k+7)//8)*(desc.m*((desc.n+t-1)//t)+desc.n*((desc.m+t-1)//t)),
                write_beats=desc.m*((desc.n+1)//2),write_valid_bytes=4*desc.m*desc.n)


def compare_bytes(name, actual, wanted, address):
    require(len(actual) == len(wanted), f'{name}: incomplete allocation download')
    if actual != wanted:
        offset = next(index for index,(got,expected) in enumerate(zip(actual,wanted)) if got != expected)
        raise ValueError(f'{name}: DDR[0x{address+offset:08x}] got 0x{actual[offset]:02x}, expected 0x{wanted[offset]:02x}')


def verify_completed_identity(device,job_id):
    status = device.read_reg(0x0c)
    require(status & DONE and not status & (BUSY|ERROR|RESET_REQUIRED) and device.read_reg(0x44) == job_id,
            'Completed job identity/status changed during snapshot capture')


def run_job(device, prepared, job_id, mode, sample, seed, directory, retain=False, timeout=30):
    """Compare every byte from one read of each A/BT/C allocation."""
    directory = Path(directory)
    directory.mkdir(exist_ok=False)
    desc = replace(prepared['packed'].descriptor,mode=mode)
    images = {name:(address,raw) for name,address,raw in prepared['images']}
    c_address,c_initial = images['c']
    initial = c_sentinel(len(c_initial),seed,sample)
    record = dict(schema_version=1,state='RUNNING',passed=False,job_id=job_id,mode=mode,sample=sample,
                  seed=seed,m=desc.m,n=desc.n,k=desc.k,descriptor=asdict(desc),utc_started=utc(),utc_finished=None,
                  input_a_sha256_bytes=prepared['input_a_sha256_bytes'],raw_b_sha256_bytes=prepared['raw_b_sha256_bytes'],
                  oracle_sha256_bytes=prepared['oracle_sha256_bytes'],c_before_sha256_bytes=sha_bytes(initial),
                  input_residency='Uploaded in this connection; reused only within this case',snapshot_sha256_bytes={})
    atomic_json(directory/'job.json',record)
    try:
        begin = time.perf_counter()
        device.write_memory(c_address,initial)
        initialized = time.perf_counter()
        device.configure(desc)
        configured = time.perf_counter()
        record.update(stage='START',c_initialize_seconds=initialized-begin,configure_seconds=configured-initialized)
        atomic_json(directory/'job.json',record)
        device.start(job_id)
        counters = device.wait(job_id,timeout)
        completed = time.perf_counter()
        require(set(counters) == set(COUNTERS) and all(type(value) is int and 0 <= value < 2**64 for value in counters.values()),
                'Invalid frozen counter snapshot')
        require(counters['job_cycles'] >= counters['compute_cycles'] > 0,'Invalid job duration')
        identity = device.identity
        for key,wanted in traffic(desc,identity['p'],identity['t']).items():
            require(counters[key] == wanted,f'Frozen {key}: got {counters[key]}, expected {wanted}')
        record.update(stage='DOWNLOAD',job_wall_seconds=completed-configured,**counters)
        atomic_json(directory/'job.json',record)
        verify_completed_identity(device,job_id)
        actual = {name:device.read_memory(address,len(raw)) for name,(address,raw) in images.items()}
        downloaded = time.perf_counter()
        wanted_c = expected_c(initial,prepared['expected'],desc)
        for name,(address,raw) in images.items():
            compare_bytes(name,actual[name],wanted_c if name == 'c' else raw,address)
        verify_completed_identity(device,job_id)
        validated = time.perf_counter()
        record['snapshot_sha256_bytes'] = {name:sha_bytes(raw) for name,raw in actual.items()}
        if retain:
            record['saved_snapshots'] = {name:snapshots(directory/f'{name}_after.bin.gz',raw) for name,raw in actual.items()}
            record['saved_snapshots']['c_before'] = snapshots(directory/'c_before.bin.gz',initial)
            record['saved_snapshots']['oracle'] = snapshots(directory/'oracle_int32.bin.gz',prepared['expected'])
        hz = identity['core_hz']
        total_bytes = sum(len(raw) for raw in actual.values())
        record.update(state='PASS',passed=True,stage='PASS',utc_finished=utc(),p=identity['p'],t=identity['t'],
            build_id=identity['build_id'],core_hz=hz,compared_elements=desc.m*desc.n,
            allocation_bytes_checked=total_bytes,guard_input_bytes_checked=total_bytes-4*desc.m*desc.n,
            allocation_download_seconds=downloaded-completed,validation_seconds=validated-downloaded,
            resident_host_seconds=downloaded-begin,core_seconds=counters['job_cycles']/hz,
            useful_gops=2*desc.m*desc.n*desc.k*hz/counters['job_cycles']/1e9,
            useful_utilization=desc.m*desc.n*desc.k/(identity['p']**2*counters['job_cycles']))
    except (Exception,KeyboardInterrupt) as error:
        record.update(state='FAIL',passed=False,utc_finished=utc(),error=dict(type=type(error).__name__,message=str(error)))
        atomic_json(directory/'job.json',record)
        raise
    atomic_json(directory/'job.json',record)
    return record


def order(modes,samples):
    return [(sample,mode) for sample in range(samples) for mode in
            (modes if sample%2 == 0 else tuple(reversed(modes)))]


def write_csv(path,runs):
    fields = list(dict.fromkeys(key for run in runs for key in run)) or ['job_id','passed']
    temporary = Path(path).with_name(Path(path).name+'.tmp')
    with temporary.open('w',encoding='utf-8',newline='') as stream:
        writer = csv.DictWriter(stream,fieldnames=fields)
        writer.writeheader()
        writer.writerows(runs)
    temporary.replace(path)


def counts(runs):
    return dict(completed_jobs=sum(run.get('passed') is True for run in runs),
                compared_outputs=sum(run.get('compared_elements',0) for run in runs if run.get('passed')),
                allocation_bytes_checked=sum(run.get('allocation_bytes_checked',0) for run in runs if run.get('passed')),
                guard_input_bytes_checked=sum(run.get('guard_input_bytes_checked',0) for run in runs if run.get('passed')))


def distributions(runs):
    result = {}
    for mode in sorted(set(run['mode'] for run in runs)):
        selected = [run for run in runs if run['mode'] == mode and run.get('passed')]
        result[str(mode)] = dict(samples=len(selected),metrics={})
        for field in tuple(COUNTERS)+('core_seconds','useful_gops','useful_utilization'):
            values = [run[field] for run in selected]
            if values:
                result[str(mode)]['metrics'][field] = dict(minimum=min(values),median=statistics.median(values),maximum=max(values))
    return result


def seals(directory):
    return {path.relative_to(directory).as_posix():sha(path) for path in sorted(directory.rglob('*'))
            if path.is_file() and path.name != 'seal.json'}


def verify_sealed(directory,binding):
    directory = Path(directory)
    path = directory/'seal.json'
    require(path.is_file(),'Interrupted or unsealed unit cannot resume; use a fresh output directory: '+str(directory))
    record = json.loads(path.read_text(encoding='utf-8'))
    require(record.get('result') == 'PASS' and record.get('binding') == binding,'Completed unit identity changed: '+str(directory))
    require(record.get('saved_artifact_sha256_bytes') == seals(directory),'Completed unit artifact changed: '+str(directory))
    report = json.loads((directory/'results.json').read_text(encoding='utf-8'))
    require(report.get('state') == 'PASS' and report.get('passed') is True and report.get('binding') == binding and
            report.get('checked_counts') == counts(report['runs']),'Completed unit summary differs: '+str(directory))
    return report


class Session:
    def __init__(self,args,manifest,run_plan):
        self.args,self.manifest,self.plan = args,manifest,run_plan
        self.before,self.manifest_sha = source_hashes(),sha(args.manifest)
        self.device = None
        self.next_job = 1
        self.binding = dict(plan_sha256=canonical_hash(run_plan),manifest_sha256_bytes=self.manifest_sha,
                            build_id=manifest['build_id'],bitstream_sha256=manifest['bitstream_sha256'],
                            host_source_sha256_utf8_lf=self.before)

    def unchanged(self):
        require(source_hashes() == self.before and sha(self.args.manifest) == self.manifest_sha,
                'Host runner/source or selected build manifest changed during execution')
        require(qualified_manifest(self.args.manifest) == self.manifest,'Qualified image or its saved evidence changed')

    def connect(self):
        if self.device is None:
            self.unchanged()
            self.device = GEMM.open(self.args.port,self.manifest['baud'],self.args.timeout,retries=0)
            self.device.identify(self.manifest)
            self.device.wait_ready(self.args.job_timeout)
        return self.device

    def close(self):
        if self.device is not None:
            self.device.close()
            self.device = None

    def case(self,shape,seed,modes,samples,directory,runs,retain=False,on_result=None):
        device = self.connect()
        prepared = prepare(shape,seed,self.plan['oracle'])
        began = time.perf_counter()
        for name,address,raw in prepared['images']:
            if name != 'c':
                device.write_memory(address,raw)
        uploaded = time.perf_counter()
        case = dict(shape=list(shape),seed=seed,descriptor=asdict(prepared['packed'].descriptor),
                    preparation_seconds=prepared['preparation_seconds'],oracle_seconds=prepared['oracle_seconds'],
                    input_upload_seconds=uploaded-began,input_a_sha256_bytes=prepared['input_a_sha256_bytes'],
                    raw_b_sha256_bytes=prepared['raw_b_sha256_bytes'],oracle_sha256_bytes=prepared['oracle_sha256_bytes'],
                    images=[dict(name=name,address=address,bytes=len(raw),sha256_bytes=sha_bytes(raw)) for name,address,raw in prepared['images']])
        atomic_json(directory/f'case_{len(runs):06d}.json',case)
        for sample,mode in order(modes,samples):
            job_id = self.next_job
            require(job_id < 2**32,'Job ID space exhausted')
            self.next_job += 1
            run = run_job(device,prepared,job_id,mode,sample,seed,directory/f'job_{job_id:06d}',retain,self.args.job_timeout)
            runs.append(run)
            self.unchanged()
            if on_result is not None:
                on_result()
            print(f"PASS {directory.name} job {job_id} MODE={mode} {shape[0]}x{shape[1]}x{shape[2]}: "
                  f"{run['job_cycles']} cycles, {run['useful_gops']:.6f} useful GOPS",flush=True)

    def unit(self,name,action):
        directory = self.args.output/name
        binding = dict(self.binding,unit=name)
        if directory.exists():
            require(self.args.resume,'Output unit exists; choose a new directory or use --resume')
            report = verify_sealed(directory,binding)
            print('SKIP sealed PASS:',name,flush=True)
            return report
        directory.mkdir(parents=True,exist_ok=False)
        report = dict(schema_version=1,kind='gemm_release_'+name.split('/')[0],state='RUNNING',passed=False,
            binding=binding,utc_started=utc(),utc_finished=None,manifest=self.manifest,
            port=self.args.port,host=dict(python=platform.python_version(),platform=platform.platform(),
                                        pyserial=version('pyserial'),source_sha256_utf8_lf=self.before),
            timing_semantics=TIMING,runs=[],continuous_exercise_seconds=0,
            startup_scope='Already programmed operator-managed FPGA; this runner never resets or programs it',
            resume_scope='This unit is one uninterrupted connection; only whole sealed completed units can be skipped')
        began = time.perf_counter()
        def persist():
            report['elapsed_seconds'] = time.perf_counter()-began
            report['checked_counts'] = counts(report['runs'])
            report['distributions'] = distributions(report['runs'])
            if self.device is not None:
                report['identity'] = self.device.identity
                report['transport'] = dict(retries=self.device.link.retry_count,rejected_frames=self.device.link.decoder.rejected,
                                           poisoned=self.device.link.poisoned)
            atomic_json(directory/'results.json',report)
            write_csv(directory/'results.csv',report['runs'])
        persist()
        try:
            action(directory,report,persist)
            require(report['runs'] and all(run['passed'] for run in report['runs']),'Unit did not validate every attempted job')
            require(report.get('transport',dict(retries=0,rejected_frames=0,poisoned=False)) == dict(retries=0,rejected_frames=0,poisoned=False),
                    'A retry, rejected frame or poisoned connection occurred')
            self.unchanged()
            self.close()  # Close succeeds before sealing a completed unit.
            report.update(state='PASS',passed=True,source_hashes_unchanged=True)
        except (Exception,KeyboardInterrupt) as error:
            report.update(state='FAIL',passed=False,error=dict(type=type(error).__name__,message=str(error)))
            raise
        finally:
            try:
                self.close()
            except Exception as error:
                report.update(state='FAIL',passed=False,close_error=dict(type=type(error).__name__,message=str(error)))
            report['utc_finished'] = utc()
            persist()
        require(report['passed'],'Failed serial close prevents completion')
        atomic_json(directory/'seal.json',dict(schema_version=1,result='PASS',binding=binding,
                                               saved_artifact_sha256_bytes=seals(directory)))
        return report


def run_maximum(session):
    def action(directory,report,persist):
        session.case((1024,1024,256),session.plan['seed'],tuple(session.plan['modes']),1,directory,report['runs'],retain=True,on_result=persist)
        persist()
        require(counts(report['runs'])['compared_outputs'] == 1024*1024*len(session.plan['modes']),
                'Maximum phase omitted useful output comparisons')
    return session.unit('maximum',action)


def run_endurance(session):
    require(tuple(session.plan['modes']) == (0,1),'Release endurance requires both modes in the same continuous session')
    def action(directory,report,persist):
        session.connect()
        began = time.perf_counter()
        cycle = 0
        while True:
            for index,shape in enumerate(MIXED):
                seed = session.plan['seed']+cycle*104729+index*9973
                modes = (0,1) if (cycle+index)%2 == 0 else (1,0)
                session.case(shape,seed,modes,1,directory,report['runs'],on_result=persist)
                report['continuous_exercise_seconds'] = time.perf_counter()-began
                report['completed_mixed_cycles'] = cycle
                persist()
            cycle += 1
            report['completed_mixed_cycles'] = cycle
            report['continuous_exercise_seconds'] = time.perf_counter()-began
            persist()
            if report['continuous_exercise_seconds'] >= session.plan['minimum_continuous_seconds']:
                break
        require(cycle >= 1 and report['continuous_exercise_seconds'] >= 1800,'Endurance duration/complete mixed cycle not reached')
    return session.unit('endurance',action)


def run_benchmark(session):
    reports = []
    for case in session.plan['benchmark_cases']:
        if session.args.case_indexes is not None and case['index'] not in session.args.case_indexes:
            continue
        name = f"benchmark/case_{case['index']:02d}_{case['m']}x{case['n']}x{case['k']}"
        def action(directory,report,persist,case=case):
            session.case((case['m'],case['n'],case['k']),case['seed'],tuple(session.plan['modes']),
                         session.plan['samples'],directory,report['runs'],on_result=persist)
            persist()
            for mode in session.plan['modes']:
                require(sum(run['mode'] == mode for run in report['runs']) == session.plan['samples'],
                        'Benchmark did not complete every per-mode sample')
        reports.append(session.unit(name,action))
    return reports


def completed_units(session):
    """Enumerate verified historical units; never infer PASS from a filename."""
    names = ['maximum','endurance']
    names.extend(f"benchmark/case_{case['index']:02d}_{case['m']}x{case['n']}x{case['k']}"
                 for case in session.plan['benchmark_cases'])
    completed = []
    for name in names:
        directory = session.args.output/name
        if (directory/'seal.json').is_file():
            report = verify_sealed(directory,dict(session.binding,unit=name))
            if name.startswith('benchmark/'):
                index = int(name.split('case_',1)[1].split('_',1)[0])
                case = next(case for case in session.plan['benchmark_cases'] if case['index'] == index)
                verify_benchmark_samples(report,case,session.plan)
            completed.append(name)
    return completed


def verify_benchmark_samples(report,case,run_plan):
    """Validate complete case coverage independently of its directory name."""
    runs = report['runs']
    expected_order = order(tuple(run_plan['modes']),run_plan['samples'])
    require(len(runs) == len(expected_order),'Sealed benchmark omitted samples')
    input_identity = None
    pair_c = {}
    for run,(sample,mode) in zip(runs,expected_order):
        require(run.get('state') == 'PASS' and run.get('passed') is True and
                all(run.get(field) == wanted for field,wanted in dict(sample=sample,mode=mode,seed=case['seed'],
                     m=case['m'],n=case['n'],k=case['k'],p=8,core_hz=100000000,
                     build_id=report['binding']['build_id'],compared_elements=case['m']*case['n']).items()),
                'Sealed benchmark sample identity/order/validation differs')
        desc = Descriptor(**run['descriptor'])
        require((desc.m,desc.n,desc.k,desc.mode) == (case['m'],case['n'],case['k'],mode),
                'Sealed benchmark descriptor differs')
        require(run['t'] in (8,32) and run['t'] == report['manifest']['t'], 'Sealed benchmark tile geometry differs')
        require(all(type(run.get(key)) is int and 0 <= run[key] < 2**64 for key in COUNTERS),
                'Sealed benchmark frozen counter is invalid')
        require(run['job_cycles'] >= run['compute_cycles'] > 0 and
                all(run[key] == value for key,value in traffic(desc,8,run['t']).items()),
                'Sealed benchmark frozen counter contract differs')
        identity = (run['input_a_sha256_bytes'],run['raw_b_sha256_bytes'],run['oracle_sha256_bytes'],
                    asdict(replace(desc,mode=0)))
        if input_identity is None:
            input_identity = identity
        require(identity == input_identity,'Benchmark inputs/layout changed across samples or modes')
        require(sample not in pair_c or pair_c[sample] == run['c_before_sha256_bytes'],
                'Matched modes started with different C bytes')
        pair_c[sample] = run['c_before_sha256_bytes']
    require(report.get('checked_counts') == counts(runs),'Sealed benchmark comparison totals differ')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',required=True)
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True,help='fresh result directory, or a matching sealed checkpoint with --resume')
    parser.add_argument('--phase',choices=('maximum','endurance','benchmark','all'),default='maximum')
    parser.add_argument('--modes',choices=('0','1','both'),default='both')
    parser.add_argument('--samples',type=int,default=30)
    parser.add_argument('--duration',type=float,default=1800)
    parser.add_argument('--seed',type=lambda value:int(value,0),default=20261004)
    parser.add_argument('--oracle',choices=('python','numpy'),default='python')
    parser.add_argument('--cases',help='execute these benchmark indexes 0..15, comma-separated; other sealed cases remain resumable')
    parser.add_argument('--resume',action='store_true')
    parser.add_argument('--timeout',type=float,default=2)
    parser.add_argument('--job-timeout',type=float,default=30)
    args = parser.parse_args(argv)
    args.manifest,args.output = args.manifest.resolve(),args.output.resolve()
    try:
        require(all(math.isfinite(value) and value > 0 for value in (args.timeout,args.job_timeout)), 'Timeouts must be finite and positive')
        modes = selected_modes(args.modes)
        indexes = None if args.cases is None else tuple(int(value) for value in args.cases.split(','))
        # Case selection is an execution request, not a change to the immutable
        # full-grid plan. A later --resume can add other complete case units.
        run_plan = plan(modes,args.samples,args.duration,args.seed,args.oracle)
        if indexes is not None:
            require(indexes and len(set(indexes)) == len(indexes) and
                    all(0 <= index < len(benchmark_shapes()) for index in indexes), 'Case indexes must be unique and in 0..15')
        args.case_indexes = indexes
        if args.phase in ('endurance','all'):
            require(modes == (0,1),'Endurance requires --modes both')
        if args.oracle == 'numpy':
            import numpy
        manifest = release_manifest(args.manifest,modes)  # All gates precede COM open.
        binding = dict(plan=run_plan,manifest_sha256_bytes=sha(args.manifest),build_id=manifest['build_id'],
                       bitstream_sha256=manifest['bitstream_sha256'],host_source_sha256_utf8_lf=source_hashes())
        if args.output.exists():
            require(args.resume and (args.output/'plan.json').is_file(),'Existing output requires matching sealed checkpoints and --resume')
            require(json.loads((args.output/'plan.json').read_text(encoding='utf-8')) == binding,'Resume plan, image or host sources changed')
        else:
            require(not args.resume,'--resume requires an existing plan/checkpoint')
            args.output.mkdir(parents=True,exist_ok=False)
            atomic_json(args.output/'plan.json',binding)
        session = Session(args,manifest,run_plan)
        unit_names = []
        if args.phase in ('maximum','all'):
            unit_names.append('maximum')
        if args.phase in ('endurance','all'):
            unit_names.append('endurance')
        if args.phase in ('benchmark','all'):
            unit_names.extend(f"benchmark/case_{case['index']:02d}_{case['m']}x{case['n']}x{case['k']}"
                              for case in run_plan['benchmark_cases'] if indexes is None or case['index'] in indexes)
        # Check every selected prior seal before opening COM for any new unit.
        for name in unit_names:
            directory = args.output/name
            if directory.exists():
                verify_sealed(directory,dict(session.binding,unit=name))
        completed_units(session)  # All existing seals are checked before COM opens.
        reports = []
        try:
            if args.phase in ('maximum','all'):
                reports.append(run_maximum(session))
            if args.phase in ('endurance','all'):
                reports.append(run_endurance(session))
            if args.phase in ('benchmark','all'):
                reports.extend(run_benchmark(session))
        finally:
            session.close()
        available = completed_units(session)
        full_grid = sum(name.startswith('benchmark/') for name in available) == len(run_plan['benchmark_cases'])
        result = dict(schema_version=1,result='PASS',requested_phase=args.phase,plan_sha256=canonical_hash(run_plan),
            build_id=manifest['build_id'],bitstream_sha256=manifest['bitstream_sha256'],source_hashes_unchanged=True,
            completed_units=[report['binding']['unit'] for report in reports],
            checked_counts={key:sum(report['checked_counts'][key] for report in reports) for key in counts([])},
            scope='Only the explicitly requested completed units; skipped units remain their original sealed historical runs',
            continuous_endurance='No duration is carried across reconnects or resumed units',
            available_completed_units=available,full_grid_benchmark_complete=full_grid,
            all_requested_release_phases_complete=full_grid and 'maximum' in available and 'endurance' in available,
            utc_recorded=utc())
        atomic_json(args.output/'summary.json',result)
        print('PASS:',args.output/'summary.json',flush=True)
        return 0
    except (Exception,KeyboardInterrupt) as error:
        print(f'FAIL: {type(error).__name__}: {error}',file=sys.stderr,flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
