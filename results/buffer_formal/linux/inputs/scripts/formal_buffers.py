"""Inductive read-FIFO and bounded reduced-scheduler checks on unchanged RTL."""
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
    from .formal_buffers_witness import digest, require, review_witness
except ImportError:
    from formal_buffers_witness import digest, require, review_witness

ROOT = Path(__file__).resolve().parents[1]
BOUND = 32
INPUTS = ('rtl/memory/gemm_tile_dma_read_queue.sv', 'rtl/control/gemm_tile_scheduler.sv',
          'formal/tile_read_queue.sv', 'formal/tile_scheduler.sv',
          'scripts/formal_buffers.py', 'scripts/formal_buffers_witness.py', 'requirements-formal.txt',
          'tb/test_formal_buffers.py')
QUEUE_OBSERVATIONS = dict(owned='owned', head='head', tail='tail', received='received',delivered='delivered',
    load_pending='load_pending',completion_pending='completion_pending',
    **{f'row{i}':f'rows[{i}]' for i in range(4)}, **{f'word{i}':f'words[{i}]' for i in range(4)},
    **{f'beats{i}':f'beats[{i}]' for i in range(4)})
SCHEDULER_OBSERVATIONS = dict(input0='input_state[0]', input1='input_state[1]',
    output0='output_state[0]', output1='output_state[1]', load_state='load_state',
    store_state='store_state', core_state='core_state', job_state='job_state',
    admitted='admission_count', computed='compute_count', retired='retirement_count', total='total_tiles',
    input_tag0='input_tag[0]', input_tag1='input_tag[1]', output_tag0='output_tag[0]', output_tag1='output_tag[1]')


def preparation(kind, configuration):
    module, rtl, harness, parameters, observations = (
        ('gemm_tile_dma_read_queue', INPUTS[0], INPUTS[2], f'-set T {configuration}', QUEUE_OBSERVATIONS)
        if kind == 'queue' else
        ('gemm_tile_scheduler', INPUTS[1], INPUTS[3], '-set P 4 -set T 8', SCHEDULER_OBSERVATIONS))
    top = 'tile_read_queue_formal' if kind == 'queue' else 'tile_scheduler_formal'
    lines = [f'read_verilog -formal -sv -D SYNTHESIS /work/inputs/{rtl}',
             f'chparam {parameters} {module}', 'proc', 'memory_map', f'cd {module}']
    # Observation-only output aliases; no -cut, -input or driver replacement.
    lines += [f'rename -output {original} obs_{alias}' for alias, original in observations.items()]
    lines += ['cd ..', f'read_verilog -formal -sv /work/inputs/{harness}',
              f'chparam -set {"T" if kind == "queue" else "MODE"} {configuration} {top}',
              f'prep -top {top} -flatten', 'memory_map', 'async2sync', 'chformal -assert -lower',
              'chformal -assume -lower', 'opt -full -keepdc', 'check', 'stat']
    return '\n'.join(lines) + '\n'


