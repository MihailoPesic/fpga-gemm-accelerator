"""Replay a completed controlled comparison from two sealed grid packages.

This is software-only. Original job records carry numerical-check receipts,
not retained matrix bytes or UART frames. Figures are bound to their actual
collector execution and input records; the verifier checks their numerical
source data, not a second rendering of every pixel.
"""
import argparse
import csv
from datetime import datetime
import gzip
import hashlib
import importlib.util
import io
import json
import math
from pathlib import Path, PurePosixPath
import statistics
import sys
import tarfile

sys.dont_write_bytecode = True
GRID = tuple((m, m, k) for m in (32, 64, 128, 256) for k in (16, 64, 256)) + (
    (31, 33, 17), (65, 63, 255), (1, 64, 256), (64, 1, 256))
COUNTERS = ('job_cycles', 'compute_cycles', 'read_beats', 'write_beats',
            'write_valid_bytes', 'input_wait_cycles', 'read_stall_cycles', 'write_stall_cycles')
TIMES = ('core_seconds', 'useful_gops', 'useful_utilization', 'resident_host_seconds',
         'c_initialize_seconds', 'configure_seconds', 'job_wall_seconds',
         'allocation_download_seconds', 'validation_seconds')
PREPARATION = ('preparation_seconds', 'oracle_seconds', 'input_upload_seconds')
FIGURES = tuple(stem + '.' + suffix for stem in
                ('useful_gops', 'transfer_volume', 'tail_latency') for suffix in ('png', 'pdf'))
IDENTITIES = {
    't8': (8, 0xEED7B111, '21845beeb677bc8c0445231646cefb6e3633bdcf58a2d8c0566bcceaf44d6f37', 480),
    't32': (32, 0x9D4BEB4D, 'd187f51bac37c3682641243f1db112579ad14c8ac45a1b3df52e704bf58c8327', 960)}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def load(path):
    return json.loads(Path(path).read_bytes())


def normalized(raw):
    return sha(raw.decode('utf-8').replace('\r\n', '\n').replace('\r', '\n').encode())


def stats(values):
    require(values and all(type(x) in (int, float) and math.isfinite(x) and x >= 0 for x in values),
            'Non-finite, negative or absent measurements')
    return dict(minimum=min(values), median=statistics.median(values), maximum=max(values))


def timestamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def unpack(directory):
    inventory = load(directory / 'archive_inventory.json')
    encoded = (directory / 'records.tar.gz').read_bytes()
    raw = gzip.decompress(encoded)
    require((len(encoded), sha(encoded), len(raw), sha(raw)) == (
        inventory['archive_bytes'], inventory['archive_sha256_bytes'],
        inventory['tar_bytes'], inventory['tar_sha256_bytes']), 'Original record archive identity differs')
    result = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:') as archive:
        for item in archive.getmembers():
            path = PurePosixPath(item.name)
            require(item.isfile() and item.name not in result and item.name and
                    not path.is_absolute() and path.as_posix() == item.name and
                    '\\' not in item.name and ':' not in item.name and
                    all(part not in ('.', '..') for part in path.parts), 'Unsafe/duplicate archive entry')
            result[item.name] = archive.extractfile(item).read()
    require(set(result) == set(inventory['members']) and len(result) == inventory['original_files'] and
            sum(map(len, result.values())) == inventory['raw_bytes'], 'Original archive inventory differs')
    for name, raw in result.items():
        require((len(raw), sha(raw)) == (inventory['members'][name]['bytes'],
                inventory['members'][name]['sha256_bytes']), 'Original archived bytes differ: ' + name)
    return result


