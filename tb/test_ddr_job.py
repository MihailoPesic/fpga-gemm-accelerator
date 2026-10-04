"""Full matrix oracle and transaction scoreboards for the serial DDR controller."""
import json
import logging
import os
from pathlib import Path
import random

import cocotb
from cocotbext.axi import AxiBus, AxiRam
from common import tick


P, T = int(os.environ['GEMM_P']), int(os.environ['GEMM_T'])
COUNTERS = ('job_cycles', 'compute_cycles', 'read_beats', 'write_beats',
            'write_valid_bytes', 'input_wait_cycles', 'read_stall_cycles', 'write_stall_cycles')
COVERAGE = {'p': P, 't': T, 'seed': 20261111+P+T, 'jobs': 0, 'seeded_jobs': 0,
            'output_values': 0, 'macrotiles': 0, 'invalid_descriptors': 0,
            'shapes': [], 'faults': {}, 'command_statuses': {}, 'stalls': {},
            'axi_read_beats': 0, 'axi_write_beats': 0, 'axi_write_responses': 0}


def save_coverage():
    Path(os.environ['GEMM_COVERAGE']).write_text(json.dumps(COVERAGE, indent=2)+'\n')


def pauses(seed):
    rng = random.Random(seed)
    while True:
        yield rng.randrange(4) == 0


def round64(value):
    return (value+63)//64*64


