"""Collector tests with synthetic sealed records; never board measurements."""
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from scripts import collect_benchmarks as collector
from scripts import qualify_release as runner
from host.gemm import Descriptor


def fixture(directory,tile,modes,case_indexes=(0,)):
    """Create explicit software records, not FPGA timing or correctness proof."""
    directory=Path(directory)
    directory.mkdir(parents=True)
    run_plan=runner.plan(modes)
    bit_hash=hashlib.sha256(f'SOFTWARE_ONLY_TILE_{tile}'.encode()).hexdigest()
    source={'software_fixture.py':'a'*64}
    saved=dict(plan=run_plan,manifest_sha256_bytes='b'*64,build_id=tile,bitstream_sha256=bit_hash,
               host_source_sha256_utf8_lf=source)
    runner.atomic_json(directory/'plan.json',saved)
    for case in run_plan['benchmark_cases']:
        if case['index'] not in case_indexes: continue
        unit=f"benchmark/case_{case['index']:02d}_{case['m']}x{case['n']}x{case['k']}"
        folder=directory/unit
        folder.mkdir(parents=True)
        binding=dict(plan_sha256=runner.canonical_hash(run_plan),manifest_sha256_bytes=saved['manifest_sha256_bytes'],
            build_id=tile,bitstream_sha256=bit_hash,host_source_sha256_utf8_lf=source,unit=unit)
        manifest=dict(result='PASS',p=8,t=tile,kmax=256,core_hz=100000000,build_id=tile,bitstream_sha256=bit_hash,
            baud=1000000,read_slots=4,part='software-fixture',id=0x314d474e,version=0x200,
            kind='overlap_ddr_gemm',enable_overlap=True,configuration_sha256_utf8_lf='c'*64,
            source_sha256_utf8_lf={'SOFTWARE_ONLY_RTL':'d'*64})
        m,n,k=case['m'],case['n'],case['k']
        runs=[]
        for sample,mode in runner.order(modes,30):
            desc=Descriptor.layout(m,n,k,mode=mode)
            counts=runner.traffic(desc,8,tile)
            cycles=counts['compute_cycles']+(counts['read_beats']+counts['write_beats'])//(mode+1)+100
            allocation=desc.m*desc.a_stride+desc.n*desc.bt_stride+desc.m*desc.c_stride+384
            c_hash=hashlib.sha256(f'SOFTWARE_C_CASE_{case["index"]}_SAMPLE_{sample}'.encode()).hexdigest()
            runs.append(dict(state='PASS',passed=True,job_id=len(runs)+1,sample=sample,mode=mode,
                seed=case['seed'],m=m,n=n,k=k,p=8,t=tile,core_hz=100000000,build_id=tile,
                descriptor=runner.asdict(desc),compared_elements=m*n,allocation_bytes_checked=allocation,
                guard_input_bytes_checked=allocation-4*m*n,input_a_sha256_bytes='1'*64,raw_b_sha256_bytes='2'*64,
                oracle_sha256_bytes='3'*64,c_before_sha256_bytes=c_hash,job_cycles=cycles,
                **counts,input_wait_cycles=0,read_stall_cycles=7,write_stall_cycles=11,
                core_seconds=cycles/100000000,useful_gops=2*m*n*k*100000000/cycles/1e9,
                useful_utilization=m*n*k/(64*cycles),resident_host_seconds=2.,validation_seconds=.2,
                c_initialize_seconds=.4,configure_seconds=.1,job_wall_seconds=.5,allocation_download_seconds=1.))
        report=dict(schema_version=1,kind='SOFTWARE_ONLY_COLLECTOR_FIXTURE',state='PASS',passed=True,binding=binding,
            manifest=manifest,runs=runs,checked_counts=runner.counts(runs),source_hashes_unchanged=True,
            transport=dict(retries=0,rejected_frames=0,poisoned=False))
        runner.atomic_json(folder/'case_000000.json',dict(shape=[m,n,k],seed=case['seed'],
            input_a_sha256_bytes='1'*64,raw_b_sha256_bytes='2'*64,oracle_sha256_bytes='3'*64,
            preparation_seconds=.01,oracle_seconds=.02,input_upload_seconds=.3))
        runner.atomic_json(folder/'results.json',report)
        runner.write_csv(folder/'results.csv',runs)
        runner.atomic_json(folder/'seal.json',dict(result='PASS',binding=binding,saved_artifact_sha256_bytes=runner.seals(folder)))
    return directory


def rewrite(directory,alter):
    folder=next((Path(directory)/'benchmark').iterdir())
    report=json.loads((folder/'results.json').read_text())
    alter(report)
    runner.atomic_json(folder/'results.json',report)
    runner.write_csv(folder/'results.csv',report['runs'])
    runner.atomic_json(folder/'seal.json',dict(result='PASS',binding=report['binding'],saved_artifact_sha256_bytes=runner.seals(folder)))


