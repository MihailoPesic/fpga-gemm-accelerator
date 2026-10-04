from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from host.gemm import GEMM, pack_inputs, validate
from scripts.hw_test_ddr_gemm import source_hashes
from scripts.program_ddr_gemm import qualified_manifest

def require(condition, message):
    if not condition:
        raise RuntimeError(message)

out = ROOT / 'build/current_matrix_api_demo'
require(not out.exists(), 'Choose a fresh output directory')
manifest_path = ROOT / 'build/gemm_p8_overlap_final/build.json'
manifest = qualified_manifest(manifest_path)
require(manifest['build_id'] == 0x2c680af7, 'Exact current board image required')
out.mkdir()
sources = source_hashes()
a = [[1, -2, 3], [4, 5, -6]]
b = [[7, 8], [-9, 10], [11, -12]]
expected = [[58, -48], [-83, 154]]
report = dict(result='RUNNING', utc_started=datetime.now(timezone.utc).isoformat(),
    scope='Supplied-matrix Python API on the already programmed current MODE=1 image; no reset or programming',
    port='COM11', build_id=manifest['build_id'], bitstream_sha256_bytes=manifest['bitstream_sha256'],
    manifest_sha256_bytes=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
    source_sha256_utf8_lf=sources, a=a, b=b, expected=expected)
device = None
try:
    device = GEMM.open('COM11', baud=manifest['baud'], retries=0)
    report['identity'] = device.identify(manifest)
    device.wait_ready()
    packed = pack_inputs(a, b, mode=1)
    device.upload(packed, guards=True)
    device.configure(packed.descriptor)
    device.start(job_id=1)
    counters = device.wait(job_id=1)
    c = device.read_output()
    validate(c, a, b)
    require(c == expected, 'Supplied-matrix output mismatch')
    guards = device.check_guards(packed)
    require(guards == 752, 'Complete supplied-matrix guard count differs')
    require(device.link.retry_count == 0 and device.link.decoder.rejected == 0
            and not device.link.poisoned, 'Transport was not error-free')
    require(source_hashes() == sources, 'Host sources changed')
    report.update(result='PASS', descriptor=asdict(packed.descriptor), c=c, counters=counters,
        compared_elements=4, guard_bytes_checked=guards, source_hashes_unchanged=True,
        transport=dict(retries=0, rejected_frames=0, poisoned=False))
    print('FPGA returned', c, ';', counters['job_cycles'], 'cycles;', guards, 'guard bytes checked')
except (Exception, KeyboardInterrupt) as exc:
    report.update(result='FAIL', error=dict(type=type(exc).__name__, message=str(exc)))
finally:
    if device is not None:
        try:
            device.close()
        except Exception as exc:
            report.update(result='FAIL', close_error=str(exc))
    report['utc_finished'] = datetime.now(timezone.utc).isoformat()
    (out / 'results.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8', newline='\n')
raise SystemExit(0 if report['result'] == 'PASS' else 1)
