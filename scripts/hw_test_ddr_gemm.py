"""Run the bounded 48-job P4/P8 T32 DDR GEMM board qualification."""
import argparse
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
import math
from pathlib import Path
import platform
import random
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from host.gemm import GEMM
from host.gemm.__main__ import host_source_hashes, save_results
from scripts.program_ddr_gemm import qualified_manifest


def case_plan(p=4, mode=0):
    if type(p) is not int or p not in (4, 8):
        raise ValueError('qualification requires P4 or P8')
    if type(mode) is not int or mode not in (0, 1):
        raise ValueError('qualification requires MODE=0 or 1')
    shapes = (
        ('signed_scalar', 1, 1, 1, 3, 'all_minus128'),
        ('signed_extremes', 1, 2, 256, 3, 'minus128_by_minus128_and_plus127'),
        ('odd_tail', 5, 3, 9, 3, 'seeded_signed_int8'),
        ('column_tile_boundary', 31, 33, 17, 3, 'seeded_signed_int8'),
        ('multi_tile_kmax', 33, 35, 256, 3, 'seeded_signed_int8'),
        ('asymmetric_k255', 65, 63, 255, 3, 'seeded_signed_int8'),
        ('resident_dense', 32, 32, 256, 30, 'seeded_signed_int8'),
    )
    cases, first_job = [], 1
    for index, (name, m, n, k, repeats, pattern) in enumerate(shapes):
        cases.append(dict(case_index=index, name=name, m=m, n=n, k=k, repeats=repeats,
                          seed=20261002+index, pattern=pattern, first_job_id=first_job,
                          final_job_id=first_job+repeats-1))
        first_job += repeats
    plan = dict(schema_version=1, p=p, t=32, kmax=256, core_hz=100000000,
                baud=115200, mode='serial', guards=True, cases=cases,
                total_jobs=48, total_compared_outputs=49593)
    # Leave the archived serial plan, including its exact serialized identity,
    # unchanged. Overlap selects the same matrices, seeds and repetitions.
    if mode:
        plan.update(mode='overlap', job_mode=1)
    return plan


def plan_hash(plan):
    return hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()


def source_hashes():
    sources = host_source_hashes()
    for path in (Path(__file__).resolve(), ROOT/'scripts/program_ddr_gemm.py'):
        sources[path.relative_to(ROOT).as_posix()] = hashlib.sha256(
            path.read_text(encoding='utf-8').encode('utf-8')).hexdigest()
    return sources


def qualification_manifest(path, plan):
    mode = plan.get('job_mode', 0)
    if (type(plan.get('p')) is not int or plan['p'] not in (4, 8) or
            type(mode) is not int or mode not in (0, 1) or
            plan.get('mode') != ('overlap' if mode else 'serial') or
            any(plan.get(name) != value for name, value in
                {'t': 32, 'kmax': 256, 'core_hz': 100000000, 'baud': 115200}.items())):
        raise ValueError('unsupported board qualification geometry, clock or baud')
    manifest = qualified_manifest(path)
    expected = {name: plan[name] for name in ('p', 't', 'kmax', 'core_hz', 'baud')}
    if any(manifest.get(name) != value for name, value in expected.items()):
        raise ValueError(f"This qualification plan requires P{plan['p']}/T32, 100 MHz and 115200 baud")
    if mode and (manifest.get('kind') != 'overlap_ddr_gemm' or manifest.get('version') != 0x200 or
                 manifest.get('enable_overlap') is not True):
        raise ValueError('MODE=1 qualification requires the matching overlap-capable image')
    if (not isinstance(manifest.get('source_commit'), str) or
            not isinstance(manifest.get('source_dirty'), bool)):
        raise ValueError('Manifest lacks complete source identity metadata')
    return manifest