class CollectTests(unittest.TestCase):
    def test_partial_controls_metrics_and_transfer_volume(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            a,b=fixture(root/'t8',8,(0,)),fixture(root/'t32',32,(0,1))
            result=collector.collect(a,b,allow_partial=True)
            self.assertFalse(result['full_grid_complete'])
            self.assertEqual(result['completed_case_indexes'],[0])
            self.assertEqual(result['missing_case_indexes'],list(range(1,16)))
            rows={item['series']:item for item in result['series']}
            self.assertEqual([rows[letter]['samples'] for letter in 'ABC'],[30,30,30])
            self.assertEqual(rows['A']['metrics']['read_beats']['median'],512)
            self.assertEqual(rows['B']['metrics']['read_beats']['median'],128)
            self.assertEqual(rows['C']['metrics']['read_beats']['median'],128)
            self.assertEqual(result['comparisons'][0]['reuse_input_byte_ratio'],4)
            self.assertEqual(result['comparisons'][0]['transfer_byte_ratio'],8192/5120)
            self.assertEqual(result['comparisons'][0]['overlap_cycle_ratio'],
                             rows['B']['metrics']['job_cycles']['median']/rows['C']['metrics']['job_cycles']['median'])
            self.assertEqual(rows['C']['resident_input_preparation']['input_upload_seconds'],.3)
            self.assertEqual(rows['C']['metrics']['allocation_download_seconds']['median'],1.)

    def test_full_grid_requires_every_case_and_validates_late_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            a,b=fixture(root/'t8',8,(0,),range(16)),fixture(root/'t32',32,(0,1),range(16))
            result=collector.collect(a,b)
            self.assertTrue(result['full_grid_complete'])
            self.assertEqual(result['completed_case_indexes'],list(range(16)))
            self.assertEqual(result['missing_case_indexes'],[])
            self.assertEqual(sum(row['samples'] for row in result['series']),1440)
            folder=next(folder for folder in (b/'benchmark').iterdir() if folder.name.startswith('case_15_'))
            (folder/'case_000000.json').write_text('{}\n',encoding='utf-8')
            with self.assertRaises(ValueError): collector.collect(a,b,allow_partial=True)

    def test_missing_cases_require_explicit_partial_mode(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            a,b=fixture(root/'t8',8,(0,)),fixture(root/'t32',32,(0,1))
            with self.assertRaisesRegex(ValueError,'all 16 cases'):
                collector.collect(a,b)
            with redirect_stderr(io.StringIO()):
                code=collector.main(['--t8',str(a),'--t32',str(b),'--output',str(root/'no_output'),'--table-only'])
            self.assertEqual(code,1)
            self.assertFalse((root/'no_output').exists())

    def test_artifact_csv_and_sample_tampering_are_rejected(self):
        for defect in ('artifact','csv','missing_sample','resident_inputs'):
            with self.subTest(defect=defect),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary)
                a,b=fixture(root/'t8',8,(0,)),fixture(root/'t32',32,(0,1))
                folder=next((b/'benchmark').iterdir())
                if defect=='artifact':
                    with (folder/'results.json').open('a') as stream: stream.write(' ')
                elif defect=='csv':
                    (folder/'results.csv').write_text('different,columns\n')
                    seal=json.loads((folder/'seal.json').read_text())
                    seal['saved_artifact_sha256_bytes']=runner.seals(folder)
                    runner.atomic_json(folder/'seal.json',seal)
                elif defect=='missing_sample':
                    def remove(report):
                        report['runs'].pop()
                        report['checked_counts']=runner.counts(report['runs'])
                    rewrite(b,remove)
                else:
                    metadata=json.loads((folder/'case_000000.json').read_text())
                    metadata['raw_b_sha256_bytes']='f'*64
                    runner.atomic_json(folder/'case_000000.json',metadata)
                    rewrite(b,lambda report:None)
                with self.assertRaises(ValueError): collector.collect(a,b,True)

    def test_cross_build_source_clock_read_depth_and_input_changes_are_rejected(self):
        for defect in ('source','clock','read_slots','baud','inputs','c_initialization'):
            with self.subTest(defect=defect),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary)
                a,b=fixture(root/'t8',8,(0,)),fixture(root/'t32',32,(0,1))
                def change(report):
                    if defect=='source': report['manifest']['source_sha256_utf8_lf']['SOFTWARE_ONLY_RTL']='e'*64
                    elif defect=='clock': report['manifest']['core_hz']=99000000
                    elif defect=='read_slots': report['manifest']['read_slots']=1
                    elif defect=='baud': report['manifest']['baud']=115200
                    elif defect=='inputs':
                        for run in report['runs']: run['raw_b_sha256_bytes']='e'*64
                    else:
                        for run in report['runs']: run['c_before_sha256_bytes']='f'*64
                rewrite(b,change)
                with self.assertRaises(ValueError): collector.collect(a,b,True)

    def test_table_only_preserves_provenance_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            a,b=fixture(root/'t8',8,(0,)),fixture(root/'t32',32,(0,1))
            output=root/'comparison'
            arguments=['--t8',str(a),'--t32',str(b),'--output',str(output),'--allow-partial','--table-only']
            with redirect_stdout(io.StringIO()): self.assertEqual(collector.main(arguments),0)
            manifest=json.loads((output/'manifest.json').read_text())
            self.assertFalse(manifest['full_grid_complete'])
            self.assertIsNone(manifest['matplotlib'])
            self.assertEqual(len(manifest['input_provenance']),2)
            for name,digest in manifest['saved_artifact_sha256_bytes'].items():
                self.assertEqual(runner.sha(output/name),digest)
            with redirect_stderr(io.StringIO()): self.assertEqual(collector.main(arguments),1)


if __name__=='__main__':
    unittest.main()
