"""Byte-addressed transfer oracle with production banking and signed GEMM."""
from collections import deque
import json
import logging
import os
from pathlib import Path
import random

import cocotb
from cocotbext.axi import AxiBus, AxiRam
from common import tick


COVERAGE = {'p': int(os.environ['GEMM_P']), 't': int(os.environ['GEMM_T']),
            'read_slots': int(os.environ.get('GEMM_READ_SLOTS', '1')),
            'seed': 20261107, 'jobs': 0, 'output_values': 0, 'shapes': [],
            'input_buffers': [], 'output_buffers': [], 'invalid_requests': 0,
            'read_lengths': [], 'write_lengths': [], 'stalls': {}, 'faults': {},
            'axi_read_bursts': 0, 'axi_write_bursts': 0, 'axi_read_beats': 0,
            'axi_write_beats': 0, 'axi_write_valid_bytes': 0, 'axi_write_responses': 0,
            'read_occupancy': [], 'queued_scenarios': [],
            'read_command_and_retirement_same_edge': 0}


def save_coverage():
    Path(os.environ['GEMM_COVERAGE']).write_text(json.dumps(COVERAGE, indent=2)+'\n')


def pauses(seed):
    rng = random.Random(seed)
    while True:
        yield rng.randrange(4) == 0


def expected_bursts(base, stride, rows, row_bytes):
    """Partition the independently enumerated useful-row word addresses."""
    result = []
    for row in range(rows):
        words = [base+row*stride+offset for offset in range(0, row_bytes, 8)]
        group = []
        for address in words:
            if group and (len(group) == 16 or address//4096 != group[0]//4096):
                result.append((group[0], len(group)))
                group = []
            group.append(address)
        if group:
            result.append((group[0], len(group)))
    return result


class Harness:
    INPUTS = ('req_valid', 'req_write', 'req_bt', 'req_buf', 'req_base', 'req_stride',
              'req_rows', 'req_row_bytes', 'done_ready', 'start', 'input_buf', 'output_buf',
              'm', 'n', 'k', 'inject_response_error', 'inject_response_strb',
              'inject_rd_tag', 'inject_rd_index', 'inject_rd_last', 'inject_wr_done', 'inject_spurious_b',
              'inject_mem_fatal', 'inject_mem_code', 'inject_rresp', 'inject_bresp')
    GATES = ('allow_load', 'allow_read', 'allow_response', 'allow_rd_cmd', 'allow_rd_done', 'allow_wr_cmd', 'allow_wr_data')
    CHANNELS = {
        'ar': ('m_axi_arvalid', 'm_axi_arready', tuple('m_axi_ar'+x for x in
                ('addr', 'len', 'id', 'size', 'burst', 'lock', 'cache', 'prot', 'qos', 'region'))),
        'aw': ('m_axi_awvalid', 'm_axi_awready', tuple('m_axi_aw'+x for x in
                ('addr', 'len', 'id', 'size', 'burst', 'lock', 'cache', 'prot', 'qos', 'region'))),
        'w': ('m_axi_wvalid', 'm_axi_wready', ('m_axi_wdata', 'm_axi_wstrb', 'm_axi_wlast')),
        'load': ('load_valid', 'load_ready', ('load_bt', 'load_buf', 'load_q', 'load_word', 'load_data')),
        'read': ('read_valid', 'read_ready', ('read_buf', 'read_row', 'read_pair')),
        'write_data': ('wr_data_valid', 'wr_data_ready', ('wr_data', 'wr_data_strb')),
        'result_response': ('tile_response_valid', 'tile_response_ready',
                            ('tile_response_data', 'tile_response_strb', 'tile_response_error')),
        'done': ('done_valid', 'done_ready', ('done_status',)),
    }

    def __init__(self, dut, random_stalls=True):
        self.dut = dut
        self.p, self.t = COVERAGE['p'], COVERAGE['t']
        self.read_slots = COVERAGE['read_slots']
        self.rng = random.Random(COVERAGE['seed']+self.p+self.t)
        self.ram = AxiRam(AxiBus.from_prefix(dut, 'm_axi'), dut.clk, dut.rst, size=2**27)
        self.ram.read_if.log.setLevel(logging.WARNING)
        self.ram.write_if.log.setLevel(logging.WARNING)
        self.channels = (self.ram.read_if.ar_channel, self.ram.read_if.r_channel,
                         self.ram.write_if.aw_channel, self.ram.write_if.w_channel,
                         self.ram.write_if.b_channel)
        if random_stalls:
            for i, channel in enumerate(self.channels):
                channel.set_pause_generator(pauses(3100+i))
        self.random_local = random_stalls
        self.fixed_gates = {}
        self.cycle = 0
        self.clear_logs()

    def put(self, **signals):
        for name, value in signals.items():
            getattr(self.dut, name).value = value

    def value(self, name):
        return int(getattr(self.dut, name).value)

    def clear_logs(self):
        self.logs = {name: [] for name in self.CHANNELS}
        self.logs.update({name: [] for name in ('b', 'r', 'rd_cmd', 'wr_cmd', 'response', 'rd_done', 'wr_done')})
        self.held = {}
        self.axi_reads = deque()
        self.local_reads = deque()

    async def reset(self):
        self.put(clk=0, rst=1)
        for name in self.INPUTS:
            self.put(**{name: 0})
        for name in self.GATES:
            self.put(**{name: 1})
        for channel in self.channels:
            channel.pause = False
        self.fixed_gates.clear()
        for _ in range(5):
            await tick(self.dut)
        self.put(rst=0)
        self.clear_logs()
        for _ in range(3):
            await self.step()
        assert self.value('req_ready') and not self.value('fatal')

    async def step(self, **signals):
        for name in self.GATES:
            value = self.fixed_gates.get(name, int(not self.random_local or self.rng.randrange(4) != 0))
            self.put(**{name: value})
        self.put(**signals)
        names = {'req_valid', 'req_ready', 'busy', 'fatal', 'fatal_code', 'axi_quiescent',
                 'start_ready', 'compute_busy', 'compute_done', 'compute_cmd_error',
                 'm_axi_bvalid', 'm_axi_bready', 'm_axi_bresp',
                 'm_axi_rvalid', 'm_axi_rready', 'm_axi_rresp', 'm_axi_rlast', 'm_axi_rid', 'm_axi_rdata',
                 'rd_cmd_valid', 'rd_cmd_ready', 'rd_cmd_addr', 'rd_cmd_beats', 'rd_cmd_tag',
                 'wr_cmd_valid', 'wr_cmd_ready', 'wr_cmd_addr', 'wr_cmd_beats',
                 'response_valid', 'response_ready', 'response_error',
                 'rd_done_valid', 'rd_done_ready', 'rd_done_status', 'rd_done_tag',
                 'wr_done_valid', 'wr_done_ready', 'wr_done_status',
                 'tile_response_valid', 'rd_data_valid', 'rd_data_ready', 'rd_data_index',
                 'rd_data_last', 'rd_data_tag', 'inject_rd_tag', 'inject_rd_index', 'inject_rd_last',
                 'inject_mem_fatal', 'mem_fatal'}
        for valid, ready, fields in self.CHANNELS.values():
            names.update((valid, ready, *fields))

        def snapshot():
            # Unwritten BRAM words are deliberately uninitialized. Inspect data
            # only when its valid qualifier is asserted.
            qualifiers = {'load_data': 'load_valid', 'm_axi_wdata': 'm_axi_wvalid',
                          'm_axi_wstrb': 'm_axi_wvalid',
                          'tile_response_data': 'tile_response_valid',
                          'wr_data': 'wr_data_valid'}
            qualifiers.update({name: 'm_axi_rvalid' for name in
                               ('m_axi_rdata', 'm_axi_rid', 'm_axi_rresp', 'm_axi_rlast')})
            qualifiers['m_axi_bresp'] = 'm_axi_bvalid'
            values = {}
            for name in names:
                try:
                    values[name] = 0 if name in qualifiers and not self.value(qualifiers[name]) else self.value(name)
                except ValueError:
                    raise AssertionError(f'Undefined active signal {name}') from None
            return values

        sample = await tick(self.dut, snapshot)
        self.cycle += 1
        # Ownership is established by an earlier AR edge, never by the AR
        # sampled beside this R beat. Follow accepted addresses, not live ARADDR.
        if sample['m_axi_rvalid'] and sample['m_axi_rready']:
            assert self.axi_reads, 'R response without an earlier accepted AR'
            owner = self.axi_reads[0]
            assert sample['m_axi_rid'] == 0
            assert sample['m_axi_rlast'] == (owner['index'] == owner['beats']-1)
            expected = int.from_bytes(self.ram.read(owner['addr']+8*owner['index'], 8), 'little')
            assert sample['m_axi_rdata'] == expected, 'AXI read lost address/response ordering'
            owner['index'] += 1
            if sample['m_axi_rlast']:
                self.axi_reads.popleft()
        if sample['m_axi_arvalid'] and sample['m_axi_arready']:
            self.axi_reads.append(dict(addr=sample['m_axi_araddr'],
                                       beats=sample['m_axi_arlen']+1, index=0))

        # Tags are opaque metadata. A FIFO of accepted local commands checks
        # repeated tags and slot wrap without treating a tag as a unique job ID.
        if sample['rd_data_valid'] and sample['rd_data_ready']:
            assert self.local_reads, 'Read data without a previously accepted command'
            owner = self.local_reads[0]
            if not sample['inject_rd_tag']:
                assert sample['rd_data_tag'] == owner['tag']
            if not sample['inject_rd_index']:
                assert sample['rd_data_index'] == owner['received']
            if not sample['inject_rd_last']:
                assert sample['rd_data_last'] == (owner['received'] == owner['beats']-1)
            owner['received'] += 1
        retire = sample['rd_done_valid'] and sample['rd_done_ready']
        allocate = sample['rd_cmd_valid'] and sample['rd_cmd_ready']
        if retire:
            assert self.local_reads, 'Read completion before command acceptance'
            owner = self.local_reads.popleft()
            assert sample['rd_done_tag'] == owner['tag']
            if sample['rd_done_status'] == 0:
                assert owner['received'] == owner['beats'], 'Success omitted buffered words'
        if allocate:
            self.local_reads.append(dict(beats=sample['rd_cmd_beats'],
                                         tag=sample['rd_cmd_tag'], received=0))
        assert len(self.local_reads) <= self.read_slots, 'Read credit was reused too early'
        COVERAGE['read_occupancy'] = sorted(set(COVERAGE['read_occupancy']+[len(self.local_reads)]))
        COVERAGE['read_command_and_retirement_same_edge'] += int(retire and allocate)
        for name, (valid, ready, fields) in self.CHANNELS.items():
            payload = tuple(sample[x] for x in fields)
            if name in self.held:
                assert sample[valid] and payload == self.held[name], f'{name}: payload/VALID changed while stalled'
            if sample[valid] and not sample[ready]:
                self.held[name] = payload
                COVERAGE['stalls'][name] = COVERAGE['stalls'].get(name, 0)+1
            else:
                self.held.pop(name, None)
            if (name == 'write_data' and sample['wr_done_valid'] and sample['wr_done_ready']
                    and sample['wr_done_status'] != 0 and sample['mem_fatal']):
                # The burst engine explicitly cancels unconsumed local data
                # when a fatal terminates collection before any AXI write.
                self.held.pop(name, None)
            if sample[valid] and sample[ready]:
                self.logs[name].append(payload)
                if name in ('ar', 'aw'):
                    addr, length, ident, size, burst, *sidebands = payload
                    assert ident == 0 and size == 3 and burst == 1 and not any(sidebands)
                    assert addr % 8 == 0 and 0 <= length <= 15
                    assert addr//4096 == (addr+8*(length+1)-1)//4096
                    bin_name = 'read_lengths' if name == 'ar' else 'write_lengths'
                    COVERAGE[bin_name] = sorted(set(COVERAGE[bin_name]+[length+1]))
                    COVERAGE['axi_read_bursts' if name == 'ar' else 'axi_write_bursts'] += 1
                if name == 'w':
                    COVERAGE['axi_write_beats'] += 1
                    COVERAGE['axi_write_valid_bytes'] += payload[1].bit_count()
        for name, valid, ready, fields in (
            ('b', 'm_axi_bvalid', 'm_axi_bready', ('m_axi_bresp',)),
            ('r', 'm_axi_rvalid', 'm_axi_rready', ('m_axi_rresp', 'm_axi_rlast')),
            ('rd_cmd', 'rd_cmd_valid', 'rd_cmd_ready', ('rd_cmd_addr', 'rd_cmd_beats')),
            ('wr_cmd', 'wr_cmd_valid', 'wr_cmd_ready', ('wr_cmd_addr', 'wr_cmd_beats')),
            ('response', 'response_valid', 'response_ready', ('response_error',)),
            ('rd_done', 'rd_done_valid', 'rd_done_ready', ('rd_done_status',)),
            ('wr_done', 'wr_done_valid', 'wr_done_ready', ('wr_done_status',)),
        ):
            if sample[valid] and sample[ready]:
                self.logs[name].append(tuple(sample[x] for x in fields))
                if name in ('r', 'b'):
                    COVERAGE['axi_read_beats' if name == 'r' else 'axi_write_responses'] += 1
        return sample

    async def until(self, condition, limit=50000):
        for _ in range(limit):
            sample = await self.step()
            if condition(sample):
                return sample
        raise AssertionError(f'Timeout at cycle {self.cycle}: {condition}')

    async def idle(self, count):
        for _ in range(count):
            await self.step()

    async def request(self, write=0, bt=0, buf=0, base=0xff8, stride=256, rows=1, row_bytes=8):
        self.put(req_valid=1, req_write=write, req_bt=bt, req_buf=buf,
                 req_base=base, req_stride=stride, req_rows=rows, req_row_bytes=row_bytes,
                 done_ready=0)
        await self.until(lambda s: s['req_ready'])
        self.put(req_valid=0, req_write=1-write, req_bt=1-bt, req_buf=1-buf,
                 req_base=0xffffffff, req_stride=3, req_rows=0, req_row_bytes=0)

    async def completion(self, status=0, stall=7):
        await self.until(lambda s: s['done_valid'])
        assert self.value('done_status') == status, (self.value('done_status'), status)
        await self.idle(stall)
        assert self.value('done_valid') and self.value('done_status') == status
        assert not self.value('req_ready'), 'New operation accepted before previous terminal response'
        self.put(done_ready=1)
        await self.step()
        self.put(done_ready=0)
        await self.step()
        assert not self.value('done_valid')

    async def compute(self, m, n, k, ib, ob):
        assert self.value('start_ready')
        self.put(start=1, m=m, n=n, k=k, input_buf=ib, output_buf=ob)
        await self.step()
        self.put(start=0, m=0, n=0, k=0, input_buf=1-ib, output_buf=1-ob)
        await self.until(lambda s: s['compute_done'])
        assert not self.value('compute_cmd_error')
        assert self.value('job_cycles') == ((m+self.p-1)//self.p)*((n+self.p-1)//self.p)*(k+3*self.p+3)

    async def load(self, matrix, k, buf, bt, base, stride):
        region = bytearray(self.rng.randrange(1, 256) for _ in range(len(matrix)*stride+16))
        for row, values in enumerate(matrix):
            for col, value in enumerate(values):
                region[8+row*stride+col] = value & 255
        self.ram.write(base-8, region)
        first_load, first_ar = len(self.logs['load']), len(self.logs['ar'])
        await self.request(bt=bt, buf=buf, base=base, stride=stride, rows=len(matrix), row_bytes=k)
        await self.completion()
        expect = [(bt, buf, row, word,
                   int.from_bytes(region[8+row*stride+word*8:16+row*stride+word*8], 'little'))
                  for row in range(len(matrix)) for word in range((k+7)//8)]
        assert self.logs['load'][first_load:] == expect
        assert [(x[0], x[1]+1) for x in self.logs['ar'][first_ar:]] == expected_bursts(base, stride, len(matrix), k)
        assert self.ram.read(base-8, len(region)) == region, 'Input memory was modified'

    async def job(self, m, n, k, ib, ob, extrema=False):
        a = [[-128 if extrema else self.rng.randrange(-128, 128) for _ in range(k)] for _ in range(m)]
        # B is generated in its mathematical K-by-N layout; host-style transpose
        # here is explicit and independent of the RTL bank address formula.
        b = [[-128 if extrema else self.rng.randrange(-128, 128) for _ in range(n)] for _ in range(k)]
        bt = [list(column) for column in zip(*b)]
        golden = [[sum(int(a[i][q])*int(b[q][j]) for q in range(k))
                   for j in range(n)] for i in range(m)]
        stride = ((k+7)//8)*8 + self.rng.choice((0, 8, 24))
        await self.load(a, k, ib, 0, 0xff8, stride)
        await self.load(bt, k, ib, 1, 0x20ff8, stride)
        await self.compute(m, n, k, ib, ob)
        base, stride = 0x40ff8, ((4*n+7)//8)*8+24
        region = bytearray(self.rng.randrange(256) for _ in range(m*stride+16))
        expected = bytearray(region)
        for i in range(m):
            for j in range(n):
                expected[8+i*stride+j*4:12+i*stride+j*4] = golden[i][j].to_bytes(4, 'little', signed=True)
        self.ram.write(base-8, region)
        first_aw, first_w, first_b = (len(self.logs[x]) for x in ('aw', 'w', 'b'))
        first_read = len(self.logs['read'])
        await self.request(write=1, buf=ob, base=base, stride=stride, rows=m, row_bytes=4*n)
        await self.completion()
        bursts = expected_bursts(base, stride, m, 4*n)
        assert [(x[0], x[1]+1) for x in self.logs['aw'][first_aw:]] == bursts
        assert len(self.logs['b'])-first_b == len(bursts)
        assert self.ram.read(base-8, len(region)) == expected, (m, n, k, ib, ob)
        assert self.logs['read'][first_read:] == [(ob, i, j) for i in range(m) for j in range((n+1)//2)]
        strobes = [0x0f if n % 2 and j == n//2 else 0xff for i in range(m) for j in range((n+1)//2)]
        assert [x[1] for x in self.logs['w'][first_w:]] == strobes
        COVERAGE['jobs'] += 1
        COVERAGE['output_values'] += m*n
        COVERAGE['shapes'].append([m, n, k])
        COVERAGE['input_buffers'] = sorted(set(COVERAGE['input_buffers']+[ib]))
        COVERAGE['output_buffers'] = sorted(set(COVERAGE['output_buffers']+[ob]))
        return golden


@cocotb.test()
async def complete_matrix_transfers(dut):
    h = Harness(dut)
    await h.reset()
    p, t = h.p, h.t
    cases = [(1, 1, 1), (1, min(t, p+1), 7), (min(t, p+1), 1, 8),
             (3, 5, 9), (t-1, t, 255), (t, t-1, 256), (t, t, 256)]
    cases += [(h.rng.randrange(1, t+1), h.rng.randrange(1, t+1),
               h.rng.choice((1, 7, 8, 9, 31, 32, 33, 255, 256))) for _ in range(8)]
    for index, shape in enumerate(cases):
        await h.job(*shape, index % 2, (index//2) % 2, extrema=index == 6)
    assert all(COVERAGE['stalls'].get(name, 0) for name in ('ar', 'aw', 'w', 'load', 'read', 'result_response', 'done'))
    save_coverage()


@cocotb.test()
async def invalid_descriptors_have_no_side_effects(dut):
    h = Harness(dut)
    await h.reset()
    cases = [dict(rows=0), dict(rows=h.t+1), dict(row_bytes=0), dict(row_bytes=257),
             dict(base=1), dict(stride=1), dict(stride=0), dict(stride=8, row_bytes=9),
             dict(base=0x8000000), dict(base=0x7fffff8, row_bytes=9),
             dict(base=0xfffffff8), dict(stride=0xfffffff8, rows=2),
             dict(write=1, row_bytes=1), dict(write=1, row_bytes=6),
             dict(write=1, row_bytes=4*h.t+4), dict(write=1, rows=h.t+1)]
    for changes in cases:
        before = {name: len(h.logs[name]) for name in ('ar', 'aw', 'w', 'load', 'read')}
        await h.request(**changes)
        await h.completion(3)
        assert {name: len(h.logs[name]) for name in before} == before, changes
        assert not h.value('fatal') and h.value('req_ready')
        COVERAGE['invalid_requests'] += 1
    # Last legal DDR beat is accepted without a widened-address false rejection.
    h.ram.write(0x7fffff8, b'\x80\x7f\x01\xff\x02\x03\x04\x05')
    await h.request(base=0x7fffff8, stride=8)
    await h.completion()
    assert h.logs['ar'][-1][:2] == (0x7fffff8, 0)
    save_coverage()


@cocotb.test()
async def delayed_response_and_fault_obligations(dut):
    h = Harness(dut, random_stalls=False)
    await h.reset()
    # Prepare a complete result tile, then hold every B response. Local result
    # readiness and accepted W beats are not a terminal memory completion.
    await h.job(2, 3, 9, 0, 0)
    h.ram.write(0x60ff8-8, bytes([0xa6])*80)
    h.ram.write_if.b_channel.pause = True
    b_before = len(h.logs['b'])
    await h.request(write=1, base=0x60ff8, stride=24, rows=2, row_bytes=12)
    await h.until(lambda s: s['m_axi_wvalid'] and s['m_axi_wready'] and s['m_axi_wlast'])
    await h.idle(30)
    assert not h.value('done_valid') and len(h.logs['b']) == b_before
    assert len(h.logs['wr_cmd']) == b_before+1, 'Next row/burst issued before B response'
    h.ram.write_if.b_channel.pause = False
    await h.completion()
    COVERAGE['faults']['delayed_b_no_early_done'] = 1

    # Non-OKAY read response: the burst engine must publish no corrupted input.
    await h.reset()
    h.put(inject_rresp=2)
    await h.request(rows=2, row_bytes=256)
    await h.completion(7)
    assert h.value('fatal') and h.value('fatal_code') == 7
    assert not h.logs['load'] and 1 <= len(h.logs['ar']) <= h.read_slots
    assert len(h.logs['rd_cmd']) == len(h.logs['rd_done'])
    assert h.value('axi_quiescent') and not h.value('req_ready')
    COVERAGE['faults']['rresp'] = 1

    # Late B failure terminates the operation and prevents later row commands.
    await h.reset()
    await h.job(2, 3, 7, 1, 1)
    aw_before = len(h.logs['aw'])
    h.put(inject_bresp=2)
    await h.request(write=1, buf=1, base=0x60ff8, stride=24, rows=2, row_bytes=12)
    await h.completion(7, stall=20)
    assert h.value('fatal_code') == 7 and len(h.logs['aw']) == aw_before+1
    assert h.value('axi_quiescent')
    COVERAGE['faults']['bresp'] = 1

    # Error on the last response must suppress successful operation completion.
    await h.reset()
    await h.job(2, 3, 7, 1, 1)
    aw_before = len(h.logs['aw'])
    bursts = expected_bursts(0x60ff8, 24, 2, 12)
    await h.request(write=1, buf=1, base=0x60ff8, stride=24, rows=2, row_bytes=12)
    await h.until(lambda s: len(h.logs['aw']) == aw_before+len(bursts))
    h.put(inject_bresp=2)
    await h.completion(7, stall=20)
    assert h.value('fatal_code') == 7 and len(h.logs['aw']) == aw_before+len(bursts)
    assert h.value('axi_quiescent')
    COVERAGE['faults']['final_bresp'] = 1

    # An accepted operation that cannot offer its first burst must terminate on
    # memory fatal without issuing any new AXI transaction or local-bank access.
    for write in (0, 1):
        await h.reset()
        h.fixed_gates['allow_wr_cmd' if write else 'allow_rd_cmd'] = 0
        await h.request(write=write)
        await h.idle(4)
        h.put(inject_mem_fatal=1, inject_mem_code=9)
        await h.completion(9)
        assert not any(h.logs[x] for x in ('ar', 'aw', 'w', 'load', 'read'))
        COVERAGE['faults'][f'precommand_fatal_{write}'] = 1
    save_coverage()


@cocotb.test()
async def local_protocol_faults_and_drain(dut):
    h = Harness(dut, random_stalls=False)
    # Read metadata is checked at the adapter even when the burst engine's
    # response is OK. Corrupt only the final delivered word.
    for injection in ('inject_rd_tag', 'inject_rd_index', 'inject_rd_last'):
        await h.reset()
        h.ram.write(0x2000, bytes(range(24)))
        await h.request(base=0x2000, stride=24, rows=2, row_bytes=24)
        await h.until(lambda s: s['rd_data_valid'] and s['rd_data_ready'] and s['rd_data_index'] == 1)
        h.put(**{injection: 1})
        await h.until(lambda s: s['rd_data_valid'] and s['rd_data_ready'])
        h.put(**{injection: 0})
        await h.completion(8)
        assert h.value('fatal_code') == 8 and len(h.logs['ar']) == min(2, h.read_slots)
        assert len(h.logs['rd_cmd']) == len(h.logs['rd_done'])
        assert len(h.logs['load']) == 2, 'Malformed final beat reached operand bank'
        assert h.value('axi_quiescent')
        COVERAGE['faults'][injection] = 1

    # Failure of a C response makes the remainder of the already accepted burst
    # harmless through zero byte strobes, while preserving the B obligation.
    for injection in ('inject_response_error', 'inject_response_strb'):
        await h.reset()
        await h.job(2, 5, 9, 0, 0)
        base, stride = 0x60000, 32
        sentinel = bytes([0xab])*80
        h.ram.write(base-8, sentinel)
        aw_before, w_before, reads_before = (len(h.logs[x]) for x in ('aw', 'w', 'read'))
        await h.request(write=1, base=base, stride=stride, rows=2, row_bytes=20)
        await h.until(lambda s: s['response_valid'] and s['response_ready'])
        # Fault word 1 for a response error, or the final odd-column word's
        # required 0x0f mask for the mask-mismatch case.
        good_words = 1
        if injection == 'inject_response_strb':
            await h.until(lambda s: s['response_valid'] and s['response_ready'])
            good_words = 2
        h.put(**{injection: 1})
        await h.completion(8)
        h.put(**{injection: 0})
        assert h.value('fatal_code') == 8 and len(h.logs['aw']) == aw_before+1
        words = h.logs['w'][w_before:]
        assert len(words) == 3 and [word[1] for word in words] == [0xff]*good_words+[0]*(3-good_words)
        assert len(h.logs['read'])-reads_before == good_words+1
        actual = h.ram.read(base-8, len(sentinel))
        end = 8+8*good_words
        assert actual[:8] == sentinel[:8] and actual[end:] == sentinel[end:]
        assert h.value('axi_quiescent')
        COVERAGE['faults'][injection] = 1

    # A C read which has already been offered cannot disappear under a fatal.
    # Delay acceptance, then delay the resulting response, and finally drain it.
    await h.reset()
    await h.job(1, 5, 8, 0, 0)
    base = 0x60000
    sentinel = bytes([0xc3])*32
    h.ram.write(base, sentinel)
    read_before, w_before = len(h.logs['read']), len(h.logs['w'])
    h.fixed_gates.update(allow_read=0, allow_response=0)
    await h.request(write=1, base=base, stride=24, rows=1, row_bytes=20)
    await h.until(lambda s: s['read_valid'])
    h.put(inject_mem_fatal=1, inject_mem_code=9)
    await h.idle(5)
    assert h.value('read_valid') and not h.value('done_valid')
    h.fixed_gates['allow_read'] = 1
    await h.until(lambda s: s['read_valid'] and s['read_ready'])
    await h.idle(5)
    assert h.value('tile_response_valid') and not h.value('done_valid')
    h.fixed_gates['allow_response'] = 1
    await h.completion(9)
    assert len(h.logs['read']) == read_before+1
    assert [x[1] for x in h.logs['w'][w_before:]] == [0, 0, 0]
    assert h.ram.read(base, len(sentinel)) == sentinel and h.value('axi_quiescent')
    COVERAGE['faults']['fatal_with_offered_c_read'] = 1

    # A stalled operand write must preserve its offered payload through a fault.
    await h.reset()
    h.fixed_gates['allow_load'] = 0
    h.ram.write(0x2000, bytes(range(24)))
    await h.request(base=0x2000, stride=24, row_bytes=24)
    await h.until(lambda s: s['load_valid'])
    h.put(inject_mem_fatal=1, inject_mem_code=9)
    await h.idle(5)
    h.fixed_gates['allow_load'] = 1
    await h.completion(9)
    assert len(h.logs['load']) == 1 and h.value('axi_quiescent')
    COVERAGE['faults']['fatal_with_stalled_load'] = 1

    # A forged early OK terminal response cannot cancel the buffered engine's
    # accepted write. Hold it until consumed, then await the genuine AXI B.
    await h.reset()
    await h.job(1, 5, 7, 0, 0)
    base = 0x60000
    sentinel = bytes([0x67])*32
    h.ram.write(base, sentinel)
    w_before, b_before = len(h.logs['w']), len(h.logs['b'])
    h.fixed_gates['allow_response'] = 0
    await h.request(write=1, base=base, stride=24, rows=1, row_bytes=20)
    await h.until(lambda s: s['tile_response_valid'])
    h.put(inject_wr_done=1)
    await h.idle(5)
    assert h.value('fatal') and not h.value('done_valid')
    h.fixed_gates['allow_response'] = 1
    await h.until(lambda s: s['wr_done_valid'] and s['wr_done_ready'])
    h.put(inject_wr_done=0)
    await h.completion(8)
    assert len(h.logs['b']) == b_before+1 and h.value('axi_quiescent')
    assert [x[1] for x in h.logs['w'][w_before:]] == [0, 0, 0]
    assert h.ram.read(base, len(sentinel)) == sentinel
    COVERAGE['faults']['premature_write_ok_with_pending_response'] = 1

    # Exercise the real burst engine's W_COLLECT cancellation: an unsolicited B
    # is an AXI protocol fault, and its terminal error cannot bypass a C read
    # already offered by the adapter. No AW or W obligation exists yet.
    for phase in ('offered_read', 'held_write_data'):
        await h.reset()
        await h.job(1, 5, 8, 0, 0)
        base = 0x60000
        sentinel = bytes([0x4d])*32
        h.ram.write(base, sentinel)
        before = {name: len(h.logs[name]) for name in ('aw', 'w', 'b', 'read')}
        if phase == 'offered_read':
            h.fixed_gates.update(allow_read=0, allow_response=0)
        else:
            h.fixed_gates['allow_wr_data'] = 0
        await h.request(write=1, base=base, stride=24, rows=1, row_bytes=20)
        await h.until(lambda s: s['read_valid'] if phase == 'offered_read' else s['wr_data_valid'])
        h.put(inject_spurious_b=1)
        await h.step()
        h.put(inject_spurious_b=0)
        if phase == 'offered_read':
            await h.idle(5)
            assert h.value('read_valid') and not h.value('done_valid')
            assert h.value('wr_done_valid') and h.value('wr_done_status') == 8
            h.fixed_gates['allow_read'] = 1
            await h.until(lambda s: s['read_valid'] and s['read_ready'])
            await h.idle(5)
            assert h.value('tile_response_valid') and not h.value('done_valid')
            h.fixed_gates['allow_response'] = 1
        await h.completion(8)
        assert h.value('fatal_code') == 8 and h.value('axi_quiescent')
        assert all(len(h.logs[name]) == before[name] for name in ('aw', 'w', 'b'))
        assert len(h.logs['read']) == before['read']+1
        assert h.ram.read(base, len(sentinel)) == sentinel
        COVERAGE['faults'][f'engine_cancel_{phase}'] = 1
    save_coverage()


@cocotb.test()
async def gather_transition_fault_edges(dut):
    """Faults on command/data acceptance must preserve newly issued obligations."""
    h = Harness(dut, random_stalls=False)
    await h.reset()
    await h.job(2, 5, 7, 0, 0)
    base, stride = 0x60000, 32
    sentinel = bytes([0x96])*80
    h.ram.write(base-8, sentinel)
    before = {name: len(h.logs[name]) for name in ('wr_cmd', 'read', 'response', 'aw', 'w', 'b')}
    h.fixed_gates.update(allow_wr_cmd=0, allow_read=0, allow_response=0)
    await h.request(write=1, base=base, stride=stride, rows=2, row_bytes=20)
    await h.until(lambda s: s['wr_cmd_valid'] and not s['wr_cmd_ready'])

    # The command handshakes on the exact edge an unsolicited B makes the
    # real burst engine fatal. Its registered cancellation appears afterwards.
    # The adapter has already entered its direct C-request path on this edge;
    # that offered request must survive cancellation and response backpressure.
    h.fixed_gates['allow_wr_cmd'] = 1
    edge = await h.step(inject_spurious_b=1)
    assert edge['wr_cmd_valid'] and edge['wr_cmd_ready']
    h.put(inject_spurious_b=0)
    assert h.value('fatal') and h.value('fatal_code') == 8
    assert h.value('read_valid') and not h.value('read_ready')
    await h.idle(5)
    assert h.value('read_valid') and not h.value('done_valid')
    assert h.value('wr_done_valid') and h.value('wr_done_status') == 8
    assert not h.value('wr_done_ready'), 'Cancellation bypassed an offered C request'
    h.fixed_gates['allow_read'] = 1
    await h.until(lambda s: s['read_valid'] and s['read_ready'])
    await h.until(lambda s: s['tile_response_valid'])
    await h.idle(5)
    assert h.value('tile_response_valid') and not h.value('done_valid')
    assert not h.value('wr_done_ready'), 'Cancellation bypassed a held C response'
    h.fixed_gates['allow_response'] = 1
    await h.completion(8, stall=11)
    assert h.value('axi_quiescent') and not h.value('req_ready')
    assert len(h.logs['wr_cmd']) == before['wr_cmd']+1
    assert len(h.logs['read']) == before['read']+1
    assert len(h.logs['response']) == before['response']+1
    assert all(len(h.logs[name]) == before[name] for name in ('aw', 'w', 'b'))
    assert h.ram.read(base-8, len(sentinel)) == sentinel
    COVERAGE['faults']['command_accept_same_edge_engine_fatal'] = 1

    for fault in ('memory_fatal', 'premature_success'):
        await h.reset()
        golden = await h.job(2, 5, 7, 0, 0)
        h.ram.write(base-8, sentinel)
        before = {name: len(h.logs[name]) for name in
                  ('wr_cmd', 'read', 'response', 'write_data', 'aw', 'w', 'b')}
        h.fixed_gates['allow_wr_data'] = 0
        h.ram.write_if.b_channel.pause = True
        await h.request(write=1, base=base, stride=stride, rows=2, row_bytes=20)
        await h.until(lambda s: s['wr_data_valid'] and not s['wr_data_ready'])
        await h.idle(5)
        # This first nonfinal word was offered before the fault. It is still
        # valid data, and must handshake unchanged when READY is released.
        held_word = (h.value('wr_data'), h.value('wr_data_strb'))
        assert held_word[1] == 0xff
        h.fixed_gates.update(allow_wr_data=1, allow_read=0)
        if fault == 'memory_fatal':
            edge = await h.step(inject_mem_fatal=1, inject_mem_code=9)
            expected_status = 9
        else:
            # Unlike registered mem_fatal, this exercises local_fault on the
            # same accepted-word edge. A premature success cannot discharge
            # the command: the remaining words and genuine B are still owed.
            edge = await h.step(inject_wr_done=1)
            assert edge['wr_done_valid'] and edge['wr_done_ready']
            assert edge['wr_done_status'] == 0
            h.put(inject_wr_done=0)
            expected_status = 8
        assert edge['wr_data_valid'] and edge['wr_data_ready']
        assert (edge['wr_data'], edge['wr_data_strb']) == held_word
        assert h.value('fatal') and h.value('fatal_code') == expected_status

        # The optimized nonfinal-word transition must observe stop_work on
        # this edge. Keep C request READY low: an erroneous extra request would
        # become an obligation and prevent the required zero-filled drain.
        await h.until(lambda s: s['m_axi_wvalid'] and s['m_axi_wready'] and s['m_axi_wlast'])
        await h.idle(8)
        assert not h.value('read_valid') and not h.value('done_valid')
        assert len(h.logs['b']) == before['b']
        assert len(h.logs['wr_cmd']) == before['wr_cmd']+1
        assert len(h.logs['read']) == before['read']+1
        assert len(h.logs['response']) == before['response']+1
        assert [word[1] for word in h.logs['write_data'][before['write_data']:]] == [0xff, 0, 0]
        assert [word[1] for word in h.logs['w'][before['w']:]] == [0xff, 0, 0]
        h.ram.write_if.b_channel.pause = False
        await h.completion(expected_status, stall=11)
        assert len(h.logs['b']) == before['b']+1
        assert len(h.logs['aw']) == before['aw']+1
        assert h.value('axi_quiescent') and not h.value('req_ready')
        expected = bytearray(sentinel)
        expected[8:16] = b''.join(value.to_bytes(4, 'little', signed=True) for value in golden[0][:2])
        assert h.ram.read(base-8, len(sentinel)) == expected
        COVERAGE['faults'][f'nonfinal_word_accept_same_edge_{fault}'] = 1
    save_coverage()


@cocotb.test()
async def queued_read_credits_and_row_metadata(dut):
    """A read credit includes AXI validation, bank delivery and local DONE."""
    h = Harness(dut, random_stalls=False)
    await h.reset()
    base, stride, rows, width = 0xff8, 264, min(h.t, 8), 256
    image = bytes((i*73+(i//257)*19+11) & 255 for i in range(rows*stride))
    h.ram.write(base, image)
    plan = expected_bursts(base, stride, rows, width)
    h.ram.read_if.r_channel.pause = True
    h.fixed_gates.update(allow_load=0, allow_rd_done=0)
    await h.request(bt=1, buf=1, base=base, stride=stride, rows=rows, row_bytes=width)
    await h.until(lambda s: len(h.logs['ar']) == h.read_slots)
    await h.idle(20)
    assert len(h.logs['ar']) == len(h.logs['rd_cmd']) == h.read_slots
    assert not h.logs['r'] and not h.logs['load'] and not h.logs['rd_done']
    assert [(x[0], x[1]+1) for x in h.logs['ar']] == plan[:h.read_slots]

    h.ram.read_if.r_channel.pause = False
    await h.until(lambda s: len(h.logs['r']) == sum(beats for _, beats in plan[:h.read_slots]))
    await h.idle(20)
    assert len(h.logs['ar']) == h.read_slots, 'AXI completion alone returned a read credit'
    assert not h.logs['load'] and not h.logs['rd_done'] and not h.value('done_valid')
    assert h.value('load_valid'), 'Validated head did not offer its first bank word'

    h.fixed_gates['allow_load'] = 1
    await h.until(lambda s: len(h.logs['load']) == plan[0][1])
    await h.idle(20)
    assert len(h.logs['ar']) == h.read_slots, 'Final bank word bypassed held local DONE'
    assert not h.logs['rd_done'] and not h.value('done_valid')
    h.fixed_gates['allow_rd_done'] = 1
    await h.completion(stall=19)
    expected = [(1, 1, row, word,
                 int.from_bytes(image[row*stride+8*word:row*stride+8*word+8], 'little'))
                for row in range(rows) for word in range(width//8)]
    assert h.logs['load'] == expected
    assert [(x[0], x[1]+1) for x in h.logs['ar']] == plan
    assert len(h.logs['rd_cmd']) == len(h.logs['rd_done']) == len(plan)
    assert not h.axi_reads and not h.local_reads and h.value('axi_quiescent')
    COVERAGE['queued_scenarios'].append('full_credit_before_first_R_and_bank_DONE_credit')

    # The final useful word touches the last byte in the 128 MiB window.
    # Independent row enumeration also checks metadata over several slot wraps.
    first = len(h.logs['load'])
    base = (1 << 27)-1024
    image = bytes((i*31+7) & 255 for i in range(1024))
    h.ram.write(base, image)
    await h.request(bt=0, buf=0, base=base, stride=256, rows=4, row_bytes=256)
    await h.completion()
    assert h.logs['load'][first:] == [
        (0, 0, row, word, int.from_bytes(image[row*256+8*word:row*256+8*word+8], 'little'))
        for row in range(4) for word in range(32)]
    assert h.logs['ar'][-1][0]+8*(h.logs['ar'][-1][1]+1) == 1 << 27
    COVERAGE['queued_scenarios'].append('last_DDR_word_after_metadata_wrap')
    save_coverage()


@cocotb.test()
async def queued_read_fault_drains_accepted_commands(dut):
    h = Harness(dut, random_stalls=False)
    await h.reset()
    if h.read_slots == 1:
        # Existing fault tests cover the default serial path; the scenarios
        # below deliberately require multiple prior accepted read commands.
        save_coverage()
        return

    # Fill three slots while denying the fourth local command. Block AXI AR
    # before admitting that fourth command, making its offered address an
    # irrevocable AXI obligation at the later response fault.
    h.ram.read_if.r_channel.pause = True
    await h.request(base=0xff8, stride=264, rows=4, row_bytes=256)
    await h.until(lambda s: len(h.logs['rd_cmd']) == 3)
    h.fixed_gates['allow_rd_cmd'] = 0
    await h.until(lambda s: len(h.logs['ar']) == 3)
    h.ram.read_if.ar_channel.pause = True
    await h.idle(4)
    h.fixed_gates['allow_rd_cmd'] = 1
    await h.until(lambda s: s['m_axi_arvalid'] and not s['m_axi_arready'])
    held = (h.value('m_axi_araddr'), h.value('m_axi_arlen'))
    assert len(h.logs['rd_cmd']) == 4 and len(h.logs['ar']) == 3
    h.put(inject_rresp=2)
    h.ram.read_if.r_channel.pause = False
    await h.until(lambda s: s['fatal'])
    await h.idle(12)
    assert h.value('m_axi_arvalid') and (h.value('m_axi_araddr'), h.value('m_axi_arlen')) == held
    assert h.value('busy') and not h.value('done_valid') and not h.logs['load']
    assert len(h.logs['rd_cmd']) == 4
    h.ram.read_if.ar_channel.pause = False
    await h.completion(7)
    assert len(h.logs['ar']) == len(h.logs['rd_done']) == 4
    assert not h.logs['load'] and not h.axi_reads and not h.local_reads
    assert h.value('axi_quiescent') and not h.value('req_ready')
    COVERAGE['queued_scenarios'].append('RRESP_with_three_issued_and_one_stalled_AR')

    # A local command never accepted by the burst engine may be canceled.
    # Earlier accepted commands must still drain; the operand set is invalid.
    await h.reset()
    h.ram.read_if.r_channel.pause = True
    await h.request(base=0xff8, stride=264, rows=4, row_bytes=256)
    await h.until(lambda s: len(h.logs['rd_cmd']) == 2)
    h.fixed_gates['allow_rd_cmd'] = 0
    await h.until(lambda s: s['rd_cmd_valid'] and not s['rd_cmd_ready'])
    await h.until(lambda s: len(h.logs['ar']) == 2)
    h.put(inject_mem_fatal=1, inject_mem_code=9)
    await h.idle(12)
    assert h.value('busy') and not h.value('done_valid')
    assert len(h.logs['rd_cmd']) == 2 and not h.logs['load']
    h.ram.read_if.r_channel.pause = False
    await h.completion(9)
    assert len(h.logs['ar']) == len(h.logs['rd_done']) == 2
    assert not h.logs['load'] and not h.axi_reads and not h.local_reads
    assert h.value('axi_quiescent')
    COVERAGE['queued_scenarios'].append('external_fatal_cancels_unaccepted_local_offer_only')
    save_coverage()
