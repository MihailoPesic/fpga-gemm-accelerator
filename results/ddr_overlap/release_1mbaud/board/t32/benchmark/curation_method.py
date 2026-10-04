"""Prepare completed T32 evidence only after genuine recovered-parent success.

This is software-only curation. It never opens a serial port or runs a child.
The default output is private. Publication requires an explicit --publish.
"""
import argparse
import csv
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import shutil

from curate_failed_release_endurance import archive, zipped
from verify_release_records import (
    BITSTREAM, BUILD_ID, check_plan, counts, load, require, sha, text_hash,
    unit_names, verify_package, verify_unit,
)

ROOT = Path(__file__).resolve().parent.parent


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8', newline='\n')


def csv_bytes(runs):
    fields = list(dict.fromkeys(key for run in runs for key in run))
    buffer = io.StringIO(newline='')
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    writer.writerows(runs)
    return buffer.getvalue().encode('utf-8')


def completed_receipt_gate(execution_dir):
    """Fail before reading evolving job data or creating any output."""
    raw = (execution_dir / 'execution.json').read_bytes()
    execution = json.loads(raw)
    require(execution['state'] == 'PASS' and execution['utc_finished'] is not None,
            'Recovered parent is not actually complete; no archive written')
    receipt = load(execution_dir / 'native_receipt.json')
    require(receipt['actual_exit_code'] == 0 and receipt['session_id'] == 78344 and
            receipt['tool_chunk_id'] and receipt['execution_sha256_bytes'] == sha(raw) and
            execution['utc_finished'] <= receipt['observed_utc'] and
            len(execution['stages']) == 1 and execution['stages'][0]['actual_exit_code'] == 0,
            'Genuine successful recovered native/stage receipt required')
    return raw, execution


