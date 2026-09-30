"""Verify a preview manifest, then load its bitstream into FPGA configuration RAM."""
import argparse
from pathlib import Path
import os
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from host.preview.client import load_manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=ROOT / 'build/preview/build.json')
    parser.add_argument('--vivado', default=shutil.which('vivado') or
                        'C:/AMDDesignTools/2026.1/Vivado/bin/vivado.bat')
    args = parser.parse_args()
    manifest_path = args.manifest.resolve()
    manifest = load_manifest(manifest_path)
    if manifest['part'] != 'xc7a50ticsg324-1L':
        raise ValueError('This programming script targets the verified Nexys A7-50T configuration')
    bitstream = manifest_path.parent / manifest['bitstream']
    command = [args.vivado, '-mode', 'batch', '-notrace', '-source',
               str(ROOT / 'scripts/program_preview.tcl'), '-log',
               str(manifest_path.parent / 'program.log'), '-journal',
               str(manifest_path.parent / 'program.jou'), '-tclargs', str(bitstream)]
    kwargs = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    print(f'Programming build 0x{manifest["build_id"]:08x}; SHA-256 {manifest["bitstream_sha256"]}', flush=True)
    subprocess.run(command, cwd=manifest_path.parent, check=True, **kwargs)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
