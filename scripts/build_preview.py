"""Build the P4 BRAM preview and record source/bitstream identity."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def source_files():
    return (sorted((ROOT / 'rtl').rglob('*.sv')) +
            [ROOT / 'accelerator nexys.srcs/sources_1/new' / f'uart_{kind}.sv'
             for kind in ('rx', 'tx')] +
            [ROOT / 'platform/nexys_a7/gemm_preview_top.sv'])


def hashes(paths):
    return {str(p.relative_to(ROOT)).replace('\\', '/'):
            hashlib.sha256(p.read_text(encoding='utf-8').encode()).hexdigest()
            for p in paths}


def tcl_path(path):
    value = str(path.resolve()).replace('\\', '/')
    if any(c in value for c in '{}\n\r'):
        raise ValueError('Tcl build paths cannot contain braces or newlines')
    return '{' + value + '}'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vivado', default=shutil.which('vivado') or
                        'C:/AMDDesignTools/2026.1/Vivado/bin/vivado.bat')
    parser.add_argument('--baud', type=int, choices=(115200, 1000000), default=115200)
    parser.add_argument('--build-dir', type=Path, default=ROOT / 'build/preview')
    args = parser.parse_args()
    out = args.build_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    sources = source_files()
    constraint = ROOT / 'platform/nexys_a7/preview.xdc'
    inputs = sources + [constraint, Path(__file__).resolve(), ROOT / 'scripts/build_preview.tcl']
    source_hashes = hashes(inputs)
    identity = {'sources': source_hashes, 'baud': args.baud,
                'core_hz': 100000000, 'part': 'xc7a50ticsg324-1L'}
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).digest()
    build_id = int.from_bytes(digest[:4], 'little')
    config = out / 'config.tcl'
    config.write_text('\n'.join([
        f'set preview_out {tcl_path(out)}',
        f'set preview_sources [list {" ".join(tcl_path(p) for p in sources)}]',
        f'set preview_xdc {tcl_path(constraint)}',
        f'set preview_baud {args.baud}', f'set preview_build_id {build_id}', '']), encoding='utf-8')
    # Preserve the exact input identity even if synthesis/timing later fails.
    (out / 'inputs.json').write_text(json.dumps({
        'build_id': build_id, 'baud': args.baud, 'core_hz': identity['core_hz'],
        'part': identity['part'], 'source_sha256_utf8_lf': source_hashes,
    }, indent=2) + '\n', encoding='utf-8')
    command = [args.vivado, '-mode', 'batch', '-notrace', '-source',
               str(ROOT / 'scripts/build_preview.tcl'), '-log', str(out / 'vivado.log'),
               '-journal', str(out / 'vivado.jou'), '-tclargs', str(config)]
    # Prevent stale success files from surviving a failed build. Never remove
    # source files or whole directories here.
    for name in ('build.json', 'timing_pass.json', 'gemm_preview.bit'):
        (out / name).unlink(missing_ok=True)
    kwargs = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    print(f'Building preview ID 0x{build_id:08x}, {args.baud} baud; logs: {out}', flush=True)
    with (out / 'console.txt').open('w', encoding='utf-8') as log:
        result = subprocess.run(command, cwd=out, stdout=log, stderr=subprocess.STDOUT, **kwargs)
    if result.returncode or not (out / 'timing_pass.json').exists():
        raise RuntimeError(f'Vivado did not pass the build gates. Read {out / "console.txt"}')
    if hashes(inputs) != source_hashes:
        raise RuntimeError('Sources changed during the build; rebuild before using this image')
    bitstream = out / 'gemm_preview.bit'
    git = lambda *cmd: subprocess.check_output(['git', '-C', str(ROOT), *cmd], text=True).strip()
    manifest = {
        'schema_version': 1, 'build_id': build_id, 'bitstream': bitstream.name,
        'bitstream_sha256': hashlib.sha256(bitstream.read_bytes()).hexdigest(),
        'source_commit': git('rev-parse', 'HEAD'),
        'source_dirty': bool(git('status', '--porcelain')),
        'source_sha256_utf8_lf': source_hashes,
        'core_hz': identity['core_hz'], 'baud': args.baud, 'part': identity['part'],
        'p': 4, 't': 32, 'kmax': 256,
        'physical_checks': json.loads((out / 'timing_pass.json').read_text()),
    }
    (out / 'build.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print(f'PASS: {out / "build.json"}', flush=True)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
