"""Load a qualified DDR GEMM image after cold board power-up in JTAG mode."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from host.gemm import load_manifest

REPORTS = frozenset(('timing.txt', 'check_timing.txt', 'utilization.txt', 'clocks.txt',
                    'clock_interaction.txt', 'clock_utilization.txt', 'route.txt', 'drc.txt',
                    'methodology.txt', 'cdc.txt', 'pulse_width.txt',
                    'pulse_width_violations.txt', 'bus_skew.txt'))
IDENTITY_FIELDS = ('kind', 'part', 'id', 'version', 'mode', 'build_id', 'p', 't', 'kmax',
                   'core_hz', 'baud', 'simulation_baud', 'simulation_debug',
                   'source_sha256_utf8_lf', 'configuration_sha256_utf8_lf', 'generated_sha256')


def verified_bytes(path, expected_hash):
    if not isinstance(expected_hash, str) or not re.fullmatch(r'[0-9a-f]{64}', expected_hash):
        raise ValueError(f'Missing or invalid SHA-256 for {path.name}')
    raw = path.read_bytes()
    if not raw or hashlib.sha256(raw).hexdigest() != expected_hash:
        raise ValueError(f'Qualification evidence changed: {path.name}')
    return raw


def qualified_manifest(path):
    """Bind saved evidence to this image without requiring the original checkout."""
    path = Path(path).resolve()
    manifest = load_manifest(path)
    read_slots = manifest.get('read_slots', 1)
    if type(read_slots) is not int or read_slots not in (1, 4):
        raise ValueError('Manifest lacks a supported read-command depth')
    overlap = manifest.get('kind') == 'overlap_ddr_gemm'
    expected = {'result': 'PASS', 'kind': 'overlap_ddr_gemm' if overlap else 'serial_ddr_gemm',
                'mode': 'selectable' if overlap else 'serial',
                'part': 'xc7a50ticsg324-1L', 'id': 0x314d474e, 'version': 0x200 if overlap else 0x100,
                'core_hz': 100_000_000, 'vivado_exit_code': 0, 'source_hashes_unchanged': True}
    if (any(manifest.get(key) != value for key, value in expected.items()) or
            manifest.get('source_hashes_unchanged') is not True):
        raise ValueError('A qualified Nexys A7-50T DDR GEMM build manifest is required')
    if overlap and manifest.get('enable_overlap') is not True:
        raise ValueError('Overlap build manifest lacks its scheduler selection')
    if not overlap and manifest.get('enable_overlap', False) is not False:
        raise ValueError('Serial build manifest has a conflicting scheduler selection')
    for field in ('source_sha256_utf8_lf', 'generated_sha256'):
        hashes = manifest.get(field)
        if (not isinstance(hashes, dict) or not hashes or
                any(not isinstance(name, str) or not name or not isinstance(digest, str) or
                    not re.fullmatch(r'[0-9a-f]{64}', digest) for name, digest in hashes.items())):
            raise ValueError(f'Manifest lacks a valid archived {field} identity')
    configuration = manifest.get('configuration_sha256_utf8_lf')
    if not isinstance(configuration, str) or not re.fullmatch(r'[0-9a-f]{64}', configuration):
        raise ValueError('Manifest lacks a valid archived MIG configuration identity')
    checks = manifest.get('physical_checks')
    if (not isinstance(checks, dict) or checks.get('core_hz') != manifest['core_hz'] or
            checks.get('bus_skew_checks') != 10 or
            any(type(checks.get(name)) not in (int, float) or not math.isfinite(checks[name]) or
                checks[name] < 0 for name in ('wns_ns', 'whs_ns', 'bus_skew_worst_slack_ns'))):
        raise ValueError('Manifest lacks passing routed setup, hold and bus-skew checks')
    simulation = json.loads(verified_bytes(path.parent/'simulation.json',
                                          manifest.get('simulation_sha256_bytes')))
    if (not isinstance(simulation, dict) or simulation.get('schema_version') != 1 or
            simulation.get('result') != 'PASS' or simulation.get('vivado_exit_code') != 0 or
            simulation.get('source_hashes_unchanged') is not True or
            any(key not in manifest or simulation.get(key) != manifest[key] for key in IDENTITY_FIELDS)):
        raise ValueError('Vendor simulation does not qualify this exact source/build identity')
    # Earlier serial archives predate this field and have depth one. New
    # archives bind the selected depth to the matching simulation identity.
    simulated_slots = simulation.get('read_slots', 1)
    if type(simulated_slots) is not int or simulated_slots != read_slots:
        raise ValueError('Vendor simulation read-command depth differs from the image')
    if overlap and simulation.get('enable_overlap') is not True:
        raise ValueError('Vendor simulation does not qualify the overlap scheduler')
    if not overlap and simulation.get('enable_overlap', False) is not False:
        raise ValueError('Serial vendor simulation has a conflicting scheduler selection')
    reports = manifest.get('physical_report_sha256_bytes')
    if not isinstance(reports, dict) or set(reports) != REPORTS:
        raise ValueError('Manifest lacks the complete routed-report identity')
    for name, digest in reports.items():
        verified_bytes(path.parent/name, digest)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=ROOT/'build/gemm/build.json')
    parser.add_argument('--vivado', default=shutil.which('vivado') or
                        'C:/AMDDesignTools/2026.1/Vivado/bin/vivado.bat')
    args = parser.parse_args(argv)
    path = args.manifest.resolve()
    manifest = qualified_manifest(path)
    bitstream = path.parent/manifest['bitstream']
    command = [args.vivado, '-mode', 'batch', '-notrace', '-source',
               str(ROOT/'scripts/program_ddr_gemm.tcl'), '-log', str(path.parent/'program.log'),
               '-journal', str(path.parent/'program.jou'), '-tclargs', str(bitstream)]
    kwargs = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    print(f'Programming DDR GEMM 0x{manifest["build_id"]:08x}; '
          f'SHA-256 {manifest["bitstream_sha256"]}', flush=True)
    subprocess.run(command, cwd=path.parent, check=True, **kwargs)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
