"""Restore the qualified T32 image after a new confirmed cold start."""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from host.gemm import GEMM, pack_inputs, validate
from scripts.program_ddr_gemm import qualified_manifest
from scripts.qualify_release import source_hashes

parser = argparse.ArgumentParser()
parser.add_argument('--operator-confirmation', required=True)
args = parser.parse_args()
if args.operator_confirmation != 'Fresh OFF/ON complete; powered on and connected':
    raise RuntimeError('New post-T8 cold start required')
t8 = root / 'build/release_board_t8_execution'
execution = json.loads((t8 / 'execution.json').read_bytes())
native = json.loads((t8 / 'native_receipt.json').read_bytes())
sha = lambda value: hashlib.sha256(value).hexdigest()
if execution['state'] != 'PASS' or native['actual_exit_code'] != 0 or native['execution_sha256_bytes'] != sha((t8 / 'execution.json').read_bytes()):
    raise RuntimeError('Completed T8 qualification required')
manifest_path = root / 'build/gemm_release_p8_t32_1mbaud/build.json'
manifest = qualified_manifest(manifest_path)
if manifest['build_id'] != 0x9D4BEB4D:
    raise RuntimeError('Exact qualified T32 image required')
output = root / 'build/final_t32_demo_20261008'
if output.exists():
    raise RuntimeError('Fresh demo directory required')
output.mkdir()
sources = source_hashes()
def utc():
    return datetime.now(timezone.utc).isoformat()
report = dict(state='RUNNING', utc_started=utc(), utc_finished=None,
              operator_confirmation=args.operator_confirmation,
              operator_confirmation_source='Actual separate post-T8 user reply',
              build_id=manifest['build_id'], bitstream_sha256_bytes=manifest['bitstream_sha256'],
              manifest_sha256_bytes=sha(manifest_path.read_bytes()),
              source_sha256_utf8_lf=sources, stages=[], api_jobs=[])
def save():
    (output / 'execution.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8', newline='\n')
def run(name, command):
    stage = dict(name=name, utc_started=utc(), command=command, actual_exit_code=None)
    report['stages'].append(stage)
    save()
    child = subprocess.run(command, cwd=root, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    (output / (name + '.stdout.txt')).write_bytes(child.stdout)
    (output / (name + '.stderr.txt')).write_bytes(child.stderr)
    stage.update(actual_exit_code=child.returncode, utc_finished=utc(),
                 stdout_sha256_bytes=sha(child.stdout), stderr_sha256_bytes=sha(child.stderr))
    save()
    if child.returncode:
        raise RuntimeError(name + ' failed; no retry or reset')
    print('PASS ' + name, flush=True)
device = None
save()
try:
    run('program', [sys.executable, '-B', str(root / 'scripts/program_ddr_gemm.py'), '--manifest', str(manifest_path)])
    run('cli', [sys.executable, '-B', '-m', 'host.gemm', '--port', 'COM11', '--manifest', str(manifest_path),
                '--mode', '1', '--m', '5', '--n', '3', '--k', '9', '--repeats', '3', '--retries', '0',
                '--output', str(output / 'cli')])
    a = [[1, -2, 3], [4, 5, -6]]
    b = [[7, 8], [-9, 10], [11, -12]]
    expected = [[58, -48], [-83, 154]]
    device = GEMM.open('COM11', baud=manifest['baud'], retries=0)
    report['identity'] = device.identify(manifest)
    device.wait_ready()
    for job_id, mode in ((1, 0), (2, 1)):
        packed = pack_inputs(a, b, mode=mode)
        device.upload(packed, guards=True)
        device.configure(packed.descriptor)
        device.start(job_id=job_id)
        counters = device.wait(job_id=job_id)
        c = device.read_output()
        validate(c, a, b)
        guards = device.check_guards(packed)
        if c != expected or guards != 752:
            raise RuntimeError('API result or guarded allocation count differs')
        report['api_jobs'].append(dict(job_id=job_id, mode=mode, descriptor=asdict(packed.descriptor),
                                       a=a, b=b, expected=expected, c=c, counters=counters,
                                       compared_elements=4, guard_bytes_checked=guards))
        save()
        print('PASS API MODE' + str(mode) + ': ' + str(c), flush=True)
    if device.link.retry_count or device.link.decoder.rejected or device.link.poisoned or source_hashes() != sources:
        raise RuntimeError('Transport or frozen-source check failed')
    report.update(state='PASS', transport=dict(retries=0, rejected_frames=0, poisoned=False))
except (Exception, KeyboardInterrupt) as error:
    report.update(state='FAIL', error=str(error))
finally:
    if device is not None:
        try:
            device.close()
        except Exception as error:
            report.update(state='FAIL', close_error=str(error))
    report['utc_finished'] = utc()
    save()
raise SystemExit(0 if report['state'] == 'PASS' else 1)
