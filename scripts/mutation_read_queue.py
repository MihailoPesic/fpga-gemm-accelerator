"""Check that the queue suite detects two external-stop defects in private RTL copies."""
from pathlib import Path
import argparse
import hashlib
from importlib.metadata import version
import json
import os
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT/'rtl/memory/gemm_axi_burst.sv'
FILES = [SOURCE]+[ROOT/name for name in ('tb/test_axi_burst.py', 'tb/test_axi_read_queue.py',
                                      'tb/common.py', 'requirements-test.txt')]+[Path(__file__).resolve()]


def hashes():
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_text(encoding='utf-8').encode()).hexdigest()
            for p in FILES}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', type=Path, default=ROOT/'build/mutation_read_queue')
    args = parser.parse_args()
    out = args.build_dir.resolve()
    if out.exists():
        raise RuntimeError('Mutation output must be fresh')
    out.mkdir(parents=True)
    before = hashes()
    original = SOURCE.read_text(encoding='utf-8')
    mutations = (
        ('pending_reads_ignore_external_stop', '(fatal || fault_now || stop_reads)', '(fatal || fault_now)'),
        ('external_stop_arrives_one_edge_late', 'wire stop_reads = rd_cancel || read_cancelled;',
         'wire stop_reads = read_cancelled;'),
    )
    sys.path.insert(0, str(ROOT/'tb'))
    os.environ['PYTHONPATH'] = str(ROOT/'tb')+os.pathsep+os.environ.get('PYTHONPATH','')
    from cocotb_tools.runner import get_runner
    results=[]
    for name, old, new in mutations:
        assert original.count(old)==1
        build=out/name; build.mkdir()
        mutant=build/'mutated_axi_burst.sv'
        mutant.write_text(original.replace(old,new),encoding='utf-8')
        runner=get_runner('icarus')
        runner.build(sources=[mutant],hdl_toplevel='gemm_axi_burst',parameters={'READ_SLOTS':4},
                     build_dir=build,build_args=['-g2012','-Wall'],always=True,timescale=('1ns','1ps'))
        xml=runner.test(hdl_toplevel='gemm_axi_burst',test_module='test_axi_read_queue',
                        build_dir=build,test_dir=build,extra_env={'GEMM_READ_SLOTS':'4'})
        tests=list(ET.parse(xml).iter('testcase'))
        failures=[t.attrib['name'] for t in tests if list(t.iter('failure'))]
        assert len(tests)==12 and failures==['external_cancel_preserves_held_ar_and_cancels_pending'], failures
        assert not any(list(t.iter(tag)) for t in tests for tag in ('error','skipped'))
        assert hashes()==before,'Production inputs changed during mutation checks'
        results.append(dict(name=name,result='DETECTED',tests=len(tests),expected_failures=failures,
            replacement=dict(old=old,new=new),
            mutant_sha256_bytes=hashlib.sha256(mutant.read_bytes()).hexdigest(),
            failing_xml_sha256_bytes=hashlib.sha256(Path(xml).read_bytes()).hexdigest()))
    (out/'summary.json').write_text(json.dumps(dict(result='DETECTED',mutations=results,
        source_hashes_unchanged=True,source_sha256_utf8_lf=before,
        versions={'python':sys.version, **{name:version(name) for name in ('cocotb','cocotbext-axi','cocotb-bus')}},
        scope='Deliberately broken private RTL copies; failing tests demonstrate defect detection. Production RTL was not modified.'),indent=2)+'\n')
    print('PASS: both external read-stop mutations detected by the permanent queue regression')


if __name__=='__main__':
    main()
