"""AXI transfer checks use byte-addressed memory and handshake scoreboards."""
import json
import logging
import os
from pathlib import Path
import random

import cocotb
from cocotbext.axi import AxiBus, AxiRam
from common import tick


COVERAGE = {'ram_reads': 0, 'ram_writes': 0, 'invalid_commands': 0,
            'faults': {}, 'stalls': {}, 'read_lengths': [], 'write_lengths': [],
            'w_before_aw': 0, 'aw_before_w': 0, 'reset_cases': 0}


def save_coverage():
    path = os.environ.get('GEMM_COVERAGE')
    if path:
        Path(path).write_text(json.dumps(COVERAGE, indent=2)+'\n')


def pauses(seed):
    rng = random.Random(seed)
    while True:
        yield rng.randrange(4) == 0


class Harness:
    LOCAL_INPUTS = ('rd_cancel', 'rd_cmd_valid', 'rd_cmd_addr', 'rd_cmd_beats', 'rd_cmd_tag',
                    'rd_data_ready', 'rd_done_ready', 'wr_cmd_valid', 'wr_cmd_addr',
                    'wr_cmd_beats', 'wr_cmd_tag', 'wr_data_valid', 'wr_data',
                    'wr_data_strb', 'wr_done_ready')
    AXI_INPUTS = ('m_axi_arready', 'm_axi_rvalid', 'm_axi_rdata', 'm_axi_rresp',
                  'm_axi_rlast', 'm_axi_rid', 'm_axi_awready', 'm_axi_wready',
                  'm_axi_bvalid', 'm_axi_bresp', 'm_axi_bid')
    ADDRESS_FIELDS = ('id', 'addr', 'len', 'size', 'burst', 'lock', 'cache', 'prot', 'qos', 'region')

    def __init__(self, dut, ram=False):
        self.dut, self.ram = dut, ram
        self.held = {}
        self.reads, self.read_done, self.write_done = [], [], []
        self.ar, self.aw, self.w, self.r, self.b = [], [], [], [], []
        self.aw_pending, self.w_pending = None, []
        self.read_slots = int(os.environ.get('GEMM_READ_SLOTS', '1'))
        assert self.read_slots in (1, 4), 'GEMM_READ_SLOTS must be 1 or 4'
        self.read_outstanding = 0
        self.allow_malformed_response = False
        self.cycle = 0

    def put(self, **signals):
        for name, value in signals.items():
            getattr(self.dut, name).value = value

    def value(self, name):
        return int(getattr(self.dut, name).value)

    async def reset(self):
        self.put(clk=0, rst=1)
        for name in self.LOCAL_INPUTS + (() if self.ram else self.AXI_INPUTS):
            getattr(self.dut, name).value = 0
        for _ in range(4):
            await tick(self.dut)
        self.put(rst=0, rd_done_ready=1, wr_done_ready=1, rd_data_ready=1)
        self.held.clear()
        self.aw_pending, self.w_pending = None, []
        self.read_outstanding = 0
        self.allow_malformed_response = False
        for _ in range(3):
            await self.step()
        assert not self.value('fatal') and self.value('axi_quiescent')
        COVERAGE['reset_cases'] += 1

    async def step(self, **signals):
        self.put(**signals)
        names = list(self.LOCAL_INPUTS) + list(self.AXI_INPUTS)
        names += ['rd_cmd_ready', 'wr_cmd_ready', 'wr_data_ready', 'rd_data_valid',
                  'rd_data', 'rd_data_index', 'rd_data_last', 'rd_data_tag',
                  'rd_done_valid', 'rd_done_status', 'rd_done_tag',
                  'wr_done_valid', 'wr_done_status', 'wr_done_tag',
                  'fatal', 'fatal_code', 'axi_quiescent', 'progress']
        for channel in ('ar', 'aw'):
            names += [f'm_axi_{channel}{field}' for field in self.ADDRESS_FIELDS + ('valid',)]
        names += ['m_axi_wvalid', 'm_axi_wdata', 'm_axi_wstrb', 'm_axi_wlast',
                  'm_axi_rready', 'm_axi_bready']
        def snapshot():
            values = {}
            qualifiers = {'rd_data': 'rd_data_valid', 'm_axi_wdata': 'm_axi_wvalid',
                          'm_axi_wstrb': 'm_axi_wvalid',
                          **{f'm_axi_{field}': 'm_axi_rvalid' for field in ('rdata', 'rresp', 'rid', 'rlast')},
                          **{f'm_axi_{field}': 'm_axi_bvalid' for field in ('bresp', 'bid')}}
            for name in names:
                invalid_data = name in qualifiers and not self.value(qualifiers[name])
                try:
                    values[name] = 0 if invalid_data else self.value(name)
                except ValueError as exc:
                    raise AssertionError(f'Undefined active signal: {name}') from exc
            return values
        sample = await tick(self.dut, snapshot)
        self.cycle += 1
        # Only ARs accepted on earlier edges can own this edge's response.
        # Keep the count snapshot before applying simultaneous AR/R events.
        read_response_due = self.read_outstanding > 0
        write_response_due = (self.aw_pending is not None and
                              len(self.w_pending) == self.aw_pending['len']+1)
        held_channels = {
            'ar': ('m_axi_arvalid', 'm_axi_arready', tuple(f'm_axi_ar{x}' for x in self.ADDRESS_FIELDS)),
            'aw': ('m_axi_awvalid', 'm_axi_awready', tuple(f'm_axi_aw{x}' for x in self.ADDRESS_FIELDS)),
            'w': ('m_axi_wvalid', 'm_axi_wready', ('m_axi_wdata', 'm_axi_wstrb', 'm_axi_wlast')),
            'read': ('rd_data_valid', 'rd_data_ready', ('rd_data', 'rd_data_index', 'rd_data_last', 'rd_data_tag')),
            'rd_done': ('rd_done_valid', 'rd_done_ready', ('rd_done_status', 'rd_done_tag')),
            'wr_done': ('wr_done_valid', 'wr_done_ready', ('wr_done_status', 'wr_done_tag')),
        }
        for name, (valid, ready, fields) in held_channels.items():
            payload = tuple(sample[field] for field in fields)
            if name in self.held:
                assert sample[valid] and payload == self.held[name], f'{name} changed under backpressure'
            if sample[valid] and not sample[ready]:
                self.held[name] = payload
                COVERAGE['stalls'][name] = COVERAGE['stalls'].get(name, 0)+1
            else:
                self.held.pop(name, None)
        for channel, log in (('ar', self.ar), ('aw', self.aw)):
            if sample[f'm_axi_{channel}valid'] and sample[f'm_axi_{channel}ready']:
                address = {field: sample[f'm_axi_{channel}{field}'] for field in self.ADDRESS_FIELDS}
                assert address['id'] == 0 and address['size'] == 3 and address['burst'] == 1
                assert not any(address[field] for field in ('lock', 'cache', 'prot', 'qos', 'region'))
                assert address['addr'] % 8 == 0 and 0 <= address['len'] <= 15
                assert address['addr'] // 4096 == (address['addr']+8*(address['len']+1)-1) // 4096
                log.append(address)
                if channel == 'ar':
                    retiring_read = (read_response_due and sample['m_axi_rvalid'] and
                                     sample['m_axi_rready'] and sample['m_axi_rlast'])
                    assert self.read_outstanding-int(retiring_read) < self.read_slots, 'Read capacity exceeded'
                    self.read_outstanding += 1
                else:
                    assert self.aw_pending is None, 'More than one write burst outstanding'
                    self.aw_pending = address
                    if not self.w_pending:
                        COVERAGE['aw_before_w'] += 1
        if sample['m_axi_wvalid'] and sample['m_axi_wready']:
            word = (sample['m_axi_wdata'], sample['m_axi_wstrb'], sample['m_axi_wlast'])
            self.w.append(word)
            if not self.w_pending and self.aw_pending is None:
                COVERAGE['w_before_aw'] += 1
            self.w_pending.append(word)
            assert len(self.w_pending) <= 16
        if sample['m_axi_rvalid'] and sample['m_axi_rready']:
            assert read_response_due or self.allow_malformed_response, 'R arrived before a prior AR handshake'
            self.r.append((sample['m_axi_rdata'], sample['m_axi_rresp'], sample['m_axi_rlast'], sample['m_axi_rid']))
            if sample['m_axi_rlast'] and read_response_due:
                self.read_outstanding -= 1
        if sample['m_axi_bvalid'] and sample['m_axi_bready']:
            assert write_response_due or self.allow_malformed_response, 'B arrived before prior AW and final W handshakes'
            if write_response_due:
                assert [word[2] for word in self.w_pending] == [0]*(len(self.w_pending)-1)+[1]
                self.aw_pending, self.w_pending = None, []
            self.b.append((sample['m_axi_bresp'], sample['m_axi_bid']))
        if sample['rd_data_valid'] and sample['rd_data_ready']:
            self.reads.append((sample['rd_data'], sample['rd_data_index'], sample['rd_data_last'], sample['rd_data_tag']))
        if sample['rd_done_valid'] and sample['rd_done_ready']:
            self.read_done.append((sample['rd_done_status'], sample['rd_done_tag']))
        if sample['wr_done_valid'] and sample['wr_done_ready']:
            self.write_done.append((sample['wr_done_status'], sample['wr_done_tag']))
        if sample['fatal']:
            assert not sample['rd_cmd_ready'] and not sample['wr_cmd_ready']
        return sample

    async def idle(self, count=10, **signals):
        for _ in range(count):
            await self.step(**signals)

    async def wait_value(self, name, value=1, limit=2000):
        for _ in range(limit):
            if self.value(name) == value:
                return
            await self.step()
        raise AssertionError(f'Timeout waiting for {name}={value}')

    async def command(self, kind, address, beats, tag):
        self.put(**{f'{kind}_cmd_valid': 1, f'{kind}_cmd_addr': address,
                    f'{kind}_cmd_beats': beats, f'{kind}_cmd_tag': tag})
        for _ in range(2000):
            sample = await self.step()
            if sample[f'{kind}_cmd_ready']:
                self.put(**{f'{kind}_cmd_valid': 0})
                return
        raise AssertionError(f'{kind} command timeout')

    async def wait_count(self, log, count):
        for _ in range(2000):
            if len(log) >= count:
                return
            await self.step()
        raise AssertionError(f'Transfer timeout: wanted {count}, got {len(log)}')

    async def read_command(self, address, beats, tag):
        before = len(self.ar)
        await self.command('rd', address, beats, tag)
        await self.wait_count(self.ar, before+1)
        assert (self.ar[-1]['addr'], self.ar[-1]['len']) == (address, beats-1)

    async def feed(self, words, strobes):
        for index, (word, strobe) in enumerate(zip(words, strobes, strict=True)):
            await self.idle(index % 3, wr_data_valid=0)
            self.put(wr_data_valid=1, wr_data=word, wr_data_strb=strobe)
            for _ in range(2000):
                sample = await self.step()
                if sample['wr_data_ready']:
                    break
            else:
                raise AssertionError('Write-data collection timeout')
            self.put(wr_data_valid=0)

    async def completion(self, kind, before, status, tag):
        log = self.read_done if kind == 'rd' else self.write_done
        for _ in range(4000):
            if len(log) > before:
                assert len(log) == before+1 and log[-1] == (status, tag)
                return
            await self.step()
        raise AssertionError(f'{kind} completion timeout')

    async def read_beat(self, data, last, resp=0, ident=0):
        self.put(m_axi_rvalid=1, m_axi_rdata=data, m_axi_rlast=last,
                 m_axi_rresp=resp, m_axi_rid=ident)
        for _ in range(2000):
            sample = await self.step()
            if sample['m_axi_rready']:
                self.put(m_axi_rvalid=0)
                return
        raise AssertionError('AXI read acceptance timeout')


