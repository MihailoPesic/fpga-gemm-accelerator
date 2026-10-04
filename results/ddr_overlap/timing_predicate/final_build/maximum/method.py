"""Record the existing public maximum-shape CLI's actual native execution."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
from host.gemm import GEMM
from host.gemm.__main__ import host_source_hashes
from scripts.program_ddr_gemm import qualified_manifest
from build_ddr_gemm import input_hashes

def utc():
    return datetime.now(timezone.utc).isoformat()

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

out = ROOT / 'build/release_current_maximum'
if out.exists():
    raise RuntimeError('Choose a fresh output directory; previous evidence is preserved')
manifest_path = ROOT / 'build/gemm_p8_overlap_final/build.json'
manifest = qualified_manifest(manifest_path)
if manifest['build_id'] != 0x2c680af7 or input_hashes() != manifest['source_sha256_utf8_lf']:
    raise RuntimeError('Exact current qualified image and source required')
out.mkdir()
before = host_source_hashes()
command = [sys.executable, '-m', 'host.gemm', '--port', 'COM11', '--manifest', str(manifest_path),
           '--mode', '1', '--m', '1024', '--n', '1024', '--k', '256', '--repeats', '1',
           '--seed', '20261004', '--retries', '0', '--output', str(out / 'maximum')]
record = dict(state='RUNNING', utc_started=utc(), utc_finished=None, actual_exit_code=None,
              command=command, manifest_sha256_bytes=sha(manifest_path),
              source_sha256_utf8_lf=before, helper_sha256_bytes=sha(Path(__file__)),
              build_id=manifest['build_id'], bitstream_sha256_bytes=manifest['bitstream_sha256'],
              startup_scope='Continues the previously qualified powered session after fresh read-only UART identity/readiness checks; no reset or programming')

def save():
    (out / 'execution.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8', newline='\n')

save()
try:
    device = GEMM.open('COM11', manifest['baud'], retries=0)
    try:
        record['preflight_identity'] = device.identify(manifest)
        device.wait_ready()
        record['preflight_transport'] = dict(retries=device.link.retry_count, rejected_frames=device.link.decoder.rejected, poisoned=device.link.poisoned)
    finally:
        device.close()
    save()
    print('Identity/readiness PASS; starting MODE1 1024x1024x256 full comparison', flush=True)
    with (out / 'console.txt').open('w', encoding='utf-8') as stream:
        result = subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT,
                                creationflags=subprocess.CREATE_NO_WINDOW)
    record['actual_exit_code'] = result.returncode
    record['console_sha256_bytes'] = sha(out / 'console.txt')
    save()
    if result.returncode != 0:
        raise RuntimeError('Maximum test failed; inspect retained results before any reset')
    report = json.loads((out / 'maximum/results.json').read_text(encoding='utf-8'))
    if not report['passed'] or len(report['runs']) != 1 or report['runs'][0]['compared_elements'] != 1024 * 1024:
        raise RuntimeError('Maximum test did not complete every output comparison')
    if report['transport'] != dict(retries=0, rejected_frames=0):
        raise RuntimeError('Maximum test transport was not error-free')
    if host_source_hashes() != before or sha(manifest_path) != record['manifest_sha256_bytes']:
        raise RuntimeError('Host/manifest identity changed during test')
    record.update(state='PASS', results_sha256_bytes=sha(out / 'maximum/results.json'),
                  csv_sha256_bytes=sha(out / 'maximum/results.csv'))
    print('PASS maximum: 1,048,576 outputs; full input/padding/guards checked', flush=True)
except (Exception, KeyboardInterrupt) as exc:
    record.update(state='FAIL', error=f'{type(exc).__name__}: {exc}')
    print(record['error'], file=sys.stderr, flush=True)
finally:
    record['utc_finished'] = utc()
    save()
raise SystemExit(0 if record['state'] == 'PASS' else 1)

