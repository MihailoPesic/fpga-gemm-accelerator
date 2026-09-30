"""Generate the Nexys A7-50T AXI MIG and run its DDR2 vendor-model smoke test."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / 'accelerator nexys.srcs/sources_1/ip/mig_7series_0/mig_a.prj'
AXI = {'C0_C_RD_WR_ARB_ALGORITHM': 'RD_PRI_REG', 'C0_S_AXI_ADDR_WIDTH': '27',
       'C0_S_AXI_DATA_WIDTH': '128', 'C0_S_AXI_ID_WIDTH': '4',
       'C0_S_AXI_SUPPORTS_NARROW_BURST': '1'}


def text_hash(path):
    return hashlib.sha256(path.read_text(encoding='utf-8').encode()).hexdigest()


def byte_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def derive_configuration():
    tree = ET.parse(BASELINE)
    project = tree.getroot()
    controllers = project.findall('Controller')
    if len(controllers) != 1:
        raise ValueError('Expected one saved DDR2 controller')
    controller = controllers[0]
    required = {'MemoryDevice': 'DDR2_SDRAM/Components/MT47H64M16HR-25E',
                'TimePeriod': '5000', 'PHYRatio': '4:1', 'InputClkFreq': '100',
                'DataWidth': '16', 'RowAddress': '13', 'ColAddress': '10',
                'BankAddress': '3', 'PortInterface': 'NATIVE'}
    for name, value in required.items():
        if controller.findtext(name) != value:
            raise ValueError(f'Unexpected baseline {name}; review the platform configuration')
    for name, value in {'TargetFPGA': 'xc7a50ti-csg324/-1L',
                        'SystemClock': 'No Buffer', 'ReferenceClock': 'No Buffer',
                        'SysResetPolarity': 'ACTIVE LOW'}.items():
        if project.findtext(name) != value:
            raise ValueError(f'Unexpected baseline {name}')
    pins = {pin.attrib['name']: dict(pin.attrib)
            for pin in controller.findall('PinSelection/Pin')}
    if len(pins) != 46 or controller.find('AXIParameters') is not None:
        raise ValueError('Unexpected baseline pin/interface configuration')
    # Preserve the proven memory timings, clock inputs and all DDR pin fields.
    # AMD UG911 documents the XML_INPUT_FILE configuration flow. Only the
    # logical interface and module name change; Vivado regenerates the IP.
    project.find('ModuleName').text = 'gemm_mig_axi'
    controller.find('PortInterface').text = 'AXI'
    ET.SubElement(controller, 'C0_MEM_SIZE').text = '134217728'
    axi = ET.SubElement(controller, 'AXIParameters')
    for name, value in AXI.items():
        ET.SubElement(axi, name).text = value
    ET.indent(tree)
    return ET.tostring(project, encoding='unicode') + '\n', pins


def tcl_path(path):
    value = str(path.resolve()).replace('\\', '/')
    if any(char in value for char in '{}\r\n'):
        raise ValueError('Tcl paths cannot contain braces or newlines')
    return '{' + value + '}'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vivado', default=shutil.which('vivado') or
                        'C:/AMDDesignTools/2026.1/Vivado/bin/vivado.bat')
    parser.add_argument('--build-dir', type=Path, default=ROOT / 'build/axi_mig')
    args = parser.parse_args()
    out = args.build_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    xml, pins = derive_configuration()
    prj = out / 'mig_axi.prj'
    if prj.exists() and prj.read_text(encoding='utf-8') != xml:
        raise ValueError('MIG settings changed; select a fresh --build-dir for regeneration')
    prj.write_text(xml, encoding='utf-8')
    sources = [BASELINE, Path(__file__).resolve(), ROOT / 'scripts/test_mig.tcl',
               ROOT / 'tb/vendor/tb_mig_axi.sv']
    hashes = {path.relative_to(ROOT).as_posix(): text_hash(path) for path in sources}
    (out / 'config.tcl').write_text(
        f'set mig_repo {tcl_path(ROOT)}\nset mig_out {tcl_path(out)}\n', encoding='utf-8')
    for name in ('summary.json', 'simulation_pass.txt'):
        (out / name).unlink(missing_ok=True)
    command = [args.vivado, '-mode', 'batch', '-notrace', '-source',
               str(ROOT / 'scripts/test_mig.tcl'), '-log', str(out / 'vivado.log'),
               '-journal', str(out / 'vivado.jou'), '-tclargs', str(out / 'config.tcl')]
    kwargs = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    print(f'AXI MIG vendor simulation; logs: {out}', flush=True)
    with (out / 'console.txt').open('w', encoding='utf-8') as log:
        result = subprocess.run(command, cwd=out, stdout=log, stderr=subprocess.STDOUT, **kwargs)
    log = (out / 'console.txt').read_text(encoding='utf-8', errors='replace')
    pattern = (r'MIG_AXI_MONITOR_PASS calibration_ns=([\d.]+) read_bursts=(\d+) '
               r'write_bursts=(\d+) read_beats=(\d+) write_beats=(\d+) write_responses=(\d+)')
    monitor = re.search(pattern, log)
    addresses = re.search(r'MIG_AXI_ADDRESSES ar_min=(\d+) ar_max=(\d+) aw_min=(\d+) aw_max=(\d+)', log)
    if (result.returncode or monitor is None or addresses is None or 'TEST PASSED' not in log or
            'MIG_AXI_FINAL_PASS' not in log or
            re.search(r'TEST FAILED|MIG_AXI_MONITOR_FAIL|Fatal:|FATAL:|ERROR:|CRITICAL WARNING:', log) or
            not (out / 'simulation_pass.txt').exists()):
        raise RuntimeError(f'MIG simulation failed; inspect {out / "console.txt"}')
    if any(text_hash(ROOT / name) != digest for name, digest in hashes.items()):
        raise RuntimeError('Sources changed during simulation; rerun before accepting results')
    xci_path = out / 'project/axi_mig.srcs/sources_1/ip/gemm_mig_axi/gemm_mig_axi.xci'
    xci = json.loads(xci_path.read_text())['ip_inst']
    configured_xml = xci['parameters']['component_parameters']['XML_INPUT_FILE'][0]['value']
    if (xci_path.parent / configured_xml).resolve() != prj.resolve():
        raise RuntimeError('Generated MIG references a different memory configuration')
    params = xci['parameters']['model_parameters']
    actual = {name: params[name][0]['value'] for name in
              ('USE_AXI', 'C_S_AXI_DATA_WIDTH', 'C_S_AXI_ADDR_WIDTH',
               'C_S_AXI_ID_WIDTH', 'DDR2_nCK_PER_CLK', 'SYSCLK_TYPE', 'REFCLK_TYPE',
               'MEM_TYPE', 'C_S_AXI_MEM_SIZE')}
    expected = {'USE_AXI': '1', 'C_S_AXI_DATA_WIDTH': '128', 'C_S_AXI_ADDR_WIDTH': '27',
                'C_S_AXI_ID_WIDTH': '4', 'DDR2_nCK_PER_CLK': '4',
                'SYSCLK_TYPE': 'NOBUF', 'REFCLK_TYPE': 'NOBUF',
                'MEM_TYPE': 'DDR2', 'C_S_AXI_MEM_SIZE': '134217728'}
    if actual != expected:
        raise RuntimeError(f'Generated MIG parameters differ: {actual}')
    counts = dict(zip(('read_bursts', 'write_bursts', 'read_beats', 'write_beats',
                       'write_responses'), map(int, monitor.groups()[1:])))
    if min(counts.values()) <= 0:
        raise RuntimeError('Vendor test did not complete both AXI directions')
    gen = out / 'project/axi_mig.gen/sources_1/ip/gemm_mig_axi/gemm_mig_axi'
    imported = sorted((out / 'project/imports').glob('*'))
    for path in imported:
        originals = list((gen / 'example_design').rglob(path.name))
        if len(originals) != 1 or path.read_bytes() != originals[0].read_bytes():
            raise RuntimeError(f'Imported vendor example differs from generated source: {path.name}')
    constraints = (gen / 'user_design/constraints/gemm_mig_axi.xdc').read_text()
    for setting, field in (('PACKAGE_PIN', 'PADName'), ('IOSTANDARD', 'IOSTANDARD')):
        assignments = dict((port, value) for value, port in re.findall(
            r'^set_property ' + setting + r'\s+(\S+)\s+\[get_ports\s+\{(ddr2_[^}]+)\}',
            constraints, re.MULTILINE))
        if assignments != {name: pin[field] for name, pin in pins.items()}:
            raise RuntimeError(f'Generated DDR {setting} assignments differ from the saved board map')
    for name in ('gemm_mig_axi_mig.v', 'gemm_mig_axi_mig_sim.v'):
        rtl = (gen / 'user_design/rtl' / name).read_text()
        if not re.search(r'parameter\s+C_S_AXI_SUPPORTS_NARROW_BURST\s*=\s*1\s*,', rtl):
            raise RuntimeError(f'Narrow AXI support missing from generated {name}')
    generated_files = sorted(path for path in gen.rglob('*') if path.is_file() and
                             path.suffix in ('.v', '.vh', '.prj', '.xdc'))
    git = lambda *cmd: subprocess.check_output(['git', '-C', str(ROOT), *cmd], text=True).strip()
    summary = {'result': 'PASS', 'utc': datetime.now(timezone.utc).isoformat(),
               'scope': 'Generated AXI MIG plus vendor DDR2 model; no SmartConnect, custom DMA or board test',
               'source_commit': git('rev-parse', 'HEAD'),
               'source_dirty': bool(git('status', '--porcelain')),
               'source_sha256_utf8_lf': hashes,
               'configuration_sha256_utf8_lf': text_hash(prj),
               'configuration_reference_checked': True,
               'ip': xci['component_reference'], 'ip_revision': xci['ip_revision'],
               'vivado': (out / 'simulation_pass.txt').read_text().strip(),
               'part': 'xc7a50ticsg324-1L', 'generated_parameters': actual,
               'memory_bytes': 134217728, 'memory_clock_hz': 200000000,
               'ui_clock_hz_measured_in_simulation': 50000000,
               'ddr_pins_preserved': len(pins), 'axi_configuration': AXI,
               'calibration': 'FAST', 'calibration_ns': float(monitor.group(1)),
               'vendor_post_calibration_ns': 50000,
               'traffic': counts,
               'accepted_address_start_extrema': dict(zip(('ar_min', 'ar_max', 'aw_min', 'aw_max'),
                                                          map(int, addresses.groups()))),
               'example_address_parameters': {'BEGIN_ADDRESS': 0, 'END_ADDRESS': 0x00000fff,
                                              'PRBS_EADDR_MASK_POS': 0xff000000},
               'narrow_traffic_test': False,
               'limitations': 'Example traffic is not exhaustive memory coverage or a physical DDR test.',
               'console_sha256_utf8_lf': text_hash(out / 'console.txt'),
               'imported_example_sha256_bytes': {
                   path.relative_to(out).as_posix(): byte_hash(path) for path in imported},
               'generated_sha256_bytes': {path.relative_to(out).as_posix(): byte_hash(path)
                                          for path in generated_files}}
    (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    print(f'PASS: {out / "summary.json"}', flush=True)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