def grid_package(directory, key, check_current=False, repo=None):
    """Run its sealed, hardware-free verifier before using any job records."""
    directory = Path(directory).resolve()
    package = load(directory / 'manifest.json')
    require(package['result'] == 'PASS' and package['phase'] == 'benchmark', 'Completed benchmark package required')
    require(sha((directory / 'verify.py').read_bytes()) == package['saved_artifact_sha256_bytes']['verify.py'],
            'Grid verifier source seal differs')
    spec = importlib.util.spec_from_file_location('comparison_grid_' + key, directory / 'verify.py')
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    result = verifier.verify_package(directory, check_current, repo)
    tile, build_id, bitstream, jobs = IDENTITIES[key]
    require(result['result'] == 'PASS' and result['phase'] == 'benchmark' and result['units'] == 16 and
            result['checked_counts']['completed_jobs'] == jobs, 'Grid does not contain all required jobs')
    build = load(directory / 'build.json')
    require((build['result'], build['p'], build['t'], build['kmax'], build['core_hz'],
             build['read_slots'], build['baud'], build['version'], build['enable_overlap'],
             build['build_id'], build['bitstream_sha256']) ==
            ('PASS', 8, tile, 256, 100000000, 4, 1000000, 0x200, True, build_id, bitstream),
            'Exact comparison image differs')
    plan = load(directory / 'plan.json')
    require(plan['plan']['modes'] == ([0] if key == 't8' else [0, 1]) and
            plan['plan']['samples'] == 30 and plan['plan']['seed'] == 20261004 and
            plan['plan']['oracle'] == 'numpy', 'Exact benchmark plan differs')
    execution, native = load(directory / 'execution.json'), load(directory / 'native_receipt.json')
    require(execution['state'] == 'PASS' and native['actual_exit_code'] == 0 and
            native['execution_sha256_bytes'] == sha((directory / 'execution.json').read_bytes()) and
            all(stage['actual_exit_code'] == 0 for stage in execution['stages']),
            'Actual completed parent exits required')
    return dict(directory=directory, package=package, build=build, plan=plan,
                records=unpack(directory), manifest_sha256_bytes=sha((directory / 'manifest.json').read_bytes()))


def traffic(shape, tile):
    m, n, k = shape
    reads = writes = compute = 0
    for i in range(0, m, tile):
        r = min(tile, m - i)
        for j in range(0, n, tile):
            c = min(tile, n - j)
            reads += (r + c) * len(range(0, k, 8))
            writes += r * len(range(0, c, 2))
            compute += len(range(0, r, 8)) * len(range(0, c, 8)) * (k + 23)
    return dict(read_beats=reads, write_beats=writes, write_valid_bytes=4*m*n, compute_cycles=compute)


def cases(item):
    result = {}
    for index, shape in enumerate(GRID):
        unit = 'benchmark/case_%02d_%dx%dx%d' % ((index,) + shape)
        report = json.loads(item['records'][unit + '/results.json'])
        preparation = json.loads(item['records'][unit + '/case_000000.json'])
        require(report['manifest'] == item['build'] and report['transport'] ==
                dict(retries=0, rejected_frames=0, poisoned=False) and report['source_hashes_unchanged'] is True,
                'Case source/image/transport differs')
        wanted_order = [(sample, mode) for sample in range(30) for mode in
                        (item['plan']['plan']['modes'] if sample % 2 == 0 else
                         list(reversed(item['plan']['plan']['modes'])))]
        require([(r['sample'], r['mode']) for r in report['runs']] == wanted_order,
                'Missing/duplicate/reordered sample')
        for run in report['runs']:
            require(run['passed'] is True and run['state'] == 'PASS' and
                    tuple(run[name] for name in ('m', 'n', 'k')) == shape and
                    run['seed'] == 20261004 + 9973*index and run['job_cycles'] > 0,
                    'Case numerical receipt/shape/seed differs')
            require(all(run[field] == value for field, value in traffic(shape, item['build']['t']).items()),
                    'Independent tile traffic/schedule enumeration differs')
            hz, cycles = run['core_hz'], run['job_cycles']
            for field, value in (('core_seconds', cycles/hz),
                    ('useful_gops', 2*math.prod(shape)*hz/cycles/1e9),
                    ('useful_utilization', math.prod(shape)/(64*cycles))):
                require(math.isclose(run[field], value, rel_tol=1e-12, abs_tol=1e-12),
                        'Derived run arithmetic differs: ' + field)
        result[index] = dict(unit=unit, report=report, preparation=preparation,
            seal_sha256_bytes=sha(item['records'][unit + '/seal.json']),
            results_sha256_bytes=sha(item['records'][unit + '/results.json']))
    return result


