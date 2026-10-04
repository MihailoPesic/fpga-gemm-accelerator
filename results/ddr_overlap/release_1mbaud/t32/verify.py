"""Verify retained T32 build evidence without invoking Vivado or hardware."""

import argparse
import csv
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import tarfile


BUILD_ID = 0x9D4BEB4D
BITSTREAM = 'd187f51bac37c3682641243f1db112579ad14c8ac45a1b3df52e704bf58c8327'
REPORTS = ('timing.txt', 'check_timing.txt', 'utilization.txt', 'clocks.txt',
           'clock_interaction.txt', 'clock_utilization.txt', 'route.txt', 'drc.txt',
           'methodology.txt', 'cdc.txt', 'pulse_width.txt',
           'pulse_width_violations.txt', 'bus_skew.txt')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def load(directory, name):
    return json.loads((directory / name).read_bytes())


def safe_name(name):
    path = PurePosixPath(name)
    require(name and not path.is_absolute() and '\\' not in name and
            all(part not in ('.', '..') for part in path.parts), 'Unsafe archive path')


def text_hash(data):
    return sha(data.decode('utf-8').replace('\r\n', '\n').replace('\r', '\n').encode('utf-8'))


def expected_counters(m, n, k):
    reads = writes = compute = 0
    for i in range(0, m, 32):
        rows = min(32, m - i)
        for j in range(0, n, 32):
            cols = min(32, n - j)
            reads += (rows + cols) * len(range(0, k, 8))
            writes += rows * len(range(0, cols, 2))
            compute += len(range(0, rows, 8)) * len(range(0, cols, 8)) * (k + 23)
    return dict(read_beats=reads, write_beats=writes, compute_cycles=compute,
                write_bytes=4 * m * n)


