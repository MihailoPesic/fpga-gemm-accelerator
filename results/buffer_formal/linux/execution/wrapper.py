"""Private hidden WSL formal execution; no hardware or source mutations."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
AREA = ROOT / 'build/formal_buffers_linux_execution_escalated_20261004'
RESULTS = ROOT / 'build/formal_buffers_linux_final_20261004'
ENVIRONMENT = ROOT / 'build/formal-linux-env_20261004'
INPUTS = ('rtl/memory/gemm_tile_dma_read_queue.sv', 'rtl/control/gemm_tile_scheduler.sv',
          'formal/tile_read_queue.sv', 'formal/tile_scheduler.sv',
          'scripts/formal_buffers.py', 'scripts/formal_buffers_witness.py',
          'requirements-formal.txt', 'tb/test_formal_buffers.py')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def utc():
    return datetime.now(timezone.utc).isoformat()


def main():
    if AREA.exists() or RESULTS.exists() or ENVIRONMENT.exists():
        raise RuntimeError('Refusing to overwrite execution, environment or query output')
    AREA.mkdir()
    before = {name: sha(ROOT / name) for name in INPUTS}
    record = dict(schema_version=1, result='RUNNING', utc_started=utc(), hardware_accessed=False,
                  wrapper_sha256_bytes=sha(Path(__file__)),
                  source_sha256_bytes_before=before, executions=[], actual_exit_code=None,
                  scope='Linux/WSL execution of the public exact 21-query formal-buffer runner',
                  setup_scope='Fresh optional workspace venv without system changes; existing workspace pip supplies --python bootstrap')

    def save():
        (AREA / 'execution.json').write_text(json.dumps(record, indent=2)+'\n',encoding='utf-8',newline='\n')

    def run(name, script):
        path = AREA / (name+'.sh')
        path.write_text(script,encoding='utf-8',newline='\n')
        command = ['wsl', '-d', 'Ubuntu', '--', 'bash', '--noprofile', '--norc', '-s']
        entry = dict(name=name, command=command, script_sha256_bytes=sha(path),
                     utc_started=utc(), actual_exit_code=None)
        record['executions'].append(entry)
        save()
        began = time.monotonic()
        with (AREA / (name+'.stdout.txt')).open('wb') as stdout, (AREA / (name+'.stderr.txt')).open('wb') as stderr:
            result = subprocess.run(command, cwd=ROOT, input=script.encode(), stdout=stdout, stderr=stderr,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
        entry.update(actual_exit_code=result.returncode, utc_finished=utc(),seconds=time.monotonic()-began,
                     stdout_sha256_bytes=sha(AREA/(name+'.stdout.txt')),
                     stderr_sha256_bytes=sha(AREA/(name+'.stderr.txt')))
        save()
        print(f'{name}: actual WSL exit {result.returncode}',flush=True)
        if result.returncode:
            print((AREA/(name+'.stderr.txt')).read_text(encoding='utf-8',errors='replace'),flush=True)
            print((AREA/(name+'.stdout.txt')).read_text(encoding='utf-8',errors='replace')[-4000:],flush=True)
            raise RuntimeError(f'{name} failed; original logs retained')

    # Explicit literal LF shell input; no constructed destructive commands.
    prefix = "set -eu\nunset PYTHONPATH\nexport PYTHONNOUSERSITE=1\ncd '/mnt/c/Users/mihai/Documents/continium/accelerator nexys'\n"
    try:
        save()
        run('inventory',prefix+"python3 --version\n.venv/bin/python -m pip --version\n")
        run('create_environment',prefix+"test ! -e build/formal-linux-env_20261004\npython3 -m venv --without-pip build/formal-linux-env_20261004\n")
        run('install_pins',prefix+"export PIP_CACHE_DIR=\"$PWD/build/formal_linux_pip_cache\"\nexport PIP_DISABLE_PIP_VERSION_CHECK=1\n.venv/bin/python -m pip --python build/formal-linux-env_20261004/bin/python install -r requirements-formal.txt\n")
        run('package_check',prefix+".venv/bin/python -m pip --python build/formal-linux-env_20261004/bin/python check\n")
        if {name:sha(ROOT/name) for name in INPUTS} != before:
            raise RuntimeError('Formal sources changed before query execution')
        run('formal_queries',prefix+"build/formal-linux-env_20261004/bin/python scripts/formal_buffers.py --build-dir build/formal_buffers_linux_final_20261004\n")
        summary = json.loads((RESULTS/'summary.json').read_text(encoding='utf-8'))
        commands = summary['commands']
        if not (summary.get('result') == 'PASS' and summary.get('passed') is True and summary.get('queries') == 21 and
                len(commands) == 22 and len(summary.get('witnesses',[])) == 15 and
                all(command.get('passed') is True and command.get('exit_code') == 0 for command in commands) and
                summary.get('source_hashes_unchanged') is True):
            raise RuntimeError('Actual formal summary does not establish all 21 queries and 15 decoded witnesses')
        record.update(result='PASS', actual_exit_code=0, queries=21, decoded_witnesses=15,
                      summary_sha256_bytes=sha(RESULTS/'summary.json'),
                      source_hashes_unchanged=True, saved_query_artifact_sha256_bytes={
                          str(path.relative_to(RESULTS)).replace('\\','/'):sha(path)
                          for path in RESULTS.rglob('*') if path.is_file()},
                      versions=summary['versions'])
    except Exception as error:
        record.update(result='FAIL', actual_exit_code=1, error=f'{type(error).__name__}: {error}')
    finally:
        after = {name:sha(ROOT/name) for name in INPUTS}
        record.update(source_sha256_bytes_after=after, source_hashes_unchanged=after==before, utc_finished=utc())
        if after != before:
            record.update(result='FAIL',actual_exit_code=1,source_error='Formal sources changed')
        save()
    print(json.dumps(dict(result=record['result'],execution=str(AREA/'execution.json'),queries=record.get('queries',0))),flush=True)
    return record['actual_exit_code']


if __name__ == '__main__':
    raise SystemExit(main())