def verify_data(result, t8, t32):
    require(result['result'] == 'PASS' and result['kind'] == 'controlled_gemm_reuse_overlap_comparison' and
            result['full_grid_complete'] is True and result['common_cases'] == 16 and
            result['completed_case_indexes'] == list(range(16)) and result['missing_case_indexes'] == [],
            'Only a completed 16-case controlled comparison may be published')
    require(result['measurement_scope'] == 'DDR-resident job counters through final successful result write response; host transfer/validation times remain separate' and
            result['comparison_scope'] == 'A to B changes T8 to T32 in MODE0; B to C changes only MODE on the same T32 bitstream' and
            result['traffic_scope'] == 'Accepted AXI beats and WSTRB bytes; excludes PHY granularity, command overhead and host traffic',
            'Collector measurement/traffic scope differs')
    for field in ('p', 'core_hz', 'baud', 'read_slots', 'part', 'id', 'version', 'kind',
                  'enable_overlap', 'configuration_sha256_utf8_lf', 'source_sha256_utf8_lf'):
        require(t8['build'][field] == t32['build'][field], 'A/B/C changes more than T/MODE: ' + field)
    require(len(t8['build']['source_sha256_utf8_lf']) == 32 and
            len(t8['plan']['host_source_sha256_utf8_lf']) == 6 and
            t8['plan']['host_source_sha256_utf8_lf'] == t32['plan']['host_source_sha256_utf8_lf'],
            'Exact core32/host6 source bindings required')
    expected_plans = {key: sha(item['directory'].joinpath('plan.json').read_bytes())
                      for key, item in (('t8', t8), ('t32', t32))}
    require(result['plan_sha256_bytes'] == expected_plans, 'Collector plans differ from sealed grids')
    data = {'t8': cases(t8), 't32': cases(t32)}
    require(len(result['series']) == 48 and len(result['comparisons']) == 16 and
            len(result['input_provenance']) == 32, 'Incomplete comparison tables')
    for index, shape in enumerate(GRID):
        selected, reference = {}, None
        for offset, (letter, key, mode, label) in enumerate(
                (('A', 't8', 0, 'T8, serial'), ('B', 't32', 0, 'T32, serial'), ('C', 't32', 1, 'T32, overlap'))):
            item, case = (t8 if key == 't8' else t32), data[key][index]
            runs = [r for r in case['report']['runs'] if r['mode'] == mode]
            signature = [(r['sample'], r['seed'], r['input_a_sha256_bytes'], r['raw_b_sha256_bytes'],
                r['oracle_sha256_bytes'], r['c_before_sha256_bytes'],
                {k: v for k, v in r['descriptor'].items() if k != 'mode'}) for r in runs]
            if reference is None:
                reference = signature
            require(signature == reference and len(runs) == 30, 'Inputs/layout/C sentinels differ across A/B/C')
            metrics = {field: stats([r[field] for r in runs]) for field in COUNTERS + TIMES}
            metrics['accepted_axi_beat_bytes'] = stats([8*(r['read_beats']+r['write_beats']) for r in runs])
            metrics['read_plus_useful_write_bytes'] = stats([8*r['read_beats']+r['write_valid_bytes'] for r in runs])
            entry = result['series'][3*index + offset]
            wanted = dict(case_index=index, m=shape[0], n=shape[1], k=shape[2], series=letter, label=label,
                mode=mode, tile=item['build']['t'], samples=30, build_id=item['build']['build_id'],
                bitstream_sha256=item['build']['bitstream_sha256'], metrics=metrics,
                resident_input_preparation={field: case['preparation'][field] for field in PREPARATION},
                residency_scope='One A/BT upload before all samples and modes in this sealed case; C is initialized for every job',
                raw_counters=[{field: r[field] for field in COUNTERS} for r in runs])
            require(entry == wanted, 'Collector series/distributions/raw counters differ')
            selected[letter] = metrics
        wanted = dict(case_index=index, shape=list(shape),
            reuse_cycle_ratio=selected['A']['job_cycles']['median']/selected['B']['job_cycles']['median'],
            overlap_cycle_ratio=selected['B']['job_cycles']['median']/selected['C']['job_cycles']['median'],
            reuse_input_byte_ratio=selected['A']['read_beats']['median']/selected['B']['read_beats']['median'],
            transfer_byte_ratio=selected['A']['read_plus_useful_write_bytes']['median']/selected['B']['read_plus_useful_write_bytes']['median'])
        require(result['comparisons'][index] == wanted, 'Collector ratio of cycle medians/traffic differs')
        for offset, (label, key) in enumerate((('T8', 't8'), ('T32', 't32'))):
            provenance, case = result['input_provenance'][2*index+offset], data[key][index]
            require(provenance['case_index'] == index and provenance['series_build'] == label and
                provenance['seal_sha256_bytes'] == case['seal_sha256_bytes'] and
                provenance['results_sha256_bytes'] == case['results_sha256_bytes'] and
                provenance['path'].replace('\\', '/').endswith('/' + case['unit']),
                'Collector original unit provenance differs')
    return dict(completed_jobs=1440, common_cases=16, series=48, samples_per_series=30)


