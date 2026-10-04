"""Independent DDR-byte and signed-matrix oracles for concurrent tile DMA."""
from collections import deque
import json
import logging
import os
from pathlib import Path
import random

import cocotb
from cocotbext.axi import AxiBus, AxiRam
from common import tick


COVERAGE = dict(p=int(os.environ['GEMM_P']), t=int(os.environ['GEMM_T']),
                read_slots=int(os.environ['GEMM_READ_SLOTS']), seed=20261004,
                jobs=0, output_values=0, shapes=[], read_occupancy=[],
                simultaneous_descriptors=0, compute_load_edges=0,
                compute_store_read_edges=0, three_way_edges=0,
                read_bursts=0, write_bursts=0, write_responses=0,
                read_lengths=[], write_lengths=[], stalls={}, scenarios=[])


def save_coverage(scenario):
    COVERAGE['scenarios'].append(scenario)
    Path(os.environ['GEMM_COVERAGE']).write_text(json.dumps(COVERAGE, indent=2)+'\n')


def pauses(seed):
    rng = random.Random(seed)
    while True:
        yield rng.randrange(4) == 0


def bursts(base, stride, rows, useful_bytes):
    """Enumerate row words first, then partition by page and maximum length."""
    result = []
    for row in range(rows):
        group = []
        for offset in range(0, useful_bytes, 8):
            address = base+row*stride+offset
            if group and (len(group) == 16 or address//4096 != group[0]//4096):
                result.append((group[0], len(group)))
                group = []
            group.append(address)
        if group:
            result.append((group[0], len(group)))
    return result


class Harness:
    GATES = ('allow_load', 'allow_read', 'allow_response', 'allow_rd_cmd',
             'allow_rd_done', 'allow_wr_cmd', 'allow_wr_data')
    INPUTS = ('start', 'input_buf', 'output_buf', 'm', 'n', 'k',
              'inject_response_error', 'inject_response_strb', 'inject_rd_tag',
              'inject_rd_index', 'inject_rd_last', 'inject_wr_done', 'inject_spurious_b',
              'inject_mem_fatal', 'inject_mem_code', 'inject_rresp', 'inject_bresp')
    CHANNELS = {
        'ar': ('m_axi_arvalid', 'm_axi_arready', tuple('m_axi_ar'+s for s in
               ('addr', 'len', 'id', 'size', 'burst', 'lock', 'cache', 'prot', 'qos', 'region'))),
        'aw': ('m_axi_awvalid', 'm_axi_awready', tuple('m_axi_aw'+s for s in
               ('addr', 'len', 'id', 'size', 'burst', 'lock', 'cache', 'prot', 'qos', 'region'))),
        'w': ('m_axi_wvalid', 'm_axi_wready', ('m_axi_wdata', 'm_axi_wstrb', 'm_axi_wlast')),
        'r': ('m_axi_rvalid', 'm_axi_rready', ('m_axi_rdata', 'm_axi_rresp', 'm_axi_rlast')),
        'b': ('m_axi_bvalid', 'm_axi_bready', ('m_axi_bresp',)),
        'load': ('load_valid', 'load_ready', ('load_bt', 'load_buf', 'load_q', 'load_word', 'load_data')),
        'read': ('read_valid', 'read_ready', ('read_buf', 'read_row', 'read_pair')),
        'response': ('tile_response_valid', 'tile_response_ready',
                     ('tile_response_data', 'tile_response_strb', 'tile_response_error')),
        'wr_data': ('wr_data_valid', 'wr_data_ready', ('wr_data', 'wr_data_strb')),
        'load_done': ('load_done_valid', 'load_done_ready', ('load_done_status',)),
        'store_done': ('store_done_valid', 'store_done_ready', ('store_done_status',)),
        'rd_cmd': ('rd_cmd_valid', 'rd_cmd_ready', ('rd_cmd_addr', 'rd_cmd_beats', 'rd_cmd_tag')),
        'rd_done': ('rd_done_valid', 'rd_done_ready', ('rd_done_status', 'rd_done_tag')),
        'burst_rd_done': ('burst_rd_done_valid', 'burst_rd_done_ready', ('rd_done_status', 'rd_done_tag')),
        'wr_cmd': ('wr_cmd_valid', 'wr_cmd_ready', ('wr_cmd_addr', 'wr_cmd_beats', 'wr_cmd_tag')),
        'wr_done': ('wr_done_valid', 'wr_done_ready', ('wr_done_status', 'wr_done_tag')),
    }

    def __init__(self, dut, random_stalls=True):
        self.dut = dut
        self.p, self.t, self.depth = (COVERAGE[s] for s in ('p', 't', 'read_slots'))
        self.rng = random.Random(COVERAGE['seed']+self.p+self.t)
        self.ram = AxiRam(AxiBus.from_prefix(dut, 'm_axi'), dut.clk, dut.rst, size=2**27)
        self.ram.read_if.log.setLevel(logging.WARNING)
        self.ram.write_if.log.setLevel(logging.WARNING)
        self.channels = (self.ram.read_if.ar_channel, self.ram.read_if.r_channel,
                         self.ram.write_if.aw_channel, self.ram.write_if.w_channel,
                         self.ram.write_if.b_channel)
        if random_stalls:
            for index, channel in enumerate(self.channels):
                channel.set_pause_generator(pauses(8200+index))
        self.random_local = random_stalls
        self.fixed = {}
        self.cycle = 0
        self.logs = {name: [] for name in self.CHANNELS}
        self.held = {}
        self.external_reads = deque()
        self.local_reads = deque()
        self.frozen_fault = None

    def put(self, **values):
        for name, value in values.items():
            getattr(self.dut, name).value = value

    def value(self, name):
        return int(getattr(self.dut, name).value)

    async def reset(self):
        self.put(clk=0, rst=1)
        for name in self.INPUTS:
            self.put(**{name: 0})
        for direction in ('load', 'store'):
            for suffix in ('valid', 'buf', 'base', 'stride', 'rows', 'row_bytes'):
                self.put(**{f'{direction}_req_{suffix}': 0})
            self.put(**{f'{direction}_done_ready': 0})
        self.put(load_req_bt=0)
        self.fixed.clear()
        for gate in self.GATES:
            self.put(**{gate: 1})
        for channel in self.channels:
            channel.pause = False
        for _ in range(5):
            await tick(self.dut)
        self.put(rst=0)
        self.logs = {name: [] for name in self.CHANNELS}
        self.held.clear()
        self.external_reads.clear()
        self.local_reads.clear()
        self.frozen_fault = None
        for _ in range(3):
            await self.step()
        assert self.value('load_req_ready') and self.value('store_req_ready')
        assert not self.value('fatal')

    async def step(self, **values):
        for gate in self.GATES:
            self.put(**{gate: self.fixed.get(gate, int(not self.random_local or self.rng.randrange(4) != 0))})
        self.put(**values)
        names = {'fatal', 'fatal_code', 'busy', 'load_busy', 'store_busy',
                 'load_req_valid', 'load_req_ready', 'store_req_valid', 'store_req_ready',
                 'compute_busy', 'compute_done', 'compute_cmd_error', 'job_cycles',
                 'start_ready', 'axi_quiescent', 'local_idle', 'mem_fatal',
                 'rd_data_valid', 'rd_data_ready', 'rd_data_index', 'rd_data_last', 'rd_data_tag',
                 'inject_rd_tag', 'inject_rd_index', 'inject_rd_last'}
        for valid, ready, fields in self.CHANNELS.values():
            names.update((valid, ready, *fields))
        qualifiers = {field: valid for valid, _, fields in self.CHANNELS.values() for field in fields}

        def sample():
            result = {}
            for name in names:
                try:
                    result[name] = 0 if name in qualifiers and not self.value(qualifiers[name]) else self.value(name)
                except ValueError:
                    raise AssertionError(f'Undefined active signal {name}') from None
            return result

        s = await tick(self.dut, sample)
        self.cycle += 1
        # R is checked against previously accepted AR addresses, never live ARADDR.
        if s['m_axi_rvalid'] and s['m_axi_rready']:
            assert self.external_reads, 'Unowned AXI read response'
            owner = self.external_reads[0]
            assert s['m_axi_rlast'] == (owner['index'] == owner['beats']-1)
            expect = int.from_bytes(self.ram.read(owner['addr']+8*owner['index'], 8), 'little')
            assert s['m_axi_rdata'] == expect
            owner['index'] += 1
            if s['m_axi_rlast']:
                self.external_reads.popleft()
        if s['m_axi_arvalid'] and s['m_axi_arready']:
            self.external_reads.append(dict(addr=s['m_axi_araddr'], beats=s['m_axi_arlen']+1, index=0))
        if s['rd_data_valid'] and s['rd_data_ready']:
            assert self.local_reads
            owner = self.local_reads[0]
            for field, expected in (('tag', owner['tag']), ('index', owner['received']),
                                    ('last', int(owner['received'] == owner['beats']-1))):
                if not s['inject_rd_'+field]:
                    assert s['rd_data_'+field] == expected
            owner['received'] += 1
        if s['rd_done_valid'] and s['rd_done_ready']:
            assert self.local_reads
            owner = self.local_reads.popleft()
            assert s['rd_done_tag'] == owner['tag']
            if s['rd_done_status'] == 0:
                assert owner['received'] == owner['beats']
        if s['rd_cmd_valid'] and s['rd_cmd_ready']:
            self.local_reads.append(dict(tag=s['rd_cmd_tag'], beats=s['rd_cmd_beats'], received=0))
        assert len(self.local_reads) <= self.depth
        COVERAGE['read_occupancy'] = sorted(set(COVERAGE['read_occupancy']+[len(self.local_reads)]))
        for name, (valid, ready, fields) in self.CHANNELS.items():
            payload = tuple(s[field] for field in fields)
            # Test gates hide local VALID temporarily. Monitor the original
            # burst terminal above that gate; only log delivered terminals here.
            if name == 'rd_done':
                if s[valid] and s[ready]:
                    self.logs[name].append(payload)
                continue
            if name in ('rd_cmd', 'wr_cmd') and s['fatal']:
                self.held.pop(name, None)
            if name in self.held:
                assert s[valid] and payload == self.held[name], f'{name} changed while stalled'
            if s[valid] and not s[ready]:
                self.held[name] = payload
                COVERAGE['stalls'][name] = COVERAGE['stalls'].get(name, 0)+1
            else:
                self.held.pop(name, None)
            # Local commands may be canceled before acceptance; AXI offers may not.
            if name in ('rd_cmd', 'wr_cmd') and s['fatal']:
                self.held.pop(name, None)
            if name == 'wr_data' and s['wr_done_valid'] and s['wr_done_ready'] and s['wr_done_status'] and s['mem_fatal']:
                self.held.pop(name, None)
            if s[valid] and s[ready]:
                self.logs[name].append(payload)
                if name in ('ar', 'aw'):
                    address, length, ident, size, burst, *sidebands = payload
                    assert ident == 0 and size == 3 and burst == 1 and not any(sidebands)
                    assert address % 8 == 0 and 0 <= length <= 15
                    assert address//4096 == (address+8*(length+1)-1)//4096
                    key = 'read' if name == 'ar' else 'write'
                    COVERAGE[key+'_bursts'] += 1
                    COVERAGE[key+'_lengths'] = sorted(set(COVERAGE[key+'_lengths']+[length+1]))
                if name == 'b':
                    COVERAGE['write_responses'] += 1
        if s['fatal']:
            assert not s['load_req_ready'] and not s['store_req_ready']
            assert not s['rd_cmd_valid'] and not s['wr_cmd_valid']
        # The code is frozen after the wrapper observation edge, not during
        # its earlier combinational priority-selection interval.
        if int(self.dut.dma.first_fault_q.value):
            if self.frozen_fault is None:
                self.frozen_fault = self.value('fatal_code')
            assert self.value('fatal') and self.value('fatal_code') == self.frozen_fault
        dual = s['load_req_valid'] and s['load_req_ready'] and s['store_req_valid'] and s['store_req_ready']
        COVERAGE['simultaneous_descriptors'] += int(dual)
        loading = s['load_valid'] and s['load_ready']
        reading = s['read_valid'] and s['read_ready']
        COVERAGE['compute_load_edges'] += int(s['compute_busy'] and loading)
        COVERAGE['compute_store_read_edges'] += int(s['compute_busy'] and reading)
        COVERAGE['three_way_edges'] += int(s['compute_busy'] and loading and reading)
        return s

    async def until(self, predicate, limit=50000):
        for _ in range(limit):
            s = await self.step()
            if predicate(s):
                return s
        raise AssertionError(f'Timeout at cycle {self.cycle}')

    async def idle(self, cycles):
        for _ in range(cycles):
            await self.step()

    def offer(self, direction, *, buf=0, bt=0, base=0xff8, stride=256, rows=1, row_bytes=8):
        values = dict(valid=1, buf=buf, base=base, stride=stride, rows=rows, row_bytes=row_bytes)
        self.put(**{f'{direction}_req_{name}': value for name, value in values.items()})
        self.put(**{f'{direction}_done_ready': 0})
        if direction == 'load':
            self.put(load_req_bt=bt)

    def withdraw(self, direction):
        # Change live fields after acceptance to detect missing descriptor snapshots.
        self.put(**{f'{direction}_req_{name}': value for name, value in
                    dict(valid=0, buf=1, base=0xffffffff, stride=3, rows=0, row_bytes=0).items()})
        if direction == 'load':
            self.put(load_req_bt=1)

    async def request(self, direction, **descriptor):
        self.offer(direction, **descriptor)
        await self.until(lambda s: s[direction+'_req_ready'])
        self.withdraw(direction)

    async def complete(self, direction, status=0, hold=7):
        await self.until(lambda s: s[direction+'_done_valid'])
        assert self.value(direction+'_done_status') == status
        await self.idle(hold)
        assert self.value(direction+'_done_valid') and self.value(direction+'_done_status') == status
        assert self.value(direction+'_busy') and not self.value(direction+'_req_ready')
        self.put(**{direction+'_done_ready': 1})
        await self.step()
        self.put(**{direction+'_done_ready': 0})
        await self.step()
        assert not self.value(direction+'_done_valid')

    def input_region(self, matrix, k, buf, bt, base, stride=272):
        region = bytearray(self.rng.randrange(1, 256) for _ in range(len(matrix)*stride+16))
        for row, values in enumerate(matrix):
            region[8+row*stride:8+row*stride+k] = bytes(x & 255 for x in values)
        self.ram.write(base-8, region)
        return dict(buf=buf, bt=bt, base=base, stride=stride, rows=len(matrix), row_bytes=k), region

    async def load_matrix(self, matrix, k, buf, bt, base):
        descriptor, region = self.input_region(matrix, k, buf, bt, base)
        first_ar, first_load = len(self.logs['ar']), len(self.logs['load'])
        await self.request('load', **descriptor)
        await self.complete('load')
        self.check_load(descriptor, region, first_ar, first_load)

    def check_load(self, d, region, first_ar, first_load):
        expected = [(d['bt'], d['buf'], row, word,
                     int.from_bytes(region[8+row*d['stride']+word*8:16+row*d['stride']+word*8], 'little'))
                    for row in range(d['rows']) for word in range((d['row_bytes']+7)//8)]
        assert self.logs['load'][first_load:] == expected
        assert [(x[0], x[1]+1) for x in self.logs['ar'][first_ar:]] == bursts(d['base'], d['stride'], d['rows'], d['row_bytes'])
        assert self.ram.read(d['base']-8, len(region)) == region

    def matrices(self, m, n, k):
        a = [[self.rng.randrange(-128, 128) for _ in range(k)] for _ in range(m)]
        b = [[self.rng.randrange(-128, 128) for _ in range(n)] for _ in range(k)]
        # Force signed extrema in every reduction, including K=1.
        a[0][0], b[0][0] = -128, -128
        bt = [list(column) for column in zip(*b)]
        golden = [[sum(int(a[i][q])*int(b[q][j]) for q in range(k)) for j in range(n)] for i in range(m)]
        return a, bt, golden

    async def inputs(self, a, bt, k, ib):
        await self.load_matrix(a, k, ib, 0, 0xff8+ib*0x10000)
        await self.load_matrix(bt, k, ib, 1, 0x20ff8+ib*0x10000)

    async def launch(self, m, n, k, ib, ob):
        assert self.value('start_ready')
        s = await self.step(start=1, m=m, n=n, k=k, input_buf=ib, output_buf=ob)
        assert s['start_ready']
        self.put(start=0, m=0, n=0, k=0, input_buf=1-ib, output_buf=1-ob)

    async def finish_compute(self, m, n, k):
        if self.value('compute_busy'):
            await self.until(lambda s: s['compute_done'])
        assert not self.value('compute_cmd_error')
        assert self.value('job_cycles') == ((m+self.p-1)//self.p)*((n+self.p-1)//self.p)*(k+3*self.p+3)

    async def prepare(self, m, n, k, ib=0, ob=0):
        a, bt, golden = self.matrices(m, n, k)
        await self.inputs(a, bt, k, ib)
        await self.launch(m, n, k, ib, ob)
        await self.finish_compute(m, n, k)
        return golden

    def output_region(self, golden, buf, base):
        m, n = len(golden), len(golden[0])
        stride = ((4*n+7)//8)*8+24
        region = bytearray(self.rng.randrange(256) for _ in range(m*stride+16))
        expected = bytearray(region)
        for row in range(m):
            for col in range(n):
                expected[8+row*stride+4*col:12+row*stride+4*col] = golden[row][col].to_bytes(4, 'little', signed=True)
        self.ram.write(base-8, region)
        d = dict(buf=buf, base=base, stride=stride, rows=m, row_bytes=4*n)
        mark = {key: len(self.logs[key]) for key in ('aw', 'w', 'b', 'read')}
        return d, expected, mark

    def check_store(self, d, expected, mark, k):
        m, n = d['rows'], d['row_bytes']//4
        assert self.ram.read(d['base']-8, len(expected)) == expected, 'Result/padding/guard mismatch'
        assert [(x[0], x[1]+1) for x in self.logs['aw'][mark['aw']:]] == bursts(d['base'], d['stride'], m, 4*n)
        assert len(self.logs['b'])-mark['b'] == len(bursts(d['base'], d['stride'], m, 4*n))
        assert self.logs['read'][mark['read']:] == [(d['buf'], row, pair) for row in range(m) for pair in range((n+1)//2)]
        strobes = [15 if n % 2 and pair == n//2 else 255 for row in range(m) for pair in range((n+1)//2)]
        assert [x[1] for x in self.logs['w'][mark['w']:]] == strobes
        expected_last = [int(index == length-1)
                         for _, length in bursts(d['base'], d['stride'], m, 4*n)
                         for index in range(length)]
        assert [x[2] for x in self.logs['w'][mark['w']:]] == expected_last
        COVERAGE['jobs'] += 1
        COVERAGE['output_values'] += m*n
        COVERAGE['shapes'].append([m, n, k])

    async def store(self, golden, buf=0, base=0x40ff8, k=1):
        d, expected, mark = self.output_region(golden, buf, base)
        await self.request('store', **d)
        await self.complete('store')
        self.check_store(d, expected, mark, k)

    async def drained(self):
        await self.until(lambda s: s['axi_quiescent'] and s['local_idle'] and not s['busy'])
        assert not self.external_reads and not self.local_reads
        assert len(self.logs['rd_cmd']) == len(self.logs['rd_done'])
        assert len(self.logs['wr_cmd']) == len(self.logs['wr_done'])


@cocotb.test()
async def pipeline_with_three_independent_owners(dut):
    h = Harness(dut)
    await h.reset()
    # Input and result buffer numbers name separate memories: next input1,
    # active input0/result1, preceding result0 are all legal together.
    previous = await h.prepare(h.t, h.t-1, 33, ib=1, ob=0)
    active_a, active_bt, active = h.matrices(h.t, h.t, 256)
    await h.inputs(active_a, active_bt, 256, 0)
    following_a, following_bt, following = h.matrices(h.t-1, h.t-1, 255)
    load_d, load_region = h.input_region(following_a, 255, 1, 0, 0x10ff8)
    store_d, expected, mark = h.output_region(previous, 0, 0x40ff8)
    first_ar, first_load = len(h.logs['ar']), len(h.logs['load'])
    h.ram.write_if.b_channel.clear_pause_generator()
    h.ram.write_if.b_channel.pause = True
    # Align one accepted operand word and C-bank read while compute is active;
    # after that directed edge the channels resume independent random stalls.
    h.fixed.update(allow_load=0, allow_read=0)
    h.offer('load', **load_d)
    h.offer('store', **store_d)
    await h.launch(h.t, h.t, 256, 0, 1)
    h.withdraw('load')
    h.withdraw('store')
    await h.until(lambda s: s['load_valid'] and s['read_valid'] and s['compute_busy'])
    h.fixed.update(allow_load=1, allow_read=1)
    aligned = await h.step()
    assert aligned['compute_busy'] and aligned['load_ready'] and aligned['read_ready']
    del h.fixed['allow_load']
    del h.fixed['allow_read']
    await h.complete('load')
    h.check_load(load_d, load_region, first_ar, first_load)
    assert h.value('store_busy') and not h.value('store_done_valid')
    assert len(h.logs['b']) == mark['b'], 'Store released before its delayed B response'
    # Another load can be accepted while the writer retains its buffer.
    await h.load_matrix(following_bt, 255, 1, 1, 0x30ff8)
    h.ram.write_if.b_channel.pause = False
    await h.complete('store')
    h.check_store(store_d, expected, mark, 33)
    await h.finish_compute(h.t, h.t, 256)
    await h.store(active, buf=1, base=0x60ff8, k=256)
    await h.launch(h.t-1, h.t-1, 255, 1, 0)
    await h.finish_compute(h.t-1, h.t-1, 255)
    await h.store(following, buf=0, base=0x80ff8, k=255)
    await h.drained()
    assert COVERAGE['simultaneous_descriptors'] and COVERAGE['three_way_edges']
    assert COVERAGE['compute_load_edges'] and COVERAGE['compute_store_read_edges']
    assert max(COVERAGE['read_occupancy']) == h.depth
    save_coverage('three_way_pipeline_delayed_b_snapshot_guards')


@cocotb.test()
async def independent_completion_and_bad_descriptors(dut):
    h = Harness(dut, random_stalls=False)
    await h.reset()
    golden = await h.prepare(2, 3, 9)
    h.ram.write(0x2000, bytes(range(16)))
    await h.request('load', base=0x2000, stride=16, row_bytes=9)
    await h.until(lambda s: s['load_done_valid'])
    # Held successful load DONE does not block stores or change their status.
    await h.store(golden, k=9)
    assert h.value('load_done_valid') and h.value('load_done_status') == 0
    await h.complete('load')
    # Held store DONE permits multiple subsequent operand operations.
    d, expected, mark = h.output_region(golden, 0, 0x60ff8)
    await h.request('store', **d)
    await h.until(lambda s: s['store_done_valid'])
    for base in (0x2000, 0x3000):
        h.ram.write(base, bytes(range(16)))
        await h.request('load', buf=1, base=base, stride=16, row_bytes=9)
        await h.complete('load')
        assert h.value('store_done_status') == 0 and h.value('store_done_valid')
    await h.complete('store')
    h.check_store(d, expected, mark, 9)
    # Invalid descriptors are isolated: only the valid sibling issues traffic.
    for bad_direction in ('load', 'store'):
        good_direction = 'store' if bad_direction == 'load' else 'load'
        before = {key: len(h.logs[key]) for key in ('ar', 'aw', 'load', 'read')}
        h.offer(bad_direction, rows=0)
        if good_direction == 'load':
            h.offer('load', buf=1, base=0x2000, stride=16, row_bytes=9)
        else:
            d, expected, mark = h.output_region(golden, 0, 0x80ff8)
            h.offer('store', **d)
        s = await h.step()
        assert s['load_req_ready'] and s['store_req_ready']
        h.withdraw('load')
        h.withdraw('store')
        await h.complete(bad_direction, 3)
        await h.complete(good_direction)
        if bad_direction == 'load':
            assert len(h.logs['ar']) == before['ar'] and len(h.logs['load']) == before['load']
            h.check_store(d, expected, mark, 9)
        else:
            assert len(h.logs['aw']) == before['aw'] and len(h.logs['read']) == before['read']
        assert not h.value('fatal')
    # A later fatal invalidates job success without rewriting held successful DONE.
    await h.request('load', buf=1, base=0x2000, stride=16, row_bytes=9)
    await h.until(lambda s: s['load_done_valid'])
    h.put(inject_bresp=2)
    await h.request('store', buf=0, base=0xa0ff8, stride=24, rows=2, row_bytes=12)
    await h.complete('store', 7)
    assert h.value('load_done_valid') and h.value('load_done_status') == 0
    await h.complete('load')
    await h.drained()
    save_coverage('independent_done_bad_desc_immutable_success_after_fatal')


@cocotb.test()
async def first_fault_and_admission(dut):
    h = Harness(dut, random_stalls=False)
    for code in (0, 3, 7, 9, 10):
        await h.reset()
        h.fixed.update(allow_rd_cmd=0, allow_wr_cmd=0)
        h.offer('load')
        h.offer('store', row_bytes=4)
        s = await h.step()
        assert s['load_req_ready'] and s['store_req_ready']
        h.withdraw('load')
        h.withdraw('store')
        await h.idle(4)
        h.fixed.update(allow_rd_cmd=1, allow_wr_cmd=1)
        h.put(inject_mem_fatal=1, inject_mem_code=code)
        await h.step()
        expected = code if code in (7, 9, 10) else 8
        assert h.value('fatal_code') == expected
        h.put(inject_mem_code=10 if expected != 10 else 9)
        await h.complete('load', expected)
        await h.complete('store', expected)
        assert not any(h.logs[key] for key in ('ar', 'aw', 'w', 'load', 'read', 'rd_cmd', 'wr_cmd'))
        h.put(inject_mem_fatal=0, inject_mem_code=0, load_req_valid=1, store_req_valid=1)
        await h.idle(3)
        assert h.value('fatal_code') == expected and not h.value('load_req_ready') and not h.value('store_req_ready')
        h.put(load_req_valid=0, store_req_valid=0)
        await h.drained()
    # Local reader metadata fault must stop a sibling command immediately,
    # including READY rising before the sibling receives its registered stop.
    await h.reset()
    await h.prepare(1, 1, 1)
    h.fixed['allow_wr_cmd'] = 0
    await h.request('store', base=0x6000, stride=8, row_bytes=4)
    h.ram.write(0x2000, bytes(range(32)))
    await h.request('load', base=0x2000, stride=32, row_bytes=32)
    h.put(inject_rd_tag=1)
    await h.until(lambda s: s['rd_data_valid'] and s['rd_data_ready'])
    h.put(inject_rd_tag=0)
    assert h.value('fatal') and h.value('fatal_code') == 8
    h.fixed['allow_wr_cmd'] = 1
    await h.complete('load', 8)
    await h.complete('store', 8)
    assert not h.logs['wr_cmd'] and not h.logs['aw']
    await h.drained()
    # Mirror the boundary: a local writer fault blocks an unaccepted read
    # command even when its test admission gate opens before shared-stop capture.
    await h.reset()
    await h.prepare(1, 3, 9)
    commands_before, addresses_before = len(h.logs['rd_cmd']), len(h.logs['ar'])
    h.fixed.update(allow_rd_cmd=0, allow_response=0)
    h.ram.write(0x2000, bytes(range(32)))
    await h.request('load', buf=1, base=0x2000, stride=32, row_bytes=32)
    await h.request('store', base=0x6000, stride=16, row_bytes=12)
    await h.until(lambda s: s['tile_response_valid'])
    h.put(inject_response_error=1)
    h.fixed['allow_response'] = 1
    await h.until(lambda s: s['tile_response_valid'] and s['tile_response_ready'])
    assert h.value('fatal') and h.value('fatal_code') == 8
    h.put(inject_response_error=0)
    h.fixed['allow_rd_cmd'] = 1
    await h.complete('load', 8)
    await h.complete('store', 8)
    assert len(h.logs['rd_cmd']) == commands_before and len(h.logs['ar']) == addresses_before, 'Fault admitted the pending load command'
    await h.drained()
    save_coverage('external_first_fault_normalization_local_sibling_command_gate')


@cocotb.test()
async def reader_fault_retains_issued_write(dut):
    h = Harness(dut, random_stalls=False)
    for stage in ('aw', 'w', 'read', 'response'):
        await h.reset()
        await h.prepare(2, 3, 9)
        if stage == 'aw':
            h.ram.write_if.aw_channel.pause = True
        elif stage == 'w':
            h.ram.write_if.w_channel.pause = True
        else:
            h.fixed['allow_'+stage] = 0
        await h.request('store', base=0x40ff8, stride=24, rows=2, row_bytes=12)
        valid, ready, fields = h.CHANNELS[stage]
        await h.until(lambda s: s[valid] and not s[ready])
        held = tuple(h.value(field) for field in fields)
        h.ram.write(0x2000, bytes(range(256))*4)
        h.put(inject_rresp=2)
        await h.request('load', buf=1, base=0x2000, stride=256, rows=4, row_bytes=256)
        await h.complete('load', 7)
        await h.idle(20)
        assert h.value(valid) and tuple(h.value(field) for field in fields) == held
        assert not h.value('store_done_valid')
        if stage in ('aw', 'w'):
            assert not h.value('axi_quiescent')
            (h.ram.write_if.aw_channel if stage == 'aw' else h.ram.write_if.w_channel).pause = False
        else:
            h.fixed['allow_'+stage] = 1
        await h.complete('store', 7)
        expected_writes = int(stage in ('aw', 'w'))
        assert len(h.logs['aw']) == len(h.logs['b']) == expected_writes
        assert h.value('fatal_code') == 7
        await h.drained()
        save_coverage('read_error_retains_'+stage)


@cocotb.test()
async def writer_fault_retains_accepted_load(dut):
    h = Harness(dut, random_stalls=False)
    await h.reset()
    await h.prepare(1, 3, 9)
    h.fixed['allow_load'] = 0
    h.ram.write(0x2000, bytes(range(256))*4)
    await h.request('load', buf=1, base=0x2000, stride=256, rows=4, row_bytes=256)
    await h.until(lambda s: s['load_valid'] and not s['load_ready'] and len(h.local_reads) == h.depth)
    held_load = tuple(h.value(field) for field in h.CHANNELS['load'][2])
    h.fixed['allow_response'] = 0
    await h.request('store', base=0x40ff8, stride=24, row_bytes=12)
    await h.until(lambda s: s['tile_response_valid'])
    h.put(inject_response_error=1)
    h.fixed['allow_response'] = 1
    await h.until(lambda s: s['tile_response_valid'] and s['tile_response_ready'])
    h.put(inject_response_error=0)
    await h.idle(4)
    assert h.value('fatal_code') == 8
    h.put(inject_mem_fatal=1, inject_mem_code=9)
    await h.idle(20)
    assert h.value('load_valid') and tuple(h.value(field) for field in h.CHANNELS['load'][2]) == held_load
    assert h.value('fatal_code') == 8, 'Later external fault replaced the first captured store fault'
    h.fixed['allow_load'] = 1
    await h.complete('load', 8)
    await h.complete('store', 8)
    await h.drained()
    save_coverage('c_response_error_preserves_held_operand_and_first_code')
