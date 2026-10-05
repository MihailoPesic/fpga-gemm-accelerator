"""Preserve a completed software collector with referenced board-grid evidence."""
import argparse
from datetime import datetime, timezone
import gzip
import io
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'build'))
from verify_release_comparison import grid_package, load, require, sha, timestamp, verify_data, verify_package


def save(path, value):
    path.write_bytes((json.dumps(value, indent=2) + '\n').encode())


def zipped(raw):
    stream = io.BytesIO()
    with gzip.GzipFile(filename='', fileobj=stream, mode='wb', mtime=0, compresslevel=9) as encoded:
        encoded.write(raw)
    result = stream.getvalue()
    require(gzip.decompress(result) == raw, 'Lossless console compression failed')
    return result


def prepare(execution_directory, output):
    execution_directory, output = Path(execution_directory).resolve(), Path(output).resolve()
    require(output.is_relative_to((ROOT / 'build').resolve()) and not output.exists(),
            'Fresh private candidate under workspace build/ required')
    execution_raw = (execution_directory / 'execution.json').read_bytes()
    execution = json.loads(execution_raw)
    native_raw = (execution_directory / 'native_receipt.json').read_bytes()
    native = json.loads(native_raw)
    require(execution['state'] == 'PASS' and execution['actual_exit_code'] == 0 and
            native['actual_exit_code'] == 0 and native['execution_sha256_bytes'] == sha(execution_raw),
            'Actual completed child and native collector exits required')
    require((native['session_id'] is None or
             (type(native['session_id']) is int and native['session_id'] > 0)) and
            isinstance(native['tool_chunk_id'], str) and native['tool_chunk_id'] and
            timestamp(native['observed_utc']) >= timestamp(execution['utc_finished']),
            'Completed native chunk and post-exit observation required; session must be null or an actual positive ID')
    packages = {key: grid_package(path, key, True, ROOT)
                for key, path in execution['input_package_paths'].items()}
    require(set(packages) == {'t8', 't32'} and execution['input_package_manifest_sha256_bytes'] ==
            {key: item['manifest_sha256_bytes'] for key, item in packages.items()}, 'Input package receipts differ')
    collector_directory = execution_directory / 'collector'
    collector_raw = (collector_directory / 'manifest.json').read_bytes()
    collector_manifest = json.loads(collector_raw)
    require(sha(collector_raw) == execution['collector_manifest_sha256_bytes'], 'Original collector manifest differs')
    counts = verify_data(load(collector_directory / 'comparison.json'), packages['t8'], packages['t32'])
    source = ROOT / 'scripts/collect_benchmarks.py'
    method = ROOT / 'build/run_release_comparison.py'
    verifier = ROOT / 'build/verify_release_comparison.py'
    require(sha(source.read_bytes()) == execution['collector_sha256_bytes'] and
            sha(method.read_bytes()) == execution['helper_sha256_bytes'] and
            sha(verifier.read_bytes()) == execution['verifier_sha256_bytes'], 'Recorded collection method/source changed')
    console = (execution_directory / 'console.txt').read_bytes()
    require(sha(console) == execution['console_sha256_bytes'], 'Actual collector console differs')
    original = {name: (collector_directory / name).read_bytes()
                for name in ('manifest.json', *collector_manifest['saved_artifact_sha256_bytes'])}
    require(all(sha(original[name]) == digest for name, digest in
                collector_manifest['saved_artifact_sha256_bytes'].items()), 'Original collector output seal differs')
    output.mkdir(parents=True)
    (output / 'collector').mkdir()
    for name, raw in original.items():
        (output / 'collector' / name).write_bytes(raw)
    for name, raw in (
        ('execution.json', execution_raw), ('native_receipt.json', native_raw),
        ('console.txt.gz', zipped(console)), ('collect_benchmarks.py', source.read_bytes()),
        ('collection_method.py', method.read_bytes()), ('verify.py', verifier.read_bytes()),
        ('curation_method.py', Path(__file__).read_bytes()),
        ('requirements-benchmark.txt', (ROOT / 'requirements-benchmark.txt').read_bytes())):
        (output / name).write_bytes(raw)
    save(output / 'input_packages.json', {key: dict(manifest_sha256_bytes=item['manifest_sha256_bytes'],
        public_path='results/ddr_overlap/release_1mbaud/board/' + key + '/benchmark',
        build_id=item['build']['build_id'], bitstream_sha256_bytes=item['build']['bitstream_sha256'])
        for key, item in packages.items()})
    (output / 'README.md').write_text('''# Controlled operand reuse and overlap

P8 at 100 MHz, READ4 and 1 Mbaud. A uses T8/MODE0; B uses T32/MODE0;
C uses T32/MODE1. All 16 shapes contain 30 completed samples per series:
480 T8 jobs and 960 T32 jobs. B and C use one identical T32 bitstream.
Core32 and host6 source identities, shapes, seeds, A/B mathematical input
digests, descriptors and freshly initialized C sentinel digests match.

Three figures, each saved as PNG and PDF, and the CSV are original outputs
of the source-bound collector.
The collector's native completion receipt records its actual exit and tool
chunk. Its session ID is null when the command completed directly, or the
actual positive session ID when it yielded. Board grid receipts retain
their independently checked actual yielded session IDs.
Throughput uses useful operations divided by JOB_CYCLES through the final
successful DDR write response. Inputs remain resident between samples;
packing, input upload, C initialization, host transfer and validation times
are retained separately. Error bars show the minimum/maximum around each
series median. Speedup tables use a ratio of cycle medians, rather than
the median of per-sample speedups. Traffic counts accepted AXI beats and
useful write strobes; they do not measure physical DDR bus overhead.

The two referenced grid packages preserve every original case/job record,
seal and actual completed parent receipt. They are not duplicated here.
Their verifiers must pass before this verifier independently checks the
matched signatures, tile traffic, distributions, ratios and table data.
No per-job raw matrix bytes or UART transcript were retained for the
grid, so this replay does not reproduce its numerical output comparisons.
The T32 recovery package retains the earlier failed parent separately;
copied completed maximum/dense units are explicitly identified as reused.

From this directory, with both sibling grid packages available:

```text
python -B -O verify.py --t8 ../board/t8/benchmark --t32 ../board/t32/benchmark
```

The command is software-only. Source checks additionally require
`--check-current --repo PATH_TO_CHECKOUT`.
''', encoding='utf-8', newline='\n')
    manifest = dict(schema_version=1, result='PASS', kind='controlled_1mbaud_grid_comparison',
        checked_counts=counts, input_package_manifest_sha256_bytes=execution['input_package_manifest_sha256_bytes'],
        utc_curated=datetime.now(timezone.utc).isoformat(), hardware_accessed=False,
        saved_artifact_sha256_bytes={p.relative_to(output).as_posix(): sha(p.read_bytes())
                                    for p in output.rglob('*') if p.is_file()})
    save(output / 'manifest.json', manifest)
    result = verify_package(output, packages['t8']['directory'], packages['t32']['directory'], True, ROOT)
    require((execution_directory / 'execution.json').read_bytes() == execution_raw and
            (execution_directory / 'native_receipt.json').read_bytes() == native_raw and
            all((collector_directory / name).read_bytes() == raw for name, raw in original.items()),
            'Completed collection records changed during curation')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execution', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = prepare(args.execution, args.output)
    print(json.dumps(dict(**result, output=str(args.output.resolve()),
                         manifest_sha256_bytes=sha((args.output / 'manifest.json').read_bytes())), indent=2))


if __name__ == '__main__':
    main()