@cocotb.test()
async def ram_and_independent_channel_stalls(dut):
    dut.clk.value = 0
    dut.rst.value = 1
    ram = AxiRam(AxiBus.from_prefix(dut, 'm_axi'), dut.clk, dut.rst, size=2**32)
    ram.read_if.log.setLevel(logging.WARNING)
    ram.write_if.log.setLevel(logging.WARNING)
    channels = (ram.read_if.ar_channel, ram.read_if.r_channel,
                ram.write_if.aw_channel, ram.write_if.w_channel, ram.write_if.b_channel)
    for index, channel in enumerate(channels):
        channel.set_pause_generator(pauses(9100+index))
    h = Harness(dut, ram=True)
    await h.reset()
    rng = random.Random(271828)
    for count in range(1, 17):
        # Finish exactly at a 4 KiB edge, including the single final beat.
        address = 0x2000-count*8
        source = bytes(rng.randrange(256) for _ in range(count*8))
        ram.write(address, source)
        before, first = len(h.read_done), len(h.reads)
        await h.command('rd', address, count, count)
        await h.completion('rd', before, 0, count)
        expected = [(int.from_bytes(source[i*8:i*8+8], 'little'), i, i == count-1, count)
                    for i in range(count)]
        assert h.reads[first:] == expected
        assert (h.ar[-1]['addr'], h.ar[-1]['len']) == (address, count-1)
        COVERAGE['ram_reads'] += 1
        COVERAGE['read_lengths'].append(count)

        address = 0x4000-count*8
        guard = bytes([0xa5]) * (count*8+16)
        ram.write(address-8, guard)
        words = [rng.getrandbits(64) for _ in range(count)]
        masks = [0xff if i % 3 else 0x0f for i in range(count)]
        masks[-1] = 0x0f
        before, first_w = len(h.write_done), len(h.w)
        await h.command('wr', address, count, 100+count)
        await h.feed(words[:-1], masks[:-1])
        await h.idle(12)
        assert len(h.w) == first_w and not h.value('m_axi_awvalid') and not h.value('m_axi_wvalid')
        await h.feed(words[-1:], masks[-1:])
        await h.completion('wr', before, 0, 100+count)
        expected = bytearray(guard)
        for i, (word, mask) in enumerate(zip(words, masks, strict=True)):
            for lane, value in enumerate(word.to_bytes(8, 'little')):
                if mask & (1 << lane):
                    expected[8+i*8+lane] = value
        assert ram.read(address-8, len(expected)) == expected
        assert h.w[first_w:] == [(word, mask, i == count-1)
                                 for i, (word, mask) in enumerate(zip(words, masks, strict=True))]
        COVERAGE['ram_writes'] += 1
        COVERAGE['write_lengths'].append(count)

    # All 256 byte strobes, including no-op writes, against untouched guard bytes.
    for mask in range(256):
        address = 0x8000+mask*16
        ram.write(address, bytes([0x96])*16)
        word = 0x0123456789abcdef
        before = len(h.write_done)
        if mask == 255:
            h.put(wr_done_ready=0)
        await h.command('wr', address, 1, 0x200+mask)
        await h.feed([word], [mask])
        if mask == 255:
            await h.wait_value('wr_done_valid')
            await h.idle(30)
            assert len(h.write_done) == before and not h.value('wr_cmd_ready')
            h.put(wr_done_ready=1)
        await h.completion('wr', before, 0, 0x200+mask)
        expected = bytes(value if mask & (1 << lane) else 0x96
                         for lane, value in enumerate(word.to_bytes(8, 'little'))) + bytes([0x96])*8
        assert ram.read(address, 16) == expected
        COVERAGE['ram_writes'] += 1

    # Both directions active; read sink and terminal consumer can stall without
    # blocking the unrelated write or changing any already offered payload.
    read_address, write_address = 0x10000, 0x12000
    source = bytes(range(128))
    ram.write(read_address, source)
    first_data, before_rd, before_wr = len(h.reads), len(h.read_done), len(h.write_done)
    h.put(rd_data_ready=0, rd_done_ready=0)
    await h.command('rd', read_address, 16, 0x700)
    await h.command('wr', write_address, 16, 0x701)
    await h.feed(list(range(16)), [0xff]*15+[0x0f])
    await h.completion('wr', before_wr, 0, 0x701)
    await h.wait_value('rd_data_valid')
    await h.idle(40)
    assert len(h.reads) == first_data and len(h.read_done) == before_rd
    h.put(rd_data_ready=1)
    await h.wait_value('rd_done_valid')
    await h.idle(30)
    assert len(h.read_done) == before_rd
    h.put(rd_done_ready=1)
    await h.completion('rd', before_rd, 0, 0x700)
    assert [word[0] for word in h.reads[first_data:]] == [int.from_bytes(source[i:i+8], 'little') for i in range(0, 128, 8)]
    COVERAGE['ram_reads'] += 1
    COVERAGE['ram_writes'] += 1

    # Address arithmetic uses the final useful byte, not a wrapping 32-bit sum.
    ram.write(0xfffffff8, b'lastbeat')
    before = len(h.read_done)
    await h.command('rd', 0xfffffff8, 1, 0x777)
    await h.completion('rd', before, 0, 0x777)
    assert h.reads[-1][0] == int.from_bytes(b'lastbeat', 'little')
    COVERAGE['ram_reads'] += 1
    assert not h.value('fatal')
    save_coverage()


