"""Verify the Linux proof archive and optionally extract its exact tool logs."""
import argparse
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import re


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


def expected_queries():
    names = []
    for tile in (8, 32):
        names.extend(f'queue_{tile}_{case}' for case in
                     ('base', 'induction', 'full', 'wrap', 'stalls', 'stop_drain'))
    for mode in (0, 1):
        names.extend(f'scheduler_{mode}_{case}' for case in
                     ('safety', 'success', 'held', 'fault_drain') + (('overlap',) if mode else ()))
    return names


def verify(area):
    area = Path(area).resolve()
    manifest = json.loads((area / 'manifest.json').read_text(encoding='utf-8'))
    require(manifest['result'] == 'PASS' and manifest['hardware_accessed'] is False,
            'Archive does not report the expected software-only PASS')
    actual = {p.relative_to(area).as_posix() for p in area.rglob('*')
              if p.is_file() and '__pycache__' not in p.parts}
    require(actual == set(manifest['files']) | {'manifest.json'}, 'Archive file inventory changed')
    for name, record in manifest['files'].items():
        path = (area / name).resolve()
        path.relative_to(area)
        payload = path.read_bytes()
        require(len(payload) == record['bytes'] and digest(payload) == record['sha256_bytes'],
                f'Artifact changed: {name}')
    raw_logs = {}
    for record in manifest['compressed_logs']:
        require(Path(record['raw_name']).name == record['raw_name'], 'Invalid raw log name')
        require(record['raw_name'] not in raw_logs, 'Duplicate raw log')
        stored = (area / record['stored_path']).read_bytes()
        require(len(stored) == record['stored_bytes'] and digest(stored) == record['stored_sha256_bytes'],
                f'Compressed log changed: {record["stored_path"]}')
        payload = gzip.decompress(stored)
        require(len(payload) == record['raw_bytes'] and digest(payload) == record['raw_sha256_bytes'],
                f'Decompressed log changed: {record["raw_name"]}')
        raw_logs[record['raw_name']] = payload
    require(set(raw_logs) == {name + '.log' for name in ['tool_version'] + expected_queries()},
            'Expected exactly 22 tool logs')
    summary_bytes = (area / 'summary.json').read_bytes()
    summary = json.loads(summary_bytes)
    execution_bytes = (area / 'execution.json').read_bytes()
    execution = json.loads(execution_bytes)
    native_bytes = (area / 'execution/native_tool_completion.json').read_bytes()
    native = json.loads(native_bytes)
    require(digest(summary_bytes) == manifest['original_summary_sha256_bytes'] and
            digest(execution_bytes) == manifest['actual_execution_sha256_bytes'] and
            digest(native_bytes) == manifest['native_completion_sha256_bytes'], 'Receipt identity changed')
    require(summary['result'] == 'PASS' and summary['passed'] is True and summary['queries'] == 21 and
            summary['hardware_accessed'] is False and summary['source_hashes_unchanged'] is True,
            'Incomplete actual proof summary')
    require(execution['result'] == 'PASS' and execution['actual_exit_code'] == 0 and
            execution['queries'] == 21 and execution['decoded_witnesses'] == 15 and
            execution['source_hashes_unchanged'] is True and execution['hardware_accessed'] is False,
            'Incomplete actual WSL execution')
    require(native['actual_exit_code'] == 0 and native['tool_completion']['exit_code'] == 0,
            'Native wrapper did not actually exit successfully')
    require(execution['summary_sha256_bytes'] == digest(summary_bytes), 'Execution/summary mismatch')
    require(digest((area / 'execution/wrapper.py').read_bytes()) == execution['wrapper_sha256_bytes'],
            'Actual execution wrapper snapshot changed')
    sources = summary['source_hashes_before']
    require(len(sources) == 8 and sources == summary['source_hashes_after'], 'Source set changed during proof')
    require(execution['source_sha256_bytes_before'] == execution['source_sha256_bytes_after'] ==
            {name: record['bytes'] for name, record in sources.items()}, 'Execution/source identity mismatch')
    for name, record in sources.items():
        payload = (area / 'inputs' / name).read_bytes()
        normalized = payload.decode('utf-8').replace('\r\n', '\n').encode('utf-8')
        require(digest(payload) == record['bytes'] and digest(normalized) == record['utf8_lf'],
                f'Input snapshot changed: {name}')
    require(summary['versions'] == execution['versions'] and
            summary['versions']['yosys'].startswith('Yosys 0.69 (git sha1 9f75ca1f9,'), 'Unexpected actual tool version')
    pins = dict(line.split('==', 1) for line in (area / 'inputs/requirements-formal.txt').read_text().splitlines()
                if line and not line.startswith('#'))
    require(summary['versions']['packages'] == pins, 'Actual tool packages do not match pinned inputs')
    commands = summary['commands']
    require([c['name'] for c in commands] == ['tool_version'] + expected_queries(), 'Query set/order changed')
    for command in commands:
        require(command['passed'] is True and command['exit_code'] == 0, f'Failed query: {command["name"]}')
        log = raw_logs[command['log']]
        require(digest(log) == command['log_sha256_bytes'], f'Command/log mismatch: {command["name"]}')
        if command['name'] == 'tool_version':
            continue
        script = (area / command['script']).read_bytes()
        require(digest(script) == command['script_sha256_bytes'], f'Command/script mismatch: {command["name"]}')
        assertions = 37 if command['kind'] == 'queue' else 36 if command['configuration'] == 0 else 33
        text = log.decode('utf-8')
        counts = re.findall(r'^\s*(\d+)\s+\$assert\s*$', text, re.MULTILINE)
        require(command['assertions'] == assertions and counts and int(counts[-1]) == assertions,
                f'Assertion set mismatch: {command["name"]}')
        problems = re.findall(r'Found and reported (\d+) problems\.', text)
        require(problems and all(int(count) == 0 for count in problems), 'Undriven/conflicting proof logic')
        marker = ('SAT proof finished - model found: FAIL!' if command['target'] else
                  'Induction step proven: SUCCESS!' if command['induction'] else
                  'SAT proof finished - no model found: SUCCESS!')
        require(marker in text, f'Actual SAT result missing: {command["name"]}')
        if not command['target']:
            imports = text.count('Import proof for assert:')
            expected = assertions * (command['induction_length'] + 1 if command['induction'] else command['bound'])
            require(imports == command['assertion_imports'] == expected, 'Assertion import count mismatch')
            if command['induction']:
                require(command['reset_low_all_step_frames'] is True and '-set-at 1 rst 1' not in script.decode('utf-8'),
                        'Induction step is not reset independent')
    stages = execution['executions']
    require([s['name'] for s in stages] == ['inventory', 'create_environment', 'install_pins', 'package_check', 'formal_queries'],
            'Actual WSL stage set changed')
    for stage in stages:
        require(stage['actual_exit_code'] == 0, f'Failed actual WSL stage: {stage["name"]}')
        for suffix, key in (('.sh', 'script_sha256_bytes'), ('.stdout.txt', 'stdout_sha256_bytes'),
                            ('.stderr.txt', 'stderr_sha256_bytes')):
            require(digest((area / 'execution' / (stage['name'] + suffix)).read_bytes()) == stage[key],
                    f'Actual stage receipt mismatch: {stage["name"]}{suffix}')
    for name, expected in execution['saved_query_artifact_sha256_bytes'].items():
        payload = raw_logs[name] if name in raw_logs else (area / name).read_bytes()
        require(digest(payload) == expected, f'Original output receipt mismatch: {name}')
    decoder_path = area / 'inputs/scripts/formal_buffers_witness.py'
    spec = importlib.util.spec_from_file_location('archived_buffer_witness', decoder_path)
    decoder = importlib.util.module_from_spec(spec)
    # Compile bytes directly: importing the archive must not create generated files.
    exec(compile(decoder_path.read_bytes(), str(decoder_path), 'exec'), decoder.__dict__)
    require(len(summary['witnesses']) == 15, 'Expected 15 reachable witnesses')
    for witness in summary['witnesses']:
        replay = decoder.review_witness(area / witness['vcd'], area / witness['model'],
                                       32, witness['kind'], witness['configuration'], witness['target'])
        require(replay == witness, f'Witness replay mismatch: {witness["vcd"]}')
    tables = '\n\n'.join(w['vcd'] + '\n' + w['cycle_table'] for w in summary['witnesses']) + '\n'
    require((area / 'witness_cycles.txt').read_bytes() == tables.encode('utf-8'), 'Witness cycle tables changed')
    return manifest, raw_logs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--extract-logs', type=Path, help='fresh directory inside repository build/')
    args = parser.parse_args()
    area = Path(__file__).resolve().parent
    try:
        output = args.extract_logs.resolve() if args.extract_logs else None
        if output:
            output.relative_to(area.parents[2] / 'build')
            require(not output.exists(), 'Log output already exists; choose a fresh directory')
        manifest, raw_logs = verify(area)
        if output:
            output.mkdir(parents=True)
            for name, payload in raw_logs.items():
                (output / name).write_bytes(payload)
        print(f'PASS {len(manifest["files"])} artifact hashes; 22 exact tool logs; 21 queries; 15 replayed witnesses')
        return 0
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, gzip.BadGzipFile) as error:
        print(f'FAIL {type(error).__name__}: {error}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
