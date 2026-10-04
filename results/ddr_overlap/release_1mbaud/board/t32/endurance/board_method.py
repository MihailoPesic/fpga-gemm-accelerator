"""Restart interrupted qualification without resetting or replaying a START."""
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'scripts'))
from scripts.program_ddr_gemm import qualified_manifest
from scripts.qualify_release import sha,source_hashes,verify_sealed,canonical_hash
from scripts.build_ddr_gemm import input_hashes

def utc():
    return datetime.now(timezone.utc).isoformat()

prior=ROOT/'build/release_board_t32_execution'
old=ROOT/'build/release_board_t32_measurements'
recovery=ROOT/'build/release_t32_standby_recovery/record.json'
execution=ROOT/'build/release_board_t32_recovered_execution'
results=ROOT/'build/release_board_t32_recovered_measurements'
manifest_path=ROOT/'build/gemm_release_p8_t32_1mbaud/build.json'
if execution.exists() or results.exists():
    raise RuntimeError('Fresh recovery execution and results directories required')
original=json.loads((prior/'execution.json').read_text(encoding='utf-8'))
native=json.loads((prior/'native_receipt.json').read_text(encoding='utf-8'))
recovered=json.loads(recovery.read_text(encoding='utf-8'))
manifest=qualified_manifest(manifest_path)
frozen=source_hashes()
if (original['state']!='FAIL' or original['stages'][-1]['actual_exit_code']!=1 or
    native['actual_exit_code']!=1 or native['execution_sha256_bytes']!=sha(prior/'execution.json') or
    recovered['result']!='PASS' or recovered['last_job_id']!=384 or
    recovered['transport']!=dict(retries=0,rejected_frames=0,poisoned=False) or
    recovered['reset_or_reprogrammed'] is not False or recovered['hardware_mutated'] is not False or
    frozen!=original['source_sha256_utf8_lf'] or frozen!=recovered['source_sha256_utf8_lf'] or
    input_hashes()!=manifest['source_sha256_utf8_lf'] or manifest['build_id']!=0x9d4beb4d):
    raise RuntimeError('Exact failed-run, retained completion, image and frozen source bindings required')
plan=json.loads((old/'plan.json').read_text(encoding='utf-8'))
execution.mkdir()
results.mkdir()
shutil.copyfile(old/'plan.json',results/'plan.json')
record=dict(state='RUNNING',utc_started=utc(),utc_finished=None,
    port='COM11',baud=manifest['baud'],build_id=manifest['build_id'],
    bitstream_sha256_bytes=manifest['bitstream_sha256'],manifest_sha256_bytes=sha(manifest_path),
    source_sha256_utf8_lf=frozen,helper_sha256_bytes=sha(Path(__file__)),
    keep_awake_source_sha256_bytes=sha(ROOT/'scripts/keep_awake.py'),
    original_execution_sha256_bytes=sha(prior/'execution.json'),
    original_native_receipt_sha256_bytes=sha(prior/'native_receipt.json'),
    recovery_record_sha256_bytes=sha(recovery),
    startup_scope='Same reported cold-loaded image; read-only reconnect confirmed retained job384 and memory; no reset/reprogramming or timed-out START replay',
    duration_scope='New endurance starts from zero in one new connection; interrupted duration is never accumulated',
    sleep_inhibitor_scope='Per-command ES_CONTINUOUS|ES_SYSTEM_REQUIRED; no permanent power-plan change; released by child exit',
    reused_completed_units=[],stages=[])
def save():
    temporary=execution/'execution.json.tmp'
    temporary.write_text(json.dumps(record,indent=2)+'\n',encoding='utf-8',newline='\n')
    temporary.replace(execution/'execution.json')
save()
try:
    for unit in ('maximum','benchmark/case_11_256x256x256'):
        binding=dict(plan_sha256=canonical_hash(plan['plan']),manifest_sha256_bytes=plan['manifest_sha256_bytes'],
            build_id=plan['build_id'],bitstream_sha256=plan['bitstream_sha256'],
            host_source_sha256_utf8_lf=plan['host_source_sha256_utf8_lf'],unit=unit)
        report=verify_sealed(old/unit,binding)
        (results/unit).parent.mkdir(parents=True,exist_ok=True)
        shutil.copytree(old/unit,results/unit)
        verify_sealed(results/unit,binding)
        record['reused_completed_units'].append(dict(unit=unit,original_seal_sha256_bytes=sha(old/unit/'seal.json'),
            copied_seal_sha256_bytes=sha(results/unit/'seal.json'),original_utc_finished=report['utc_finished'],
            checked_counts=report['checked_counts'],scope='Exact previously sealed completed unit; not a rerun'))
    command=[sys.executable,str(ROOT/'scripts/keep_awake.py'),'--',sys.executable,
        str(ROOT/'scripts/qualify_release.py'),'--port','COM11','--manifest',str(manifest_path),
        '--output',str(results),'--modes','both','--oracle','numpy','--samples','30',
        '--duration','1800','--seed','20261004','--phase','all','--resume']
    stage=dict(name='full_qualification',command=command,utc_started=utc(),utc_finished=None,actual_exit_code=None)
    record['stages'].append(stage)
    save()
    print('Starting fresh endurance and remaining grid with temporary idle-sleep inhibition',flush=True)
    with (execution/'full_qualification_console.txt').open('w',encoding='utf-8') as stream:
        child=subprocess.run(command,cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
    stage.update(actual_exit_code=child.returncode,utc_finished=utc(),console_sha256_bytes=sha(execution/'full_qualification_console.txt'))
    save()
    if child.returncode:
        raise RuntimeError('Recovered qualification failed; preserve records without retry/reset')
    summary=json.loads((results/'summary.json').read_text(encoding='utf-8'))
    if summary['result']!='PASS' or not summary['all_requested_release_phases_complete'] or source_hashes()!=frozen or input_hashes()!=manifest['source_sha256_utf8_lf']:
        raise RuntimeError('Incomplete release units or changed frozen identities')
    record.update(state='PASS',summary_sha256_bytes=sha(results/'summary.json'),available_completed_units=summary['available_completed_units'])
except (Exception,KeyboardInterrupt) as error:
    record.update(state='FAIL',error=f'{type(error).__name__}: {error}')
    print(record['error'],file=sys.stderr,flush=True)
finally:
    record['utc_finished']=utc()
    save()
raise SystemExit(0 if record['state']=='PASS' else 1)