@cocotb.test()
async def invalid_descriptors_and_response_faults(dut):
    h = Harness(dut)
    await h.reset()
    for kind in ('rd', 'wr'):
        for address, count in ((0, 0), (0, 17), (0, 31), (1, 1), (7, 2),
                               (0xff8, 2), (0xff0, 3), (0xfffffff8, 2)):
            before = len(h.read_done if kind == 'rd' else h.write_done)
            counts = len(h.ar), len(h.aw), len(h.w)
            await h.command(kind, address, count, 55)
            await h.completion(kind, before, 3, 55)
            assert (len(h.ar), len(h.aw), len(h.w)) == counts and not h.value('fatal')
            COVERAGE['invalid_commands'] += 1

    for name, beats in (
        ('rresp_exokay', [(10, 0, 1, 0), (11, 0, 0, 0), (12, 1, 0, 0)]),
        ('rresp_slverr', [(10, 0, 0, 0), (11, 0, 2, 0), (12, 1, 0, 0)]),
        ('rresp_decerr', [(10, 0, 0, 0), (11, 0, 0, 0), (12, 1, 3, 0)]),
        ('rid', [(10, 0, 0, 1), (11, 0, 0, 0), (12, 1, 0, 0)]),
        ('rid_and_rresp', [(10, 0, 2, 1), (11, 0, 0, 0), (12, 1, 0, 0)]),
        ('early_last', [(10, 1, 0, 0)]),
        ('late_last', [(10, 0, 0, 0), (11, 0, 0, 0), (12, 0, 0, 0),
                       (13, 0, 0, 0), (14, 1, 0, 0)]),
    ):
        await h.reset()
        h.put(m_axi_arready=1)
        before, first_data = len(h.read_done), len(h.reads)
        await h.read_command(0x1000, 3, 91)
        for word in beats:
            await h.read_beat(*word)
        status = 7 if name.startswith('rresp') else 8
        await h.completion('rd', before, status, 91)
        assert h.value('fatal') and h.value('fatal_code') == status
        assert len(h.reads) == first_data and h.value('axi_quiescent')
        COVERAGE['faults'][name] = 1

    # Missing RLAST must keep draining, without falsely completing or wrapping
    # the 16-entry buffer, until the response ends or platform reset occurs.
    await h.reset()
    h.put(m_axi_arready=1)
    before, first_data = len(h.read_done), len(h.reads)
    await h.read_command(0x2000, 1, 92)
    for index in range(35):
        await h.read_beat(index, 0)
    await h.idle(20)
    assert h.value('fatal') and h.value('fatal_code') == 8
    assert len(h.read_done) == before and len(h.reads) == first_data
    assert h.value('m_axi_rready') and not h.value('axi_quiescent')
    await h.reset()  # Coordinated model/platform reset, not a transaction abort.
    COVERAGE['faults']['missing_last_reset'] = 1

    for response, ident in ((1, 0), (2, 0), (3, 0), (0, 1)):
        await h.reset()
        # W may transfer before AW; AXI does not couple those handshakes.
        h.put(m_axi_wready=1, m_axi_awready=0)
        before, first_w, first_b, first_aw = len(h.write_done), len(h.w), len(h.b), len(h.aw)
        await h.command('wr', 0x3000, 3, 93)
        await h.feed([10, 20, 30], [0xff, 0x55, 0x0f])
        for _ in range(100):
            if len(h.w) == first_w+3:
                break
            await h.step()
        assert len(h.w) == first_w+3 and h.value('m_axi_awvalid')
        await h.idle(20)
        assert len(h.write_done) == before and len(h.b) == first_b
        h.put(m_axi_awready=1)
        await h.wait_count(h.aw, first_aw+1)
        await h.idle(30)
        assert len(h.write_done) == before
        h.put(m_axi_bvalid=1, m_axi_bresp=response, m_axi_bid=ident)
        while len(h.b) == first_b:
            await h.step()
        h.put(m_axi_bvalid=0)
        status = 8 if ident else 7
        await h.completion('wr', before, status, 93)
        assert h.value('fatal') and h.value('fatal_code') == status
        COVERAGE['faults'][f'bresp_{response}_bid_{ident}'] = 1
    save_coverage()


