"""Continue the qualified powered session only after its maximum test passes."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
from scripts.program_ddr_gemm import qualified_manifest
from scripts.qualify_release import source_hashes, sha, verify_sealed, canonical_hash
from build_ddr_gemm import input_hashes


def utc():
    return datetime.now(timezone.utc).isoformat()


out = ROOT / 'build/release_current_endurance_execution'
results = ROOT / 'build/release_current_endurance'
maximum = ROOT / 'build/release_current_maximum/execution.json'
manifest_path = ROOT / 'build/gemm_p8_overlap_final/build.json'
if out.exists() or results.exists():
    raise RuntimeError('Fresh endurance output directories required')
manifest = qualified_manifest(manifest_path)
if manifest['build_id'] != 0x2c680af7 or input_hashes() != manifest['source_sha256_utf8_lf']:
    raise RuntimeError('Exact current qualified source/image required')
frozen_sources = source_hashes()
if frozen_sources['scripts/qualify_release.py'] != 'fdfe9d020e3b89e565fbfb6e3246a0a93f4ba1184138c83a7c44d28dc5af7381':
    raise RuntimeError('Reviewed release runner changed')
out.mkdir()
command = [sys.executable, str(ROOT / 'scripts/qualify_release.py'), '--port', 'COM11',
           '--manifest', str(manifest_path), '--output', str(results), '--phase', 'endurance',
           '--modes', 'both', '--oracle', 'numpy', '--duration', '1800']
record = dict(state='WAITING_FOR_MAXIMUM', utc_started=utc(), utc_finished=None,
              actual_exit_code=None, command=command, source_sha256_utf8_lf=frozen_sources,
              manifest_sha256_bytes=sha(manifest_path), helper_sha256_bytes=sha(Path(__file__)),
              build_id=manifest['build_id'], bitstream_sha256_bytes=manifest['bitstream_sha256'],
              startup_scope='Same qualified powered session; no reset or programming; maximum process must first exit successfully')


def save():
    temporary = out / 'execution.json.tmp'
    temporary.write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8', newline='\n')
    temporary.replace(out / 'execution.json')


save()
try:
    while True:
        previous = json.loads(maximum.read_text(encoding='utf-8'))
        if previous['state'] == 'FAIL':
            raise RuntimeError('Maximum test failed; endurance must not access COM11')
        if previous['state'] == 'PASS':
            if previous['actual_exit_code'] != 0 or previous['utc_finished'] is None:
                raise RuntimeError('Maximum lacks its successful actual exit/finish')
            break
        if previous['state'] != 'RUNNING':
            raise RuntimeError('Unexpected maximum state')
        time.sleep(2)
    if source_hashes() != frozen_sources or input_hashes() != manifest['source_sha256_utf8_lf']:
        raise RuntimeError('Source changed before endurance')
    record.update(state='RUNNING', maximum_execution_sha256_bytes=sha(maximum),
                  test_utc_started=utc())
    save()
    print('Maximum actual exit PASS; starting uninterrupted mixed MODE0/1 endurance', flush=True)
    with (out / 'console.txt').open('w', encoding='utf-8') as stream:
        result = subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT,
                                creationflags=subprocess.CREATE_NO_WINDOW)
    record.update(actual_exit_code=result.returncode, console_sha256_bytes=sha(out / 'console.txt'))
    save()
    if result.returncode != 0:
        raise RuntimeError('Endurance failed; retain results and do not reset')
    binding = json.loads((results / 'plan.json').read_text(encoding='utf-8'))
    unit_binding = dict(plan_sha256=canonical_hash(binding['plan']),
        manifest_sha256_bytes=binding['manifest_sha256_bytes'], build_id=binding['build_id'],
        bitstream_sha256=binding['bitstream_sha256'],
        host_source_sha256_utf8_lf=binding['host_source_sha256_utf8_lf'], unit='endurance')
    report = verify_sealed(results / 'endurance', unit_binding)
    if report['continuous_exercise_seconds'] < 1800 or set(run['mode'] for run in report['runs']) != {0, 1}:
        raise RuntimeError('Endurance duration/modes incomplete')
    if source_hashes() != frozen_sources or sha(manifest_path) != record['manifest_sha256_bytes']:
        raise RuntimeError('Execution source/manifest changed')
    record.update(state='PASS', completed_jobs=report['checked_counts']['completed_jobs'],
        compared_outputs=report['checked_counts']['compared_outputs'],
        continuous_exercise_seconds=report['continuous_exercise_seconds'],
        seal_sha256_bytes=sha(results / 'endurance/seal.json'))
    print('PASS endurance:', record['completed_jobs'], 'jobs;',
          record['continuous_exercise_seconds'], 'continuous host-paced seconds', flush=True)
except (Exception, KeyboardInterrupt) as error:
    record.update(state='FAIL', error=f'{type(error).__name__}: {error}')
    print(record['error'], file=sys.stderr, flush=True)
finally:
    record['utc_finished'] = utc()
    save()
raise SystemExit(0 if record['state'] == 'PASS' else 1)