def verify_package(directory, t8_path, t32_path, check_current=False, repo=None):
    directory = Path(directory).resolve()
    manifest = load(directory / 'manifest.json')
    actual = {p.relative_to(directory).as_posix(): sha(p.read_bytes()) for p in directory.rglob('*')
              if p.is_file() and p != directory / 'manifest.json'}
    require(manifest['result'] == 'PASS' and manifest['kind'] == 'controlled_1mbaud_grid_comparison' and
            actual == manifest['saved_artifact_sha256_bytes'], 'Comparison package seal differs')
    t8, t32 = (grid_package(path, key, check_current, repo)
               for path, key in ((t8_path, 't8'), (t32_path, 't32')))
    require(manifest['input_package_manifest_sha256_bytes'] ==
            dict(t8=t8['manifest_sha256_bytes'], t32=t32['manifest_sha256_bytes']), 'Referenced grid package seals differ')
    result = load(directory / 'collector/comparison.json')
    counts = verify_data(result, t8, t32)
    require(manifest['checked_counts'] == counts, 'Comparison scope/counts differ')
    collector = load(directory / 'collector/manifest.json')
    require(collector['result'] == 'PASS' and collector['full_grid_complete'] is True and
            collector['missing_case_indexes'] == [] and collector['input_provenance'] == result['input_provenance'] and
            collector['matplotlib'] == '3.10.6' and collector['numpy'] == '2.3.3', 'Collector result/tool versions differ')
    saved = collector['saved_artifact_sha256_bytes']
    require(set(saved) == {'comparison.json', 'comparison.csv', *FIGURES} and
            all(sha((directory / 'collector' / name).read_bytes()) == digest for name, digest in saved.items()),
            'Original collector figure/table bytes differ')
    for name in FIGURES:
        raw = (directory / 'collector' / name).read_bytes()
        require(raw.startswith(b'\x89PNG\r\n\x1a\n') if name.endswith('.png') else raw.startswith(b'%PDF-'),
                'Figure format differs: ' + name)
    rows = []
    for item in result['series']:
        row = {field: item[field] for field in ('case_index', 'm', 'n', 'k', 'series', 'label', 'mode',
                                               'tile', 'samples', 'build_id', 'bitstream_sha256')}
        for metric, values in item['metrics'].items():
            row.update({metric + '_' + statistic: value for statistic, value in values.items()})
        rows.append(row)
    reader = csv.DictReader(io.StringIO((directory / 'collector/comparison.csv').read_bytes().decode('utf-8')))
    require(reader.fieldnames == list(rows[0]) and list(reader) ==
            [{field: str(value) for field, value in row.items()} for row in rows], 'Collector JSON/CSV differ')
    source = (directory / 'collect_benchmarks.py').read_bytes()
    require(sha(source) == result['collector_sha256_bytes'], 'Exact plot/table collector source differs')
    execution, native = load(directory / 'execution.json'), load(directory / 'native_receipt.json')
    require(execution['state'] == 'PASS' and execution['actual_exit_code'] == 0 and
            native['actual_exit_code'] == 0 and native['execution_sha256_bytes'] ==
            sha((directory / 'execution.json').read_bytes()), 'Actual collector outer/child PASS receipts required')
    require((native['session_id'] is None or
             (type(native['session_id']) is int and native['session_id'] > 0)) and
            isinstance(native['tool_chunk_id'], str) and native['tool_chunk_id'] and
            timestamp(native['observed_utc']) >= timestamp(execution['utc_finished']),
            'Completed native chunk and post-exit observation required; session must be null or an actual positive ID')
    require(execution['collector_sha256_bytes'] == sha(source) and execution['input_package_manifest_sha256_bytes'] ==
            manifest['input_package_manifest_sha256_bytes'] and execution['collector_manifest_sha256_bytes'] ==
            sha((directory / 'collector/manifest.json').read_bytes()) and execution['console_sha256_bytes'] ==
            sha(gzip.decompress((directory / 'console.txt.gz').read_bytes())) and
            execution['helper_sha256_bytes'] == sha((directory / 'collection_method.py').read_bytes()),
            'Collector invocation/input/output/console receipts differ')
    requirements = (directory / 'requirements-benchmark.txt').read_bytes()
    pins = dict(line.split('==') for line in requirements.decode('utf-8').splitlines()
                if line and not line.startswith('#'))
    require(sha(requirements) == execution['requirements_benchmark_sha256_bytes'] and
            execution['package_versions'] == pins and
            execution['python'].split()[0] == collector['python'] and
            execution['hardware_accessed'] is False and manifest['hardware_accessed'] is False and
            execution['verifier_sha256_bytes'] == sha((directory / 'verify.py').read_bytes()) and
            execution['source_sha256_utf8_lf'] == t32['build']['source_sha256_utf8_lf'] and
            execution['host_source_sha256_utf8_lf'] == t32['plan']['host_source_sha256_utf8_lf'],
            'Actual collector software/source versions differ')
    bindings = load(directory / 'input_packages.json')
    require(bindings == {key: dict(manifest_sha256_bytes=item['manifest_sha256_bytes'],
        public_path='results/ddr_overlap/release_1mbaud/board/' + key + '/benchmark',
        build_id=item['build']['build_id'], bitstream_sha256_bytes=item['build']['bitstream_sha256'])
        for key, item in (('t8', t8), ('t32', t32))}, 'Grid package references differ')
    command = execution['command']
    require(len(command) == 9 and command[1] == '-B' and
            command.count('--t8') == command.count('--t32') == command.count('--output') == 1 and
            command[2].replace('\\', '/').endswith('/scripts/collect_benchmarks.py') and
            command[command.index('--t8') + 1].replace('\\', '/').endswith('/inputs/t8') and
            command[command.index('--t32') + 1].replace('\\', '/').endswith('/inputs/t32') and
            command[command.index('--output') + 1].replace('\\', '/').endswith('/collector') and
            '--allow-partial' not in command and '--table-only' not in command,
            'Executed collector must include the full grid and all six figure files')
    require(timestamp(execution['utc_started']) <= timestamp(result['utc_recorded']) <=
            timestamp(execution['utc_finished']), 'Comparison lies outside actual collector execution')
    console = gzip.decompress((directory / 'console.txt.gz').read_bytes()).decode('utf-8')
    require('PASS: 16/16 common cases; 6 figures;' in console and 'FAIL:' not in console,
            'Completed full-figure collector console required')
    if check_current:
        require(repo is not None and (Path(repo) / 'scripts/collect_benchmarks.py').read_bytes() == source,
                'Current plot/table collector source differs')
    return dict(result='PASS', **counts, scope='Sealed job records, exact parent receipts, matched signatures, counters and collector artifacts; no raw output-byte or UART replay')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--t8', type=Path, required=True, help='Sealed T8 benchmark package')
    parser.add_argument('--t32', type=Path, required=True, help='Sealed T32 benchmark package')
    parser.add_argument('--check-current', action='store_true')
    parser.add_argument('--repo', type=Path)
    args = parser.parse_args()
    require(not args.check_current or args.repo is not None, '--check-current requires --repo')
    print(json.dumps(verify_package(Path(__file__).resolve().parent, args.t8, args.t32,
                                  args.check_current, args.repo), indent=2))


if __name__ == '__main__':
    main()