@cocotb.test()
async def concurrent_faults_preserve_issued_obligations(dut):
    h = Harness(dut)
    await h.reset()
    # A bad read response arrives while both AW and W are stalled. The write
    # must retain VALID/payload and finish its own issued transaction normally.
    h.put(m_axi_arready=1, m_axi_awready=0, m_axi_wready=0)
    before_rd, before_wr, first_b = len(h.read_done), len(h.write_done), len(h.b)
    first_aw, first_w = len(h.aw), len(h.w)
    await h.command('wr', 0x4000, 2, 100)
    await h.feed([0x1234, 0x5678], [0xff, 0x0f])
    await h.wait_value('m_axi_awvalid')
    await h.wait_value('m_axi_wvalid')
    await h.read_command(0x6000, 1, 101)
    await h.read_beat(0, 1, 2)
    await h.completion('rd', before_rd, 7, 101)
    await h.idle(30)
    assert h.value('m_axi_awvalid') and h.value('m_axi_wvalid') and not h.value('axi_quiescent')
    h.put(m_axi_awready=1, m_axi_wready=1)
    await h.wait_count(h.aw, first_aw+1)
    await h.wait_count(h.w, first_w+2)
    h.put(m_axi_bvalid=1, m_axi_bresp=0, m_axi_bid=0)
    while len(h.b) == first_b:
        await h.step()
    h.put(m_axi_bvalid=0)
    await h.completion('wr', before_wr, 0, 100)
    assert h.value('fatal_code') == 7 and h.value('axi_quiescent')
    COVERAGE['faults']['issued_write_survives_read_error'] = 1

    # A write still collecting local data owns no AXI obligations. It rejects
    # the incomplete command when the other direction fails.
    await h.reset()
    h.put(m_axi_arready=1)
    before_rd, before_wr = len(h.read_done), len(h.write_done)
    first_aw, first_w = len(h.aw), len(h.w)
    await h.command('wr', 0x7000, 4, 102)
    await h.feed([1], [0xff])
    await h.read_command(0x8000, 1, 103)
    h.put(wr_data_valid=1, wr_data=2, wr_data_strb=0xff)
    await h.read_beat(0, 1, 2)
    await h.completion('rd', before_rd, 7, 103)
    await h.completion('wr', before_wr, 8, 102)
    assert len(h.aw) == first_aw and len(h.w) == first_w
    h.put(wr_data_valid=0)
    COVERAGE['faults']['unissued_write_canceled'] = 1

    # An accepted same-edge command is rejected, even though READY was high
    # before the response fault latched. It must never acquire AXI obligations.
    await h.reset()
    h.put(m_axi_arready=1)
    before_rd, before_wr = len(h.read_done), len(h.write_done)
    first_aw, first_w = len(h.aw), len(h.w)
    await h.read_command(0x9000, 1, 104)
    sample = await h.step(wr_cmd_valid=1, wr_cmd_addr=0xa000, wr_cmd_beats=1,
                          wr_cmd_tag=105, m_axi_rvalid=1, m_axi_rdata=0,
                          m_axi_rlast=1, m_axi_rresp=2, m_axi_rid=0)
    assert sample['wr_cmd_ready'] and sample['m_axi_rready']
    h.put(wr_cmd_valid=0, m_axi_rvalid=0)
    await h.completion('rd', before_rd, 7, 104)
    await h.completion('wr', before_wr, 8, 105)
    assert len(h.aw) == first_aw and len(h.w) == first_w
    COVERAGE['faults']['same_edge_command_rejected'] = 1

    # A verified read already publishing under backpressure keeps its offered
    # data and own successful completion when an unrelated write fails.
    await h.reset()
    h.put(m_axi_arready=1, m_axi_awready=1, m_axi_wready=1, rd_data_ready=0)
    before_rd, before_wr = len(h.read_done), len(h.write_done)
    first_data, first_aw, first_w, first_b = len(h.reads), len(h.aw), len(h.w), len(h.b)
    await h.read_command(0xb000, 2, 106)
    await h.read_beat(0x11223344, 0)
    await h.read_beat(0xaabbccdd, 1)
    await h.wait_value('rd_data_valid')
    await h.command('wr', 0xc000, 2, 107)
    await h.feed([20, 30], [0xff, 0x0f])
    await h.wait_count(h.aw, first_aw+1)
    await h.wait_count(h.w, first_w+2)
    h.put(m_axi_bvalid=1, m_axi_bresp=2, m_axi_bid=0)
    await h.wait_count(h.b, first_b+1)
    h.put(m_axi_bvalid=0)
    await h.completion('wr', before_wr, 7, 107)
    await h.idle(30)
    assert len(h.reads) == first_data and h.value('rd_data_valid')
    h.put(rd_data_ready=1)
    await h.completion('rd', before_rd, 0, 106)
    assert h.reads[first_data:] == [(0x11223344, 0, 0, 106), (0xaabbccdd, 1, 1, 106)]
    assert h.value('fatal_code') == 7
    COVERAGE['faults']['published_read_survives_write_error'] = 1
    save_coverage()


