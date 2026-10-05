"""Run the T8 comparison only after a separate operator-confirmed cold start."""
import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
from scripts.program_ddr_gemm import qualified_manifest
from scripts.qualify_release import source_hashes, sha
from scripts.build_ddr_gemm import input_hashes

parser = argparse.ArgumentParser()
parser.add_argument('--operator-confirmation', required=True)
args = parser.parse_args()
if args.operator_confirmation != 'Fresh OFF/ON complete; powered on and connected':
    raise RuntimeError('The separate actual T8 cold-start confirmation is required')
prior_path = ROOT / 'build/release_board_t32_recovered_execution/execution.json'
prior = json.loads(prior_path.read_text(encoding='utf-8'))
native = json.loads((prior_path.parent / 'native_receipt.json').read_text(encoding='utf-8'))
if prior['state'] != 'PASS' or any(item['actual_exit_code'] != 0 for item in prior['stages']):
    raise RuntimeError('T32 must complete and release COM11 before the T8 image is loaded')
if native['actual_exit_code'] != 0 or native['execution_sha256_bytes'] != sha(prior_path):
    raise RuntimeError('Actual T32 native completion receipt required')

def utc():
    return datetime.now(timezone.utc).isoformat()

manifest_path = ROOT / 'build/gemm_release_p8_t8_1mbaud/build.json'
execution = ROOT / 'build/release_board_t8_execution'
results = ROOT / 'build/release_board_t8_measurements'
smoke = ROOT / 'build/release_board_t8_smoke'
if any(path.exists() for path in (execution, results, smoke)):
    raise RuntimeError('Fresh execution and measurement directories required')
manifest = qualified_manifest(manifest_path)
review_path = manifest_path.parent / 'release_reset_review/record.json'
review = json.loads(review_path.read_text(encoding='utf-8'))
if (manifest['build_id'] != 0xeed7b111 or manifest['baud'] != 1000000 or
        input_hashes() != manifest['source_sha256_utf8_lf'] or
        review['actual_exit_code'] != 0 or review['result'] != 'PASS_PROVISIONAL_REVIEW' or
        not review['same_board_reset_endpoints_as_qualified_serial']):
    raise RuntimeError('Exact reviewed T8 image/source required')
for name, digest in review['saved_artifact_sha256_bytes'].items():
    if sha(review_path.parent / name) != digest:
        raise RuntimeError('Own-checkpoint review artifact changed: ' + name)
frozen_sources = source_hashes()
if frozen_sources['scripts/qualify_release.py'] != 'fdfe9d020e3b89e565fbfb6e3246a0a93f4ba1184138c83a7c44d28dc5af7381':
    raise RuntimeError('Reviewed release runner changed')
execution.mkdir()
record = dict(state='RUNNING', utc_started=utc(), utc_finished=None,
    operator_confirmation=args.operator_confirmation,
    operator_confirmation_source='Separate actual user reply after T32 qualification completed',
    startup_scope='Reported cold power cycle, JP1 JTAG, J6 connected; no reset or reprogramming after successful T8 image load',
    qualification_scope='T8 MODE0 baseline: full 16-case grid with 30 samples; no T8 maximum-shape or endurance claim',
    port='COM11', baud=manifest['baud'], build_id=manifest['build_id'],
    bitstream_sha256_bytes=manifest['bitstream_sha256'],
    manifest_sha256_bytes=sha(manifest_path), review_record_sha256_bytes=sha(review_path),
    source_sha256_utf8_lf=frozen_sources, helper_sha256_bytes=sha(Path(__file__)),
    t32_execution_sha256_bytes=sha(prior_path),
    keep_awake_source_sha256_bytes=sha(ROOT / 'scripts/keep_awake.py'),
    python=sys.version, packages={name:importlib.metadata.version(name) for name in ('numpy','pyserial')},
    stages=[])

def save():
    temporary = execution / 'execution.json.tmp'
    temporary.write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8', newline='\n')
    temporary.replace(execution / 'execution.json')

def run(name, command):
    if source_hashes() != frozen_sources or input_hashes() != manifest['source_sha256_utf8_lf']:
        raise RuntimeError('Frozen sources changed before ' + name)
    item = dict(name=name, command=command, utc_started=utc(), utc_finished=None, actual_exit_code=None)
    record['stages'].append(item)
    save()
    print('Starting ' + name, flush=True)
    console = execution / (name + '_console.txt')
    with console.open('w', encoding='utf-8') as stream:
        result = subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT,
                                creationflags=subprocess.CREATE_NO_WINDOW)
    item.update(actual_exit_code=result.returncode, utc_finished=utc(), console_sha256_bytes=sha(console))
    save()
    if result.returncode != 0:
        raise RuntimeError(name + ' failed; retain results, no retry/reset')
    print('PASS ' + name + '; actual exit 0', flush=True)

save()
try:
    run('program', [sys.executable, str(ROOT / 'scripts/program_ddr_gemm.py'), '--manifest', str(manifest_path)])
    run('smoke', [sys.executable, '-m', 'host.gemm', '--port', 'COM11', '--manifest', str(manifest_path),
        '--mode', '0', '--m', '5', '--n', '3', '--k', '9', '--repeats', '1', '--retries', '0',
        '--output', str(smoke)])
    base = [sys.executable, str(ROOT / 'scripts/keep_awake.py'), '--',
        sys.executable, str(ROOT / 'scripts/qualify_release.py'), '--port', 'COM11',
        '--manifest', str(manifest_path), '--output', str(results), '--modes', '0', '--oracle', 'numpy',
        '--samples', '30', '--duration', '1800', '--seed', '20261004']
    run('dense_benchmark', base + ['--phase', 'benchmark', '--cases', '11'])
    record['dense_benchmark_result_sha256_bytes'] = sha(results / 'benchmark/case_11_256x256x256/results.json')
    save()
    run('full_benchmark', base + ['--phase', 'benchmark', '--resume'])
    summary = json.loads((results / 'summary.json').read_text(encoding='utf-8'))
    if summary['result'] != 'PASS' or not summary['full_grid_benchmark_complete']:
        raise RuntimeError('Incomplete T8 MODE0 full benchmark grid')
    if source_hashes() != frozen_sources or sha(manifest_path) != record['manifest_sha256_bytes']:
        raise RuntimeError('Host or build identity changed during measurement')
    record.update(state='PASS', summary_sha256_bytes=sha(results / 'summary.json'),
                  available_completed_units=summary['available_completed_units'])
except (Exception, KeyboardInterrupt) as error:
    record.update(state='FAIL', error=f'{type(error).__name__}: {error}')
    print(record['error'], file=sys.stderr, flush=True)
finally:
    record['utc_finished'] = utc()
    save()
raise SystemExit(0 if record['state'] == 'PASS' else 1)