def matrices(case):
    m, n, k = (case[name] for name in ('m', 'n', 'k'))
    if case['pattern'] == 'all_minus128':
        return [[-128]*k for _ in range(m)], [[-128]*n for _ in range(k)]
    if case['pattern'] == 'minus128_by_minus128_and_plus127':
        return [[-128]*k], [[-128, 127] for _ in range(k)]
    rng = random.Random(case['seed'])
    a = [[rng.randrange(-128, 128) for _ in range(k)] for _ in range(m)]
    b = [[rng.randrange(-128, 128) for _ in range(n)] for _ in range(k)]
    return a, b


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, epilog='Default P4 plan SHA-256: '+plan_hash(case_plan()))
    parser.add_argument('--port', required=True)
    parser.add_argument('--p', type=int, choices=(4, 8), default=4,
                        help='required array geometry; default preserves the original P4 plan')
    parser.add_argument('--mode', type=int, choices=(0, 1), default=0,
                        help='0 serial; 1 overlap, requiring a qualified overlap-capable image')
    parser.add_argument('--manifest', type=Path, required=True,
                        help='qualified build.json for the programmed image')
    parser.add_argument('--output', type=Path,
                        help='new/empty result directory; default is hardware_qualification/<UTC timestamp> beside the manifest')
    parser.add_argument('--timeout', type=float, default=2.0, help='UART command timeout, seconds')
    parser.add_argument('--job-timeout', type=float, default=30.0)
    parser.add_argument('--retries', type=int, default=2)
    args = parser.parse_args(argv)
    if (not math.isfinite(args.timeout) or args.timeout <= 0 or
            not math.isfinite(args.job_timeout) or args.job_timeout <= 0 or args.retries < 0):
        parser.error('finite positive timeouts and nonnegative retries required')
    plan = case_plan(args.p, args.mode)
    assert sum(case['repeats'] for case in plan['cases']) == plan['total_jobs']
    assert sum(case['m']*case['n']*case['repeats'] for case in plan['cases']) == plan['total_compared_outputs']
    manifest_path = args.manifest.resolve()
    out = (args.output or manifest_path.parent/'hardware_qualification'/
           datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')).resolve()
    if out.exists() and any(out.iterdir()):
        parser.error('output directory is not empty; choose a new directory to preserve previous evidence')
    out.mkdir(parents=True, exist_ok=True)
    began, recorded_sources = time.perf_counter(), source_hashes()
    report = dict(schema_version=1,
                  kind=('overlap_ddr_gemm_board_qualification' if args.mode else 'serial_ddr_gemm_board_qualification'),
                  mode=args.mode, passed=False,
                  state='RUNNING', utc_started=utc_now(), utc_finished=None, elapsed_seconds=0,
                  port=args.port, manifest=str(args.manifest.resolve()), case_plan=plan,
                  case_plan_sha256=plan_hash(plan), runs=[], cases=[],
                  expected_jobs=plan['total_jobs'], expected_compared_outputs=plan['total_compared_outputs'],
                  startup_scope='Host identity and idle readiness only; power/reset sequence is recorded separately',
                  residency='Each case uploads once, then reuses DDR inputs for its repeated jobs; each next case reloads inputs and C sentinels',
                  validation='Every output compared with a wide-integer oracle; full input allocations, C padding and allocation guards checked after every job',
                  timing_semantics=dict(
                      job_wall_seconds='Configuration readback, JOB_ID write, START, polling and frozen counter reads',
                      host_inclusive_seconds='First-run packing/upload/configuration plus command/counter/output transfers; validation and guard checks excluded',
                      guard_read_check_seconds='Full allocation readback plus guard/padding comparisons; includes a second read of useful C bytes',
                      elapsed_seconds='Complete wall time including identity checks, all transfers, all validation, and result persistence'),
                  host=dict(python=platform.python_version(), platform=platform.platform(),
                            source_sha256_utf8_lf=recorded_sources))
    device, current_case, current_report, case_began = None, None, None, None

    def persist():
        report['elapsed_seconds'] = time.perf_counter()-began
        report['checked_counts'] = dict(
            completed_jobs=len(report['runs']),
            compared_outputs=sum(run['compared_elements'] for run in report['runs']),
            guard_bytes_checked=sum(run['guard_bytes_checked'] for run in report['runs']))
        if device is not None:
            report['transport'] = dict(retries=device.link.retry_count,
                                       rejected_frames=device.link.decoder.rejected,
                                       poisoned=device.link.poisoned)
        save_results(out, report)
        summary = {key: value for key, value in report.items() if key not in ('runs', 'build')}
        summary['results_sha256'] = hashlib.sha256((out/'results.json').read_bytes()).hexdigest()
        summary['results_csv_sha256'] = hashlib.sha256((out/'results.csv').read_bytes()).hexdigest()
        if 'build' in report:
            summary['build_identity'] = {key: report['build'][key] for key in (
                'build_id', 'bitstream_sha256', 'source_commit', 'source_dirty',
                'source_sha256_utf8_lf', 'p', 't', 'kmax', 'baud', 'core_hz', 'part')}
            summary['build_identity']['read_slots'] = report['build'].get('read_slots', 1)
            for key in ('id', 'version', 'kind', 'mode', 'enable_overlap'):
                if key in report['build']:
                    summary['build_identity'][key] = report['build'][key]
        (out/'summary.json').write_text(json.dumps(summary, indent=2)+'\n', encoding='utf-8')

    persist()
    try:
        manifest = qualification_manifest(manifest_path, plan)  # All gates precede opening COM.
        report['build'] = manifest
        report['manifest_sha256'] = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        report['host']['pyserial'] = version('pyserial')
        persist()
        device = GEMM.open(args.port, manifest['baud'], args.timeout, args.retries)
        report['identity'] = device.identify(manifest)
        device.wait_ready(args.job_timeout)
        persist()
        for case in plan['cases']:
            current_case, case_began = case, time.perf_counter()
            directory = out/f'{case["case_index"]+1:02d}_{case["name"]}'
            current_report = dict(schema_version=1,
                                  kind=('overlap_ddr_gemm_board_case' if args.mode else 'serial_ddr_gemm_board_case'),
                                  mode=args.mode, passed=False,
                                  utc_started=utc_now(), utc_finished=None, elapsed_seconds=0,
                                  case=case, case_plan_sha256=report['case_plan_sha256'],
                                  identity=report['identity'], manifest_sha256=report['manifest_sha256'],
                                  host=report['host'], build_id=manifest['build_id'],
                                  bitstream_sha256=manifest['bitstream_sha256'], runs=[])
            case_summary = dict(case, passed=False, directory=directory.name, completed_jobs=0)
            report['cases'].append(case_summary)
            save_results(directory, current_report)
            persist()
            a, b = matrices(case)

            def record(run):
                item = dict(run, case_index=case['case_index'], case_name=case['name'],
                            seed=case['seed'], utc_recorded=utc_now())
                current_report['runs'].append(item)
                report['runs'].append(item)
                case_summary['completed_jobs'] = len(current_report['runs'])
                current_report['elapsed_seconds'] = time.perf_counter()-case_began
                save_results(directory, current_report)
                persist()
                print(f"PASS job {item['job_id']}/48 {case['name']}: {item['job_cycles']} cycles, "
                      f"{item['compared_elements']} outputs, {item['useful_gops']:.6f} useful GOPS", flush=True)

            device.benchmark(a, b, job_id=case['first_job_id'], repeats=case['repeats'],
                             padding_seed=case['seed'], guards=True, timeout=args.job_timeout,
                             on_result=record, mode=args.mode)
            counts = [run['job_cycles'] for run in current_report['runs']]
            current_report.update(passed=len(counts) == case['repeats'], utc_finished=utc_now(),
                                  elapsed_seconds=time.perf_counter()-case_began,
                                  summary=dict(jobs=len(counts), job_cycles_min=min(counts),
                                               job_cycles_median=statistics.median(counts), job_cycles_max=max(counts)))
            case_summary.update(passed=current_report['passed'], summary=current_report['summary'])
            save_results(directory, current_report)
            persist()
        if source_hashes() != recorded_sources:
            raise RuntimeError('Host or execution-harness sources changed during qualification')
        if hashlib.sha256(manifest_path.read_bytes()).hexdigest() != report['manifest_sha256']:
            raise RuntimeError('Build manifest changed during qualification')
        if hashlib.sha256((manifest_path.parent/manifest['bitstream']).read_bytes()).hexdigest() != manifest['bitstream_sha256']:
            raise RuntimeError('Selected local bitstream changed during qualification')
        if (len(report['runs']) != plan['total_jobs'] or
                sum(run['compared_elements'] for run in report['runs']) != plan['total_compared_outputs'] or
                not all(case['passed'] for case in report['cases'])):
            raise RuntimeError('Qualification did not complete every planned job and output comparison')
        report.update(passed=True, state='PASS', source_hashes_unchanged=True)
    except (Exception, KeyboardInterrupt) as exc:
        failure = dict(type=type(exc).__name__, message=str(exc), utc=utc_now(),
                       case_index=current_case['case_index'] if current_case else None,
                       next_job_id=(current_case['first_job_id']+len(current_report['runs'])) if current_case else None)
        if hasattr(exc, 'status'):
            failure['device_status'] = exc.status
        if hasattr(exc, 'opcode'):
            failure['opcode'] = exc.opcode
        report.update(passed=False, state='FAIL', error=failure)
        if current_report is not None:
            current_report.update(passed=False, error=failure, utc_finished=utc_now(),
                                  elapsed_seconds=time.perf_counter()-case_began)
            save_results(out/f'{current_case["case_index"]+1:02d}_{current_case["name"]}', current_report)
            report['cases'][-1]['passed'] = False
        print(f"FAIL: {failure['type']}: {failure['message']}", file=sys.stderr, flush=True)
    finally:
        if device is not None:
            try:
                device.close()
            except Exception as exc:
                report.update(passed=False, state='FAIL', close_error=f'{type(exc).__name__}: {exc}')
        report['utc_finished'] = utc_now()
        persist()
    print(f"{report['state']}: {out/'summary.json'}", flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
