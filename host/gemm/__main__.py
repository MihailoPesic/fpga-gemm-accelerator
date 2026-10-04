"""Run deterministic DDR GEMM jobs and save complete comparisons/counters."""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import platform
import random
import statistics
import sys

from .client import GEMM, load_manifest


def host_source_hashes():
    root = Path(__file__).resolve().parents[2]
    sources = sorted((root/'host/gemm').glob('*.py'))+[root/'host/preview/protocol.py']
    return {p.relative_to(root).as_posix(): hashlib.sha256(
        p.read_text(encoding='utf-8').encode('utf-8')).hexdigest() for p in sources}


def save_results(directory, report):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory/'results.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    fields = list(dict.fromkeys(key for item in report['runs'] for key in item)) or ['job_id', 'passed', 'error']
    with (directory/'results.csv').open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(report['runs'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', required=True)
    parser.add_argument('--manifest', type=Path, help='optional matching build.json; validates its selected bitstream file')
    parser.add_argument('--baud', type=int, choices=(115200, 1000000), help='build setting; default 115200 without a manifest')
    parser.add_argument('--m', type=int, default=33)
    parser.add_argument('--n', type=int, default=35)
    parser.add_argument('--k', type=int, default=17)
    parser.add_argument('--repeats', type=int, default=3, help='upload once, then run DDR-resident jobs')
    parser.add_argument('--mode', type=int, choices=(0, 1), default=0,
                        help='0 serial; 1 overlap, requiring the matching overlap-capable image')
    parser.add_argument('--seed', type=lambda value: int(value, 0), default=20261002)
    parser.add_argument('--extreme', action='store_true', help='use -128 for every A/B element')
    parser.add_argument('--no-guards', action='store_true', help='omit guard initialization/readback; every output is still compared')
    parser.add_argument('--clear-status', action='store_true', help='explicitly clear recoverable idle status after identification')
    parser.add_argument('--timeout', type=float, default=2.0, help='UART command timeout, seconds')
    parser.add_argument('--job-timeout', type=float, default=30.0)
    parser.add_argument('--retries', type=int, default=2)
    parser.add_argument('--output', type=Path, default=Path('build/gemm/results'))
    args = parser.parse_args(argv)
    if not (1 <= args.m <= 1024 and 1 <= args.n <= 1024 and 1 <= args.k <= 256):
        parser.error('M,N must be 1..1024 and K must be 1..256')
    if args.repeats < 1 or args.repeats >= 2**32 or args.timeout <= 0 or args.job_timeout <= 0 or args.retries < 0:
        parser.error('positive bounded repeats/timeouts and nonnegative retries required')
    sources = host_source_hashes()
    report = {'schema_version': 1, 'kind': 'serial_ddr_gemm', 'passed': False,
              'utc': datetime.now(timezone.utc).isoformat(), 'seed': args.seed,
              'port': args.port, 'mode': args.mode, 'shape': {'m': args.m, 'n': args.n, 'k': args.k},
              'input_pattern': 'all_minus128' if args.extreme else 'seeded_signed_int8',
              'guards_enabled': not args.no_guards, 'runs': [],
              'measurement_scope': 'JOB_CYCLES covers accepted validated START through final successful result B response; host times are separate',
              'timing_semantics': {'job_wall_seconds': 'pre-START configuration readback and JOB_ID write, START command, polling and frozen counter reads',
                                   'host_inclusive_seconds': 'first-run packing/upload/configuration plus command/counter/output transfers; validation and guard read/check time excluded',
                                   'guard_read_check_seconds': 'optional extra memory readback and guard/padding comparisons',
                                   'packing_seconds': 'includes the explicit B transpose and padding'},
              'session_policy': 'inputs uploaded in this connection; reset/reconnect requires re-identification and reload',
              'host': {'python': platform.python_version(), 'platform': platform.platform(),
                       'source_sha256_utf8_lf': sources}}
    save_results(args.output, report)
    device = None
    try:
        manifest = load_manifest(args.manifest) if args.manifest is not None else None
        baud = manifest['baud'] if manifest is not None else args.baud or 115200
        if manifest is not None and args.baud is not None and args.baud != baud:
            raise ValueError('--baud differs from the selected manifest')
        report['baud'] = baud
        report['build'] = manifest
        report['host']['pyserial'] = version('pyserial')
        device = GEMM.open(args.port, baud, args.timeout, args.retries)
        report['identity'] = device.identify(manifest)
        report['kind'] = 'overlap_ddr_gemm' if report['identity']['version'] == 0x200 else 'serial_ddr_gemm'
        report['identity_check'] = ('UART identity/geometry/BUILD_ID/clock and selected local bitstream SHA-256'
                                    if manifest else 'UART identity/geometry/BUILD_ID/clock; no selected local bitstream verification')
        if args.clear_status:
            device.clear_status()
        device.wait_ready(args.job_timeout)
        rng = random.Random(args.seed)
        a = [[-128 if args.extreme else rng.randrange(-128, 128) for _ in range(args.k)] for _ in range(args.m)]
        b = [[-128 if args.extreme else rng.randrange(-128, 128) for _ in range(args.n)] for _ in range(args.k)]

        def record(item):
            report['runs'].append(item)
            save_results(args.output, report)
            print(f"PASS job {item['job_id']}: {item['job_cycles']} cycles, {item['useful_gops']:.6f} useful GOPS")

        device.benchmark(a, b, repeats=args.repeats, padding_seed=args.seed,
                         guards=not args.no_guards, timeout=args.job_timeout, on_result=record, mode=args.mode)
        if host_source_hashes() != sources:
            raise RuntimeError('host sources changed during the measurement')
        cycles = [item['job_cycles'] for item in report['runs']]
        report['summary'] = {'jobs': len(cycles), 'job_cycles_min': min(cycles),
                             'job_cycles_median': statistics.median(cycles), 'job_cycles_max': max(cycles)}
        report['passed'] = len(cycles) == args.repeats and all(item['passed'] for item in report['runs'])
    except (Exception, KeyboardInterrupt) as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
        print(report['error'], file=sys.stderr)
    finally:
        if device is not None:
            report['transport'] = {'retries': device.link.retry_count,
                                   'rejected_frames': device.link.decoder.rejected}
            try:
                device.close()
            except OSError as exc:
                report['passed'] = False
                report['close_error'] = str(exc)
        save_results(args.output, report)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
