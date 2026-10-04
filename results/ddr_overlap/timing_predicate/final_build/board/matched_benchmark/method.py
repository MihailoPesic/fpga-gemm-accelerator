"""Private, matched MODE=0/1 board benchmark; never programs or resets the FPGA.

Requires an already programmed, qualified P8/T32/READ4 overlap image. Uploads
identical A and BT once, initializes C before every job, and stops at the first
failure. Use a new result directory. Hardware access happens only in main().
"""
import argparse
from dataclasses import asdict, replace
from datetime import datetime, timezone
import csv
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
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from host.gemm import Descriptor, GEMM, golden, pack_inputs
from host.gemm.__main__ import host_source_hashes
from host.gemm.client import COUNTERS, OVERLAP_VERSION
from scripts.program_ddr_gemm import qualified_manifest

M, N, K, PAIRS = 64, 64, 256, 30
GUARD = 64


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def sources():
    result = host_source_hashes()
    for path in (Path(__file__).resolve(), ROOT/'scripts/program_ddr_gemm.py'):
        result[path.relative_to(ROOT).as_posix()] = sha256(
            path.read_text(encoding='utf-8').encode('utf-8'))
    return result


def make_inputs(seed):
    rng = random.Random(seed)
    a = [[rng.randrange(-128, 128) for _ in range(K)] for _ in range(M)]
    b = [[rng.randrange(-128, 128) for _ in range(N)] for _ in range(K)]
    # The reduction is full-width, but input and output row padding still exist.
    desc = Descriptor(M, N, K, 0x00000fc0, 0x00100fc0, 0x00200fc0,
                      320, 320, 320, mode=0)
    packed = {mode: pack_inputs(a, b, seed, descriptor=replace(desc, mode=mode),
                                guard_bytes=GUARD) for mode in (0, 1)}
    images = tuple(packed[0].images(guards=True))
    if images != tuple(packed[1].images(guards=True)):
        raise AssertionError('MODE changed packed memory bytes')
    return a, b, packed, images


def wide_oracle(a, b):
    expected = golden(a, b)  # Python integers, not an INT8 matrix multiply.
    if any(not -(2**31) <= item < 2**31 for row in expected for item in row):
        raise AssertionError('wide-integer oracle exceeded signed INT32')
    return expected


