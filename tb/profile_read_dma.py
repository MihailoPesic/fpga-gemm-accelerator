"""Controlled tile-local read-depth comparison; no physical DDR timing model."""
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import random

import cocotb

from test_tile_dma import Harness


CASES = (('dense', 32, 32, 256, 20261011),
         ('odd', 31, 29, 17, 20261012))
PHASES = ('a_load', 'bt_load', 'compute', 'c_store')
COUNTERS = ('ar_bursts', 'r_beats', 'aw_bursts', 'w_beats', 'b_responses',
            'write_valid_bytes', 'bank_words', 'read_commands', 'read_completions')


def digest(data):
    return hashlib.sha256(data).hexdigest()


class ProfileHarness(Harness):
    def __init__(self, dut):
        super().__init__(dut, random_stalls=False)
        self.phase = None
        self.phases = {}
        self.regions = {}
        self.accepted_reads = 0

    def begin(self, phase):
        assert self.phase is None and phase not in self.phases
        self.phase = phase
        self.phases[phase] = dict(start_cycle=self.cycle, counters=dict.fromkeys(COUNTERS, 0),
                                  max_axi_reads=self.accepted_reads, read_occupancy=Counter())

    def finish(self, phase):
        assert self.phase == phase
        record = self.phases[phase]
        record['end_cycle'] = self.cycle
        record['cycles'] = self.cycle-record['start_cycle']
        record['read_occupancy'] = dict(sorted(record['read_occupancy'].items()))
        self.phase = None

    async def step(self, **signals):
        sample = await super().step(**signals)
        # Count public accepted AR obligations; handle an old RLAST before
        # appending a same-edge AR, matching the AXI ordering rule.
        ar = sample['m_axi_arvalid'] and sample['m_axi_arready']
        read = sample['m_axi_rvalid'] and sample['m_axi_rready']
        if read:
            assert self.accepted_reads > 0
            if sample['m_axi_rlast']:
                self.accepted_reads -= 1
        if ar:
            self.accepted_reads += 1
        assert self.accepted_reads == len(self.axi_reads)
        assert 0 <= self.accepted_reads <= self.read_slots
        if self.phase is not None:
            record = self.phases[self.phase]
            record['max_axi_reads'] = max(record['max_axi_reads'], self.accepted_reads)
            record['read_occupancy'][self.accepted_reads] += 1
            counts = record['counters']
            counts['ar_bursts'] += int(ar)
            counts['r_beats'] += int(read)
            for key, channel in (('aw_bursts', 'm_axi_aw'), ('w_beats', 'm_axi_w'),
                                 ('b_responses', 'm_axi_b'), ('bank_words', 'load'),
                                 ('read_commands', 'rd_cmd'), ('read_completions', 'rd_done')):
                prefix = channel if channel.startswith('m_axi_') else channel+'_'
                counts[key] += int(sample[prefix+'valid'] and sample[prefix+'ready'])
            if sample['m_axi_wvalid'] and sample['m_axi_wready']:
                counts['write_valid_bytes'] += sample['m_axi_wstrb'].bit_count()
        return sample

    async def request(self, write=0, bt=0, buf=0, base=0xff8, stride=256, rows=1, row_bytes=8):
        if write:
            self.begin('c_store')
        phase = 'c_store' if write else 'bt_load' if bt else 'a_load'
        assert self.phase == phase
        initial = bytes(self.ram.read(base-8, rows*stride+16))
        self.regions[phase] = dict(base=base, stride=stride, rows=rows, row_bytes=row_bytes,
                                  buffer=buf, checked_start=base-8, checked_bytes=len(initial),
                                  initial_sha256=digest(initial))
        await super().request(write=write, bt=bt, buf=buf, base=base, stride=stride,
                              rows=rows, row_bytes=row_bytes)

    async def completion(self, status=0, stall=0):
        # The normal helper deliberately stalls DONE for seven cycles. Remove
        # that deliberate stall in both compared builds; retain its actual
        # observation/retirement edges and all correctness checks.
        await super().completion(status=status, stall=stall)
        if self.phase == 'c_store':
            self.finish('c_store')

    async def load(self, matrix, k, buf, bt, base, stride):
        phase = 'bt_load' if bt else 'a_load'
        self.begin(phase)
        await super().load(matrix, k, buf, bt, base, stride)
        self.finish(phase)

    async def compute(self, m, n, k, ib, ob):
        self.begin('compute')
        await super().compute(m, n, k, ib, ob)
        self.phases['compute']['tile_job_cycles'] = self.value('job_cycles')
        self.finish('compute')


@cocotb.test()
async def compare_read_depth_workloads(dut):
    h = ProfileHarness(dut)
    assert (h.p, h.t) == (8, 32), 'This controlled comparison fixes P8/T32'
    await h.reset()
    jobs = []
    for index, (name, m, n, k, seed) in enumerate(CASES):
        assert h.phase is None and h.accepted_reads == 0
        h.phases, h.regions = {}, {}
        h.clear_logs()
        h.rng = random.Random(seed)
        start = h.cycle
        golden = await h.job(m, n, k, index & 1, (index+1) & 1)
        cycles = h.cycle-start
        assert set(h.phases) == set(PHASES)
        assert cycles == sum(item['cycles'] for item in h.phases.values())
        assert h.accepted_reads == 0 and not h.local_reads and not h.axi_reads
        assert h.value('axi_quiescent') and h.value('req_ready') and not h.value('fatal')
        counts = {key: sum(phase['counters'][key] for phase in h.phases.values()) for key in COUNTERS}
        assert counts['r_beats'] == counts['bank_words'] == (m+n)*((k+7)//8)
        assert counts['w_beats'] == m*((n+1)//2)
        assert counts['write_valid_bytes'] == 4*m*n
        assert counts['aw_bursts'] == counts['b_responses']
        assert counts['ar_bursts'] == counts['read_commands'] == counts['read_completions']
        for phase, region in h.regions.items():
            final = bytes(h.ram.read(region['checked_start'], region['checked_bytes']))
            region['final_sha256'] = digest(final)
            if phase != 'c_store':
                assert region['final_sha256'] == region['initial_sha256'], 'Store modified an input or its guards'
        golden_bytes = b''.join(value.to_bytes(4, 'little', signed=True) for row in golden for value in row)
        jobs.append(dict(name=name, shape=[m,n,k], seed=seed, phases=h.phases,
                         helper_sequence_cycles=cycles, counters=counts, regions=h.regions,
                         max_axi_reads=max(phase['max_axi_reads'] for phase in h.phases.values()),
                         compared_output_values=m*n,
                         checked_memory_bytes=sum(region['checked_bytes'] for region in h.regions.values()),
                         golden_int32_sha256=digest(golden_bytes),
                         reads=[dict(address=entry[0], beats=entry[1]+1) for entry in h.logs['ar']],
                         writes=[dict(address=entry[0], beats=entry[1]+1) for entry in h.logs['aw']],
                         write_strobes=[entry[1] for entry in h.logs['w']]))
    record = dict(schema_version=1, result='PASS', p=8, t=32, read_slots=h.read_slots,
                  model='cocotbext-axi AxiRam; no inserted channel or local stalls',
                  cycle_scope='Software-driven tile helper sequence: A load, BT load, compute, C store. '
                              'Includes request validation, buffered delivery and helper DONE observation/retirement; '
                              'completion(stall=0). Excludes reset and host preparation. '
                              'Not the full DDR job counter, MIG timing, UART throughput or board performance.',
                  jobs=jobs)
    Path(os.environ['GEMM_READ_PROFILE']).write_text(json.dumps(record, indent=2)+'\n', encoding='utf-8')
