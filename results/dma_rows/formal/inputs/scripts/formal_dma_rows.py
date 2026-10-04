"""Bounded SAT checks of the row planner at 20 timeframes and read depths 1/4."""
import argparse
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

try:
    from .formal_dma_rows_witness import BOUND, digest, require, review_witness
except ImportError:
    from formal_dma_rows_witness import BOUND, digest, require, review_witness

ROOT = Path(__file__).resolve().parents[1]
ASSERTIONS = 18
INPUTS = ('rtl/memory/gemm_dma_rows.sv', 'formal/dma_rows.sv',
          'scripts/formal_dma_rows.py', 'scripts/formal_dma_rows_witness.py',
          'requirements-formal.txt')
PREPARE = (
    'read_verilog -formal -sv -D SYNTHESIS /work/inputs/rtl/memory/gemm_dma_rows.sv /work/inputs/formal/dma_rows.sv\n'
    'chparam -set READ_SLOTS {slots} dma_rows_formal\n'
    'prep -top dma_rows_formal -flatten\n'
    'async2sync\nchformal -assert -lower\nopt -full -keepdc\ncheck\nstat\n'
)


def hashes():
    return {name: dict(bytes=digest(ROOT / name), utf8_lf=hashlib.sha256(
        (ROOT / name).read_text(encoding='utf-8').encode('utf-8')).hexdigest()) for name in INPUTS}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', type=Path, default=ROOT / 'build/formal_dma_rows',
                        help='fresh directory under build; existing paths are rejected')
    parser.add_argument('--yosys', help='path to the pinned yowasp-yosys executable')
    args = parser.parse_args(argv)
    area = args.build_dir.resolve()
    try:
        area.relative_to((ROOT / 'build').resolve())
    except ValueError:
        parser.error('--build-dir must be inside this repository\'s build directory')
    if area.exists():
        parser.error('--build-dir already exists; choose a fresh result directory')
    area.mkdir(parents=True)
    report = dict(schema_version=1, result='FAIL', passed=False, module='gemm_dma_rows',
                  scope='Bounded safety and reachable public-command witnesses, not the full accelerator',
                  bound_sat_timeframes=BOUND, induction=False, configurations=[1, 4],
                  assertions_per_configuration=ASSERTIONS,
                  fixed_descriptor=dict(base=0xff0, stride=64, rows=4, row_bytes=20),
                  assumptions=[
                      'One abstract synchronous posedge transition per SAT timeframe; bound includes initial reset',
                      'Synchronous reset high at timeframe 1 and low thereafter',
                      'Initially unconstrained registers are defined binary values',
                      'Request direction/valid, burst ready, completion valid/status, cancel/status, and DONE ready independently symbolic',
                      'No fairness or eventual-response assumption'],
                  properties=[
                      'Burst VALID and complete payload stable while stalled, except unaccepted queued-read cancellation/error withdrawal',
                      'DONE VALID/status stable while stalled',
                      'Public outstanding-count bounds, owned completions, and no premature DONE',
                      'Exact offered-burst address/order/row/word/flags/tail-strobe for the fixed descriptor'],
                  reachability=[
                      'Successful read/write with five accepted/completed bursts, real offer/DONE stalls, and no cancellation',
                      'READ4 reaches four outstanding commands and simultaneous issue/completion',
                      'Nonzero READ4 cancellation leaves accepted traffic for a later completion before nonzero DONE'],
                  excluded=['Arbitrary descriptors', 'Four-state X behavior', 'AXI/data/banks/compute/scheduler/UART/DDR PHY',
                            'Unbounded or inductive correctness'],
                  hardware_accessed=False, utc_started=datetime.now(timezone.utc).isoformat(),
                  commands=[], witnesses=[])
    original = {}
    child_env = os.environ.copy()
    cache = ROOT / 'build/formal_cache'
    temporary = area / 'tmp'
    cache.mkdir(exist_ok=True)
    temporary.mkdir()
    child_env.update(YOWASP_CACHE_DIR=str(cache), YOWASP_MOUNT='/work=.',
                     TEMP=str(temporary), TMP=str(temporary), PYTHONNOUSERSITE='1', RAYON_NUM_THREADS='1')

    def save():
        (area / 'summary.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')

    def run(name, command, script=None):
        entry = dict(name=name, argv=command, log=name + '.log', passed=False)
        if script:
            entry.update(script=script.name, script_sha256_bytes=digest(script))
        report['commands'].append(entry)
        save()
        start = time.perf_counter()
        log = area / entry['log']
        try:
            with log.open('w', encoding='utf-8') as stream:
                result = subprocess.run(command, cwd=area, env=child_env, stdout=stream,
                                        stderr=subprocess.STDOUT, timeout=90,
                                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            entry['exit_code'] = result.returncode
            require(result.returncode == 0, f'{name}: tool exit {result.returncode}; see {log}')
            return log.read_text(encoding='utf-8'), entry
        except subprocess.TimeoutExpired:
            entry.update(exit_code=None, timed_out=True)
            raise RuntimeError(f'{name}: tool exceeded 90s; no proof result') from None
        finally:
            entry['seconds'] = time.perf_counter() - start
            if log.exists():
                entry['log_sha256_bytes'] = digest(log)
            save()

    try:
        before = hashes()
        report['source_hashes_before'] = before
        for name in INPUTS:
            original[name] = (ROOT / name).read_bytes()
            snapshot = area / 'inputs' / name
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            snapshot.write_bytes(original[name])
            require(digest(snapshot) == before[name]['bytes'], f'Input copy mismatch: {name}')
        pins = dict(line.split('==', 1) for line in (ROOT / 'requirements-formal.txt').read_text().splitlines()
                    if line and not line.startswith('#'))
        installed = {name: version(name) for name in pins}
        report['versions'] = dict(python=sys.version, python_executable=sys.executable, packages=installed)
        require(installed == pins, 'Formal package versions differ from requirements-formal.txt')
        sibling = Path(sys.executable).parent / ('yowasp-yosys.exe' if os.name == 'nt' else 'yowasp-yosys')
        executable = args.yosys or (str(sibling) if sibling.is_file() else shutil.which('yowasp-yosys'))
        require(bool(executable), 'Install requirements-formal.txt in the Python environment used by this runner')
        executable = str(Path(executable).resolve())
        require(Path(executable).is_file(), f'Yosys executable not found: {executable}')
        tool, entry = run('tool_version', [executable, '-V'])
        identities = [line.strip() for line in tool.splitlines() if line.startswith('Yosys ')]
        require(len(identities) == 1 and identities[0].startswith('Yosys 0.69 (git sha1 9f75ca1f9,'),
                'Unexpected Yosys binary version')
        entry['passed'] = True
        report['versions']['yosys'] = identities[0]
        report['versions']['yosys_executable'] = executable
        for slots in (1, 4):
            cases = [('safety', None), ('read_done', 'cover_read_done'), ('write_done', 'cover_write_done')]
            if slots == 4:
                cases += [(name, 'cover_' + name) for name in ('credit_four', 'simultaneous', 'cancelled_drain')]
            for case, target in cases:
                require(hashes() == before, 'Formal source inputs changed before a query')
                name = f'read{slots}_{case}'
                sat = (f'sat -seq {BOUND} -set-def-inputs -set-init-def -set rst 0 -set-at 1 rst 1 '
                       '-timeout 30 -show-ports ')
                if target:
                    sat += f'-falsify -prove {target} 0'
                    marker = 'SAT proof finished - model found: FAIL!'
                else:
                    sat += '-verify -prove-asserts'
                    marker = 'SAT proof finished - no model found: SUCCESS!'
                script = area / (name + '.ys')
                script.write_text(PREPARE.format(slots=slots) + sat +
                                  f' -dump_json /work/{name}.json -dump_vcd /work/{name}.vcd\n', encoding='utf-8')
                log, entry = run(name, [executable, '-s', '/work/' + script.name], script)
                require(marker in log, f'{name}: missing required SAT outcome')
                counts = re.findall(r'^\s*(\d+)\s+\$assert\s*$', log, flags=re.MULTILINE)
                require(counts and int(counts[-1]) == ASSERTIONS, f'{name}: expected exactly {ASSERTIONS} assertions')
                checks = re.findall(r'Found and reported (\d+) problems\.', log)
                require(checks and all(int(count) == 0 for count in checks), f'{name}: structural check failed')
                entry.update(read_slots=slots, target=target, assertions=ASSERTIONS)
                if target:
                    model, vcd = area / (name + '.json'), area / (name + '.vcd')
                    require(model.is_file() and model.stat().st_size and vcd.is_file() and vcd.stat().st_size,
                            f'{name}: missing real SAT witness')
                    witness = review_witness(vcd, model, slots, target)
                    report['witnesses'].append(witness)
                    entry.update(model_sha256_bytes=digest(model), vcd_sha256_bytes=digest(vcd),
                                 independently_decoded=True)
                else:
                    imports = log.count('Import proof for assert:')
                    require(imports == ASSERTIONS * BOUND, f'{name}: missing assertion imports')
                    entry['assertion_imports'] = imports
                require(hashes() == before, 'Formal source inputs changed during a query')
                require(digest(script) == entry['script_sha256_bytes'], f'{name}: query script changed')
                entry['passed'] = True
                save()
                print(f'PASS {name}', flush=True)
        require(len(report['commands']) == 10 and len(report['witnesses']) == 7,
                'Incomplete formal query set')
        report.update(result='PASS', passed=True)
    except Exception as error:
        report['error'] = f'{type(error).__name__}: {error}'
        print(report['error'], flush=True)
    finally:
        unchanged = all((ROOT / name).is_file() and (ROOT / name).read_bytes() == data
                        and (area / 'inputs' / name).is_file()
                        and (area / 'inputs' / name).read_bytes() == data for name, data in original.items())
        report['source_hashes_unchanged'] = unchanged and len(original) == len(INPUTS)
        if not report['source_hashes_unchanged']:
            report.update(result='FAIL', passed=False, source_error='Input source/snapshot changed or missing')
        report['source_hashes_after'] = hashes() if all((ROOT / name).is_file() for name in INPUTS) else None
        report['utc_finished'] = datetime.now(timezone.utc).isoformat()
        save()
        tables = []
        for witness in report['witnesses']:
            tables += [witness['vcd'], 'step rst req/write BVBR address beats pending CVCR DVDR status cancel cancel_status',
                       witness['cycle_table'], '']
        (area / 'witness_cycles.txt').write_text('\n'.join(tables), encoding='utf-8')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