def bursts(address, length):
    """Enumerate an aligned byte interval's legal INCR transactions."""
    if address % 8 or length % 8 or not length:
        raise ValueError('burst enumeration needs complete aligned words')
    result = []
    while length:
        count = min(length, 16*8, 4096-address % 4096)
        result.append(dict(address=address, bytes=count, beats=count//8))
        address += count
        length -= count
    return result


def expected_traffic(desc, p=8, t=32):
    """Enumerate useful matrix slices, independently of scheduler RTL."""
    reads, writes, microtiles, tile_shapes = [], [], [], []
    for i0 in range(0, desc.m, t):
        for j0 in range(0, desc.n, t):
            rows, columns = min(t, desc.m-i0), min(t, desc.n-j0)
            tag = len(tile_shapes)
            tile_shapes.append(dict(tag=tag, i0=i0, j0=j0, rows=rows, columns=columns))
            for name, origin, count, base, stride in (
                    ('a', i0, rows, desc.a_base, desc.a_stride),
                    ('bt', j0, columns, desc.bt_base, desc.bt_stride)):
                for row in range(origin, origin+count):
                    for burst in bursts(base+row*stride, (desc.k+7)//8*8):
                        reads.append(dict(tile=tag, matrix=name, row=row, **burst))
            for row in range(i0, i0+rows):
                for burst in bursts(desc.c_base+row*desc.c_stride+4*j0,
                                    (4*columns+7)//8*8):
                    writes.append(dict(tile=tag, row=row, useful_bytes=min(
                        burst['bytes'], 4*columns-(burst['address']-(desc.c_base+row*desc.c_stride+4*j0))),
                        **burst))
            for row in range(0, rows, p):
                for column in range(0, columns, p):
                    microtiles.append(dict(tile=tag, row=row, column=column,
                                           rows=min(p, rows-row), columns=min(p, columns-column)))
    counts = dict(read_beats=sum(item['beats'] for item in reads),
                  write_beats=sum(item['beats'] for item in writes),
                  write_valid_bytes=sum(item['useful_bytes'] for item in writes),
                  compute_cycles=sum(desc.k+3*p-1 for _ in microtiles))
    return dict(counts=counts, macrotiles=tile_shapes, microtiles=microtiles,
                read_transactions=reads, write_transactions=writes,
                analytical_read_bursts=len(reads), analytical_write_bursts=len(writes))


def job_order():
    result = []
    for pair in range(PAIRS):
        for position, mode in enumerate((0, 1) if pair % 2 == 0 else (1, 0)):
            result.append(dict(job_id=len(result)+1, pair=pair, position=position, mode=mode))
    return result


def c_sentinel(length, pair):
    # Reinitialize useful C as well as its padding and guards. Identical resident
    # inputs must not let a missing result write pass by retaining a prior C.
    # Both modes in each pair receive identical starting memory bytes.
    return bytes(1+((pair+1)*53+index*37) % 255 for index in range(length))


def check_image(name, address, actual, expected):
    if len(actual) != len(expected):
        raise AssertionError(f'{name}: short allocation readback')
    if actual != expected:
        offset = next(i for i, (got, want) in enumerate(zip(actual, expected)) if got != want)
        raise AssertionError(f'{name}: DDR[0x{address+offset:08x}] expected '
                             f'0x{expected[offset]:02x}, got 0x{actual[offset]:02x}')


class SerialTrace:
    """Record complete transmitted and received bytes, including failed writes."""
    def __init__(self, serial, path):
        self.serial = serial
        self.file = Path(path).open('x', encoding='utf-8')
        self.epoch = time.perf_counter_ns()

    def __getattr__(self, name):
        return getattr(self.serial, name)

    def record(self, **event):
        self.file.write(json.dumps(dict(utc=utc_now(), elapsed_ns=time.perf_counter_ns()-self.epoch,
                                       **event), separators=(',', ':'))+'\n')
        self.file.flush()

    def write(self, raw):
        try:
            count = self.serial.write(raw)
        except Exception as exc:
            self.record(direction='tx', attempted_hex=bytes(raw).hex(), error=f'{type(exc).__name__}: {exc}')
            raise
        self.record(direction='tx', requested_bytes=len(raw), accepted_bytes=count,
                    hex=bytes(raw[:count or 0]).hex())
        return count

    def read(self, size):
        try:
            raw = self.serial.read(size)
        except Exception as exc:
            self.record(direction='rx', error=f'{type(exc).__name__}: {exc}')
            raise
        if raw:
            self.record(direction='rx', hex=raw.hex())
        return raw

    def close(self):
        self.serial.close()
        self.record(event='serial_closed')


def distributions(runs):
    result = {}
    for mode in (0, 1):
        selected = [run for run in runs if run.get('passed') and run['mode'] == mode]
        fields = tuple(COUNTERS)+('useful_gops', 'core_seconds')
        result[str(mode)] = dict(jobs=len(selected), counters={})
        for name in fields:
            values = [run[name] for run in selected]
            if values:
                result[str(mode)]['counters'][name] = dict(values=values, minimum=min(values),
                    median=statistics.median(values), maximum=max(values))
    pairs = []
    for pair in range(PAIRS):
        selected = {run['mode']: run for run in runs if run.get('passed') and run['pair'] == pair}
        if set(selected) == {0, 1}:
            pairs.append(dict(pair=pair, serial_job_id=selected[0]['job_id'],
                overlap_job_id=selected[1]['job_id'], serial_cycles=selected[0]['job_cycles'],
                overlap_cycles=selected[1]['job_cycles'],
                serial_over_overlap_cycles=selected[0]['job_cycles']/selected[1]['job_cycles']))
    result['matched_pairs'] = pairs
    if pairs:
        ratios = [item['serial_over_overlap_cycles'] for item in pairs]
        result['paired_speedup'] = dict(minimum=min(ratios), median=statistics.median(ratios),
                                        maximum=max(ratios))
    if all(result[str(mode)]['jobs'] for mode in (0, 1)):
        result['ratio_of_cycle_medians'] = (result['0']['counters']['job_cycles']['median']/
                                           result['1']['counters']['job_cycles']['median'])
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='must not exist')
    parser.add_argument('--seed', type=lambda value: int(value, 0), default=20261004)
    parser.add_argument('--timeout', type=float, default=2.0)
    parser.add_argument('--job-timeout', type=float, default=30.0)
    args = parser.parse_args(argv)
    if any(not math.isfinite(item) or item <= 0 for item in (args.timeout, args.job_timeout)):
        parser.error('timeouts must be finite and positive')
    out, manifest_path = args.output.resolve(), args.manifest.resolve()
    if out.exists():
        parser.error('output path already exists; choose a fresh directory')
    out.mkdir(parents=True, exist_ok=False)
    began = time.perf_counter_ns()
    recorded_sources = sources()
    report = dict(schema_version=1, kind='matched_overlap_board_benchmark', state='RUNNING', passed=False,
        utc_started=utc_now(), utc_finished=None, port=args.port, manifest=str(manifest_path),
        seed=args.seed, shape=dict(m=M, n=N, k=K), expected_jobs=2*PAIRS,
        expected_jobs_per_mode=PAIRS, retries_allowed=0, runs=[], phases=[],
        order=job_order(), host=dict(python=platform.python_version(), platform=platform.platform(),
                                    source_sha256_utf8_lf=recorded_sources),
        residency='A and BT uploaded once in this identified connection and reused by all 60 jobs; '
                  'full C allocation and guards reinitialized before every job',
        comparison_scope='Same bitstream, BUILD_ID, P, T, read depth, clock, input bytes, bases and strides; only MODE differs',
        timing_semantics=dict(
            job_cycles='FPGA core timestamps: validated START acceptance through final successful result B handshake',
            core_seconds='JOB_CYCLES / actual CORE_HZ',
            job_wall_seconds='START including configuration readback/JOB_ID, polling, and all frozen-counter reads',
            output_read_seconds='Useful C download through the host read_output API',
            allocation_read_seconds='Extra complete A/BT/C allocation and guard downloads',
            validation_seconds='Every mathematical output and every allocation byte compared; oracle computed separately once',
            resident_host_seconds='Per-job C initialization, configuration, start/wait, output and allocation transfers; excludes validation',
            packing_seconds='Generate deterministic matrices and both matched descriptor packings, including B transpose and padding',
            oracle_seconds='One independent wide-integer mathematical reference, outside hardware execution',
            initial_upload_seconds='One upload of A and BT including row padding and allocation guards',
            elapsed_seconds='All identity checks, preparation, UART logging, validation, evidence writes, and close',
            logging='Raw UART bytes logged and flushed; host wall times include that overhead'))
    device, trace, current = None, None, None

    def persist():
        report['elapsed_seconds'] = (time.perf_counter_ns()-began)/1e9
        passed = [item for item in report['runs'] if item.get('passed')]
        report['checked_counts'] = dict(completed_jobs=sum('job_cycles' in item for item in report['runs']),
            validated_jobs=len(passed), compared_outputs=sum(item['compared_elements'] for item in passed),
            allocation_bytes_checked=sum(item['allocation_bytes_checked'] for item in passed))
        if device is not None:
            report['transport'] = dict(retries=device.link.retry_count,
                rejected_frames=device.link.decoder.rejected, poisoned=device.link.poisoned)
        report['distributions'] = distributions(report['runs'])
        temporary = out/'results.json.tmp'
        temporary.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
        temporary.replace(out/'results.json')
        fields = list(dict.fromkeys(key for run in report['runs'] for key in run)) or ['job_id', 'passed']
        with (out/'results.csv.tmp').open('w', newline='', encoding='utf-8') as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(report['runs'])
        (out/'results.csv.tmp').replace(out/'results.csv')

    def phase(name, action, target=None):
        target = report if target is None else target
        phase_record = dict(name=name, utc_started=utc_now(), passed=False)
        if target is not report:
            phase_record['job_id'] = target['job_id']
            target['stage'] = name
        report['phases'].append(phase_record)
        persist()
        started = time.perf_counter_ns()
        try:
            value = action()
            phase_record['passed'] = True
            return value
        except (Exception, KeyboardInterrupt) as exc:
            phase_record['error'] = f'{type(exc).__name__}: {exc}'
            raise
        finally:
            phase_record['seconds'] = (time.perf_counter_ns()-started)/1e9
            phase_record['utc_finished'] = utc_now()
            target[name+'_seconds'] = phase_record['seconds']
            persist()

    persist()
    try:
        manifest = phase('qualification', lambda: qualified_manifest(manifest_path))
        expected_identity = dict(kind='overlap_ddr_gemm', version=OVERLAP_VERSION,
                                 enable_overlap=True, p=8, t=32, read_slots=4,
                                 core_hz=100000000, baud=115200, kmax=256)
        if any(manifest.get(key) != value for key, value in expected_identity.items()):
            raise ValueError('matched plan requires qualified VERSION0x200 P8/T32/READ4 at 100MHz and 115200baud')
        if not isinstance(manifest.get('source_commit'), str) or type(manifest.get('source_dirty')) is not bool:
            raise ValueError('manifest lacks complete source commit/dirty identity')
        report['build'] = manifest
        report['manifest_sha256_bytes'] = sha256(manifest_path.read_bytes())
        report['host']['pyserial'] = version('pyserial')
        a, b, packed, images = phase('packing', lambda: make_inputs(args.seed))
        oracle = phase('oracle', lambda: wide_oracle(a, b))
        report['descriptors'] = {str(mode): asdict(item.descriptor) for mode, item in packed.items()}
        traffic = expected_traffic(packed[0].descriptor)
        report['analytical_traffic'] = traffic
        report['packed_images'] = [dict(name=name, address=address, bytes=len(raw), sha256_bytes=sha256(raw))
                                   for name, address, raw in images]
        report['input_a_sha256_bytes'] = sha256(bytes(item & 255 for row in a for item in row))
        report['raw_b_sha256_bytes'] = sha256(bytes(item & 255 for row in b for item in row))
        (out/'oracle.json').write_text(json.dumps(oracle)+'\n', encoding='utf-8')
        for name, _, raw in images:
            (out/f'initial_{name}_guarded.bin').write_bytes(raw)
        persist()
        # Every qualification/geometry/evidence/hash gate above precedes COM open.
        device = GEMM.open(args.port, manifest['baud'], args.timeout, retries=0)
        trace = SerialTrace(device.link.serial, out/'uart.jsonl')
        device.link.serial = trace
        report['identity'] = phase('identify', lambda: device.identify(manifest))
        phase('ready', lambda: device.wait_ready(args.job_timeout))
        input_images = [image for image in images if image[0] != 'c']
        phase('initial_upload', lambda: [device.write_memory(address, raw) for _, address, raw in input_images])
        desc = packed[0].descriptor
        c_name, c_address, c_initial = next(image for image in images if image[0] == 'c')
        for item in report['order']:
            current = dict(item, passed=False, stage='BEGIN', utc_started=utc_now())
            report['runs'].append(current)
            directory = out/f"job_{item['job_id']:03d}_mode{item['mode']}"
            directory.mkdir(exist_ok=False)
            current['directory'] = directory.name
            sentinel = c_sentinel(len(c_initial), item['pair'])
            (directory/'c_before.bin').write_bytes(sentinel)
            current['c_before_sha256_bytes'] = sha256(sentinel)
            phase('c_prepare', lambda: device.write_memory(c_address, sentinel), current)
            phase('configure', lambda: device.configure(packed[item['mode']].descriptor), current)

            def run_job():
                device.start(item['job_id'])
                return device.wait(item['job_id'], args.job_timeout)

            counts = phase('job_wall', run_job, current)
            current.update(counts)
            (directory/'counters.json').write_text(json.dumps(counts, indent=2)+'\n', encoding='utf-8')
            persist()
            output = phase('output_read', lambda: device.read_output(), current)
            (directory/'output.json').write_text(json.dumps(output)+'\n', encoding='utf-8')
            actual_images = {}

            def read_allocations():
                for name, address, raw in images:
                    actual = device.read_memory(address, len(raw))
                    actual_images[name] = actual
                    (directory/f'{name}_after.bin').write_bytes(actual)

            phase('allocation_read', read_allocations, current)

            def validate_all():
                if len(output) != M or any(len(row) != N for row in output):
                    raise AssertionError('downloaded output shape differs')
                for i in range(M):
                    for j in range(N):
                        if output[i][j] != oracle[i][j]:
                            raise AssertionError(f'C[{i},{j}] expected {oracle[i][j]}, got {output[i][j]}')
                wanted_c = bytearray(sentinel)
                for row in range(M):
                    begin = GUARD+row*desc.c_stride
                    wanted_c[begin:begin+4*N] = struct.pack(f'<{N}i', *oracle[row])
                for name, address, raw in images:
                    check_image(name, address, actual_images[name], bytes(wanted_c) if name == 'c' else raw)
                for name, wanted in traffic['counts'].items():
                    if counts[name] != wanted:
                        raise AssertionError(f'{name}: expected {wanted}, got {counts[name]}')
                if not counts['job_cycles'] or device.identity != report['identity']:
                    raise AssertionError('zero job duration or connection identity changed')

            phase('validation', validate_all, current)
            current.update(passed=True, stage='PASS', utc_finished=utc_now(),
                compared_elements=M*N, allocation_bytes_checked=sum(len(raw) for _, _, raw in images),
                input_allocation_and_guard_bytes_checked=sum(len(raw) for _, _, raw in input_images),
                input_padding_and_guard_bytes_checked=sum(len(raw) for _, _, raw in input_images)-M*K-N*K,
                c_padding_and_guard_bytes_checked=len(sentinel)-4*M*N,
                core_seconds=counts['job_cycles']/manifest['core_hz'],
                useful_gops=2*M*N*K*manifest['core_hz']/counts['job_cycles']/1e9,
                useful_utilization=M*N*K/(8*8*counts['job_cycles']),
                resident_host_seconds=sum(current[name+'_seconds'] for name in (
                    'c_prepare', 'configure', 'job_wall', 'output_read', 'allocation_read')))
            persist()
            print(f"PASS {item['job_id']}/60 pair{item['pair']+1} MODE={item['mode']}: "
                  f"{counts['job_cycles']} cycles, {current['useful_gops']:.6f} useful GOPS", flush=True)
        if sources() != recorded_sources:
            raise RuntimeError('host/runner source changed during measurement')
        if sha256(manifest_path.read_bytes()) != report['manifest_sha256_bytes']:
            raise RuntimeError('selected manifest changed during measurement')
        if qualified_manifest(manifest_path) != manifest:
            raise RuntimeError('qualified image/evidence changed during measurement')
        if (len(report['runs']) != 60 or not all(run['passed'] for run in report['runs']) or
                any(sum(run['mode'] == mode for run in report['runs']) != PAIRS for mode in (0, 1))):
            raise RuntimeError('matched plan did not validate every planned job')
        if device.link.retry_count or device.link.decoder.rejected or device.link.poisoned:
            raise RuntimeError('transport retry, rejected frame, or poisoned connection during benchmark')
        report.update(state='PASS', passed=True, source_hashes_unchanged=True,
                      manifest_and_qualification_unchanged=True)
    except (Exception, KeyboardInterrupt) as exc:
        failure = dict(type=type(exc).__name__, message=str(exc), utc=utc_now(),
                       job_id=current['job_id'] if current else None,
                       stage=current['stage'] if current else None, traceback=traceback.format_exc())
        for key in ('status', 'opcode'):
            if hasattr(exc, key):
                failure[key] = getattr(exc, key)
        report.update(state='FAIL', passed=False, error=failure)
        if current is not None:
            current.update(passed=False, error=f"{failure['type']}: {failure['message']}", utc_finished=utc_now())
        print(f"FAIL: {failure['type']}: {failure['message']}", file=sys.stderr, flush=True)
    finally:
        if device is not None:
            try:
                device.close()
            except Exception as exc:
                report.update(state='FAIL', passed=False, close_error=f'{type(exc).__name__}: {exc}')
        if trace is not None:
            trace.file.close()
        report['utc_finished'] = utc_now()
        persist()
        summary = {key: value for key, value in report.items() if key not in ('runs', 'phases', 'build', 'analytical_traffic')}
        summary['results_json_sha256_bytes'] = sha256((out/'results.json').read_bytes())
        summary['results_csv_sha256_bytes'] = sha256((out/'results.csv').read_bytes())
        if (out/'uart.jsonl').exists():
            summary['uart_jsonl_sha256_bytes'] = sha256((out/'uart.jsonl').read_bytes())
        (out/'summary.json').write_text(json.dumps(summary, indent=2)+'\n', encoding='utf-8')
    print(f"{report['state']}: {out/'summary.json'}", flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
