"""Curate the actual T8 MODE0 full-grid run only after genuine completion.

No serial, hardware, process launch, power or reset access. Private output is
the default; --publish is explicit and never overwrites an evidence package.
"""
import argparse
import csv
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import shutil

from curate_failed_release_endurance import archive, zipped
from verify_t8_release_records import (
    BUILD_ID, BITSTREAM, check_plan, counts, load, require, sha, text_hash,
    unit_names, verify, verify_unit,
)

ROOT = Path(__file__).resolve().parent.parent


def save(path,value):
    path.write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8',newline='\n')


def csv_bytes(runs):
    fields = list(dict.fromkeys(key for run in runs for key in run))
    buffer = io.StringIO(newline='')
    writer = csv.DictWriter(buffer,fieldnames=fields)
    writer.writeheader()
    writer.writerows(runs)
    return buffer.getvalue().encode()


def completion_gate(directory):
    """Fail before reading any job data or creating candidate/public output."""
    require((directory/'execution.json').is_file(), 'No actual T8 execution exists; no archive written')
    raw = (directory/'execution.json').read_bytes()
    execution = json.loads(raw)
    require(execution['state'] == 'PASS' and execution['utc_finished'] is not None,
            'T8 owning process has not actually completed; no archive written')
    native = load(directory/'native_receipt.json')
    require(native['actual_exit_code'] == 0 and type(native['session_id']) is int and native['session_id'] > 0 and
            native['tool_chunk_id'] and native['execution_sha256_bytes'] == sha(raw) and
            execution['utc_finished'] <= native['observed_utc'] and
            [stage['actual_exit_code'] for stage in execution['stages']] == [0,0,0,0],
            'T8 genuine native/four-stage success required')
    return raw,execution


