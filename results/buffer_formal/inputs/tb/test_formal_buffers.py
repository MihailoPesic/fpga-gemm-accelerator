"""Software checks for formal trace validation; these are not RTL proofs."""
import json
from pathlib import Path
import tempfile
import unittest

from scripts.formal_buffers_witness import decode, port_widths, queue_witness, scheduler_witness


class TraceDecodeTests(unittest.TestCase):
    def fixture(self, area):
        vcd, model = Path(area) / 'trace.vcd', Path(area) / 'trace.json'
        vcd.write_text('$var wire 1 r rst $end\n$var wire 3 d data $end\n'
                       '$enddefinitions $end\n#0\n1r\nb000 d\n#2\n0r\nb101 d\n#3\n0r\nb101 d\n')
        model.write_text(json.dumps(dict(signal=[dict(name='rst',wave='410.'),
            dict(name='data',wave='4==.',data=['','000','101'])])))
        return vcd, model

    def test_decodes_final_observation_and_binary_payload(self):
        with tempfile.TemporaryDirectory() as area:
            vcd, model = self.fixture(area)
            frames = decode(vcd, model, 3, {'rst':1,'data':3})
            self.assertEqual([(f['step'],f['rst'],f['data']) for f in frames],[(1,1,0),(2,0,5),(3,0,5)])

    def test_rejects_json_payload_mismatch(self):
        with tempfile.TemporaryDirectory() as area:
            vcd, model = self.fixture(area)
            payload=json.loads(model.read_text());payload['signal'][1]['data'][-1]='100'
            model.write_text(json.dumps(payload))
            with self.assertRaisesRegex(RuntimeError,'JSON/VCD mismatch'):
                decode(vcd,model,3,{'rst':1,'data':3})

    def test_rejects_wrong_width_missing_signal_and_short_bound(self):
        with tempfile.TemporaryDirectory() as area:
            vcd,model=self.fixture(area)
            for widths,bound in (({'rst':1,'data':4},3),({'rst':1,'data':3,'missing':1},3),({'rst':1,'data':3},4)):
                with self.subTest(widths=widths,bound=bound),self.assertRaises(RuntimeError):
                    decode(vcd,model,bound,widths)

    def test_rejects_duplicate_public_signal(self):
        with tempfile.TemporaryDirectory() as area:
            vcd,model=self.fixture(area)
            vcd.write_text('$var wire 1 duplicate rst $end\n'+vcd.read_text())
            with self.assertRaisesRegex(RuntimeError,'duplicate'):
                decode(vcd,model,3,{'rst':1,'data':3})

    def test_rejects_undefined_bus(self):
        with tempfile.TemporaryDirectory() as area:
            vcd,model=self.fixture(area)
            vcd.write_text(vcd.read_text().replace('b101','b10x'))
            with self.assertRaisesRegex(RuntimeError,'undefined'):
                decode(vcd,model,3,{'rst':1,'data':3})


class ReachabilityCheckTests(unittest.TestCase):
    @staticmethod
    def frames(kind,configuration,count):
        frames=[dict.fromkeys(port_widths(kind,configuration),0) for _ in range(count)]
        for i,frame in enumerate(frames,1):frame.update(step=i,rst=int(i==1))
        return frames

    def test_queue_cover_flag_without_accepted_traffic_is_rejected(self):
        frames=self.frames('queue',8,3);frames[-1]['cover_full']=1
        with self.assertRaisesRegex(RuntimeError,'does not demonstrate'):
            queue_witness(frames,'cover_full',8)

    def test_queue_corrupt_metadata_after_accepted_data_is_rejected(self):
        frames=self.frames('queue',8,5)
        frames[1].update(cmd_valid=1,cmd_ready=1,cmd_beats=1,plan_row=2,plan_word=3)
        frames[2].update(owned=1,expected_owned=1,tail=1,data_valid=1,data_ready=1,data_value=42)
        frames[3].update(owned=1,expected_owned=1,tail=1,bank_valid=1,bank_row=1,bank_word=3,
                         bank_data=42,bank_bt=1,bank_buf=1)
        with self.assertRaisesRegex(RuntimeError,'reordered/corrupt'):
            queue_witness(frames,'cover_stalls',8)

    def test_final_input_offer_is_not_counted_as_a_committed_edge(self):
        frames=self.frames('queue',8,3)
        frames[-1].update(cmd_valid=1,cmd_ready=1,cmd_beats=1,plan_row=31,cover_full=1)
        with self.assertRaisesRegex(RuntimeError,'does not demonstrate'):
            queue_witness(frames,'cover_full',8)

    def test_scheduler_fake_success_cover_is_rejected(self):
        frames=self.frames('scheduler',1,3);frames[-1]['cover_success']=1
        with self.assertRaisesRegex(RuntimeError,'does not demonstrate'):
            scheduler_witness(frames,'cover_success',1)

    def test_scheduler_unready_input_start_is_rejected(self):
        frames=self.frames('scheduler',1,4);frames[1]['core_start']=1
        with self.assertRaisesRegex(RuntimeError,'unready/live'):
            scheduler_witness(frames,'cover_overlap',1)


if __name__=='__main__':
    unittest.main()
