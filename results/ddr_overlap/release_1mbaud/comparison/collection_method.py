"""Collect two completed, sealed grids without contacting the board."""
import argparse
from datetime import datetime, timezone
from importlib.metadata import version
import json
from pathlib import Path
import subprocess
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'build'))
from verify_release_comparison import grid_package, require, sha


def utc():
    return datetime.now(timezone.utc).isoformat()


def save(path, value):
    pending = path.with_name(path.name + '.tmp')
    pending.write_bytes((json.dumps(value, indent=2) + '\n').encode())
    pending.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--t8', type=Path, required=True)
    parser.add_argument('--t32', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='Fresh directory under workspace build/')
    args = parser.parse_args()
    output = args.output.resolve()
    require(output.is_relative_to((ROOT / 'build').resolve()) and not output.exists(),
            'Fresh ignored workspace build/ output required')
    collector = ROOT / 'scripts/collect_benchmarks.py'
    requirements = ROOT / 'requirements-benchmark.txt'
    before = sha(collector.read_bytes())
    packages = {key: grid_package(path, key, True, ROOT)
                for key, path in (('t8', args.t8), ('t32', args.t32))}
    require(packages['t8']['build']['source_sha256_utf8_lf'] == packages['t32']['build']['source_sha256_utf8_lf'] and
            packages['t8']['plan']['host_source_sha256_utf8_lf'] == packages['t32']['plan']['host_source_sha256_utf8_lf'],
            'Core32 and host6 sources must match before collection')
    pins = dict(line.split('==') for line in requirements.read_text(encoding='utf-8').splitlines()
                if line and not line.startswith('#'))
    versions = {name: version(name) for name in pins}
    require(versions == pins, 'Install the exact optional benchmark requirements in the selected environment')
    output.mkdir(parents=True)
    record = dict(state='RUNNING', utc_started=utc(), utc_finished=None, hardware_accessed=False,
        scope='Completed 480-job T8 MODE0 and 960-job T32 MODE0/1 grids; three figures in PNG and PDF formats',
        input_package_paths={key: str(item['directory']) for key, item in packages.items()},
        input_package_manifest_sha256_bytes={key: item['manifest_sha256_bytes'] for key, item in packages.items()},
        collector_sha256_bytes=before, requirements_benchmark_sha256_bytes=sha(requirements.read_bytes()),
        source_sha256_utf8_lf=packages['t32']['build']['source_sha256_utf8_lf'],
        host_source_sha256_utf8_lf=packages['t32']['plan']['host_source_sha256_utf8_lf'],
        helper_sha256_bytes=sha(Path(__file__).read_bytes()),
        verifier_sha256_bytes=sha((ROOT / 'build/verify_release_comparison.py').read_bytes()),
        python=sys.version, package_versions=versions, command=None, actual_exit_code=None)
    save(output / 'execution.json', record)
    try:
        for key, item in packages.items():
            extracted = output / 'inputs' / key
            extracted.mkdir(parents=True)
            (extracted / 'plan.json').write_bytes((item['directory'] / 'plan.json').read_bytes())
            for name, raw in item['records'].items():
                target = extracted / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
        command = [sys.executable, '-B', str(collector), '--t8', str(output / 'inputs/t8'),
                   '--t32', str(output / 'inputs/t32'), '--output', str(output / 'collector')]
        record['command'] = command
        save(output / 'execution.json', record)
        print('Starting software-only full-grid collector and three figures in PNG and PDF formats', flush=True)
        with (output / 'console.txt').open('w', encoding='utf-8', newline='\n') as stream:
            child = subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
        record['actual_exit_code'] = child.returncode
        record['console_sha256_bytes'] = sha((output / 'console.txt').read_bytes())
        save(output / 'execution.json', record)
        require(child.returncode == 0, 'Collector failed; preserve its actual exit and log')
        manifest_path = output / 'collector/manifest.json'
        manifest = json.loads(manifest_path.read_bytes())
        require(manifest['result'] == 'PASS' and manifest['full_grid_complete'] is True and
                manifest['missing_case_indexes'] == [] and len(manifest['saved_artifact_sha256_bytes']) == 8,
                'Collector did not complete all tables and six figures')
        require(sha(collector.read_bytes()) == before and sha(requirements.read_bytes()) ==
                record['requirements_benchmark_sha256_bytes'] and
                sha(Path(__file__).read_bytes()) == record['helper_sha256_bytes'] and
                sha((ROOT / 'build/verify_release_comparison.py').read_bytes()) == record['verifier_sha256_bytes'],
                'Collection methods changed while executing')
        for key, item in packages.items():
            fresh = grid_package(item['directory'], key, True, ROOT)
            require(fresh['manifest_sha256_bytes'] == item['manifest_sha256_bytes'],
                    'Input package changed while executing')
        record.update(state='PASS', collector_manifest_sha256_bytes=sha(manifest_path.read_bytes()))
    except (Exception, KeyboardInterrupt) as error:
        record.update(state='FAIL', error=dict(type=type(error).__name__, message=str(error)))
        print('FAIL: ' + record['error']['type'] + ': ' + record['error']['message'], file=sys.stderr, flush=True)
    finally:
        record['utc_finished'] = utc()
        save(output / 'execution.json', record)
    print(json.dumps(record, indent=2))
    return 0 if record['state'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