def prepare(phase, output):
    old_execution_dir = ROOT / 'build/release_board_t32_execution'
    new_execution_dir = ROOT / 'build/release_board_t32_recovered_execution'
    raw_root = ROOT / 'build/release_board_t32_recovered_measurements'
    old_root = ROOT / 'build/release_board_t32_measurements'
    recovery_dir = ROOT / 'build/release_t32_standby_recovery'
    build_dir = ROOT / 'build/gemm_release_p8_t32_1mbaud'
    static_dir = ROOT / 'results/ddr_overlap/release_1mbaud/t32'

    execution_raw, execution = completed_receipt_gate(new_execution_dir)
    original_raw = (old_execution_dir / 'execution.json').read_bytes()
    original = json.loads(original_raw)
    original_native_raw = (old_execution_dir / 'native_receipt.json').read_bytes()
    original_native = json.loads(original_native_raw)
    require(original['state'] == 'FAIL' and [s['actual_exit_code'] for s in original['stages']] == [0, 0, 0, 1] and
            original_native['actual_exit_code'] == 1 and original_native['session_id'] == 17347 and
            original_native['tool_chunk_id'] == '679880' and
            original_native['execution_sha256_bytes'] == sha(original_raw), 'Original failure must remain recorded')
    build_raw, plan_raw = (build_dir / 'build.json').read_bytes(), (raw_root / 'plan.json').read_bytes()
    build, plan = json.loads(build_raw), json.loads(plan_raw)
    check_plan(plan['plan'])
    require(build['result'] == 'PASS' and build['build_id'] == BUILD_ID and
            sha((build_dir / build['bitstream']).read_bytes()) == build['bitstream_sha256'] == BITSTREAM and
            plan['manifest_sha256_bytes'] == execution['manifest_sha256_bytes'] == sha(build_raw),
            'Current exact image identity differs')
    parent_raw = (raw_root / 'summary.json').read_bytes()
    parent = json.loads(parent_raw)
    all_units = ['maximum', 'endurance'] + unit_names('benchmark')
    require(sha(parent_raw) == execution['summary_sha256_bytes'] and parent['result'] == 'PASS' and
            parent['source_hashes_unchanged'] is True and parent['all_requested_release_phases_complete'] is True and
            parent['full_grid_benchmark_complete'] is True and
            parent['available_completed_units'] == execution['available_completed_units'] == all_units,
            'Recovered parent did not finish all requested phases')
    static_raw = (static_dir / 'manifest.json').read_bytes()
    static = json.loads(static_raw)
    require(static['result'] == 'PASS' and all(sha((static_dir / name).read_bytes()) == digest
            for name, digest in static['saved_artifact_sha256_bytes'].items()), 'Own static archive changed')
    require((static_dir / 'build.json').read_bytes() == build_raw, 'Own static image manifest differs')
    expected_sources = dict(build['source_sha256_utf8_lf'], **plan['host_source_sha256_utf8_lf'])
    sources = {name: (ROOT / name).read_bytes() for name in expected_sources}
    require(len(sources) == 38 and all(text_hash(sources[name]) == digest for name, digest in expected_sources.items()),
            'Frozen build/host inputs changed')
    source_archive, source_inventory = archive(sources)

    units = unit_names(phase)
    originals, metrics, combined, original_file_hashes = {}, {}, [], {}
    for unit in units:
        files = {p.relative_to(raw_root / unit).as_posix(): p.read_bytes()
                 for p in (raw_root / unit).rglob('*') if p.is_file()}
        metric, runs, report = verify_unit(files, unit, plan, build)
        metrics[unit] = metric
        combined.extend(dict(unit=unit, **run) for run in runs)
        for name, raw in files.items():
            originals[unit + '/' + name] = raw
            original_file_hashes[unit + '/' + name] = sha(raw)
    record_archive, record_inventory = archive(originals)
    totals = counts(combined)
    aggregate = dict(schema_version=1, result='PASS', phase=phase, units=units,
                     build_id=BUILD_ID, runs=combined, checked_counts=totals)
    summary = dict(schema_version=1, result='PASS', phase=phase, units=units, checked_counts=totals,
                   unit_metrics=metrics, full_grid_benchmark_complete=phase == 'benchmark',
                   parent_full_qualification_complete=True, raw_output_byte_replay=False,
                   continuous_duration_carried_from_interrupted_run=False)

    require(not output.exists() and output.is_relative_to(ROOT / 'build'), 'Fresh private output directory required')
    output.mkdir(parents=True)
    def copy(name, original_path):
        destination = output / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(original_path.read_bytes())
    for name, raw in (
        ('execution.json', execution_raw), ('original_execution.json', original_raw),
        ('original_native_receipt.json', original_native_raw), ('build.json', build_raw), ('plan.json', plan_raw),
        ('parent_summary.json', parent_raw), ('static_archive_manifest.json', static_raw),
        ('records.tar.gz', record_archive), ('sources.tar.gz', source_archive)):
        (output / name).write_bytes(raw)
    for name, path in (
        ('native_receipt.json', new_execution_dir / 'native_receipt.json'),
        ('board_method.py', ROOT / 'build/run_release_board_t32_recovered.py'),
        ('original_board_method.py', ROOT / 'build/run_release_board_t32.py'),
        ('keep_awake.py', ROOT / 'scripts/keep_awake.py'),
        ('static_review_record.json', static_dir / 'routed/static/record.json'),
        ('program_log.txt', build_dir / 'program.log'),
        ('verify.py', ROOT / 'build/verify_release_records.py'),
        ('curation_method.py', Path(__file__)),
        ('recovery/record.json', recovery_dir / 'record.json'),
        ('recovery/native_receipt.json', recovery_dir / 'native_receipt.json'),
        ('recovery/method.py', ROOT / 'build/inspect_after_standby.py'),
        ('recovery/standby_events.json', old_execution_dir / 'standby_events.txt'),
        ('recovery/failed_job.json', old_root / 'endurance/job_000384/job.json'),
        ('recovery/case.json', old_root / 'endurance/case_000380.json'),
        ('reused_units/maximum_seal.json', old_root / 'maximum/seal.json'),
        ('reused_units/dense_seal.json', old_root / 'benchmark/case_11_256x256x256/seal.json')):
        copy(name, path)
    for name in ('a', 'bt', 'c'):
        copy('recovery/' + name + '_after.bin.gz', recovery_dir / (name + '_after.bin.gz'))
    for prefix, directory, stages in (('original_', old_execution_dir, original['stages']),
                                       ('', new_execution_dir, execution['stages'])):
        for stage in stages:
            raw = (directory / (stage['name'] + '_console.txt')).read_bytes()
            require(sha(raw) == stage['console_sha256_bytes'], 'Actual execution console changed')
            destination = output / (prefix + 'execution_logs') / (stage['name'] + '_console.txt.gz')
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(zipped(raw))
    save(output / 'archive_inventory.json', record_inventory)
    save(output / 'source_inventory.json', source_inventory)
    save(output / 'results.json', aggregate)
    (output / 'results.csv').write_bytes(csv_bytes(combined))
    save(output / 'summary.json', summary)
    headline = ('Continuous mixed workload in both modes' if phase == 'endurance' else
                'Complete 16-case matched benchmark grid')
    (output / 'README.md').write_text(f'''# T32 1 Mbaud {phase}

{headline} on build `0x9d4beb4d`, P8/T32/READ4 at 100 MHz.
The completed units contain {totals['completed_jobs']:,} jobs and
{totals['compared_outputs']:,} full INT32 output comparisons. Every job initializes
C afresh and checks the entire guarded A, BT and C allocations.

The original qualification stopped after a register-read timeout. A Windows
Modern Standby wake event shortly beforehand supports host sleep interruption
as a likely cause. Its actual native exit 1 and stage failure remain
preserved. Read-only reconnect verified retained job 384 without reset,
reprogramming or replaying START. The new endurance began from zero in one
connection under temporary Windows sleep inhibition; interrupted duration was
never carried forward. The actual recovered parent and child exits were 0.
Previously sealed maximum and dense units were copied unchanged and were not
rerun. Their original times and seal identities remain explicit.

Inputs stay resident in DDR between the paired samples for each prepared case.
Reported JOB_CYCLES includes all accelerator DDR transfers and final completion.
Packing, oracle construction and initial input upload are recorded once per case;
C initialization, configuration, job wall time and full-allocation download are
recorded per job. Validation time is separate. COMPUTE_CYCLES counts scheduled
microtiles; JOB_CYCLES minus COMPUTE_CYCLES is remaining job time, not a measured
DDR bandwidth or isolated load/store partition.

`records.tar.gz` preserves all original unit seals, case/job records and aggregate
JSON/CSV bytes. `results.json`, `results.csv` and `summary.json` are independently
derived aggregates. No per-job raw C or UART stream was retained for these phases,
so replay validates metadata, digests, exact counters, seeds, pairing and execution
provenance. It cannot reproduce those original numerical comparisons from bytes.
The separate read-only recovery includes its three retained DDR snapshots and
supports a narrow independent 64-output and guard replay. No warm-reset or DDR
electrical qualification is claimed.

```text
python -O verify.py --check-current --repo PATH_TO_CHECKOUT
python -O verify.py --extract-records PATH_TO_FRESH_DIRECTORY
```

These commands are software-only and never contact hardware. The general public
qualification runner remains `scripts/qualify_release.py`; the preserved method
snapshots document the exact measured execution and recovery history.
''', encoding='utf-8', newline='\n')
    manifest = dict(schema_version=1, result='PASS', kind='t32_1mbaud_' + phase + '_board', phase=phase,
        build_id=BUILD_ID, bitstream_sha256_bytes=BITSTREAM, checked_counts=totals,
        source_sha256_utf8_lf=build['source_sha256_utf8_lf'],
        static_archive_manifest_sha256_bytes=sha(static_raw),
        utc_curated=datetime.now(timezone.utc).isoformat(), hardware_accessed_by_curator=False,
        saved_artifact_sha256_bytes={p.relative_to(output).as_posix(): sha(p.read_bytes())
         for p in output.rglob('*') if p.is_file()})
    save(output / 'manifest.json', manifest)
    verified = verify_package(output, True, ROOT)
    require((new_execution_dir / 'execution.json').read_bytes() == execution_raw and
            (old_execution_dir / 'execution.json').read_bytes() == original_raw and
            all(sha((raw_root / name).read_bytes()) == digest for name, digest in original_file_hashes.items()) and
            all(text_hash((ROOT / name).read_bytes()) == digest for name, digest in expected_sources.items()),
            'Original records, executions or sources changed during curation')
    return verified


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=('endurance', 'benchmark'), required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--publish', action='store_true')
    args = parser.parse_args()
    output = (args.output or ROOT / ('build/release_t32_' + args.phase + '_archive_20261004')).resolve()
    result = prepare(args.phase, output)
    if args.publish:
        destination = ROOT / 'results/ddr_overlap/release_1mbaud/board/t32' / args.phase
        require(not destination.exists(), 'Public archive already exists; never overwrite evidence')
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(output), str(destination))
        output = destination
        verify_package(output, True, ROOT)
    print(json.dumps(dict(result='PASS', phase=args.phase, output=str(output), published=args.publish,
                          checked_counts=result['checked_counts'],
                          manifest_sha256_bytes=sha((output / 'manifest.json').read_bytes())), indent=2))


if __name__ == '__main__':
    main()
