"""Cycle attribution for the unchanged serial DDR core and behavioral AXI RAM.

Events are sampled four nanoseconds into each low phase, before the existing
Harness advances the rising edge at five nanoseconds. Handshakes use that
pre-edge sample; registered job_accepted is observed after the same edge.
An interval (a,b] contains b-a core cycles. Only accepted < edge <= final B
belongs to JOB_CYCLES. Burst terminal propagation after final B is retained
as a diagnostic, but is excluded from the additive job accounting.
"""
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import random
import struct
import sys

import cocotb
from cocotb.triggers import ReadOnly, Timer

from test_ddr_core import Harness, P, T


MODEL = os.environ.get('GEMM_PROFILE_MODEL', 'unstalled')
CASES = (
    ('dense', 32, 32, 256, 20261008),
    ('odd', 5, 3, 9, 20261004),
    ('multi_tile', 33, 35, 256, 20261006),
    ('asymmetric', 65, 63, 255, 20261007),
)
COUNTER_NAMES = ('job_cycles', 'compute_cycles', 'read_beats', 'write_beats',
                 'write_valid_bytes', 'input_wait_cycles', 'read_stall_cycles',
                 'write_stall_cycles')
JOB_PHASE = {1: 'a_load', 2: 'a_load', 3: 'bt_load', 4: 'bt_load',
             5: 'compute', 6: 'compute', 7: 'c_store', 8: 'c_store', 9: 'retire'}
DMA_STATES = ('IDLE', 'CMD_READ', 'RECEIVE', 'CMD_WRITE', 'NEXT_PAIR',
              'OFFER_READ', 'WAIT_RESPONSE', 'SEND_WORD', 'WAIT_WRITE', 'FINISH')


def rounded(value, alignment):
    return (value+alignment-1)//alignment*alignment


def padding(length, seed):
    return bytes(1+(seed+37*index) % 255 for index in range(length))


def workload(name, m, n, k, seed, job_id):
    """Independent integer oracle, with the public guarded host layout/pattern."""
    rng = random.Random(seed)
    a = [[rng.randrange(-128, 128) for _ in range(k)] for _ in range(m)]
    b = [[rng.randrange(-128, 128) for _ in range(n)] for _ in range(k)]
    golden = [[sum(int(a[i][q])*int(b[q][j]) for q in range(k))
               for j in range(n)] for i in range(m)]
    sa, sb, sc = rounded(k, 64), rounded(k, 64), rounded(4*n, 64)
    ba, bb = 64, 64+m*sa+128
    bc = bb+n*sb+128
    desc = dict(job_id=job_id, m=m, n=n, k=k, a_base=ba, bt_base=bb,
                c_base=bc, a_stride=sa, bt_stride=sb, c_stride=sc,
                mode=0, watchdog=10000000)
    a_image = b''.join(bytes(v & 255 for v in row)+padding(sa-k, seed+i*29)
                       for i, row in enumerate(a))
    bt_image = b''.join(bytes(b[q][j] & 255 for q in range(k))+
                        padding(sb-k, seed+97+j*29) for j in range(n))
    c_image = padding(m*sc, seed+2*71+37)
    regions, expected = {}, {}
    for index, (label, base, image) in enumerate((('a', ba, a_image),
                                                 ('bt', bb, bt_image),
                                                 ('c', bc, c_image))):
        raw = padding(64, seed+index*71)+image+padding(64, seed+index*71+19)
        regions[label] = (base-64, raw)
        expected[label] = raw
    out = bytearray(expected['c'])
    for i in range(m):
        for j in range(n):
            out[64+i*sc+4*j:64+i*sc+4*j+4] = struct.pack('<i', golden[i][j])
    expected['c'] = bytes(out)
    return dict(name=name, seed=seed, descriptor=desc, regions=regions,
                expected=expected, compared_outputs=m*n,
                checked_memory_bytes=sum(len(raw) for _, raw in regions.values()))