def prepare(output):
    execution_dir = ROOT/'build/release_board_t8_execution'
    measurements = ROOT/'build/release_board_t8_measurements'
    build_dir = ROOT/'build/gemm_release_p8_t8_1mbaud'
    static_dir = ROOT/'results/ddr_overlap/release_1mbaud/t8'
    t32_dir = ROOT/'build/release_board_t32_recovered_execution'
    execution_raw,execution = completion_gate(execution_dir)
    t32_raw = (t32_dir/'execution.json').read_bytes()
    t32,t32_native = json.loads(t32_raw),load(t32_dir/'native_receipt.json')
    require(t32['state'] == 'PASS' and t32_native['actual_exit_code'] == 0 and t32_native['session_id'] == 78344 and
            t32_native['execution_sha256_bytes'] == execution['t32_execution_sha256_bytes'] == sha(t32_raw),
            'Exact T32 successful native precondition required')
    build_raw,plan_raw = (build_dir/'build.json').read_bytes(),(measurements/'plan.json').read_bytes()
    build,plan = json.loads(build_raw),json.loads(plan_raw)
    check_plan(plan['plan'])
    require(build['result'] == 'PASS' and build['build_id'] == BUILD_ID and
            sha((build_dir/build['bitstream']).read_bytes()) == build['bitstream_sha256'] == BITSTREAM and
            plan['manifest_sha256_bytes'] == execution['manifest_sha256_bytes'] == sha(build_raw), 'Exact T8 image differs')
    static_raw = (static_dir/'manifest.json').read_bytes()
    static = json.loads(static_raw)
    require(static['result'] == 'PASS' and all(sha((static_dir/name).read_bytes()) == digest
            for name,digest in static['saved_artifact_sha256_bytes'].items()) and
            (static_dir/'build.json').read_bytes() == build_raw, 'T8 own static archive changed')
    parent_raw = (measurements/'summary.json').read_bytes()
    parent = json.loads(parent_raw)
    require(sha(parent_raw) == execution['summary_sha256_bytes'] and parent['result'] == 'PASS' and
            parent['full_grid_benchmark_complete'] is True and parent['source_hashes_unchanged'] is True and
            parent['available_completed_units'] == execution['available_completed_units'] == unit_names(),
            'Actual T8 full-grid summary incomplete')
    expected_sources = dict(build['source_sha256_utf8_lf'],**plan['host_source_sha256_utf8_lf'])
    sources = {name:(ROOT/name).read_bytes() for name in expected_sources}
    require(len(sources) == 38 and all(text_hash(sources[name]) == digest for name,digest in expected_sources.items()),
            'T8 frozen source identity changed')
    source_archive,source_inventory = archive(sources)
    originals,metrics,combined = {},{},[]
    for unit in unit_names():
        files = {path.relative_to(measurements/unit).as_posix():path.read_bytes()
                 for path in (measurements/unit).rglob('*') if path.is_file()}
        metric,runs,_ = verify_unit(files,unit,plan,build)
        metrics[unit] = metric
        combined.extend(dict(unit=unit,**run) for run in runs)
        originals.update({unit+'/'+name:raw for name,raw in files.items()})
    require(len(originals) == 544 and len(combined) == 480, 'T8 omitted original grid units/samples')
    record_archive,record_inventory = archive(originals)
    totals = counts(combined)
    aggregate = dict(schema_version=1,result='PASS',phase='benchmark',units=unit_names(),
                     build_id=BUILD_ID,runs=combined,checked_counts=totals)
    summary = dict(schema_version=1,result='PASS',phase='benchmark',modes=[0],units=unit_names(),
        checked_counts=totals,unit_metrics=metrics,full_grid_benchmark_complete=True,
        raw_output_byte_replay=False,maximum_qualified=False,endurance_qualified=False,physical_mode1_qualified=False)
    require(not output.exists() and output.is_relative_to(ROOT/'build'), 'Fresh private output directory required')
    output.mkdir(parents=True)
    for name,raw in (
        ('execution.json',execution_raw),('t32_execution.json',t32_raw),('build.json',build_raw),('plan.json',plan_raw),
        ('parent_summary.json',parent_raw),('static_archive_manifest.json',static_raw),
        ('records.tar.gz',record_archive),('sources.tar.gz',source_archive)):
        (output/name).write_bytes(raw)
    for name,path in (
        ('native_receipt.json',execution_dir/'native_receipt.json'),
        ('t32_native_receipt.json',t32_dir/'native_receipt.json'),
        ('board_method.py',ROOT/'build/run_release_board_t8.py'),
        ('keep_awake.py',ROOT/'scripts/keep_awake.py'),
        ('static_review_record.json',static_dir/'routed/static/record.json'),
        ('program_log.txt',build_dir/'program.log'),
        ('verify.py',ROOT/'build/verify_t8_release_records.py'),
        ('curation_method.py',Path(__file__))):
        (output/name).write_bytes(path.read_bytes())
    (output/'execution_logs').mkdir()
    for stage in execution['stages']:
        raw = (execution_dir/(stage['name']+'_console.txt')).read_bytes()
        require(sha(raw) == stage['console_sha256_bytes'], 'T8 actual stage console changed')
        (output/'execution_logs'/(stage['name']+'_console.txt.gz')).write_bytes(zipped(raw))
    save(output/'archive_inventory.json',record_inventory)
    save(output/'source_inventory.json',source_inventory)
    save(output/'results.json',aggregate)
    (output/'results.csv').write_bytes(csv_bytes(combined))
    save(output/'summary.json',summary)
    (output/'README.md').write_text(f'''# T8 serial baseline at 1 Mbaud

Build `0xeed7b111`, P8/T8/READ4 at 100 MHz, runs the full 16-case grid in
serial MODE0. Each case has 30 checked samples: 480 jobs and
{totals['compared_outputs']:,} complete INT32 output comparisons. All A, BT and C
allocations are checked, including input bytes, row padding and guards.
This archive qualifies this MODE0 workload; it makes no T8 maximum-shape,
endurance or physical MODE1 claim.

The operator confirmed a separate cold power cycle after the T32 process had
completed with a genuine native exit 0. Programming, smoke, dense and full-grid
stages all completed with actual exit 0 on the exact source-matched image.
The dense case was measured by the first benchmark child; the full-grid child
skipped its intact seal and measured the remaining cases. Both child processes
start their JOB_ID sequence at 1, so IDs are interpreted with unit paths and
execution epochs rather than as a globally unique sequence.

Inputs stay resident in DDR for each prepared case. C is reinitialized before
every sample. JOB_CYCLES includes accelerator DDR transfers and the final write
acknowledgement; packing, oracle construction, initial input upload and per-job
host phases are recorded separately. Validation time is separate. Remaining job
cycles after COMPUTE_CYCLES are not isolated measured DDR load/store bandwidth.

`records.tar.gz` preserves 544 original files: each case record, all job records,
original JSON/CSV aggregates and original PASS seals. `results.json`,
`results.csv` and `summary.json` are independently derived readable aggregates.
There are no retained per-job C snapshots or UART stream, so the standalone
validator checks metadata, digests, counters, traffic/schedule arithmetic,
seeds, C sentinels, execution receipts and source/image identities. It cannot
replay the original numerical comparisons from saved matrix bytes. Warm-reset
and DDR electrical qualification remain outside this record.

```text
python -O verify.py --check-current --repo PATH_TO_CHECKOUT
python -O verify.py --extract-records PATH_TO_FRESH_DIRECTORY
```

Validation is software-only. The supported measurement interface remains
`scripts/qualify_release.py`; method snapshots retain this actual execution.
''',encoding='utf-8',newline='\n')
    manifest = dict(schema_version=1,result='PASS',kind='t8_1mbaud_mode0_benchmark_board',phase='benchmark',
        build_id=BUILD_ID,bitstream_sha256_bytes=BITSTREAM,checked_counts=totals,modes=[0],
        source_sha256_utf8_lf=build['source_sha256_utf8_lf'],static_archive_manifest_sha256_bytes=sha(static_raw),
        hardware_accessed_by_curator=False,utc_curated=datetime.now(timezone.utc).isoformat(),
        saved_artifact_sha256_bytes={path.relative_to(output).as_posix():sha(path.read_bytes())
         for path in output.rglob('*') if path.is_file()})
    save(output/'manifest.json',manifest)
    result = verify(output,True,ROOT)
    require((execution_dir/'execution.json').read_bytes() == execution_raw and
            all(sha((measurements/name).read_bytes()) == sha(raw) for name,raw in originals.items()) and
            all(text_hash((ROOT/name).read_bytes()) == digest for name,digest in expected_sources.items()),
            'T8 original data or frozen sources changed during curation')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'build/release_t8_benchmark_archive_20261004')
    parser.add_argument('--publish',action='store_true')
    args = parser.parse_args()
    output = args.output.resolve()
    result = prepare(output)
    if args.publish:
        destination = ROOT/'results/ddr_overlap/release_1mbaud/board/t8/benchmark'
        require(not destination.exists(), 'Public T8 archive already exists; never overwrite')
        destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.move(str(output),str(destination))
        output = destination
        verify(output,True,ROOT)
    print(json.dumps(dict(result='PASS',output=str(output),published=args.publish,
        checked_counts=result['checked_counts'],manifest_sha256_bytes=sha((output/'manifest.json').read_bytes())),indent=2))


if __name__ == '__main__':
    main()
