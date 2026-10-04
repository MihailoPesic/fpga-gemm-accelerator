"""Read the retained completed job after reconnecting; never write or START."""
import ctypes
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'scripts'))
from host.gemm import GEMM
from host.gemm.client import COUNTERS, READY, DONE, BUSY, ERROR, RESET_REQUIRED, DDR_READY
from scripts.program_ddr_gemm import qualified_manifest
from scripts.qualify_release import prepare, c_sentinel, expected_c, compare_bytes, sha, source_hashes, snapshots

area=ROOT/'build/release_t32_standby_recovery'
if area.exists():
    raise RuntimeError('Fresh read-only recovery directory required')
area.mkdir()
record=dict(result='RUNNING',utc_started=datetime.now(timezone.utc).isoformat(),
    scope='Read-only reconnect and independent comparison of retained job384; failed endurance remains failed',
    hardware_mutated=False,reset_or_reprogrammed=False,replayed_start=False,
    failed_job_sha256_bytes=sha(ROOT/'build/release_board_t32_measurements/endurance/job_000384/job.json'),
    failed_execution_sha256_bytes=sha(ROOT/'build/release_board_t32_execution/execution.json'),
    standby_events_sha256_bytes=sha(ROOT/'build/release_board_t32_execution/standby_events.txt'),
    helper_sha256_bytes=sha(Path(__file__)),source_sha256_utf8_lf=source_hashes())
def save():
    (area/'record.json').write_text(json.dumps(record,indent=2)+'\n',encoding='utf-8',newline='\n')
save()
kernel=ctypes.WinDLL('kernel32',use_last_error=True)
kernel.SetThreadExecutionState.argtypes=[ctypes.c_uint32]
kernel.SetThreadExecutionState.restype=ctypes.c_uint32
if not kernel.SetThreadExecutionState(0x80000001):
    raise ctypes.WinError(ctypes.get_last_error())
record['temporary_system_sleep_inhibited']=True
device=None
try:
    manifest=qualified_manifest(ROOT/'build/gemm_release_p8_t32_1mbaud/build.json')
    device=GEMM.open('COM11',baud=manifest['baud'],retries=0)
    record['identity']=device.identify(manifest)
    status,last=device.read_reg(0x0c),device.read_reg(0x44)
    if status & (BUSY|ERROR|RESET_REQUIRED) or status & (READY|DONE|DDR_READY) != READY|DONE|DDR_READY or last != 384:
        raise RuntimeError(f'Retained ready completion differs: STATUS={status:#x}, LAST_JOB_ID={last}')
    frozen={name:device.read_reg(address)|(device.read_reg(address+4)<<32) for name,address in COUNTERS.items()}
    original=json.loads((ROOT/'build/release_board_t32_measurements/endurance/job_000384/job.json').read_text(encoding='utf-8'))
    if any(frozen[name]!=original[name] for name in COUNTERS):
        raise RuntimeError('Retained hardware counter snapshot changed')
    prepared=prepare((64,1,256),original['seed'],'numpy')
    if prepared['input_a_sha256_bytes']!=original['input_a_sha256_bytes'] or prepared['raw_b_sha256_bytes']!=original['raw_b_sha256_bytes'] or prepared['oracle_sha256_bytes']!=original['oracle_sha256_bytes']:
        raise RuntimeError('Independent prepared input/oracle differs')
    images={name:(address,raw) for name,address,raw in prepared['images']}
    initial=c_sentinel(len(images['c'][1]),original['seed'],original['sample'])
    wanted=expected_c(initial,prepared['expected'],prepared['packed'].descriptor)
    if sha_bytes := original.get('c_before_sha256_bytes'):
        import hashlib
        if hashlib.sha256(initial).hexdigest()!=sha_bytes:
            raise RuntimeError('Original C sentinel differs')
    saved={}
    total=0
    for name,(address,raw) in images.items():
        actual=device.read_memory(address,len(raw))
        compare_bytes(name,actual,wanted if name=='c' else raw,address)
        saved[name]=snapshots(area/f'{name}_after.bin.gz',actual)
        total+=len(raw)
    if device.read_reg(0x0c)!=status or device.read_reg(0x44)!=last:
        raise RuntimeError('Retained completion changed during read-only recovery')
    if source_hashes()!=record['source_sha256_utf8_lf']:
        raise RuntimeError('Frozen host sources changed')
    record.update(result='PASS',status=status,last_job_id=last,counters=frozen,saved_snapshots=saved,
        compared_outputs=64,allocation_bytes_checked=total,guard_input_bytes_checked=total-256,
        transport=dict(retries=device.link.retry_count,rejected_frames=device.link.decoder.rejected,poisoned=device.link.poisoned))
except Exception as error:
    record.update(result='FAIL',error=f'{type(error).__name__}: {error}')
finally:
    if device is not None:
        device.close()
    kernel.SetThreadExecutionState(0x80000000)
    record['utc_finished']=datetime.now(timezone.utc).isoformat()
    save()
print(json.dumps({key:record[key] for key in ('result','scope','last_job_id','compared_outputs','allocation_bytes_checked','transport') if key in record},indent=2))
raise SystemExit(0 if record['result']=='PASS' else 1)