def bursts(base, stride, rows, row_bytes):
    result = []
    for row in range(rows):
        group = []
        for address in range(base+row*stride, base+row*stride+row_bytes, 8):
            if group and (len(group) == 16 or address//4096 != group[0]//4096):
                result.append((group[0], len(group)))
                group = []
            group.append(address)
        if group:
            result.append((group[0], len(group)))
    return result


class Harness:
    GATES = ('allow_load', 'allow_read', 'allow_response', 'allow_rd_cmd', 'allow_wr_cmd', 'allow_wr_data')
    INJECT = ('inject_compute_error', 'suppress_progress', 'inject_response_error',
              'inject_response_strb', 'inject_rd_tag', 'inject_rd_index', 'inject_rd_last',
              'inject_wr_done', 'inject_spurious_b', 'inject_mem_fatal', 'inject_mem_code',
              'inject_rresp', 'inject_bresp')
    CHANNELS = {
        'ar': ('m_axi_arvalid', 'm_axi_arready', tuple('m_axi_ar'+x for x in
                ('addr', 'len', 'id', 'size', 'burst', 'lock', 'cache', 'prot', 'qos', 'region'))),
        'aw': ('m_axi_awvalid', 'm_axi_awready', tuple('m_axi_aw'+x for x in
                ('addr', 'len', 'id', 'size', 'burst', 'lock', 'cache', 'prot', 'qos', 'region'))),
        'w': ('m_axi_wvalid', 'm_axi_wready', ('m_axi_wdata', 'm_axi_wstrb', 'm_axi_wlast')),
        'start_rsp': ('start_rsp_valid', 'start_rsp_ready', ('start_rsp_status',)),
        'dma': ('dma_req_valid', 'dma_req_ready', ('dma_req_write', 'dma_req_bt', 'dma_req_buf',
                'dma_req_base', 'dma_req_stride', 'dma_req_rows', 'dma_req_row_bytes')),
    }

    def __init__(self, dut, random_stalls=True):
        self.dut = dut
        self.rng = random.Random(COVERAGE['seed'])
        self.random_stalls = random_stalls
        self.fixed_gates = {}
        self.ram = AxiRam(AxiBus.from_prefix(dut, 'm_axi'), dut.clk, dut.rst, size=2**27)
        self.ram.read_if.log.setLevel(logging.WARNING)
        self.ram.write_if.log.setLevel(logging.WARNING)
        self.channels = (self.ram.read_if.ar_channel, self.ram.read_if.r_channel,
                         self.ram.write_if.aw_channel, self.ram.write_if.w_channel,
                         self.ram.write_if.b_channel)
        if random_stalls:
            for i, channel in enumerate(self.channels):
                channel.set_pause_generator(pauses(6300+i))
        names = {'start_valid', 'start_ready', 'job_accepted', 'ready', 'busy', 'done',
                 'error', 'error_code', 'reset_required', 'axi_quiescent',
                 'dma_done_valid', 'dma_done_ready', 'dma_done_status', 'dma_busy',
                 'compute_start', 'compute_ready', 'compute_busy', 'compute_done',
                 'compute_cycles_in',
                 'compute_m', 'compute_n', 'compute_k', 'compute_input_buf', 'compute_output_buf',
                 'm_axi_rvalid', 'm_axi_rready', 'm_axi_rlast', 'm_axi_bvalid', 'm_axi_bready',
                 'm_axi_bresp', 'clear_status_code'}
        for valid, ready, fields in self.CHANNELS.values():
            names.update((valid, ready, *fields))
        self.handles = {name: getattr(dut, name) for name in names}
        self.local_handles = {name: getattr(dut.datapath, name) for name in
                              ('load_valid', 'load_ready', 'load_bt', 'load_buf', 'load_q', 'load_word', 'load_data',
                               'read_valid', 'read_ready', 'read_buf', 'read_row', 'read_pair')}
        self.cycle = 0
        self.clear_logs()

    def put(self, **signals):
        for name, value in signals.items():
            getattr(self.dut, name).value = value

    def value(self, name):
        return int(getattr(self.dut, name).value)

    def counters(self):
        return tuple(self.value(name) for name in COUNTERS)

    def clear_logs(self):
        self.logs = {name: [] for name in (*self.CHANNELS, 'r', 'b', 'load', 'read', 'core',
                                         'compute_finish', 'accepted', 'fault', 'dma_done')}
        self.held = {}
        self.r_stalls = self.w_stalls = 0
        self.input_wait_count = 0
        self.waiting_for_inputs = False
        self.dma_kind = None
        self.was_error = False

    async def reset(self, calibrate=True):
        self.put(clk=0, rst=1, ddr_ready=0, host_busy=0, start_valid=0, start_rsp_ready=0, clear_status=0,
                 allow_dma_done=1)
        self.configure(self.descriptor(1, 1, 1, 1))
        for name in self.INJECT:
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
        await self.idle(2)
        assert not self.value('ready') and not self.value('error')
        if calibrate:
            self.put(ddr_ready=1)
            await self.idle(2)
            assert self.value('ready')

    @staticmethod
    def descriptor(m, n, k, job_id):
        return dict(job_id=job_id, m=m, n=n, k=k, a_base=0xfc0, bt_base=0x200fc0,
                    c_base=0x400fc0, a_stride=round64(k)+64, bt_stride=round64(k)+128,
                    c_stride=round64(4*n)+64, mode=0, watchdog=100000)

    def configure(self, descriptor):
        self.put(**{'cfg_'+name: value for name, value in descriptor.items()})

    async def step(self, **signals):
        for name in self.GATES:
            self.put(**{name: self.fixed_gates.get(name, int(not self.random_stalls or self.rng.randrange(4) != 0))})
        self.put(**signals)

        def snapshot():
            s = {}
            for name, handle in self.handles.items():
                if name in ('m_axi_wdata', 'm_axi_wstrb') and not int(self.handles['m_axi_wvalid'].value):
                    s[name] = 0
                elif name == 'm_axi_bresp' and not int(self.handles['m_axi_bvalid'].value):
                    s[name] = 0
                elif name == 'm_axi_rlast' and not int(self.handles['m_axi_rvalid'].value):
                    s[name] = 0
                else:
                    try:
                        s[name] = int(handle.value)
                    except ValueError:
                        raise AssertionError(f'Undefined active signal {name}') from None
            for name, handle in self.local_handles.items():
                s[name] = 0 if name == 'load_data' and not int(self.local_handles['load_valid'].value) else int(handle.value)
            return s

        s = await tick(self.dut, snapshot)
        self.cycle += 1
        if s['dma_req_valid'] and not s['dma_req_write'] and not s['dma_req_bt']:
            self.waiting_for_inputs = True
        if self.waiting_for_inputs:
            self.input_wait_count += 1
        if s['dma_req_valid'] and s['dma_req_ready']:
            self.dma_kind = (s['dma_req_write'], s['dma_req_bt'])
        if s['dma_done_valid'] and s['dma_done_ready'] and self.dma_kind == (0, 1):
            self.waiting_for_inputs = False
        for name, (valid, ready, fields) in self.CHANNELS.items():
            payload = tuple(s[field] for field in fields)
            if name in self.held:
                # Controller DMA requests may be canceled only by a fatal before
                # the adapter accepts them. AXI and response payloads stay held.
                canceled = name == 'dma' and s['reset_required']
                assert canceled or (s[valid] and payload == self.held[name]), f'{name} changed while stalled'
            if s[valid] and not s[ready]:
                self.held[name] = payload
                COVERAGE['stalls'][name] = COVERAGE['stalls'].get(name, 0)+1
            else:
                self.held.pop(name, None)
            if s[valid] and s[ready]:
                self.logs[name].append((self.cycle, payload))
                if name in ('ar', 'aw'):
                    address, length, ident, size, burst, *sidebands = payload
                    assert address % 8 == 0 and 0 <= length <= 15
                    assert address//4096 == (address+8*(length+1)-1)//4096
                    assert ident == 0 and size == 3 and burst == 1 and not any(sidebands)
                if name == 'w':
                    COVERAGE['axi_write_beats'] += 1
        if s['m_axi_rvalid'] and s['m_axi_rready']:
            self.logs['r'].append(self.cycle)
            COVERAGE['axi_read_beats'] += 1
        if s['m_axi_bvalid'] and s['m_axi_bready']:
            self.logs['b'].append((self.cycle, s['m_axi_bresp']))
            COVERAGE['axi_write_responses'] += 1
        self.r_stalls += int(s['m_axi_rvalid'] and not s['m_axi_rready'])
        self.w_stalls += int(s['m_axi_wvalid'] and not s['m_axi_wready'])
        if s['load_valid'] and s['load_ready']:
            self.logs['load'].append(tuple(s[name] for name in ('load_bt', 'load_buf', 'load_q', 'load_word', 'load_data')))
        if s['read_valid'] and s['read_ready']:
            self.logs['read'].append(tuple(s[name] for name in ('read_buf', 'read_row', 'read_pair')))
        if s['dma_done_valid'] and s['dma_done_ready']:
            self.logs['dma_done'].append((self.cycle, s['dma_done_status']))
        if s['compute_start'] and s['compute_ready']:
            self.logs['core'].append(tuple(s[name] for name in
                                    ('compute_m', 'compute_n', 'compute_k', 'compute_input_buf', 'compute_output_buf')))
        if s['compute_busy'] and self.value('compute_done'):
            self.logs['compute_finish'].append(self.value('compute_cycles_in'))
        # Registered pulses are observed after their acceptance edge, avoiding a
        # one-cycle skew against AXI handshakes sampled before that same edge.
        if self.value('job_accepted'):
            self.logs['accepted'].append(self.cycle)
        post_error = bool(self.value('reset_required'))
        if post_error and not self.was_error:
            self.logs['fault'].append((self.cycle, self.counters(), self.value('error_code')))
        self.was_error = post_error
        if self.value('done'):
            assert not self.value('busy') and self.value('axi_quiescent'), 'DONE before all work drained'
        return s

    async def until(self, condition, limit=300000):
        for _ in range(limit):
            s = await self.step()
            if condition(s):
                return s
        raise AssertionError(f'Timeout at cycle {self.cycle}: {condition}')

    async def idle(self, count):
        for _ in range(count):
            await self.step()

    async def command(self, descriptor=None, expected=0, hold=0, mutate=False):
        if descriptor is not None:
            self.configure(descriptor)
        before = len(self.logs['accepted'])
        self.put(start_valid=1, start_rsp_ready=0)
        await self.until(lambda s: s['start_ready'])
        self.put(start_valid=0)
        if mutate:
            self.configure(dict(job_id=0xdeadbeef, m=0, n=0, k=0, a_base=1, bt_base=1,
                                c_base=1, a_stride=1, bt_stride=1, c_stride=1, mode=1, watchdog=1))
        await self.until(lambda s: s['start_rsp_valid'])
        assert self.value('start_rsp_status') == expected, (self.value('start_rsp_status'), expected)
        assert len(self.logs['accepted'])-before == int(expected == 0)
        await self.idle(hold)
        self.put(start_rsp_ready=1)
        await self.step()
        self.put(start_rsp_ready=0)
        COVERAGE['command_statuses'][str(expected)] = COVERAGE['command_statuses'].get(str(expected), 0)+1

    async def clear(self, expected):
        self.put(clear_status=1)
        sample = await self.step()
        self.put(clear_status=0)
        assert sample['clear_status_code'] == expected, (sample['clear_status_code'], expected)

    def prepare(self, descriptor, extrema=False):
        m, n, k = (descriptor[name] for name in ('m', 'n', 'k'))
        a = [[-128 if extrema else self.rng.randrange(-128, 128) for _ in range(k)] for _ in range(m)]
        b = [[-128 if extrema else self.rng.randrange(-128, 128) for _ in range(n)] for _ in range(k)]
        bt = [list(column) for column in zip(*b)]
        golden = [[sum(int(a[i][q])*int(b[q][j]) for q in range(k)) for j in range(n)] for i in range(m)]
        # Merge touching guard regions before initialization. This supports
        # adjacent legal allocations and the last DDR cacheline without a test
        # guard clobbering another matrix or crossing the modeled DDR window.
        intervals = sorted((max(0, descriptor[name+'_base']-64),
                            min(2**27, descriptor[name+'_base']+count*descriptor[name+'_stride']+64))
                           for name, count in (('a', m), ('bt', n), ('c', m)))
        merged = []
        for start, end in intervals:
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        raw_regions = [(base, bytearray(self.rng.randrange(1, 256) for _ in range(end-base)))
                       for base, end in merged]

        def store(regions, address, data):
            for base, raw in regions:
                if base <= address and address+len(data) <= base+len(raw):
                    raw[address-base:address-base+len(data)] = data
                    return
            raise AssertionError(f'Oracle write outside prepared ranges: {address:#x}')

        for name, values in (('a', a), ('bt', bt)):
            for row, data in enumerate(values):
                store(raw_regions, descriptor[name+'_base']+row*descriptor[name+'_stride'],
                      bytes(value & 255 for value in data))
        for base, raw in raw_regions:
            self.ram.write(base, raw)
        expected_regions = [(base, bytearray(raw)) for base, raw in raw_regions]
        for row, values in enumerate(golden):
            store(expected_regions, descriptor['c_base']+row*descriptor['c_stride'],
                  b''.join(value.to_bytes(4, 'little', signed=True) for value in values))
        regions = dict(enumerate(expected_regions))
        return regions

    async def finish(self, limit=300000):
        await self.until(lambda s: s['done'] or s['reset_required'], limit)
        assert self.value('done') and not self.value('error') and not self.value('reset_required')
        assert not self.value('busy') and self.value('ready') and self.value('axi_quiescent')

    def check_job(self, descriptor, regions):
        m, n, k = (descriptor[name] for name in ('m', 'n', 'k'))
        for base, expected in regions.values():
            assert self.ram.read(base, len(expected)) == expected, (m, n, k, hex(base))
        assert self.value('last_job_id') == descriptor['job_id']
        assert len(self.logs['accepted']) == 1
        assert self.value('job_cycles') == self.logs['b'][-1][0]-self.logs['accepted'][0], (
            self.value('job_cycles'), self.logs['b'][-1][0], self.logs['accepted'][0])
        assert self.value('read_beats') == ((k+7)//8)*(m*((n+T-1)//T)+n*((m+T-1)//T))
        assert self.value('write_beats') == m*((n+1)//2)
        assert self.value('write_valid_bytes') == 4*m*n
        expected_compute = ((m+P-1)//P)*((n+P-1)//P)*(k+3*P-1)
        assert self.value('compute_cycles') == expected_compute == sum(self.logs['compute_finish'])
        assert self.value('read_beats') == len(self.logs['r'])
        assert self.value('write_beats') == len(self.logs['w'])
        assert self.value('write_valid_bytes') == sum(payload[1].bit_count() for _, payload in self.logs['w'])
        assert self.value('read_stall_cycles') == self.r_stalls
        assert self.value('write_stall_cycles') == self.w_stalls
        assert self.value('input_wait_cycles') == self.input_wait_count > 0
        expected_dma, expected_ar, expected_aw, expected_load, expected_read, shapes = [], [], [], [], [], []
        actual_dma = [payload for _, payload in self.logs['dma']]
        tile_index = 0
        for i in range(0, m, T):
            for j in range(0, n, T):
                rows, cols = min(T, m-i), min(T, n-j)
                assert len(actual_dma) >= 3*tile_index+3
                a_buf, bt_buf, c_buf = (actual_dma[3*tile_index+x][2] for x in range(3))
                assert a_buf == bt_buf
                shapes.append((rows, cols, k, a_buf, c_buf))
                for write, plane, buf, base, stride, count, row_bytes in (
                    (0, 0, a_buf, descriptor['a_base']+i*descriptor['a_stride'], descriptor['a_stride'], rows, k),
                    (0, 1, bt_buf, descriptor['bt_base']+j*descriptor['bt_stride'], descriptor['bt_stride'], cols, k),
                    (1, 0, c_buf, descriptor['c_base']+i*descriptor['c_stride']+4*j, descriptor['c_stride'], rows, 4*cols),
                ):
                    expected_dma.append((write, plane, buf, base, stride, count, row_bytes))
                    (expected_aw if write else expected_ar).extend(bursts(base, stride, count, row_bytes))
                    if not write:
                        for row in range(count):
                            for word in range((row_bytes+7)//8):
                                value = int.from_bytes(self.ram.read(base+row*stride+8*word, 8), 'little')
                                expected_load.append((plane, buf, row, word, value))
                    else:
                        expected_read.extend((buf, row, pair) for row in range(count) for pair in range((cols+1)//2))
                tile_index += 1
        assert actual_dma == expected_dma
        assert self.logs['core'] == shapes
        assert self.logs['load'] == expected_load
        assert self.logs['read'] == expected_read
        assert [(p[0], p[1]+1) for _, p in self.logs['ar']] == expected_ar
        assert [(p[0], p[1]+1) for _, p in self.logs['aw']] == expected_aw
        assert len(self.logs['b']) == len(expected_aw)
        assert all(not resp for _, resp in self.logs['b'])
        # Serialize every accepted W beat using its independently checked AW
        # address. A byte may be written once only; padding is never authorized.
        byte_addresses, cursor = [], 0
        for address, count in expected_aw:
            for index in range(count):
                _, (data, mask, last) = self.logs['w'][cursor]
                assert last == (index == count-1)
                for lane in range(8):
                    if mask & (1 << lane):
                        byte_addresses.append(address+index*8+lane)
                cursor += 1
        useful = [descriptor['c_base']+i*descriptor['c_stride']+j for i in range(m) for j in range(4*n)]
        assert len(byte_addresses) == len(set(byte_addresses)) and sorted(byte_addresses) == useful
        COVERAGE['jobs'] += 1
        COVERAGE['output_values'] += m*n
        COVERAGE['macrotiles'] += tile_index
        COVERAGE['shapes'].append([m, n, k])

    async def descriptor_job(self, descriptor, extrema=False):
        self.dut._log.info('GEMM job_id=%d M=%d N=%d K=%d seed=%d', descriptor['job_id'],
                           descriptor['m'], descriptor['n'], descriptor['k'], COVERAGE['seed'])
        regions = self.prepare(descriptor, extrema)
        self.clear_logs()
        await self.command(descriptor, hold=7, mutate=True)
        await self.finish()
        self.check_job(descriptor, regions)
        frozen = self.counters()
        await self.idle(9)
        assert self.counters() == frozen and self.value('done')
        return descriptor, regions

    async def job(self, m, n, k, job_id, extrema=False):
        return await self.descriptor_job(self.descriptor(m, n, k, job_id), extrema)


@cocotb.test()
async def full_matrix_jobs_and_counters(dut):
    h = Harness(dut)
    await h.reset()
    cases = [(1, 1, 1), (T+1, T+3, 9), (2*T+1, T-1, 7),
             (P+1, 2*T+1, 8), (T-1, T+1, 255), (T+1, P+1, 256),
             (T+1, T+1, 256), (1024, 1, 256), (1, 1024, 256)]
    if T == 32:
        cases.append((65, 97, 17))
    for index, shape in enumerate(cases):
        await h.job(*shape, index+1, extrema=index == 6)
    for index in range(25):
        m, n = h.rng.randrange(1, 2*T+4), h.rng.randrange(1, 2*T+4)
        k = h.rng.choice((1, 2, 7, 8, 9, 16, 31, 32, 33, 64, 255, 256))
        await h.job(m, n, k, 100+index)
        COVERAGE['seeded_jobs'] += 1
    save_coverage()


@cocotb.test()
async def descriptor_rejection_and_command_lifetime(dut):
    h = Harness(dut, random_stalls=False)
    await h.reset(calibrate=False)
    await h.command(h.descriptor(1, 1, 1, 0), expected=5)
    assert not h.value('reset_required') and not h.logs['accepted'] and not h.logs['ar']
    h.put(ddr_ready=1)
    await h.idle(2)
    adjacent = h.descriptor(2, 3, 9, 0x0101)
    adjacent.update(a_base=0, bt_base=2*adjacent['a_stride'],
                    c_base=2*adjacent['a_stride']+3*adjacent['bt_stride'])
    await h.descriptor_job(adjacent)
    boundary = h.descriptor(1, 1, 1, 0x0102)
    boundary.update(c_base=0x7ffffc0, c_stride=64)
    await h.descriptor_job(boundary)
    await h.job(2, 3, 7, 0x10203040)
    baseline, identity = h.counters(), h.value('last_job_id')
    valid = h.descriptor(2, 3, 7, 0xaabbccdd)
    invalid = [dict(m=0), dict(n=0), dict(k=0), dict(m=1025), dict(n=1025), dict(k=257),
               dict(m=0x80000000), dict(k=0xffffffff), dict(a_base=1), dict(bt_base=8),
               dict(c_base=32), dict(a_stride=8), dict(bt_stride=65), dict(c_stride=12),
               dict(a_stride=0), dict(k=65, a_stride=64), dict(n=17, c_stride=64),
               dict(a_base=0x8000000), dict(bt_base=0x7ffffc0), dict(c_base=0xffffffc0),
               dict(n=1, bt_base=0x7ffffc0, bt_stride=128),
               dict(a_stride=0xffffffc0), dict(bt_stride=0xffffffc0), dict(c_stride=0xffffffc0),
               dict(bt_base=valid['a_base']), dict(c_base=valid['a_base']), dict(c_base=valid['bt_base']),
               dict(bt_base=valid['a_base']+64), dict(mode=1), dict(mode=0x80000000), dict(watchdog=0)]
    for changes in invalid:
        desc = dict(valid, **changes)
        before = {name: len(h.logs[name]) for name in ('ar', 'aw', 'w', 'dma', 'core', 'load', 'read', 'accepted')}
        await h.command(desc, expected=3, hold=4)
        assert {name: len(h.logs[name]) for name in before} == before, changes
        assert h.counters() == baseline and h.value('last_job_id') == identity and h.value('done')
        assert not h.value('busy') and not h.value('reset_required')
        COVERAGE['invalid_descriptors'] += 1
    await h.clear(0)
    assert not h.value('done') and not h.value('error')
    assert h.counters() == baseline and h.value('last_job_id') == identity

    h.put(host_busy=1)
    await h.command(valid, expected=5)
    h.put(host_busy=0)
    # Clear on the same edge as a START must be rejected, not erase its state.
    h.configure(valid)
    h.prepare(valid)
    h.clear_logs()
    h.put(start_valid=1, start_rsp_ready=0, clear_status=1)
    sample = await h.until(lambda s: s['start_ready'])
    assert sample['clear_status_code'] == 4
    h.put(start_valid=0, clear_status=0)
    await h.clear(4)  # Validation is active, though no job has been accepted yet.
    await h.until(lambda s: s['start_rsp_valid'])
    assert h.value('start_rsp_status') == 0
    # An unconsumed response must stay stable even while the job makes progress.
    h.put(start_valid=1)
    await h.idle(15)
    assert not h.value('start_ready') and h.value('start_rsp_status') == 0
    h.put(start_valid=0, start_rsp_ready=1)
    await h.step()
    h.put(start_rsp_ready=0)
    await h.command(valid, expected=4, hold=3)
    await h.clear(4)
    await h.finish()
    assert len(h.logs['accepted']) == 1
    save_coverage()


@cocotb.test()
async def final_b_timestamp_and_calibration_faults(dut):
    h = Harness(dut, random_stalls=False)
    await h.reset()
    desc = h.descriptor(1, 3, 8, 0x21)
    regions = h.prepare(desc)
    h.clear_logs()
    h.ram.write_if.b_channel.pause = True
    await h.command(desc)
    await h.until(lambda s: s['m_axi_wvalid'] and s['m_axi_wready'] and s['m_axi_wlast'])
    await h.idle(30)
    assert h.value('busy') and not h.value('done') and not h.logs['b']
    h.put(allow_dma_done=0)
    h.ram.write_if.b_channel.pause = False
    await h.until(lambda s: s['m_axi_bvalid'] and s['m_axi_bready'])
    b_tick = h.logs['b'][-1][0]
    await h.idle(25)
    assert not h.value('done') and h.value('busy')
    h.put(allow_dma_done=1)
    await h.finish()
    h.check_job(desc, regions)
    assert h.value('job_cycles') == b_tick-h.logs['accepted'][0]
    frozen = h.counters()
    h.put(ddr_ready=0)
    await h.until(lambda s: s['reset_required'])
    assert h.value('error_code') == 10 and not h.value('done')
    assert h.counters() == frozen and h.value('last_job_id') == desc['job_id']
    await h.clear(5)
    await h.command(desc, expected=5)
    h.put(ddr_ready=1)
    await h.idle(10)
    assert h.value('reset_required') and not h.value('ready')
    COVERAGE['faults']['idle_calibration_loss'] = 1

    # A loss during descriptor validation never turns into an accepted job.
    await h.reset()
    h.configure(desc)
    h.put(start_valid=1)
    await h.until(lambda s: s['start_ready'])
    h.put(start_valid=0, ddr_ready=0)
    await h.until(lambda s: s['start_rsp_valid'])
    assert h.value('start_rsp_status') == 5 and h.value('error_code') == 10
    assert not h.logs['accepted'] and not h.logs['ar'] and not h.logs['aw']
    COVERAGE['faults']['validation_calibration_loss'] = 1

    # During compute, the deterministic PE schedule drains, while subsequent
    # result stores are suppressed by the registered fatal feedback.
    await h.reset()
    desc = h.descriptor(T+1, T+1, 256, 0x22)
    h.prepare(desc)
    await h.command(desc)
    await h.until(lambda s: s['compute_busy'])
    await h.idle(25)
    h.put(ddr_ready=0)
    fault_edge = await h.step()
    assert fault_edge['compute_cycles_in'] > 0
    assert h.value('reset_required') and h.value('compute_cycles') == fault_edge['compute_cycles_in']
    await h.until(lambda s: s['reset_required'])
    freeze = h.counters()
    assert h.value('busy') and h.value('error_code') == 10
    await h.until(lambda s: not s['busy'])
    assert h.counters() == freeze and not h.value('done') and not h.logs['aw']
    COVERAGE['faults']['active_calibration_loss'] = 1
    save_coverage()


@cocotb.test()
async def fatal_responses_watchdog_and_draining(dut):
    h = Harness(dut, random_stalls=False)
    for fault in ('read_response', 'write_response', 'core_protocol', 'c_response'):
        await h.reset()
        desc = h.descriptor(T+1, T+1, 9, 0x31)
        h.prepare(desc)
        if fault == 'read_response':
            h.put(inject_rresp=2)
        elif fault == 'write_response':
            h.put(inject_bresp=2)
        elif fault == 'c_response':
            h.put(inject_response_error=1)
        await h.command(desc)
        if fault == 'core_protocol':
            await h.until(lambda s: s['compute_busy'])
            h.put(inject_compute_error=1)
        await h.until(lambda s: s['reset_required'])
        code = 7 if fault in ('read_response', 'write_response') else 8
        assert h.value('error_code') == code and not h.value('done')
        frozen = h.counters()
        first_error_cycle = h.logs['fault'][0][0]
        assert h.value('job_cycles') == first_error_cycle-h.logs['accepted'][0]
        h.put(ddr_ready=0)  # A later calibration fault cannot replace the first.
        await h.until(lambda s: not s['busy'])
        await h.idle(10)
        assert h.counters() == frozen and h.value('error_code') == code
        assert h.value('axi_quiescent') and not h.value('ready')
        await h.clear(5)
        await h.command(desc, expected=5)
        COVERAGE['faults'][fault] = 1

    for stalled in ('ar', 'b'):
        await h.reset()
        desc = h.descriptor(1, 3, 7, 0x41)
        desc['watchdog'] = 80
        h.prepare(desc)
        channel = h.ram.read_if.ar_channel if stalled == 'ar' else h.ram.write_if.b_channel
        channel.pause = True
        await h.command(desc)
        await h.until(lambda s: s['reset_required'])
        assert h.value('error_code') == 9 and h.value('busy') and not h.value('done')
        frozen = h.counters()
        await h.idle(25)
        assert h.counters() == frozen and h.value('busy')
        if stalled == 'ar':
            assert h.value('m_axi_arvalid'), 'Fatal withdrew an issued AR obligation'
            assert not h.logs['load']
        else:
            assert not h.logs['b']
        channel.pause = False
        await h.until(lambda s: not s['busy'])
        assert h.counters() == frozen and h.value('axi_quiescent') and not h.value('done')
        COVERAGE['faults'][f'watchdog_hung_{stalled}'] = 1
    save_coverage()
