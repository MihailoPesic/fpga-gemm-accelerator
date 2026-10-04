"""Release runner checks against software fixtures; never open physical COM."""
from contextlib import redirect_stderr, redirect_stdout
import copy
import gzip
import hashlib
import io
import json
from pathlib import Path
import struct
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from scripts import qualify_release as runner
from host.gemm.client import OVERLAP_VERSION
from tb.test_host_gemm import MemoryDevice, connection


class ReleaseTests(unittest.TestCase):
    def test_grid_modes_seeds_and_release_minimums(self):
        result = runner.plan((0,1))
        self.assertEqual(len(result['benchmark_cases']),16)
        self.assertEqual([(case['m'],case['n'],case['k']) for case in result['benchmark_cases']],
                         [(size,size,k) for size in (32,64,128,256) for k in (16,64,256)]+
                         [(31,33,17),(65,63,255),(1,64,256),(64,1,256)])
        self.assertEqual(result['maximum_shape'],[1024,1024,256])
        self.assertEqual(runner.order((0,1),3),[(0,0),(0,1),(1,1),(1,0),(2,0),(2,1)])
        self.assertEqual(runner.order((0,),3),[(0,0),(1,0),(2,0)])
        for samples,duration in ((29,1800),(30,1799),(30,float('nan'))):
            with self.subTest(samples=samples,duration=duration),self.assertRaises(ValueError):
                runner.plan((0,1),samples,duration)

    def test_wide_oracle_sign_extremes_and_binary_order(self):
        a=[[-128,127],[-1,2]]
        b=[[-128,127],[127,-128]]
        raw=runner.wide_output(a,b)
        self.assertEqual(struct.unpack('<4i',raw),(32513,-32512,382,-383))
        self.assertEqual(struct.unpack('<i',runner.wide_output([[-128]*256],[[-128] for _ in range(256)])),(4194304,))

    def test_t8_t32_traffic_differs_only_in_reuse(self):
        prepared=runner.prepare((33,35,9),71,'python')
        desc=prepared['packed'].descriptor
        t8,t32=(runner.traffic(desc,8,t) for t in (8,32))
        self.assertEqual(t8,dict(compute_cycles=800,read_beats=680,write_beats=594,write_valid_bytes=4620))
        self.assertEqual(t32,dict(compute_cycles=800,read_beats=272,write_beats=594,write_valid_bytes=4620))
        self.assertNotEqual(runner.c_sentinel(128,71,0),runner.c_sentinel(128,71,1))

    def fixture(self,directory,outcome='success',t=32):
        bit=b'Software-only release fixture; never programmed'
        (directory/'gemm_ddr.bit').write_bytes(bit)
        manifest=dict(schema_version=1,result='PASS',p=8,t=t,kmax=256,core_hz=100000000,baud=115200,
            source_commit='software-fixture',source_dirty=True,build_id=0x19ab27cd,bitstream='gemm_ddr.bit',
            bitstream_sha256=hashlib.sha256(bit).hexdigest(),version=OVERLAP_VERSION,kind='overlap_ddr_gemm',
            enable_overlap=True)
        path=directory/'build.json'
        path.write_text(json.dumps(manifest))
        device=MemoryDevice(8,t,OVERLAP_VERSION)
        device.outcome=outcome
        client,_,serial=connection(device)
        return path,manifest,client,device,serial

    def test_each_allocation_downloaded_once_and_full_snapshots_retained(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            _,_,client,device,serial=self.fixture(root)
            client.identify()
            prepared=runner.prepare((5,3,9),73,'python')
            for name,address,raw in prepared['images']:
                if name!='c': client.write_memory(address,raw)
            before=len(device.calls)
            record=runner.run_job(client,prepared,1,1,0,73,root/'job',retain=True)
            self.assertEqual(record['compared_elements'],15)
            self.assertEqual(record['allocation_bytes_checked'],1216)
            self.assertEqual(record['guard_input_bytes_checked'],1156)
            reads=[struct.unpack('<IH',payload) for opcode,payload in device.calls[before:] if opcode==4]
            self.assertEqual(sum(length for _,length in reads),1216)
            expected=runner.expected_c(runner.c_sentinel(448,73,0),prepared['expected'],prepared['packed'].descriptor)
            with gzip.open(root/'job/c_after.bin.gz','rb') as stream:
                self.assertEqual(stream.read(),expected)
            self.assertEqual(record['state'],'PASS')
            self.assertEqual(record['mode'],1)
            self.assertEqual(serial.closed,False)

    def test_bad_output_padding_and_counter_do_not_pass(self):
        for outcome in ('wrong_output','wrong_padding','wrong_counter'):
            with self.subTest(outcome=outcome),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary)
                _,_,client,device,_=self.fixture(root,outcome if outcome!='wrong_counter' else 'success')
                if outcome=='wrong_counter': device.count_override={0xa0:61}
                client.identify()
                prepared=runner.prepare((5,3,9),73,'python')
                for name,address,raw in prepared['images']:
                    if name!='c': client.write_memory(address,raw)
                with self.assertRaises(ValueError):
                    runner.run_job(client,prepared,1,0,0,73,root/'job')
                report=json.loads((root/'job/job.json').read_text())
                self.assertEqual((report['state'],report['passed']),('FAIL',False))
                self.assertIn('error',report)

    def test_fresh_c_detects_missing_write_on_repeated_identical_inputs(self):
        class MissingSecondWrite(MemoryDevice):
            def complete(self):
                base=self.regs[0x2c]
                length=4*self.regs[0x1c]
                before=self.read(base,length)
                super().complete()
                if self.starts==2: self.write(base,before)
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            client,device,_=connection(MissingSecondWrite(8,32,OVERLAP_VERSION))
            client.identify()
            prepared=runner.prepare((1,3,9),73,'python')
            for name,address,raw in prepared['images']:
                if name!='c': client.write_memory(address,raw)
            runner.run_job(client,prepared,1,0,0,73,root/'first')
            with self.assertRaisesRegex(ValueError,'DDR'):
                runner.run_job(client,prepared,2,1,0,73,root/'second')
            self.assertEqual(device.starts,2)

    def test_completed_unit_resume_skips_com_and_rejects_tampering(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            path,manifest,client,device,serial=self.fixture(root)
            args=SimpleNamespace(manifest=path,output=root/'results',port='FAKE',timeout=2.,job_timeout=30.,resume=False)
            args.output.mkdir()
            with patch.object(runner,'qualified_manifest',return_value=manifest),patch.object(runner.GEMM,'open',return_value=client),\
                    patch.object(runner,'version',return_value='software-fixture'):
                session=runner.Session(args,manifest,runner.plan((0,1)))
                def action(directory,report,persist):
                    session.case((5,3,9),73,(0,1),2,directory,report['runs'],on_result=persist)
                with redirect_stdout(io.StringIO()):
                    report=session.unit('fixture',action)
            self.assertEqual(report['checked_counts']['completed_jobs'],4)
            self.assertTrue(serial.closed)
            args.resume=True
            with patch.object(runner.GEMM,'open') as open_com,redirect_stdout(io.StringIO()):
                resumed=runner.Session(args,manifest,runner.plan((0,1))).unit('fixture',lambda *values:self.fail('Re-executed completed unit'))
            open_com.assert_not_called()
            self.assertEqual(resumed,report)
            job=args.output/'fixture/job_000001/job.json'
            job.write_text(job.read_text()+' ')
            with self.assertRaisesRegex(ValueError,'artifact changed'),patch.object(runner.GEMM,'open') as open_com:
                runner.Session(args,manifest,runner.plan((0,1))).unit('fixture',lambda *values:None)
            open_com.assert_not_called()

    def test_unsealed_unit_and_changed_binding_cannot_resume(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary)
            with self.assertRaisesRegex(ValueError,'Interrupted or unsealed'):
                runner.verify_sealed(directory,{'source':'different'})
            runner.atomic_json(directory/'seal.json',dict(result='PASS',binding={'source':'old'},saved_artifact_sha256_bytes={}))
            with self.assertRaisesRegex(ValueError,'identity changed'):
                runner.verify_sealed(directory,{'source':'new'})

    def test_full_grid_sample_gate_rejects_missing_misordered_and_wrong_samples(self):
        run_plan=runner.plan((0,1))
        case=run_plan['benchmark_cases'][0]
        prepared=runner.prepare((32,32,16),case['seed'],'python')
        runs=[]
        for sample,mode in runner.order((0,1),30):
            desc=runner.asdict(prepared['packed'].descriptor)
            desc['mode']=mode
            runs.append(dict(state='PASS',passed=True,sample=sample,mode=mode,seed=case['seed'],
                m=32,n=32,k=16,p=8,t=32,core_hz=100000000,build_id=99,compared_elements=1024,
                descriptor=desc,input_a_sha256_bytes=prepared['input_a_sha256_bytes'],
                raw_b_sha256_bytes=prepared['raw_b_sha256_bytes'],oracle_sha256_bytes=prepared['oracle_sha256_bytes'],
                c_before_sha256_bytes=hashlib.sha256(runner.c_sentinel(4224,case['seed'],sample)).hexdigest(),
                job_cycles=1500,compute_cycles=624,read_beats=128,write_beats=512,write_valid_bytes=4096,
                input_wait_cycles=0,read_stall_cycles=0,write_stall_cycles=0))
        report=dict(runs=runs,binding={'build_id':99},manifest={'t':32},checked_counts=runner.counts(runs))
        runner.verify_benchmark_samples(report,case,run_plan)
        for defect in ('missing','order','traffic','input','pair_c'):
            changed=copy.deepcopy(report)
            if defect=='missing': changed['runs'].pop()
            elif defect=='order': changed['runs'][1]['mode']=0
            elif defect=='traffic': changed['runs'][7]['write_valid_bytes']=4100
            elif defect=='input': changed['runs'][7]['input_a_sha256_bytes']='a'*64
            else: changed['runs'][1]['c_before_sha256_bytes']='a'*64
            with self.subTest(defect=defect),self.assertRaises(ValueError):
                runner.verify_benchmark_samples(changed,case,run_plan)

    def test_manifest_mode_and_cli_failures_precede_com_open(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            path,manifest,_,_,_=self.fixture(root)
            for changes in ({'version':0x100},{'kind':'serial_ddr_gemm'},{'enable_overlap':False},{'p':4},{'core_hz':99000000}):
                with self.subTest(changes=changes),patch.object(runner,'qualified_manifest',return_value=dict(manifest,**changes)),\
                     patch.object(runner.GEMM,'open') as open_com,redirect_stderr(io.StringIO()):
                    code=runner.main(['--port','FAKE','--manifest',str(path),'--output',str(root/'output')])
                self.assertEqual(code,1)
                open_com.assert_not_called()
            for arguments in (['--samples','29'],['--duration','1799'],['--phase','endurance','--modes','0'],['--cases','16']):
                with self.subTest(arguments=arguments),patch.object(runner,'qualified_manifest') as qualification,\
                     patch.object(runner.GEMM,'open') as open_com,redirect_stderr(io.StringIO()):
                    code=runner.main(['--port','FAKE','--manifest',str(path),'--output',str(root/'output')]+arguments)
                self.assertEqual(code,1)
                qualification.assert_not_called()
                open_com.assert_not_called()

    def test_endurance_completes_entire_mixed_cycle_in_one_connection(self):
        class SoftwareSession:
            plan=runner.plan((0,1))
            def __init__(self): self.calls=[]; self.connections=0
            def connect(self): self.connections+=1
            def case(self,shape,seed,modes,samples,directory,runs,on_result=None):
                self.calls.append((shape,seed,modes,samples))
                runs.extend(dict(mode=mode,passed=True) for mode in modes)
            def unit(self,name,action):
                report={'runs':[]}
                action(Path('SOFTWARE_ONLY'),report,lambda:None)
                return report
        session=SoftwareSession()
        clock=iter(range(0,100000,400))
        with patch.object(runner.time,'perf_counter',side_effect=lambda:next(clock)):
            report=runner.run_endurance(session)
        self.assertEqual(session.connections,1)
        self.assertEqual([call[0] for call in session.calls],list(runner.MIXED))
        self.assertEqual(len(report['runs']),16)
        self.assertEqual(report['completed_mixed_cycles'],1)
        self.assertGreaterEqual(report['continuous_exercise_seconds'],1800)


if __name__=='__main__':
    unittest.main()