def hashes():
    return {name: dict(bytes=digest(ROOT / name), utf8_lf=hashlib.sha256(
        (ROOT / name).read_text(encoding='utf-8').encode()).hexdigest()) for name in INPUTS}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', type=Path, default=ROOT / 'build/formal_buffers')
    parser.add_argument('--only', choices=('queue', 'scheduler'))
    args = parser.parse_args(argv)
    area = args.build_dir.resolve()
    try:
        area.relative_to((ROOT / 'build').resolve())
    except ValueError:
        parser.error('--build-dir must be inside build/')
    if area.exists():
        parser.error('--build-dir already exists; choose a fresh directory')
    area.mkdir(parents=True)
    report = dict(schema_version=1, result='FAIL', passed=False, witness_bound_sat_timeframes=BOUND,
        scheduler_safety_bound_sat_timeframes=BOUND,
        queue_safety='Separate reset-based 8-timeframe base and reset-low induction-only sequence4 (maximum induction length8)',
        hardware_accessed=False, utc_started=datetime.now(timezone.utc).isoformat(),
        commands=[], witnesses=[],
        scope='Actual four-entry read metadata FIFO and reduced two-tile scheduler ownership, not the full accelerator',
        assumptions=[
            'One abstract synchronous posedge per SAT timeframe; reset high only at timeframe1, defined initial binary registers',
            'Witness final timeframe is an observation after the last committed transition; final input offers are not counted as executed handshakes',
            'FIFO: one-beat non-final descriptors, row<T, arbitrary 5-bit word/address and 64-bit data, fixed saved BT/buffer=1',
            'FIFO: planner and data source keep unaccepted offers stable; ordered head-tag/index0/LAST and successful completion only after bank delivery, except stop drains with status7',
            'Scheduler: one prevalidated P4/T8 M1/N9/K1 job, MODE0/1, bases0x1000/0x2000/0x3000 and strides64',
            'Scheduler: abstract engines accept at most one operation each; terminals occur only for accepted work; compute reports BUSY before DONE',
            'Scheduler: DMA statuses are0/7, external memory fatal code7 and compute-error code8; no AXI response timing is proved by the abstract engines',
            'Independent symbolic request readiness/completion delays and errors, no fairness or eventual-response assumption',
            'No AXI/DDR PHY arithmetic, full descriptor validation, watchdog, public counters or unbounded claims'],
        observation_preparation='Lower unchanged DUT memories then rename selected existing registers to observation-only output ports; no drivers cut')
    original = {}
    env = os.environ.copy()
    cache, temporary = ROOT / 'build/formal_cache', area / 'tmp'
    cache.mkdir(exist_ok=True); temporary.mkdir()
    env.update(YOWASP_CACHE_DIR=str(cache), YOWASP_MOUNT='/work=.', TEMP=str(temporary), TMP=str(temporary),
               PYTHONNOUSERSITE='1', RAYON_NUM_THREADS='1')

    def save():
        (area / 'summary.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')

    def run(name, command, script=None):
        entry = dict(name=name, argv=command, passed=False, log=name + '.log')
        if script:
            entry.update(script=script.name, script_sha256_bytes=digest(script))
        report['commands'].append(entry); save()
        start = time.perf_counter()
        try:
            with (area / entry['log']).open('w', encoding='utf-8') as stream:
                result = subprocess.run(command, cwd=area, env=env, stdout=stream, stderr=subprocess.STDOUT,
                    timeout=180, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            entry['exit_code'] = result.returncode
            require(result.returncode == 0, f'{name}: tool exit {result.returncode}')
            return (area / entry['log']).read_text(encoding='utf-8'), entry
        except subprocess.TimeoutExpired:
            entry.update(exit_code=None, timed_out=True)
            raise RuntimeError(f'{name}: tool exceeded 180s') from None
        finally:
            entry['seconds'] = time.perf_counter() - start
            if (area / entry['log']).exists():
                entry['log_sha256_bytes'] = digest(area / entry['log'])
            save()

    try:
        before = hashes(); report['source_hashes_before'] = before
        for name in INPUTS:
            original[name] = (ROOT / name).read_bytes()
            snapshot = area / 'inputs' / name
            snapshot.parent.mkdir(parents=True, exist_ok=True); snapshot.write_bytes(original[name])
        pins = dict(line.split('==', 1) for line in (ROOT / 'requirements-formal.txt').read_text().splitlines()
                    if line and not line.startswith('#'))
        installed = {name: version(name) for name in pins}
        report['versions'] = dict(python=sys.version, python_executable=sys.executable, packages=installed)
        require(installed == pins, 'Formal tool packages differ from pinned requirements')
        executable = Path(sys.executable).parent / ('yowasp-yosys.exe' if os.name == 'nt' else 'yowasp-yosys')
        if not executable.is_file():
            executable = Path(shutil.which('yowasp-yosys') or '')
        require(executable.is_file(), 'Pinned yowasp-yosys not found')
        tool, entry = run('tool_version', [str(executable), '-V'])
        identities = [line.strip() for line in tool.splitlines() if line.startswith('Yosys ')]
        require(len(identities) == 1 and identities[0].startswith('Yosys 0.69 (git sha1 9f75ca1f9,'), 'Wrong Yosys binary')
        entry['passed'] = True; report['versions']['yosys'] = identities[0]
        kinds = (args.only,) if args.only else ('queue', 'scheduler')
        expected_queries = 0
        for kind in kinds:
            for configuration in ((8, 32) if kind == 'queue' else (0, 1)):
                covers = ('full', 'wrap', 'stalls', 'stop_drain') if kind == 'queue' else (
                    ('success', 'held', 'fault_drain', 'overlap') if configuration else ('success', 'held', 'fault_drain'))
                for case in (('base', 'induction') if kind == 'queue' else ('safety',)) + covers:
                    expected_queries += 1
                    require(hashes() == before, 'Inputs changed before query')
                    name = f'{kind}_{configuration}_{case}'
                    target = None if case in ('safety', 'base', 'induction') else 'cover_' + case
                    # Default SAT is binary. -set-def* would enable separate X
                    # modeling, outside this explicitly two-state proof scope.
                    inductive = case == 'induction'
                    bound = 4 if inductive else 8 if case == 'base' else BOUND
                    sat = (f'sat -seq {bound} -set-assumes -set rst 0 '
                           '-timeout 120 -show-ports ')
                    sat += '-tempinduct-inductonly -maxsteps 8 ' if inductive else '-set-at 1 rst 1 '
                    sat += f'-falsify -prove {target} 0' if target else '-verify -prove-asserts'
                    script = area / (name + '.ys')
                    script.write_text(preparation(kind, configuration) + sat +
                        f' -dump_json /work/{name}.json -dump_vcd /work/{name}.vcd\n', encoding='utf-8')
                    log, entry = run(name, [str(executable), '-s', '/work/' + script.name], script)
                    marker = ('SAT proof finished - model found: FAIL!' if target else
                              'Induction step proven: SUCCESS!' if inductive else
                              'SAT proof finished - no model found: SUCCESS!')
                    require(marker in log, f'{name}: required SAT result missing')
                    counts = re.findall(r'^\s*(\d+)\s+\$assert\s*$', log, flags=re.MULTILINE)
                    expected_assertions = 37 if kind == 'queue' else 36 if configuration == 0 else 33
                    require(counts and int(counts[-1]) == expected_assertions,
                            f'{name}: expected exactly {expected_assertions} assertions')
                    assertions = int(counts[-1])
                    checks = re.findall(r'Found and reported (\d+) problems\.', log)
                    require(checks and all(int(count) == 0 for count in checks), f'{name}: undriven/conflicting logic')
                    entry.update(kind=kind, configuration=configuration, target=target, assertions=assertions, bound=bound,
                                 induction=inductive)
                    if target:
                        model, vcd = area / (name + '.json'), area / (name + '.vcd')
                        require(model.is_file() and vcd.is_file() and model.stat().st_size and vcd.stat().st_size, 'Missing cover trace')
                        witness = review_witness(vcd, model, BOUND, kind, configuration, target)
                        report['witnesses'].append(witness)
                        entry.update(model_sha256_bytes=digest(model), vcd_sha256_bytes=digest(vcd), independently_decoded=True)
                    else:
                        imports = log.count('Import proof for assert:')
                        entry['assertion_imports'] = imports
                        if inductive:
                            lengths = re.findall(r'^\[induction step (\d+)\] Solving problem', log, re.MULTILINE)
                            require(bool(lengths), f'{name}: missing induction length')
                            length = int(lengths[-1])
                            require(imports == assertions * (length + 1),
                                    f'{name}: incomplete induction assertion import')
                            base = next((c for c in report['commands'] if c['name'] == f'queue_{configuration}_base'), None)
                            require(base and base['passed'] and base['bound'] >= length + 2,
                                    f'{name}: corresponding reset-based base proof missing/too short')
                            require('-set-at 1 rst 1' not in script.read_text(encoding='utf-8'),
                                    f'{name}: induction step incorrectly relies on reset')
                            entry.update(induction_length=length, base_case_command=base['name'], reset_low_all_step_frames=True)
                        else:
                            require(imports == assertions * bound, f'{name}: incomplete assertion import')
                    require(hashes() == before and digest(script) == entry['script_sha256_bytes'], 'Input/script changed during query')
                    entry['passed'] = True; save(); print(f'PASS {name}', flush=True)
        require(len(report['commands']) == expected_queries + 1, 'Incomplete query set')
        report.update(passed=True, result='PASS', queries=expected_queries)
    except (Exception, KeyboardInterrupt) as error:
        report['error'] = f'{type(error).__name__}: {error}'; print(report['error'], flush=True)
    finally:
        unchanged = len(original) == len(INPUTS) and all((ROOT / name).read_bytes() == data and
                    (area / 'inputs' / name).read_bytes() == data for name, data in original.items())
        report['source_hashes_unchanged'] = unchanged
        if not unchanged:
            report.update(passed=False, result='FAIL', source_error='Source/snapshot changed')
        report['source_hashes_after'] = hashes() if all((ROOT / name).is_file() for name in INPUTS) else None
        report['utc_finished'] = datetime.now(timezone.utc).isoformat(); save()
        (area / 'witness_cycles.txt').write_text('\n\n'.join(w['vcd'] + '\n' + w['cycle_table'] for w in report['witnesses']) + '\n', encoding='utf-8')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