def verify(directory, extract=None, check_current=False):
    manifest = load(directory, 'manifest.json')
    require(manifest['result'] == 'PASS' and manifest['physical_board_validated'] is False,
            'Unexpected evidence stage')
    actual = {p.relative_to(directory).as_posix(): sha(p.read_bytes())
              for p in directory.rglob('*') if p.is_file() and p != directory / 'manifest.json'}
    require(actual == manifest['saved_artifact_sha256_bytes'], 'Saved artifact inventory/hash mismatch')
    raw = {}
    for name, entry in manifest['raw_artifacts'].items():
        safe_name(name)
        safe_name(entry['saved_path'])
        stored = (directory / entry['saved_path']).read_bytes()
        require(entry['storage'] in ('raw', 'gzip'), 'Unknown raw artifact encoding')
        data = gzip.decompress(stored) if entry['storage'] == 'gzip' else stored
        require(len(data) == entry['bytes'] and sha(data) == entry['sha256_bytes'],
                'Original artifact bytes changed: ' + name)
        raw[name] = data
    build = json.loads(raw['build.json'])
    sim = json.loads(raw['vendor/simulation.json'])
    review = json.loads(raw['routed/static/record.json'])
    build_stages = load(directory, 'build_stages.json')
    review_stages = load(directory, 'review_stages.json')
    source_check = load(directory, 'source_verification.json')
    for record in (build, sim):
        require(record['result'] == 'PASS' and record['vivado_exit_code'] == 0 and
                record['source_hashes_unchanged'] is True and
                record['physical_board_validated'] is False and record['warm_reset_qualified'] is False,
                'Original build/simulation status mismatch')
        require((record['build_id'], record['part'], record['p'], record['t'], record['kmax'],
                 record['read_slots'], record['baud'], record['simulation_baud'], record['core_hz'],
                 record['version'], record['enable_overlap']) ==
                (BUILD_ID, 'xc7a50ticsg324-1L', 8, 32, 256, 4, 1000000, 10000000,
                 100000000, 0x200, True), 'Unexpected exact build configuration')
    require(build['bitstream_sha256'] == BITSTREAM == manifest['bitstream_sha256_bytes'] and
            build['simulation_sha256_bytes'] == sha(raw['vendor/simulation.json']) and
            build['source_sha256_utf8_lf'] == sim['source_sha256_utf8_lf'] and
            build['generated_sha256'] == sim['generated_sha256'], 'Build/simulation binding mismatch')
    require(source_check['result'] == 'PASS' and source_check['build_id'] == BUILD_ID and
            source_check['source_sha256_utf8_lf'] == build['source_sha256_utf8_lf'] and
            source_check['actual_bitstream_sha256_bytes'] == BITSTREAM,
            'Source/image verification receipt mismatch')
    for record, console in ((build, 'routed/bitstream_console.txt'),
                            (sim, 'vendor/sim_console.txt')):
        require(record['console_sha256_bytes'] == sha(raw[console]) and
                record['console_sha256_utf8_lf'] == text_hash(raw[console]), 'Console byte binding mismatch')
        require(record['configuration_sha256_utf8_lf'] == text_hash(raw['configuration/mig_axi.prj']),
                'MIG configuration binding mismatch')
    require(int(raw['vendor/sim_vivado_exit.txt']) == 0 and
            int(raw['routed/bitstream_vivado_exit.txt']) == 0, 'Actual Vivado exit mismatch')
    require(len(build_stages['stages']) == 2 and
            [stage['stage'] for stage in build_stages['stages']] == ['sim', 'bitstream'],
            'Missing completed T32 stages')
    for stage in build_stages['stages']:
        require(stage['tile'] == 32 and stage['actual_exit_code'] == 0 and
                stage['utc_started'] <= stage['utc_finished'], 'Build subprocess receipt mismatch')
        command = stage['command']
        for flag, value in (('--stage', stage['stage']), ('--p', '8'), ('--t', '32'),
                            ('--read-slots', '4'), ('--baud', '1000000')):
            require(command.count(flag) == 1 and command[command.index(flag) + 1] == value,
                    'Build invocation differs: ' + flag)
        require(command.count('--overlap') == 1, 'Selectable mode omitted from invocation')
    require(len(review_stages['stages']) == 1, 'Missing static review receipt')
    stage = review_stages['stages'][0]
    require(stage['tile'] == 32 and stage['build_id'] == BUILD_ID and stage['actual_exit_code'] == 0 and
            stage['manifest_sha256_bytes'] == sha(raw['build.json']) and
            stage['review_record_sha256_bytes'] == sha(raw['routed/static/record.json']),
            'Static review stage binding mismatch')
    require(review['result'] == 'PASS_PROVISIONAL_REVIEW' and review['actual_exit_code'] == 0 and
            review['hardware_accessed'] is False and review['production_sources_modified'] is False and
            review['final_image_qualified'] is False and review['bitstream_generated'] is False,
            'Static review scope/status mismatch')
    require(review['checkpoint_sha256_bytes_before'] == review['checkpoint_sha256_bytes_after'] ==
            stage['checkpoint_sha256_bytes'] == source_check['actual_checkpoint_sha256_bytes'],
            'Checkpoint identity changed')
    require(len(review['saved_artifact_sha256_bytes']) == 28, 'Incomplete original static review')
    for name, digest in review['saved_artifact_sha256_bytes'].items():
        require(sha(raw['routed/static/' + name]) == digest, 'Original review seal mismatch: ' + name)
    require(json.loads(raw['routed/static/execution.json'])['exit_code'] == 0 and
            int(raw['routed/static/exit.txt']) == 0 and
            review['same_board_reset_endpoints_as_qualified_serial'] is True and
            review['serial_reset_selector_sha256_bytes'] == sha(raw['routed/static/reset_selector.txt']) ==
            '676d17bd14fa24cd64e84035bedbf24509fe3286cadd256747d894a6d6133ee2',
            'Reset review receipt or baseline selector mismatch')
    require(set(build['physical_report_sha256_bytes']) == set(REPORTS), 'Missing routed report')
    for name in REPORTS:
        require(sha(raw['routed/' + name]) == build['physical_report_sha256_bytes'][name],
                'Original routed report seal mismatch: ' + name)

    source_inventory = load(directory, 'source_inventory.json')
    tar_bytes = gzip.decompress((directory / 'sources.tar.gz').read_bytes())
    require(sha(tar_bytes) == source_inventory['tar_sha256_bytes'] and
            len(tar_bytes) == source_inventory['tar_bytes'], 'Source tar mismatch')
    sources = {}
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode='r:') as stream:
        for member in stream.getmembers():
            safe_name(member.name)
            require(member.isfile() and member.name not in sources, 'Invalid source member')
            sources[member.name] = stream.extractfile(member).read()
    require(set(sources) == set(source_inventory['members']) == set(build['source_sha256_utf8_lf']) and
            len(sources) == 32, 'Source snapshot inventory mismatch')
    for name, data in sources.items():
        entry = source_inventory['members'][name]
        require(len(data) == entry['bytes'] and sha(data) == entry['sha256_bytes'] and
                text_hash(data) == build['source_sha256_utf8_lf'][name], 'Source snapshot mismatch: ' + name)
    if check_current:
        root = next(p for p in directory.parents if (p / 'rtl').is_dir() and (p / 'scripts').is_dir())
        for name, digest in build['source_sha256_utf8_lf'].items():
            require(text_hash((root / name).read_bytes()) == digest, 'Current source changed: ' + name)

    require(sim['calibration'] == 'FAST' and sim['cold_start_only'] is True and
            sim['physical_pcb_delays'] is False and sim['uart_simulation_matches_build_baud'] is False and
            sim['physical_uart_baud_qualified'] is False, 'Simulation limits changed')
    console = raw['vendor/sim_console.txt'].decode('utf-8')
    observed = []
    pattern = r'DDR_GEMM_JOB_PASS\s+job_id=(\d+)\s+m=(\d+)\s+n=(\d+)\s+k=(\d+)\s+outputs=(\d+)\s+job_cycles=(\d+)\s+compute_cycles=(\d+)\s+read_beats=(\d+)\s+write_beats=(\d+)\s+write_bytes=(\d+)'
    fields = ('job_id', 'm', 'n', 'k', 'outputs', 'job_cycles', 'compute_cycles',
              'read_beats', 'write_beats', 'write_bytes')
    for match in re.finditer(pattern, console):
        observed.append(dict(zip(fields, map(int, match.groups()))))
    require(observed == sim['jobs'] and [(job['m'], job['n'], job['k']) for job in observed] ==
            [(1, 1, 1), (5, 3, 9), (1, 33, 9)], 'Simulation jobs do not match console')
    for index, job in enumerate(observed, 1):
        require(job['job_id'] == index and job['outputs'] == job['m'] * job['n'] and
                job['job_cycles'] >= job['compute_cycles'] > 0, 'Invalid simulation job')
        for field, value in expected_counters(job['m'], job['n'], job['k']).items():
            require(job[field] == value, 'Independent simulated tile count mismatch: ' + field)
    final = re.findall(r'DDR_GEMM_FINAL_PASS\s+jobs=(\d+)\s+outputs=(\d+)\s+sim_baud=(\d+)\s+packets=(\d+)\s+read_beats=(\d+)\s+write_beats=(\d+)\s+write_responses=(\d+)', console)
    keys = ('jobs', 'outputs', 'sim_baud', 'packets', 'read_beats', 'write_beats', 'write_responses')
    require(len(final) == 1 and dict(zip(keys, map(int, final[0]))) ==
            sim['traffic_including_host_transfers'] and sum(job['outputs'] for job in observed) == 49,
            'Final simulated traffic mismatch')
    timing = raw['routed/timing.txt'].decode('utf-8').splitlines()
    index = next(i for i, line in enumerate(timing) if line.lstrip().startswith('WNS(ns)'))
    values = list(map(float, timing[index + 2].split()))
    require(values[0] == build['physical_checks']['wns_ns'] == 0.017 and
            values[4] == build['physical_checks']['whs_ns'] == 0.017 and
            values[1] == values[2] == values[5] == values[6] == values[9] == values[10] == 0,
            'Routed setup/hold/pulse summary mismatch')
    require('All user specified timing constraints are met.' in '\n'.join(timing),
            'Timing completion sentence absent')
    metrics = load(directory, 'metrics.json')
    utilization = raw['routed/utilization.txt'].decode('utf-8')
    for label, key in (('Slice LUTs', 'luts'), ('Slice Registers', 'flip_flops'),
                       ('Block RAM Tile', 'bram36_equivalents'), ('DSPs', 'dsps'),
                       ('Slice', 'slices'), ('Unique Control Sets', 'control_sets')):
        match = re.search(r'^\|\s*' + re.escape(label) + r'\s*\|\s*(\d+)\s*\|', utilization, re.M)
        require(match and int(match[1]) == metrics['resources'][key], 'Utilization mismatch: ' + key)
    require(metrics['wns_ns'] == metrics['whs_ns'] == 0.017 and
            metrics['bus_skew_checks'] == build['physical_checks']['bus_skew_checks'] == 10 and
            metrics['bus_skew_worst_slack_ns'] == build['physical_checks']['bus_skew_worst_slack_ns'] == 9.019,
            'Physical metric record mismatch')
    clocks = {match[1]: float(match[2]) for match in re.finditer(
        r'^(\S+)\s+(\d+\.\d+)\s+\{', raw['routed/clocks.txt'].decode('utf-8'), re.M)}
    require(clocks == metrics['clock_period_ns'] and len(clocks) == 22 and
            clocks['CLK100MHZ'] == clocks['clk_out1_axi_ddr_bd_clocks_0'] == 10.0 and
            clocks['clk_pll_i'] == 20.0, 'Clock metric record mismatch')
    interactions = [line for line in raw['routed/clock_interaction.txt'].decode('utf-8').splitlines()
                    if re.search(r'\s(?:Clean|Ignored)\s', line)]
    require(metrics['clock_interactions'] == dict(total_pairs=len(interactions),
            clean=sum('Clean' in line for line in interactions),
            ignored=sum('Ignored' in line for line in interactions),
            ignored_max_delay_datapath_only=sum('Ignored' in line and 'Max Delay Datapath Only' in line
                                               for line in interactions),
            ignored_false_path=sum('Ignored' in line and 'False Path' in line for line in interactions)),
            'Clock interaction metric mismatch')
    cdc = {match[1]: dict(severity=match[2], checks=int(match[3])) for match in re.finditer(
        r'^(CDC-\d+)\s+(\w+)\s+(\d+)\s', raw['routed/cdc.txt'].decode('utf-8'), re.M)}
    require(cdc == metrics['cdc'] and sum(item['checks'] for item in cdc.values()) == 363,
            'CDC metric record mismatch')
    for name in ('drc', 'methodology'):
        rules = {match[1]: dict(severity=match[2], checks=int(match[3])) for match in re.finditer(
            r'^\|\s*(\w+-\d+)\s*\|\s*(Warning|Advisory|Critical Warning|Error)\s*\|[^\n]*?\|\s*(\d+)\s*\|\s*$',
            raw['routed/' + name + '.txt'].decode('utf-8'), re.M)}
        require(rules == metrics['warning_rules'][name], 'Warning metric record mismatch: ' + name)
    reset_rows = list(csv.DictReader(io.StringIO(
        raw['routed/static/reset_inventory.tsv'].decode('utf-8')), delimiter='\t'))
    require(len(reset_rows) == metrics['reset_like_pins'] == review['counts']['reset_like_pins'] and
            sum(row['classification'] == 'ASYNC_SLICE_RESET' for row in reset_rows) ==
            metrics['async_reset_pins'] == review['counts']['async_reset_pins'] == 86,
            'Reset inventory metric mismatch')
    controls = {row['pin'] for row in reset_rows
                if row['classification'] == 'VENDOR_PRIMITIVE_RESET_CONTROL'}
    destinations = set()
    for name in ('vendor_reset_control_timing.txt', 'vendor_reset_control_ignored_timing.txt'):
        destinations.update(re.findall(r'^\s*Destination:\s+(\S+)',
                            raw['routed/static/' + name].decode('utf-8'), re.M))
    require(len(controls) == metrics['vendor_reset_control_pins'] == 101 and
            len(controls - destinations) == metrics['remaining_untimed_vendor_control_pins'] == 55,
            'Vendor control timing coverage metric mismatch')
    remaining_controls = controls - destinations
    require(sum(row['pin'] in remaining_controls and row['logic_value'] == 'zero'
                for row in reset_rows) == 18 and
            sum(row['pin'] in remaining_controls and row['logic_value'] == 'unknown'
                for row in reset_rows) == 37, 'Unreported vendor control split mismatch')
    ignored_resets = raw['routed/static/async_reset_ignored_timing.txt'].decode('utf-8')
    slacks = list(map(float, re.findall(r'Slack \(MET\)\s*:\s*([\d.-]+)ns',
                 raw['routed/static/async_reset_timing.txt'].decode('utf-8'))))
    require(ignored_resets.count('Timing Exception:       False Path') ==
            metrics['async_false_path_checks'] == 170 and
            len(set(re.findall(r'^\s*Destination:\s+(\S+)', ignored_resets, re.M))) ==
            metrics['async_false_path_endpoints'] == 85 and
            slacks == metrics['timed_async_reset_slacks_ns'] == [1.383, 16.474],
            'Async reset timing metric mismatch')
    if extract is not None:
        require(not extract.exists(), 'Extraction destination already exists')
        extract.mkdir(parents=True)
        for name, data in {**raw, **{'sources/' + key: value for key, value in sources.items()}}.items():
            target = extract / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    return dict(result='PASS', scope='Vendor simulation, routed reports and provisional static review; '
                'no physical 1 Mbaud or warm-reset qualification', build_id=f'0x{BUILD_ID:08x}',
                source_snapshots=len(sources), original_routed_reports=13, original_static_artifacts=28,
                simulated_jobs=3, simulated_outputs=49, wns_ns=0.017, whs_ns=0.017,
                current_sources_checked=check_current)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-current', action='store_true')
    parser.add_argument('--extract-raw', type=Path)
    args = parser.parse_args()
    directory = Path(__file__).resolve().parent
    extract = args.extract_raw.resolve() if args.extract_raw else None
    if extract is not None:
        root = next(p for p in directory.parents if (p / 'build').is_dir() and (p / 'rtl').is_dir())
        require(extract.is_relative_to(root / 'build'), 'Extract only into a fresh workspace build directory')
    print(json.dumps(verify(directory, extract, args.check_current), indent=2))


if __name__ == '__main__':
    main()