@cocotb.test()
async def premature_responses_do_not_retire_obligations(dut):
    h = Harness(dut)
    for case in ('before_aw', 'before_w', 'same_aw_edge', 'same_final_w_edge'):
        await h.reset()
        h.allow_malformed_response = True
        aw_first = case in ('before_w', 'same_final_w_edge')
        h.put(m_axi_awready=int(aw_first), m_axi_wready=int(not aw_first))
        before, first_aw, first_w, first_b = len(h.write_done), len(h.aw), len(h.w), len(h.b)
        await h.command('wr', 0xd000, 1, 200)
        await h.feed([0x12345678], [0x0f])
        await h.wait_count(h.aw if aw_first else h.w, (first_aw if aw_first else first_w)+1)
        if case == 'same_aw_edge':
            h.put(m_axi_awready=1)
        elif case == 'same_final_w_edge':
            h.put(m_axi_wready=1)
        await h.step(m_axi_bvalid=1, m_axi_bresp=0, m_axi_bid=0)
        h.put(m_axi_bvalid=0)
        await h.idle(20)
        assert len(h.b) == first_b+1 and len(h.write_done) == before
        assert h.value('fatal_code') == 8 and not h.value('axi_quiescent')
        # The response obligation survives the malformed response. Complete
        # outstanding address/data handshakes and offer a later legal response.
        h.put(m_axi_awready=1, m_axi_wready=1)
        await h.wait_count(h.aw, first_aw+1)
        await h.wait_count(h.w, first_w+1)
        await h.idle(5)
        assert len(h.write_done) == before
        h.put(m_axi_bvalid=1)
        await h.wait_count(h.b, first_b+2)
        h.put(m_axi_bvalid=0)
        await h.completion('wr', before, 8, 200)
        assert h.value('axi_quiescent')
        COVERAGE['faults'][f'premature_b_{case}'] = 1

    await h.reset()
    h.allow_malformed_response = True
    h.put(m_axi_arready=0)
    before, first_data, first_ar = len(h.read_done), len(h.reads), len(h.ar)
    await h.command('rd', 0xe000, 1, 201)
    await h.wait_value('m_axi_arvalid')
    # A response needs a prior AR handshake, not an AR on this same edge.
    sample = await h.step(m_axi_arready=1, m_axi_rvalid=1, m_axi_rdata=0xdead,
                          m_axi_rresp=0, m_axi_rid=0, m_axi_rlast=1)
    assert sample['m_axi_arvalid'] and sample['m_axi_arready'] and sample['m_axi_rready']
    h.put(m_axi_rvalid=0)
    await h.idle(20)
    assert len(h.ar) == first_ar+1 and len(h.read_done) == before and len(h.reads) == first_data
    assert h.value('fatal_code') == 8 and not h.value('axi_quiescent')
    await h.read_beat(0xbeef, 1)
    await h.completion('rd', before, 8, 201)
    assert len(h.reads) == first_data and h.value('axi_quiescent')
    COVERAGE['faults']['premature_r_same_ar_edge'] = 1
    save_coverage()
