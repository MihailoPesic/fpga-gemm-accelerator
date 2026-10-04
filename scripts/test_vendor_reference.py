"""Check the vendor fixture's actual reference functions without DUT or DDR IP."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT/'tb/vendor/tb_ddr_gemm.sv'
SEED = 0xDD26


def expected(job, row, col, count):
    a = [-128 if job == 1 or (row == 0 and k == 0)
         else (row*17+k*11+3) % 31-15 for k in range(count)]
    b = [-128 if job == 1 else 127 if k == 0 and col == 0
         else (k*13+col*7+1) % 29-14 for k in range(count)]
    return sum(x*y for x, y in zip(a, b))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--simulator', choices=('iverilog', 'xsim'), default='iverilog')
    parser.add_argument('--build-dir', type=Path, default=ROOT/'build/test_vendor_reference')
    parser.add_argument('--vivado-bin', type=Path)
    args = parser.parse_args()
    out = args.build_dir.resolve()/args.simulator
    out.mkdir(parents=True, exist_ok=True)
    (out/'summary.json').unlink(missing_ok=True)
    recorded = (FIXTURE, Path(__file__).resolve())

    def source_hashes():
        return {p.relative_to(ROOT).as_posix(): hashlib.sha256(
            p.read_text(encoding='utf-8').encode()).hexdigest() for p in recorded}

    before = source_hashes()
    text = FIXTURE.read_text(encoding='utf-8')
    extracted = []
    for name in ('a_value', 'b_value', 'expected_element'):
        matches = re.findall(rf'function automatic integer {name}\b.*?endfunction', text, re.S)
        if len(matches) != 1:
            raise ValueError(f'Expected exactly one reference function {name}')
        extracted.append(matches[0])
    checks = re.findall(r'task automatic check_reference_oracle\b.*?endtask', text, re.S)
    if len(checks) != 1:
        raise ValueError('Expected exactly one early reference self-check')
    extracted.append(checks[0])

    cases = [(1, 0, 0, 1)] + [(2, r, c, 9) for r in range(5) for c in range(3)]
    literal = [16384, -16185, 667, -256, 606, 231, -198, -1148, -346,
               240, 1035, 255, -159, -936, -74, 93]
    if [expected(*case) for case in cases] != literal:
        raise ValueError('Independent directed reference constants disagree')
    for k in (1, 2, 7, 8, 9, 31, 32, 33, 255, 256):
        cases += [(1, 0, 0, k)] + [(2, r, c, k) for r in (0, 4) for c in (0, 2)]
    rng = random.Random(SEED)
    cases += [(2, rng.randrange(32), rng.randrange(32), rng.randrange(1, 257))
              for _ in range(128)]
    calls = '\n'.join(f"check_value({j},{r},{c},{k},32'h{expected(j,r,c,k)&0xffffffff:08x});"
                      for j, r, c, k in cases)
    harness = '''`timescale 1ns/1ps
module vendor_reference;
'''+'\n'.join(extracted)+'''
task automatic check_value(input integer job,row,col,count,input logic[31:0] wanted);
    integer got,lane;
    logic[7:0] actual_byte,expected_byte;
    begin
        #1;
        got=expected_element(job,row,col,count);
        if (got!==wanted)
            $fatal(1,"REFERENCE_FAIL job=%0d row=%0d col=%0d k=%0d got=%h expected=%h",
                   job,row,col,count,got,wanted);
        for(lane=0;lane<4;lane=lane+1) begin
            actual_byte=got>>(8*lane);
            expected_byte=wanted[8*lane+:8];
            if(actual_byte!==expected_byte) $fatal(1,"REFERENCE_FAIL byte serialization");
        end
    end
endtask
initial begin
    check_reference_oracle();
'''+calls+f'''
    $display("VENDOR_REFERENCE_PASS cases={len(cases)} bytes={4*len(cases)}");
    $finish;
end
endmodule
'''
    (out/'vendor_reference.sv').write_text(harness, encoding='utf-8', newline='\n')
    if args.simulator == 'iverilog':
        commands = [['iverilog', '-g2012', '-Wall', '-s', 'vendor_reference',
                     '-o', 'vendor_reference.vvp', 'vendor_reference.sv'],
                    ['vvp', 'vendor_reference.vvp']]
    else:
        binary = shutil.which('xvlog')
        directory = args.vivado_bin or (Path(binary).parent if binary else
                                       Path('C:/AMDDesignTools/2026.1/Vivado/bin'))
        tool = lambda name: str(directory/(name+'.bat' if os.name == 'nt' else name))
        commands = [[tool('xvlog'), '-sv', 'vendor_reference.sv'],
                    [tool('xelab'), '--debug', 'off', '--relax', '--mt', 'auto',
                     'vendor_reference', '-s', 'vendor_reference_snapshot'],
                    [tool('xsim'), 'vendor_reference_snapshot', '-runall']]
    kwargs = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    logs = []
    for index, command in enumerate(commands):
        log = out/f'command_{index}.txt'
        with log.open('wb') as stream:
            result = subprocess.run(command, cwd=out, stdout=stream,
                                    stderr=subprocess.STDOUT, **kwargs)
        logs.append(log)
        log_text = log.read_text(encoding='utf-8', errors='replace')
        if result.returncode or re.search(r'\b(?:ERROR|FATAL|Fatal)\s*:', log_text):
            raise RuntimeError(f'Reference regression failed; preserved log: {log}')
    final = logs[-1].read_text(encoding='utf-8', errors='replace')
    marker = f'VENDOR_REFERENCE_PASS cases={len(cases)} bytes={4*len(cases)}'
    if final.count(marker) != 1 or 'DDR_GEMM_ORACLE_PASS jobs=2 outputs=16' not in final:
        raise RuntimeError('Missing completed reference checks')
    if before != source_hashes():
        raise RuntimeError('Reference sources changed during regression')
    summary = {'result': 'PASS', 'simulator': args.simulator, 'cases': len(cases),
               'checked_bytes': 4*len(cases), 'seed': SEED,
               'scope': 'Extracted vendor testbench reference functions and early self-check; no DUT, vendor IP or hardware',
               'source_sha256_utf8_lf': before, 'commands': commands,
               'artifact_sha256_bytes': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                         for p in [out/'vendor_reference.sv', *logs]}}
    (out/'summary.json').write_text(json.dumps(summary, indent=2)+'\n',
                                   encoding='utf-8', newline='\n')
    print(f'PASS: {args.simulator}; {len(cases)} cases, {4*len(cases)} bytes; {out}')


if __name__ == '__main__':
    main()