def enumerated_plan(work):
    """Group independently enumerated byte addresses, never DUT state/indices."""
    d = work['descriptor']
    operations, bursts, tiles = [], [], []
    for i0 in range(0, d['m'], T):
        for j0 in range(0, d['n'], T):
            rows, cols = min(T, d['m']-i0), min(T, d['n']-j0)
            tile = len(tiles)
            tiles.append(dict(i0=i0, j0=j0, m=rows, n=cols, k=d['k']))
            for label, base, stride, count, useful in (
                ('a', d['a_base']+i0*d['a_stride'], d['a_stride'], rows, d['k']),
                ('bt', d['bt_base']+j0*d['bt_stride'], d['bt_stride'], cols, d['k']),
                ('c', d['c_base']+i0*d['c_stride']+4*j0, d['c_stride'], rows, 4*cols),
            ):
                op = dict(index=len(operations), tile=tile, kind=label, base=base,
                          stride=stride, rows=count, row_bytes=useful)
                operations.append(op)
                for row in range(count):
                    groups = []
                    addresses = list(range(base+row*stride,
                                           base+row*stride+rounded(useful, 8), 8))
                    for address in addresses:
                        if (not groups or len(groups[-1]) == 16 or
                                address//4096 != groups[-1][0]//4096):
                            groups.append([])
                        groups[-1].append(address)
                    for group in groups:
                        masks = [sum(1 << lane for lane in range(8)
                                     if address+lane < base+row*stride+useful)
                                 if label == 'c' else 255 for address in group]
                        bursts.append(dict(index=len(bursts), operation=op['index'],
                                           tile=tile, kind=label, row=row,
                                           word=(group[0]-(base+row*stride))//8,
                                           address=group[0], beats=len(group), masks=masks))
    return operations, bursts, tiles


class ProfileHarness(Harness):
    def __init__(self, dut):
        super().__init__(dut)
        # The parent harness normally injects random independent AXI stalls.
        # Profiling starts from its completely unpaused behavioral RAM instead.
        for channel in self.channels.values():
            channel.set_pause_generator(None)
            channel.pause = False
        self.force_pause = {name: False for name in self.channels}
        self.current = None
        self.delay_reads = self.delay_writes = 0
        original_read, original_write = self.ram.read_if._read, self.ram.write_if._write

        async def modeled_read(address, length):
            # The RAM calls _read once per beat. With one outstanding INCR
            # burst, ARADDR remains the current burst base until completion.
            if address == self.value('m_axi_araddr'):
                self.delay_reads += 1
                if MODEL == 'latency':
                    await Timer(80, unit='ns')
                    # Timer can resume before the coincident clock write. The
                    # baseline RAM callback runs after AR's rising edge; queue
                    # the delayed response in that same post-edge phase so the
                    # idle AXI source cannot catch an earlier edge instead.
                    await ReadOnly()
            return await original_read(address, length)

        async def modeled_write(address, data):
            # Each legal C beat has one contiguous ff/0f write operation. Delay
            # only the last word, after all W collection, before queuing B.
            final = self.value('m_axi_awaddr')+8*self.value('m_axi_awlen')
            if address == final:
                self.delay_writes += 1
                if MODEL == 'latency':
                    await Timer(60, unit='ns')
                    await ReadOnly()
            await original_write(address, data)

        self.ram.read_if._read, self.ram.write_if._write = modeled_read, modeled_write

    def begin(self, work):
        operations, bursts, tiles = enumerated_plan(work)
        self.current = dict(work=work, expected_operations=operations,
                            expected_bursts=bursts, expected_tiles=tiles,
                            accepted=None, final_b=None, done_edge=None,
                            phases=Counter({phase: 0 for phase in JOB_PHASE.values()}),
                            stalls=Counter(), gather=Counter(),
                            active_compute=0, microtiles=0, compute_spans=[],
                            operations=[], bursts=[], op=None, burst=None,
                            pending_c=None, command_offer=None, raw=Counter(),
                            delay_start=(self.delay_reads, self.delay_writes))

    def sample(self):
        d = self.dut
        v = self.value
        sample = {name: v(name) for name in (
            'host_busy', 'compute_start', 'compute_ready', 'compute_busy',
            'compute_done', 'dma_req_valid', 'dma_req_ready',
            'dma_done_valid', 'dma_done_ready', 'd_rd_cmd_valid', 'd_rd_cmd_ready',
            'd_wr_cmd_valid', 'd_wr_cmd_ready', 'd_rd_data_valid', 'd_rd_data_ready',
            'd_rd_done_valid', 'd_rd_done_ready', 'load_valid', 'load_ready',
            'read_valid', 'read_ready', 'response_valid', 'response_ready',
            'd_wr_data_valid', 'd_wr_data_ready', 'd_wr_done_valid', 'd_wr_done_ready',
            'm_axi_rvalid', 'm_axi_rready', 'm_axi_bvalid', 'm_axi_bready',
            'm_axi_wvalid', 'm_axi_wready')}
        payloads = {
            'compute_start': ('compute_m', 'compute_n', 'compute_k'),
            'dma_req_valid': ('dma_req_write', 'dma_req_bt', 'dma_req_buf', 'dma_req_base',
                              'dma_req_stride', 'dma_req_rows', 'dma_req_row_bytes'),
            'dma_done_valid': ('dma_done_status',),
            'd_rd_cmd_valid': ('d_rd_cmd_addr', 'd_rd_cmd_beats', 'd_rd_cmd_tag'),
            'd_wr_cmd_valid': ('d_wr_cmd_addr', 'd_wr_cmd_beats', 'd_wr_cmd_tag'),
            'd_rd_data_valid': ('b_rd_data', 'b_rd_data_index', 'b_rd_data_tag', 'b_rd_data_last'),
            'd_rd_done_valid': ('b_rd_done_status', 'b_rd_done_tag'),
            'load_valid': ('load_bt', 'load_buf', 'load_q', 'load_word', 'load_data'),
            'read_valid': ('read_buf', 'read_row', 'read_pair'),
            'response_valid': ('response_data', 'response_strb', 'response_error'),
            'd_wr_data_valid': ('d_wr_data', 'd_wr_data_strb'),
            'd_wr_done_valid': ('b_wr_done_status', 'b_wr_done_tag'),
            'm_axi_rvalid': ('m_axi_rdata', 'm_axi_rresp', 'm_axi_rid', 'm_axi_rlast'),
            'm_axi_bvalid': ('m_axi_bresp', 'm_axi_bid'),
        }
        for valid, fields in payloads.items():
            for name in fields:
                # Inactive channel payloads may legally be unknown, including
                # RAM contents that are deliberately not reset in the RTL.
                sample[name] = v(name) if sample[valid] else 0
        for name, (valid, ready, fields) in self.CHANNELS.items():
            sample[name] = (v(valid), v(ready), tuple(v(field) for field in fields) if v(valid) else ())
        sample['job_state'] = int(d.serial_path.job.state.value)
        sample['dma_state'] = int(d.serial_path.dma.state.value)
        sample['tile_state'] = int(d.tile.state.value)
        sample['prefetch_left'] = int(d.tile.prefetch_left.value)
        sample['micro_launch'] = int(d.tile.tile_start.value) and int(d.tile.micro_ready.value)
        return sample

    async def step(self, byte=None, error=False):
        sampled = []

        async def capture():
            await Timer(4, unit='ns')
            sampled.append(self.sample())

        before = {name: len(self.logs[name]) for name in ('ar', 'aw', 'w', 'r', 'b')}
        capture_task = cocotb.start_soon(capture())
        result = await super().step(byte, error)
        await capture_task
        assert len(sampled) == 1
        s = sampled[0]
        # Establish that the additional monitor observes the same handshake
        # edges/payloads as the regression harness's settled 5 ns sample.
        for name in ('ar', 'aw', 'w', 'r', 'b'):
            if name in self.CHANNELS:
                valid, ready, fields = s[name]
            else:
                valid, ready = s['m_axi_'+name+'valid'], s['m_axi_'+name+'ready']
                fields = s['m_axi_'+name+'resp']
            expected = [(self.cycle, fields)] if valid and ready else []
            assert self.logs[name][before[name]:] == expected, ('sampling disagreement', name, self.cycle)
        self.consume(s)
        return result

    @staticmethod
    def fire(s, prefix):
        return s[prefix+'_valid'] and s[prefix+'_ready']

    def expected_bytes(self, label, address, length):
        c = self.current
        base, original = c['work']['regions'][label]
        data = c['work']['expected'][label] if label == 'c' else original
        offset = address-base
        assert 0 <= offset <= len(data)-length
        return data[offset:offset+length]

    def check_word(self, label, address, data, mask=255):
        expected = self.expected_bytes(label, address, 8)
        raw = data.to_bytes(8, 'little')
        for lane in range(8):
            if mask & (1 << lane):
                assert raw[lane] == expected[lane], (label, hex(address+lane), raw[lane], expected[lane])

    def consume(self, s):
        c = self.current
        if self.value('job_accepted'):
            assert c is not None and c['accepted'] is None
            c['accepted'] = self.cycle
        if c is None or c['accepted'] is None or self.cycle <= c['accepted']:
            return
        edge = self.cycle-c['accepted']
        assert not s['host_busy'], 'Host acquired memory during a profiled job'
        in_job = c['final_b'] is None
        if in_job:
            assert s['job_state'] in JOB_PHASE, ('unexpected job phase', s['job_state'], edge)
            c['phases'][JOB_PHASE[s['job_state']]] += 1
            c['active_compute'] += int(s['tile_state'] == 2 and s['prefetch_left'] == 0)
            c['microtiles'] += int(s['micro_launch'])
            for prefix in ('dma_req', 'd_rd_cmd', 'd_wr_cmd', 'd_rd_data', 'load',
                           'read', 'response', 'd_wr_data', 'd_rd_done', 'd_wr_done'):
                c['stalls'][prefix] += int(s[prefix+'_valid'] and not s[prefix+'_ready'])
            c['raw']['read_stall_cycles'] += int(s['m_axi_rvalid'] and not s['m_axi_rready'])
            c['raw']['write_stall_cycles'] += int(s['m_axi_wvalid'] and not s['m_axi_wready'])
            old_burst = c['burst']
            if old_burst is not None and old_burst['kind'] == 'c' and not old_burst.get('gather_complete'):
                c['gather'][DMA_STATES[s['dma_state']]] += 1

        if self.fire(s, 'dma_req'):
            assert c['op'] is None and c['burst'] is None
            expected = c['expected_operations'][len(c['operations'])]
            fields = (s['dma_req_write'], s['dma_req_bt'], s['dma_req_buf'],
                      s['dma_req_base'], s['dma_req_stride'], s['dma_req_rows'], s['dma_req_row_bytes'])
            assert fields == (int(expected['kind'] == 'c'), int(expected['kind'] == 'bt'), 0,
                              expected['base'], expected['stride'], expected['rows'], expected['row_bytes'])
            c['op'] = dict(expected, request=edge)
            c['operations'].append(c['op'])

        if s['compute_start'] and s['compute_ready']:
            assert c['op'] is None and c['burst'] is None
            tile = c['expected_tiles'][len(c['compute_spans'])]
            assert (s['compute_m'], s['compute_n'], s['compute_k']) == (tile['m'], tile['n'], tile['k'])
            c['compute_spans'].append(dict(tile, start=edge))
        if c['compute_spans'] and 'end' not in c['compute_spans'][-1]:
            if self.value('compute_done') and not self.value('compute_busy'):
                c['compute_spans'][-1]['end'] = edge

        offered = s['d_rd_cmd_valid'] or s['d_wr_cmd_valid']
        if offered and c['command_offer'] is None:
            assert c['burst'] is None, 'More than one burst offered at once'
            c['command_offer'] = edge
        rd_command, wr_command = self.fire(s, 'd_rd_cmd'), self.fire(s, 'd_wr_cmd')
        assert not (rd_command and wr_command)
        if rd_command or wr_command:
            assert c['op'] is not None and c['burst'] is None
            expected = c['expected_bursts'][len(c['bursts'])]
            prefix = 'd_rd_cmd' if rd_command else 'd_wr_cmd'
            assert (s[prefix+'_addr'], s[prefix+'_beats'], s[prefix+'_tag']) == (
                expected['address'], expected['beats'], 0)
            assert bool(wr_command) == (expected['kind'] == 'c')
            assert expected['operation'] == c['op']['index']
            c['burst'] = dict(expected, command_offer=c['command_offer'], command=edge,
                              r=[], local_reads=[], loads=[], result_requests=[],
                              result_responses=[], local_writes=[], w=[])
            c['command_offer'] = None
            c['bursts'].append(c['burst'])

        burst = c['burst']
        for channel in ('ar', 'aw'):
            valid, ready, fields = s[channel]
            if valid and ready:
                assert in_job and burst is not None and channel not in burst
                assert (channel == 'aw') == (burst['kind'] == 'c')
                assert fields[:5] == (burst['address'], burst['beats']-1, 0, 3, 1)
                assert not any(fields[5:])
                burst[channel] = edge
                if channel == 'aw':
                    assert len(burst['local_writes']) == burst['beats']

        if s['m_axi_rvalid'] and s['m_axi_rready']:
            assert in_job and burst is not None and burst['kind'] != 'c'
            index = len(burst['r'])
            assert burst['ar'] < edge and index < burst['beats']
            assert (s['m_axi_rresp'], s['m_axi_rid'], s['m_axi_rlast']) == (0, 0, int(index+1 == burst['beats']))
            self.check_word(burst['kind'], burst['address']+8*index, s['m_axi_rdata'])
            burst['r'].append(edge)
            c['raw']['read_beats'] += 1

        if self.fire(s, 'd_rd_data'):
            assert burst is not None and burst['kind'] != 'c'
            index = len(burst['local_reads'])
            assert len(burst['r']) == burst['beats'] and burst['r'][-1] < edge
            assert (s['b_rd_data_index'], s['b_rd_data_tag'], s['b_rd_data_last']) == (
                index, 0, int(index+1 == burst['beats']))
            self.check_word(burst['kind'], burst['address']+8*index, s['b_rd_data'])
            burst['local_reads'].append(edge)

        if self.fire(s, 'load'):
            assert burst is not None and burst['kind'] != 'c'
            index = len(burst['loads'])
            assert index < len(burst['local_reads']) and burst['local_reads'][index] < edge
            assert (s['load_bt'], s['load_buf'], s['load_q'], s['load_word']) == (
                int(burst['kind'] == 'bt'), 0, burst['row'], burst['word']+index)
            self.check_word(burst['kind'], burst['address']+8*index, s['load_data'])
            burst['loads'].append(edge)

        if self.fire(s, 'read'):
            assert burst is not None and burst['kind'] == 'c' and c['pending_c'] is None
            index = len(burst['result_requests'])
            assert index < burst['beats']
            assert (s['read_buf'], s['read_row'], s['read_pair']) == (0, burst['row'], burst['word']+index)
            burst['result_requests'].append(edge)
            c['pending_c'] = index
        if self.fire(s, 'response'):
            assert burst is not None and c['pending_c'] is not None
            index = c['pending_c']
            assert burst['result_requests'][index] < edge
            assert not s['response_error'] and s['response_strb'] == burst['masks'][index]
            self.check_word('c', burst['address']+8*index, s['response_data'], s['response_strb'])
            burst['result_responses'].append(edge)
            c['pending_c'] = None
        if self.fire(s, 'd_wr_data'):
            assert burst is not None and burst['kind'] == 'c'
            index = len(burst['local_writes'])
            assert index < len(burst['result_responses']) and burst['result_responses'][index] < edge
            assert s['d_wr_data_strb'] == burst['masks'][index]
            self.check_word('c', burst['address']+8*index, s['d_wr_data'], s['d_wr_data_strb'])
            burst['local_writes'].append(edge)
            if index+1 == burst['beats']:
                burst['gather_complete'] = edge

        valid, ready, fields = s['w']
        if valid and ready:
            assert in_job and burst is not None and burst['kind'] == 'c'
            index = len(burst['w'])
            assert burst['gather_complete'] < edge and index < burst['beats']
            data, strobe, last = fields
            assert strobe == burst['masks'][index] and last == int(index+1 == burst['beats'])
            self.check_word('c', burst['address']+8*index, data, strobe)
            burst['w'].append(edge)
            c['raw']['write_beats'] += 1
            c['raw']['write_valid_bytes'] += strobe.bit_count()
        if s['m_axi_bvalid'] and s['m_axi_bready']:
            assert in_job and burst is not None and burst['kind'] == 'c'
            assert len(burst['w']) == burst['beats'] and max(burst['aw'], burst['w'][-1]) < edge
            assert (s['m_axi_bresp'], s['m_axi_bid']) == (0, 0)
            burst['b'] = edge
            if burst['index']+1 == len(c['expected_bursts']):
                c['final_b'] = edge

        rd_end, wr_end = self.fire(s, 'd_rd_done'), self.fire(s, 'd_wr_done')
        assert not (rd_end and wr_end)
        if rd_end or wr_end:
            assert burst is not None
            if rd_end:
                assert burst['kind'] != 'c'
                assert (s['b_rd_done_status'], s['b_rd_done_tag']) == (0, 0)
                assert len(burst['loads']) == burst['beats'] and burst['loads'][-1] < edge
            else:
                assert burst['kind'] == 'c' and c['pending_c'] is None
                assert (s['b_wr_done_status'], s['b_wr_done_tag']) == (0, 0)
                assert burst['b'] < edge
            burst['local_done'] = edge
            c['burst'] = None

        if self.fire(s, 'dma_done'):
            assert c['op'] is not None and c['burst'] is None and s['dma_done_status'] == 0
            c['op']['done'] = edge
            c['op'] = None
        if self.value('done') and c['done_edge'] is None:
            assert c['final_b'] is not None and not self.value('busy')
            assert not self.value('error') and not self.value('reset_required')
            assert c['op'] is None and c['burst'] is None
            c['done_edge'] = edge

    def finish(self, counters):
        c = self.current
        d = c['work']['descriptor']
        assert c['done_edge'] is not None and c['final_b'] == counters['job_cycles']
        assert sum(c['phases'].values()) == counters['job_cycles']
        assert c['phases']['a_load']+c['phases']['bt_load'] == counters['input_wait_cycles']
        microtiles = ((d['m']+P-1)//P)*((d['n']+P-1)//P)
        assert c['microtiles'] == microtiles
        assert c['active_compute'] == counters['compute_cycles'] == microtiles*(d['k']+3*P-1)
        assert len(c['operations']) == len(c['expected_operations'])
        assert len(c['bursts']) == len(c['expected_bursts'])
        assert len(c['compute_spans']) == len(c['expected_tiles'])
        assert all('end' in tile for tile in c['compute_spans'])
        for name in ('read_beats', 'write_beats', 'write_valid_bytes',
                     'read_stall_cycles', 'write_stall_cycles'):
            assert counters[name] == c['raw'][name], (name, counters[name], c['raw'][name])
        expected_reads = ((d['k']+7)//8)*(d['m']*((d['n']+T-1)//T)+d['n']*((d['m']+T-1)//T))
        assert counters['read_beats'] == expected_reads
        assert counters['write_beats'] == d['m']*((d['n']+1)//2)
        assert counters['write_valid_bytes'] == 4*d['m']*d['n']
        delays = (self.delay_reads-c['delay_start'][0], self.delay_writes-c['delay_start'][1])
        read_bursts = sum(b['kind'] != 'c' for b in c['bursts'])
        write_bursts = len(c['bursts'])-read_bursts
        assert delays == (read_bursts, write_bursts), ('one delay site per burst', delays)

        aggregate = {'read': Counter(), 'write': Counter()}
        for burst in c['bursts']:
            if burst['kind'] != 'c':
                intervals = dict(command_to_ar=burst['ar']-burst['command'],
                                 ar_to_first_r=burst['r'][0]-burst['ar'],
                                 remaining_r_span=burst['r'][-1]-burst['r'][0],
                                 last_r_to_last_bank_load=burst['loads'][-1]-burst['r'][-1],
                                 last_bank_load_to_local_done=burst['local_done']-burst['loads'][-1])
                assert intervals['ar_to_first_r'] == 2+(8 if MODEL == 'latency' else 0), (
                    'behavioral read response latency', burst['index'], intervals)
                direction = 'read'
            else:
                issued = max(burst['aw'], burst['w'][-1])
                intervals = dict(c_gather=burst['gather_complete']-burst['command'],
                                 aw_w_issue=issued-burst['gather_complete'],
                                 b_response=burst['b']-issued,
                                 b_to_local_done=burst['local_done']-burst['b'])
                assert intervals['b_response'] == 2+(6 if MODEL == 'latency' else 0), (
                    'behavioral write response latency', burst['index'], intervals)
                direction = 'write'
            assert all(value >= 0 for value in intervals.values())
            assert sum(intervals.values()) == burst['local_done']-burst['command']
            burst['interval_cycles'] = intervals
            aggregate[direction].update(intervals)

        # These are additive *inside* each DMA phase. The remaining phase
        # cycles include row validation, command offers, and terminal/control
        # propagation. For the final store exclude its post-B local terminal.
        attribution = {}
        for kind, phase in (('a', 'a_load'), ('bt', 'bt_load'), ('c', 'c_store')):
            spans = [b for b in c['bursts'] if b['kind'] == kind]
            within = sum(min(b['local_done'], c['final_b'])-b['command'] for b in spans)
            assert within <= c['phases'][phase]
            attribution[kind] = dict(burst_intervals_within_job=within,
                                     controller_planner_gaps=c['phases'][phase]-within)
        return dict(name=c['work']['name'], seed=c['work']['seed'], descriptor=d,
                    shape={key: d[key] for key in ('m', 'n', 'k')},
                    counters=counters, phases=dict(c['phases']),
                    active_compute_cycles=c['active_compute'],
                    compute_control_prefetch_cycles=c['phases']['compute']-c['active_compute'],
                    done_publication_cycles=c['done_edge']-c['final_b'],
                    tiles=len(c['expected_tiles']), microtiles=microtiles,
                    read_bursts=read_bursts, write_bursts=write_bursts,
                    latency_injection_sites=dict(read=delays[0], write=delays[1]),
                    expected_latency_delta_cycles=8*read_bursts+6*write_bursts,
                    compared_outputs=c['work']['compared_outputs'],
                    checked_memory_bytes=c['work']['checked_memory_bytes'],
                    operation_attribution=attribution,
                    burst_interval_totals={key: dict(value) for key, value in aggregate.items()},
                    contained_stall_cycles=dict(c['stalls']), c_gather_state_cycles=dict(c['gather']),
                    operations=c['operations'], compute_spans=c['compute_spans'],
                    reads=[burst for burst in c['bursts'] if burst['kind'] != 'c'],
                    writes=[burst for burst in c['bursts'] if burst['kind'] == 'c'])


@cocotb.test()
async def cycle_profile(dut):
    assert P == 4 and T in (8, 32) and MODEL in ('unstalled', 'latency')
    output = Path(os.environ['GEMM_PROFILE'])
    output.unlink(missing_ok=True)
    h = ProfileHarness(dut)
    await h.reset()
    h.force_pause.update({name: False for name in h.channels})
    report = dict(schema_version=1, result='RUNNING', p=P, t=T, model=MODEL,
                  core_hz=100000000, clock_period_ns=10, python=sys.version, jobs=[],
                  scope='Portable simulation of qualified production RTL against behavioral AXI RAM; not MIG or physical DDR latency',
                  memory_model=dict(random_channel_pauses=False,
                                    additional_first_read_delay_cycles=8 if MODEL == 'latency' else 0,
                                    additional_final_write_delay_cycles=6 if MODEL == 'latency' else 0,
                                    read_callback_wait_ns=80 if MODEL == 'latency' else 0,
                                    write_callback_wait_ns=60 if MODEL == 'latency' else 0,
                                    baseline_ar_to_first_r_cycles=2,
                                    baseline_last_aw_w_to_b_cycles=2,
                                    mechanism='cocotbext-axi _read first word / _write final contiguous word callbacks; Timer followed by ReadOnly preserves post-edge response queuing; 10 ns core clock'),
                  timing_semantics=dict(job='accepted START edge to final successful AXI B edge; host initialization/configuration excluded',
                                        phases='disjoint pre-edge controller phases over accepted < edge <= final B',
                                        burst_intervals='disjoint per burst; local terminal after final B excluded from job attribution',
                                        stalls='contained diagnostics; do not add to elapsed intervals',
                                        single_beat_read='remaining_r_span is zero; first response edge ends ar_to_first_r',
                                        sampling='4 ns low-phase sample checked against original Harness AXI logs; registered acceptance observed after that edge'),
                  initialization='Direct behavioral RAM initialization with the public 64-byte guarded layout; mathematical A/B/BT packing and oracle are independent of RTL')
    for job_id, case in enumerate(CASES, start=1):
        work = workload(*case, job_id)
        for address, raw in work['regions'].values():
            h.ram.write(address, raw)
        h.begin(work)
        await h.configure(work['descriptor'])
        await h.reg_write(0x10, 1)
        await h.until(lambda: h.value('done') or h.value('error'), limit=1000000)
        assert h.value('done') and not h.value('error') and h.value('last_job_id') == job_id
        # Check complete allocations, nonzero padding and both 64-byte guards.
        # Direct inspection adds no host traffic or simulation cycles.
        for name, (address, raw) in work['regions'].items():
            assert h.ram.read(address, len(raw)) == work['expected'][name], (case[0], name)
        counters = dict(zip(COUNTER_NAMES, await h.counters(), strict=True))
        job = h.finish(counters)
        job['input_images_sha256'] = {
            name: hashlib.sha256(raw).hexdigest() for name, (_, raw) in work['regions'].items()}
        report['jobs'].append(job)
        output.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
        dut._log.info('PROFILE_JOB_PASS model=%s T=%d case=%s cycles=%d read_bursts=%d write_bursts=%d',
                      MODEL, T, case[0], counters['job_cycles'], job['read_bursts'], job['write_bursts'])
    report['result'] = 'PASS'
    report['compared_outputs'] = sum(job['compared_outputs'] for job in report['jobs'])
    report['checked_memory_bytes'] = sum(job['checked_memory_bytes'] for job in report['jobs'])
    output.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
