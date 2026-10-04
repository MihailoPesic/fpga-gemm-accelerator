from pathlib import Path
import hashlib
import json
import shutil
import subprocess

out = Path(__file__).resolve().parent
source = out.parent/'gemm_routed.dcp'
copy = out/'copied_production_routed.dcp'
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
before = sha(source)
shutil.copy2(source, copy)
assert sha(source) == before == sha(copy)
manifest = json.loads((source.parent/'build.json').read_text())
assert manifest['result'] == 'PASS' and manifest['build_id'] == 0xd558a543
identity = dict(source=str(source), sha256_bytes=before,
                build_id='0xd558a543', bitstream_sha256=manifest['bitstream_sha256'],
                build_manifest_sha256=sha(source.parent/'build.json'),
                scope='Read-only production reset query; copied new routed DCP, no project opened')
(out/'checkpoint_identity.json').write_text(json.dumps(identity, indent=2)+'\n')
with (out/'console.txt').open('w', encoding='utf-8') as stream:
    run = subprocess.run(['C:/AMDDesignTools/2026.1/Vivado/bin/vivado.bat',
        '-mode', 'batch', '-notrace', '-source', str(out/'review.tcl'),
        '-log', str(out/'review.log'), '-journal', str(out/'review.jou'),
        '-tclargs', str(copy)], cwd=out, stdout=stream,
        stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
assert sha(source) == before == sha(copy), 'Checkpoint altered during read-only query'
(out/'exit.txt').write_text(str(run.returncode)+'\n')
identity.update(result='PASS' if run.returncode == 0 else 'FAIL',
                checkpoint_hashes_unchanged=True,
                evidence_sha256={name: sha(out/name) for name in
                    ('review.tcl','run.py','console.txt','reset_selector.txt','exceptions.txt')
                    if (out/name).exists()})
(out/'checkpoint_identity.json').write_text(json.dumps(identity, indent=2)+'\n')
print(json.dumps(identity, indent=2))
raise SystemExit(run.returncode)
