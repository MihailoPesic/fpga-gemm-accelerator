"""Packet-level host oracle against the production DDR GEMM system."""
from collections import deque
import json
import logging
import os
from pathlib import Path
import random
import struct
import zlib

import cocotb
from cocotbext.axi import AxiBus, AxiRam
from common import tick


P, T = int(os.environ['GEMM_P']), int(os.environ['GEMM_T'])
READ_SLOTS = int(os.environ.get('GEMM_READ_SLOTS', '1'))
assert READ_SLOTS in (1, 4)
DDR_BYTES = 1 << 27
BUILD_ID = int(os.environ['GEMM_BUILD_ID'])
VERSION = int(os.environ.get('GEMM_VERSION', '256'))
COUNTERS = tuple(range(0x80, 0xc0, 8))
CONFIG = {'job_id': 0x14, 'm': 0x18, 'n': 0x1c, 'k': 0x20,
          'a_base': 0x24, 'bt_base': 0x28, 'c_base': 0x2c,
          'a_stride': 0x30, 'bt_stride': 0x34, 'c_stride': 0x38,
          'mode': 0x3c, 'watchdog': 0x4c}
COVERAGE = {'p': P, 't': T, 'read_slots': READ_SLOTS, 'seed': 20261201+P+T, 'jobs': 0,
            'output_values': 0, 'shapes': [], 'requests': 0, 'responses': 0,
            'replays': 0, 'sequence_conflicts': 0, 'malformed_frames': 0,
            'invalid_memory_commands': 0, 'host_lengths': [], 'faults': [],
            'watchdog_limits': [], 'watchdog_progress_at_deadline': 0,
            'axi_read_bursts': 0, 'axi_write_bursts': 0, 'axi_read_beats': 0,
            'axi_write_beats': 0, 'axi_write_responses': 0, 'stalls': {},
            'axi_read_occupancy': [], 'local_read_occupancy': [],
            'gemm_read_error_ownership': {},
            'host_memory_completion_fault_priority': [],
            'host_memory_page_burst_schedule': {}}


def save_coverage():
    Path(os.environ['GEMM_COVERAGE']).write_text(json.dumps(COVERAGE, indent=2)+'\n')


def cobs_encode(data):
    encoded = bytearray([0])
    code_at, code = 0, 1
    for value in data:
        if value:
            encoded.append(value)
            code += 1
            if code == 255:
                encoded[code_at] = code
                code_at, code = len(encoded), 1
                encoded.append(0)
        else:
            encoded[code_at] = code
            code_at, code = len(encoded), 1
            encoded.append(0)
    encoded[code_at] = code
    return bytes(encoded)


def cobs_decode(encoded):
    decoded, offset = bytearray(), 0
    while offset < len(encoded):
        code = encoded[offset]
        assert code and offset+code <= len(encoded), 'Malformed response COBS'
        decoded.extend(encoded[offset+1:offset+code])
        offset += code
        if code < 255 and offset < len(encoded):
            decoded.append(0)
    return bytes(decoded)


def raw_packet(opcode, sequence, payload=b''):
    body = struct.pack('<BBHH', 1, opcode, sequence, len(payload))+payload
    return body+struct.pack('<I', zlib.crc32(body))


def packet(opcode, sequence, payload=b''):
    return cobs_encode(raw_packet(opcode, sequence, payload))+b'\0'


def decode_reply(encoded):
    raw = cobs_decode(encoded)
    assert len(raw) >= 12 and zlib.crc32(raw[:-4]) == int.from_bytes(raw[-4:], 'little')
    version, opcode, sequence, length = struct.unpack('<BBHH', raw[:6])
    assert version == 1 and len(raw) == length+10 and 2 <= length <= 242
    return opcode, sequence, int.from_bytes(raw[6:8], 'little'), raw[8:-4]


def split_words(address, length):
    """Group enumerated word addresses by page, then by legal burst capacity."""
    groups = []
    for word in range(address, address+length, 8):
        if not groups or len(groups[-1]) == 16 or word//4096 != groups[-1][0]//4096:
            groups.append([])
        groups[-1].append(word)
    return [(group[0], len(group)) for group in groups]


def round64(value):
    return (value+63)//64*64


def expected_job_reads(descriptor):
    """Enumerate matrix-row bytes in public macrotile order, then AXI limits."""
    result = []
    m, n, k = (descriptor[name] for name in ('m', 'n', 'k'))
    length = (k+7)//8*8
    for i0 in range(0, m, T):
        for j0 in range(0, n, T):
            for name, first, count in (('a', i0, min(T, m-i0)),
                                       ('bt', j0, min(T, n-j0))):
                for row in range(first, first+count):
                    result.extend(split_words(descriptor[name+'_base']+
                                              row*descriptor[name+'_stride'], length))
    return result


class Harness:
    CHANNELS = {
        'ar': ('m_axi_arvalid', 'm_axi_arready', tuple('m_axi_ar'+field for field in
               ('addr', 'len', 'id', 'size', 'burst', 'lock', 'cache', 'prot', 'qos', 'region'))),
        'aw': ('m_axi_awvalid', 'm_axi_awready', tuple('m_axi_aw'+field for field in
               ('addr', 'len', 'id', 'size', 'burst', 'lock', 'cache', 'prot', 'qos', 'region'))),
        'w': ('m_axi_wvalid', 'm_axi_wready', ('m_axi_wdata', 'm_axi_wstrb', 'm_axi_wlast')),
    }

    def __init__(self, dut):
        self.dut = dut
        self.rng = random.Random(COVERAGE['seed'])
        self.ram = AxiRam(AxiBus.from_prefix(dut, 'm_axi'), dut.clk, dut.rst, size=DDR_BYTES)
        # cocotbext-axi 0.1.28 defaults to two queued ARs plus one active
        # burst. Permit the configured DUT depth even while R is held off.
        self.ram.read_if.ar_channel.queue_occupancy_limit = max(2, READ_SLOTS)
        self.ram.read_if.log.setLevel(logging.ERROR)
        self.ram.write_if.log.setLevel(logging.ERROR)
        self.channels = dict(ar=self.ram.read_if.ar_channel, r=self.ram.read_if.r_channel,
                             aw=self.ram.write_if.aw_channel, w=self.ram.write_if.w_channel,
                             b=self.ram.write_if.b_channel)
        self.force_pause = {}
        for index, (name, channel) in enumerate(self.channels.items()):
            channel.set_pause_generator(self.pauses(name, 9100+index))
        original_read, original_write = self.ram.read_if._read, self.ram.write_if._write
        self.fail_read = self.fail_write = False

        async def checked_read(address, length):
            if self.fail_read:
                self.fail_read = False
                raise RuntimeError('Injected DDR read failure')
            return await original_read(address, length)

        async def checked_write(address, data):
            if self.fail_write:
                self.fail_write = False
                raise RuntimeError('Injected DDR write failure')
            await original_write(address, data)

        self.ram.read_if._read, self.ram.write_if._write = checked_read, checked_write
        self.sequence = 0
        self.cycle = 0
        self.replies = []
        self.partial = bytearray()
        self.held = {}
        self.tx_hold = None
        self.logs = {name: [] for name in ('ar', 'aw', 'w', 'r', 'b', 'accepted', 'rd_cmd', 'rd_done')}
        # AXI response order and local slot lifetime are separate interfaces:
        # RLAST retires the first, but only local DONE retires the second.
        self.axi_reads = deque()
        self.local_reads = deque()
        self.pause_final_b_at = None
        self.last_frame = None
        self.allow_tx = True
        self.drop_calibration_on_write = False
        self.calibration_drop_cycle = None
        self.watchdog_check = None
        self.pause_r_on_error = False

    def pauses(self, name, seed):
        rng = random.Random(seed)
        while True:
            yield self.force_pause.get(name, rng.randrange(4) == 0)

    def value(self, name):
        return int(getattr(self.dut, name).value)

    async def reset(self, calibrated=True):
        self.dut.clk.value = 0
        self.dut.rst.value = 1
        self.dut.ddr_ready.value = 0
        self.dut.rx_valid.value = 0
        self.dut.rx_error.value = 0
        self.dut.rx_data.value = 0
        self.dut.tx_ready.value = 0
        self.force_pause.clear()
        self.fail_read = self.fail_write = False
        for _ in range(5):
            await tick(self.dut)
        self.dut.rst.value = 0
        self.held.clear()
        self.axi_reads.clear()
        self.local_reads.clear()
        self.tx_hold = None
        self.partial.clear()
        self.pause_final_b_at = None
        self.drop_calibration_on_write = False
        self.calibration_drop_cycle = None
        self.pause_r_on_error = False
        await self.idle(3)
        self.dut.ddr_ready.value = int(calibrated)
        await self.idle(3)
        assert not self.value('busy') and not self.value('host_busy')

    async def step(self, byte=None, error=False):
        self.dut.rx_valid.value = int(byte is not None)
        self.dut.rx_error.value = int(error)
        self.dut.rx_data.value = 0 if byte is None else byte
        self.dut.tx_ready.value = int(self.allow_tx and self.rng.randrange(4) != 0)

        def observe():
            sample = {}
            for name, (valid, ready, fields) in self.CHANNELS.items():
                v, r = self.value(valid), self.value(ready)
                sample[name] = (v, r, tuple(self.value(field) for field in fields) if v else ())
            for name in ('r', 'b'):
                valid, ready = self.value('m_axi_'+name+'valid'), self.value('m_axi_'+name+'ready')
                sample[name] = (valid, ready, self.value('m_axi_'+name+'resp') if valid else 0)
            sample['r_payload'] = ((self.value('m_axi_rid'), self.value('m_axi_rlast'))
                                   if sample['r'][0] else None)
            sample['host_busy'] = self.value('host_busy')
            for name, fields in (
                    ('rd_cmd', ('addr', 'beats', 'tag')),
                    ('rd_done', ('status', 'tag'))):
                valid, ready = self.value('b_'+name+'_valid'), self.value('b_'+name+'_ready')
                sample[name] = (valid, ready, tuple(self.value('b_'+name+'_'+field)
                                                  for field in fields) if valid else ())
            sample['tx'] = (self.value('tx_valid'), self.value('tx_ready'),
                            self.value('tx_data') if self.value('tx_valid') else 0)
            sample['host_write'] = (int(self.dut.control.wr_data_valid.value),
                                    int(self.dut.control.wr_data_ready.value))
            if self.watchdog_check is not None:
                # Observe interface handshakes before this edge, independently
                # of the implementation's counter and timeout expression.
                local_progress = any(int(getattr(self.dut.control, name+'_valid').value) and
                                     int(getattr(self.dut.control, name+'_ready').value)
                                     for name in ('rd_cmd', 'wr_cmd', 'rd_data', 'wr_data',
                                                  'rd_done', 'wr_done'))
                axi_progress = any(sample[name][0] and sample[name][1]
                                   for name in ('ar', 'r', 'aw', 'w', 'b'))
                sample['watchdog'] = (self.value('host_busy'), local_progress or axi_progress)
            return sample

        sample = await tick(self.dut, observe)
        self.cycle += 1
        # Consume responses against the FIFO as it existed before this edge.
        # A first AR accepted on this same edge cannot legalize an R response.
        if sample['r'][0] and sample['r'][1]:
            assert self.axi_reads, 'R response without an earlier accepted AR'
            pending = self.axi_reads[0]
            ident, last = sample['r_payload']
            assert ident == 0 and bool(last) == (pending['remaining'] == 1), (
                'Read response framing/order', self.cycle, pending, sample['r_payload'])
            pending['remaining'] -= 1
            if last:
                self.axi_reads.popleft()
        if self.local_reads:
            assert sample['host_busy'] == self.local_reads[0]['host'], (
                'Read ownership changed before ordered local DONE', self.cycle, self.local_reads[0])
        if sample['rd_done'][0] and sample['rd_done'][1]:
            assert self.local_reads, 'Local read DONE without an accepted command'
            status, tag = sample['rd_done'][2]
            pending = self.local_reads.popleft()
            assert tag == pending['tag'], ('Read terminal reordered', pending, tag)
            self.logs['rd_done'].append((self.cycle, status, tag, pending['host']))
        if sample['rd_cmd'][0] and sample['rd_cmd'][1]:
            address, beats, tag = sample['rd_cmd'][2]
            assert 1 <= beats <= 16 and len(self.local_reads) < READ_SLOTS
            self.local_reads.append(dict(address=address, beats=beats, tag=tag,
                                         host=sample['host_busy']))
            self.logs['rd_cmd'].append((self.cycle, address, beats, tag, sample['host_busy']))
        if sample['ar'][0] and sample['ar'][1]:
            address, length, *_ = sample['ar'][2]
            assert len(self.axi_reads) < READ_SLOTS, 'Too many accepted AXI reads'
            self.axi_reads.append(dict(address=address, remaining=length+1))
        for name, occupancy in (('axi_read_occupancy', len(self.axi_reads)),
                                ('local_read_occupancy', len(self.local_reads))):
            if occupancy not in COVERAGE[name]:
                COVERAGE[name].append(occupancy)
        if self.watchdog_check is not None:
            check = self.watchdog_check
            busy, progress = sample['watchdog']
            if not busy:
                check['idle'] = 0
            elif progress:
                if check['idle'] == check['limit']-1:
                    check['deadline_progress'].append(self.cycle)
                check['idle'] = 0
            else:
                check['idle'] += 1
            expected_fault = busy and check['idle'] == check['limit']
            assert bool(int(self.dut.control.host_fatal.value)) == expected_fault, (
                'Watchdog expiry edge', self.cycle, check)
            if expected_fault:
                assert int(self.dut.control.host_fatal_code.value) == 9
                check['expired_at'] = self.cycle
                self.watchdog_check = None
        if self.drop_calibration_on_write and all(sample['host_write']):
            # The first local word has already entered the burst engine. A
            # coordinated-reset-required fault must not orphan its remaining
            # collection or later AXI address/data/response obligations.
            self.dut.ddr_ready.value = 0
            self.drop_calibration_on_write = False
            self.calibration_drop_cycle = self.cycle
        for name in self.CHANNELS:
            valid, ready, fields = sample[name]
            if name in self.held:
                assert valid and fields == self.held[name], f'{name} withdrew/changed while stalled'
            if valid and not ready:
                self.held[name] = fields
                COVERAGE['stalls'][name] = COVERAGE['stalls'].get(name, 0)+1
            else:
                self.held.pop(name, None)
            if valid and ready:
                self.logs[name].append((self.cycle, fields))
                if name in ('ar', 'aw'):
                    address, length, ident, size, burst, *sidebands = fields
                    assert address % 8 == 0 and 0 <= length < 16 and ident == 0
                    assert size == 3 and burst == 1 and not any(sidebands)
                    assert address//4096 == (address+8*(length+1)-1)//4096
                    assert address+8*(length+1) <= DDR_BYTES
                    key = 'axi_read_bursts' if name == 'ar' else 'axi_write_bursts'
                    COVERAGE[key] += 1
                else:
                    COVERAGE['axi_write_beats'] += 1
        for name in ('r', 'b'):
            valid, ready, status = sample[name]
            if valid and ready:
                self.logs[name].append((self.cycle, status))
                COVERAGE['axi_read_beats' if name == 'r' else 'axi_write_responses'] += 1
                if name == 'r' and status and self.pause_r_on_error:
                    # Stop launching subsequent responses immediately after
                    # the fault handshake. A word already offered may still
                    # transfer; it is never withdrawn to manufacture a stall.
                    self.force_pause['r'] = True
                    self.channels['r'].pause = True
                    self.pause_r_on_error = False
        if self.pause_final_b_at is not None and len(self.logs['b']) >= self.pause_final_b_at:
            self.force_pause['b'] = True
            self.pause_final_b_at = None
        if self.value('job_accepted'):
            self.logs['accepted'].append(self.cycle)
        valid, ready, value = sample['tx']
        if self.tx_hold is not None:
            assert valid and value == self.tx_hold, 'TX byte changed under backpressure'
        self.tx_hold = value if valid and not ready else None
        if valid and ready:
            if value:
                self.partial.append(value)
            else:
                assert self.partial, 'Empty response'
                self.replies.append(decode_reply(bytes(self.partial)))
                self.partial.clear()
                COVERAGE['responses'] += 1
        return sample

    async def idle(self, count):
        for _ in range(count):
            await self.step()

    async def until(self, condition, limit=300000):
        for _ in range(limit):
            if condition():
                return
            await self.step()
        raise AssertionError(f'Timeout cycle{self.cycle}: busy={self.value("busy")}, '
                             f'host_busy={self.value("host_busy")}, error={self.value("error_code")}, '
                             f'AR valid/ready={self.value("m_axi_arvalid")}/{self.value("m_axi_arready")}, '
                             f'R valid/ready={self.value("m_axi_rvalid")}/{self.value("m_axi_rready")}, '
                             f'accepted_AR={list(self.axi_reads)}, local_commands={list(self.local_reads)}, '
                             f'RAM queued_AR={self.channels["ar"].count()}, '
                             f'queued_R={self.channels["r"].count()}, forced_pauses={self.force_pause}')

    async def send(self, frame, error_at=None):
        for index, byte in enumerate(frame):
            await self.step(byte, error=index == error_at)
            if self.rng.randrange(4) == 0:
                await self.step()

    async def exchange(self, opcode, payload=b'', status=0, sequence=None):
        if sequence is None:
            self.sequence = (self.sequence+1) & 0xffff
            sequence = self.sequence
        target = len(self.replies)+1
        frame = packet(opcode, sequence, payload)
        self.last_frame = frame
        COVERAGE['requests'] += 1
        await self.send(frame)
        await self.until(lambda: len(self.replies) >= target)
        actual = self.replies[target-1]
        assert actual[:3] == (opcode | 0x80, sequence, status), actual
        if status:
            assert actual[3] == b'', actual
        return actual[3]

    async def reg_read(self, address, status=0):
        data = await self.exchange(2, struct.pack('<H', address), status)
        if not status:
            assert len(data) == 4
            return int.from_bytes(data, 'little')

    async def reg_write(self, address, value, status=0, sequence=None):
        assert await self.exchange(3, struct.pack('<HI', address, value), status, sequence) == b''

    async def counters(self):
        values = []
        for address in COUNTERS:
            low, high = await self.reg_read(address), await self.reg_read(address+4)
            values.append(low+(high << 32))
        return tuple(values)

    async def write(self, address, data, status=0, sequence=None):
        assert await self.exchange(5, struct.pack('<IH', address, len(data))+data,
                                   status, sequence) == b''

    async def read(self, address, length, status=0):
        data = await self.exchange(4, struct.pack('<IH', address, length), status)
        if not status:
            assert len(data) == length
        return data

    async def upload(self, address, data):
        assert len(data) % 8 == 0
        for offset in range(0, len(data), 240):
            await self.write(address+offset, data[offset:offset+240])

    async def download(self, address, length):
        assert length % 8 == 0
        result = bytearray()
        for offset in range(0, length, 240):
            result.extend(await self.read(address+offset, min(240, length-offset)))
        return bytes(result)

    async def configure(self, descriptor):
        for name, address in CONFIG.items():
            await self.reg_write(address, descriptor[name])

    def matrix(self, m, n, k, job_id, extremes=False):
        descriptor = dict(job_id=job_id, m=m, n=n, k=k, a_base=0xfc0,
                          bt_base=0x100fc0, c_base=0x200fc0, a_stride=round64(k),
                          bt_stride=round64(k), c_stride=round64(4*n), mode=0, watchdog=1000000)
        a = [[-128 if extremes else self.rng.randrange(-128, 128) for _ in range(k)] for _ in range(m)]
        b = [[-128 if extremes else self.rng.randrange(-128, 128) for _ in range(n)] for _ in range(k)]
        bt = list(zip(*b))
        golden = [[sum(int(a[i][q])*int(b[q][j]) for q in range(k)) for j in range(n)] for i in range(m)]
        regions = {}
        for name, rows in (('a', a), ('bt', bt), ('c', [[0]*n for _ in range(m)])):
            stride = descriptor[name+'_stride']
            raw = bytearray(self.rng.randrange(1, 256) for _ in range(len(rows)*stride+128))
            if name != 'c':
                for row, values in enumerate(rows):
                    raw[64+row*stride:64+row*stride+k] = bytes(value & 255 for value in values)
            regions[name] = (descriptor[name+'_base']-64, raw)
        expected_c = bytearray(regions['c'][1])
        for row, values in enumerate(golden):
            offset = 64+row*descriptor['c_stride']
            expected_c[offset:offset+4*n] = b''.join(struct.pack('<i', value) for value in values)
        return descriptor, regions, bytes(expected_c)

    async def prepare_matrix(self, descriptor, regions):
        for address, data in regions.values():
            await self.upload(address, data)
        await self.configure(descriptor)

    async def check_matrix(self, descriptor, regions, expected_c):
        for name, (address, original) in regions.items():
            expected = expected_c if name == 'c' else bytes(original)
            assert await self.download(address, len(original)) == expected, (name, descriptor)
        m, n, k = (descriptor[name] for name in ('m', 'n', 'k'))
        COVERAGE['jobs'] += 1
        COVERAGE['output_values'] += m*n
        COVERAGE['shapes'].append([m, n, k])


@cocotb.test()
async def host_memory_packets_and_retries(dut):
    h = Harness(dut)
    await h.reset()
    nonce = 0x1234abcd
    assert await h.exchange(1, struct.pack('<I', nonce)) == struct.pack('<III', nonce, 0x314d474e, VERSION)
    assert await h.reg_read(0x50) == BUILD_ID
    assert await h.reg_read(8) == (256 << 16)+(T << 8)+P
    baseline = await h.counters()
    for length in range(8, 241, 8):
        address = 0xff8 if length in (8, 128, 136, 240) else 0x3000+8*(length % 16)
        data = bytes(h.rng.randrange(256) for _ in range(length))
        sentinel = b'\xa7'*8
        h.ram.write(address-8, sentinel+bytes(length)+sentinel)
        before_aw, before_ar = len(h.logs['aw']), len(h.logs['ar'])
        await h.write(address, data)
        assert await h.read(address, length) == data
        assert h.ram.read(address-8, length+16) == sentinel+data+sentinel
        for channel, start in (('aw', before_aw), ('ar', before_ar)):
            actual = [(fields[0], fields[1]+1) for _, fields in h.logs[channel][start:]]
            assert actual == split_words(address, length)
        COVERAGE['host_lengths'].append(length)
    data = bytes(range(240))
    await h.write(DDR_BYTES-240, data)
    assert await h.read(DDR_BYTES-240, 240) == data
    before = tuple(len(h.logs[name]) for name in ('ar', 'aw', 'w'))
    for address, length, status in ((0x3000, 0, 1), (0x3000, 4, 1), (0x3000, 9, 1),
                                    (0x3000, 248, 1), (0x3000, 65535, 1),
                                    (0x3001, 8, 2), (DDR_BYTES, 8, 2),
                                    (DDR_BYTES-8, 16, 2), (0xfffffff8, 16, 2),
                                    (0xfffffff0, 240, 2)):
        await h.read(address, length, status=status)
        # Invalid writes are still fully framed packets; do not provide more
        # than the protocol's bounded payload capacity for huge declarations.
        write_data = bytes(length) if length <= 248 else b''
        await h.exchange(5, struct.pack('<IH', address, length)+write_data, status)
        COVERAGE['invalid_memory_commands'] += 2
    for opcode, payload in ((4, b''), (4, struct.pack('<IH', 0x3000, 8)+b'x'),
                            (5, struct.pack('<IH', 0x3000, 8)+b'short'),
                            (5, struct.pack('<IH', 0x3000, 8)+b'too long!')):
        await h.exchange(opcode, payload, status=1)
        COVERAGE['invalid_memory_commands'] += 1
    assert tuple(len(h.logs[name]) for name in ('ar', 'aw', 'w')) == before
    # A duplicate write must replay the response even if memory has changed.
    sequence = h.sequence+1
    await h.write(0x4000, b'abcdefgh', sequence=sequence)
    h.sequence = sequence
    counts = tuple(len(h.logs[name]) for name in ('aw', 'w', 'b'))
    h.ram.write(0x4000, b'changed!')
    await h.write(0x4000, b'abcdefgh', sequence=sequence)
    assert tuple(len(h.logs[name]) for name in ('aw', 'w', 'b')) == counts
    assert h.ram.read(0x4000, 8) == b'changed!'
    COVERAGE['replays'] += 1
    await h.write(0x4000, b'conflict', status=6, sequence=sequence)
    assert tuple(len(h.logs[name]) for name in ('aw', 'w', 'b')) == counts
    COVERAGE['sequence_conflicts'] += 1
    before = tuple(len(h.logs[name]) for name in ('ar', 'aw', 'w'))
    # Malformed framing, corrupted CRC, truncated body and UART byte error.
    damaged = bytearray(raw_packet(5, 60000, struct.pack('<IH', 0x5000, 8)+b'badwrite'))
    damaged[-1] ^= 0x80
    bad = [cobs_encode(damaged)+b'\0', packet(5, 60001, b'wrong')[:-4]+b'\0', b'\x05x\0']
    replies = len(h.replies)
    for frame in bad:
        await h.send(frame)
        await h.idle(1500)
        COVERAGE['malformed_frames'] += 1
    await h.send(packet(5, 60002, struct.pack('<IH', 0x5000, 8)+b'badwrite'), error_at=7)
    await h.idle(1500)
    COVERAGE['malformed_frames'] += 1
    assert len(h.replies) == replies and tuple(len(h.logs[name]) for name in ('ar', 'aw', 'w')) == before
    assert await h.counters() == baseline
    save_coverage()


@cocotb.test()
async def host_memory_page_burst_schedule(dut):
    h = Harness(dut)
    await h.reset()
    baseline = await h.counters()
    h.force_pause.update({channel: False for channel in h.channels})
    lengths = (8, 120, 128, 136, 240)
    first_burst_sizes = set()
    held_channels = {}
    cases = 0

    async def stalled_transfer(opcode, address, data):
        channels = ('aw', 'w') if opcode == 5 else ('ar',)
        h.force_pause.update({channel: True for channel in channels})
        await h.idle(4)
        before = {channel: len(h.logs[channel]) for channel in channels}
        h.sequence += 1
        target = len(h.replies)+1
        payload = struct.pack('<IH', address, len(data))
        if opcode == 5:
            payload += data
        COVERAGE['requests'] += 1
        await h.send(packet(opcode, h.sequence, payload))
        await h.until(lambda: all(h.value('m_axi_'+channel+'valid') for channel in channels),
                      limit=3000)
        expected_address, expected_beats = split_words(address, len(data))[0]
        for channel in channels:
            assert not h.value('m_axi_'+channel+'ready')
            if channel in ('ar', 'aw'):
                assert h.value('m_axi_'+channel+'addr') == expected_address
                assert h.value('m_axi_'+channel+'len')+1 == expected_beats
            else:
                assert h.value('m_axi_wdata').to_bytes(8, 'little') == data[:8]
                assert h.value('m_axi_wstrb') == 255
                assert bool(h.value('m_axi_wlast')) == (expected_beats == 1)
        # The fixture checks every AXI offer's full payload for immutability
        # throughout this deliberate address/data backpressure interval.
        await h.idle(20)
        assert all(len(h.logs[channel]) == before[channel] for channel in channels)
        for channel in channels:
            h.force_pause[channel] = False
            held_channels[channel] = held_channels.get(channel, 0)+20
        await h.until(lambda: len(h.replies) == target)
        reply = h.replies[-1]
        assert reply[:3] == (opcode | 0x80, h.sequence, 0), reply
        return reply[3]

    for offset in range(0, 128, 8):
        address = 0xaf80+offset
        for length in lengths:
            data = bytes((index*47+offset+length) & 255 for index in range(length))
            guard = b'\xa7'*8
            h.ram.write(address-8, guard+bytes(length)+guard)
            # Enumerate individual word addresses, grouping only at a page
            # change or sixteen words. This oracle does not reproduce the
            # RTL's capped page-distance arithmetic or registered cursor.
            expected = split_words(address, length)
            first_burst_sizes.add(expected[0][1])
            before = {name: len(h.logs[name]) for name in ('ar', 'aw', 'w', 'r', 'b', 'rd_done')}
            stall = length == 240 and offset in (0, 120)
            if stall:
                assert await stalled_transfer(5, address, data) == b''
            else:
                await h.write(address, data)
            writes = [(fields[0], fields[1]+1) for _, fields in h.logs['aw'][before['aw']:]]
            assert writes == expected, ('Host write burst schedule', address, length, writes, expected)
            assert len(h.logs['b'])-before['b'] == len(expected)
            assert len(h.logs['w'])-before['w'] == length//8
            assert h.ram.read(address-8, length+16) == guard+data+guard

            if stall:
                result = await stalled_transfer(4, address, data)
            else:
                result = await h.read(address, length)
            assert result == data
            reads = [(fields[0], fields[1]+1) for _, fields in h.logs['ar'][before['ar']:]]
            assert reads == expected, ('Host read burst schedule', address, length, reads, expected)
            assert len(h.logs['r'])-before['r'] == length//8
            terminals = h.logs['rd_done'][before['rd_done']:]
            assert len(terminals) == len(expected)
            assert all(status == 0 and tag == 0 and host for _, status, tag, host in terminals)
            assert h.ram.read(address-8, length+16) == guard+data+guard
            assert not h.value('host_busy') and not h.axi_reads and not h.local_reads
            assert not h.value('reset_required') and not h.value('error')
            cases += 1

    assert first_burst_sizes == set(range(1, 17))
    assert not h.logs['accepted'] and await h.counters() == baseline
    COVERAGE['host_memory_page_burst_schedule'] = {
        'aligned_page_tail_starts': 16, 'lengths': list(lengths), 'write_read_cases': cases,
        'first_burst_sizes': sorted(first_burst_sizes), 'held_axi_cycles': held_channels,
        'data_and_guards_preserved': True, 'unexpected_faults': 0}
    save_coverage()


@cocotb.test()
async def complete_matrix_jobs_from_host_packets(dut):
    h = Harness(dut)
    await h.reset()
    shapes = [(T+1, T+3, 9), (3, 5, 1), (1, P+1, 7), (P+1, 3, 8), (2, 3, 255), (3, 1, 256)]
    for job_id, (m, n, k) in enumerate(shapes, start=1):
        descriptor, regions, expected = h.matrix(m, n, k, job_id, extremes=k == 256)
        await h.prepare_matrix(descriptor, regions)
        accepted_before = len(h.logs['accepted'])
        ar_before = len(h.logs['ar'])
        b_before = len(h.logs['b'])
        await h.reg_write(0x10, 1)
        assert len(h.logs['accepted']) == accepted_before+1, 'START acknowledged before validation'
        for _ in range(500):
            status = await h.reg_read(0x0c)
            if status & 0x0c:
                break
        else:
            raise AssertionError('Job did not complete through packet status polling')
        assert status & 4 and not status & 8
        assert not h.value('error') and h.value('last_job_id') == job_id
        assert len(h.logs['b']) > b_before
        counters = await h.counters()
        assert counters[0] == h.logs['b'][-1][0]-h.logs['accepted'][-1]
        assert counters[1] == ((m+P-1)//P)*((n+P-1)//P)*(k+3*P-1)
        assert counters[2] == ((k+7)//8)*(m*((n+T-1)//T)+n*((m+T-1)//T))
        assert counters[3] == m*((n+1)//2) and counters[4] == 4*m*n
        assert [(fields[0], fields[1]+1) for _, fields in h.logs['ar'][ar_before:]] == expected_job_reads(descriptor)
        await h.check_matrix(descriptor, regions, expected)
        assert await h.counters() == counters, 'Host reads changed frozen job counters'
        assert await h.reg_read(0x0c) == 0x15
    previous = await h.counters()
    await h.write(0x600000, b'hostonly')
    assert await h.read(0x600000, 8) == b'hostonly'
    assert h.value('done') and h.value('last_job_id') == len(shapes)
    assert await h.counters() == previous, 'Idle host write changed completed job counters'
    before = tuple(len(h.logs[name]) for name in ('ar', 'aw', 'accepted'))
    await h.reg_write(0x18, 0)
    await h.reg_write(0x10, 1, status=3)
    assert tuple(len(h.logs[name]) for name in ('ar', 'aw', 'accepted')) == before
    assert h.value('done') and await h.counters() == previous
    save_coverage()


@cocotb.test()
async def busy_ownership_duplicate_start_and_final_b(dut):
    h = Harness(dut)
    await h.reset()
    descriptor, regions, expected = h.matrix(T+1, T+1, 7, 0xabcd)
    await h.prepare_matrix(descriptor, regions)
    h.force_pause['b'] = True
    await h.idle(3)
    job_aw_start = len(h.logs['aw'])
    sequence = h.sequence+1
    await h.reg_write(0x10, 1, sequence=sequence)
    h.sequence = sequence
    assert h.value('busy')
    # Large tiles can still be computing when the START response arrives.
    # Establish a real outstanding write before testing blocked host traffic.
    # Holding its B response then prevents further serial DMA addresses.
    await h.until(lambda: len(h.logs['aw']) > job_aw_start)
    count = len(h.logs['accepted'])
    await h.reg_write(0x10, 1, sequence=sequence)
    assert len(h.logs['accepted']) == count == 1
    COVERAGE['replays'] += 1
    await h.reg_write(0x10, 2, status=6, sequence=sequence)
    COVERAGE['sequence_conflicts'] += 1
    await h.reg_write(0x10, 1, status=4)
    await h.reg_write(0x18, 99, status=4)
    await h.reg_write(0x10, 2, status=4)
    before = len(h.logs['ar']), len(h.logs['aw'])
    h.ram.write(0x6000, b'guarded!')
    await h.write(0x6000, b'noeffect', status=4)
    await h.read(0x6000, 8, status=4)
    assert (len(h.logs['ar']), len(h.logs['aw'])) == before
    assert h.ram.read(0x6000, 8) == b'guarded!'
    assert not h.value('done') and not h.value('host_busy')
    await h.reg_read(0x80, status=4)
    # Stall exactly the last write response after allowing preceding tiles.
    expected_aw = 0
    m, n = descriptor['m'], descriptor['n']
    for i0 in range(0, m, T):
        for j0 in range(0, n, T):
            for row in range(min(T, m-i0)):
                address = descriptor['c_base']+(i0+row)*descriptor['c_stride']+4*j0
                expected_aw += len(split_words(address, ((min(T, n-j0)+1)//2)*8))
    # Every uploaded burst completed before START. Its count is therefore
    # also the completed-B baseline for locating the final result response.
    h.pause_final_b_at = job_aw_start+expected_aw-1
    h.force_pause.pop('b')
    await h.until(lambda: h.pause_final_b_at is None)
    await h.idle(200)
    assert h.value('busy') and not h.value('done') and len(h.logs['b']) == len(h.logs['aw'])-1
    h.force_pause.pop('b')
    await h.until(lambda: h.value('done') or h.value('error'))
    assert h.value('done') and not h.value('error')
    counters = await h.counters()
    assert counters[0] == h.logs['b'][-1][0]-h.logs['accepted'][0]
    await h.check_matrix(descriptor, regions, expected)
    assert await h.counters() == counters
    save_coverage()


@cocotb.test()
async def host_memory_faults_and_hung_drain(dut):
    h = Harness(dut)
    for fault in ('read', 'write', 'watchdog'):
        await h.reset()
        await h.reg_write(0x4c, 200 if fault == 'watchdog' else 1000000)
        baseline = await h.counters()
        if fault == 'read':
            h.fail_read = True
            await h.read(0x8000, 8, status=7)
        elif fault == 'write':
            h.fail_write = True
            await h.write(0x8000, b'failtest', status=7)
        else:
            h.force_pause['ar'] = True
            await h.idle(4)
            await h.read(0x8000, 8, status=9)
            assert h.value('host_busy') and h.value('m_axi_arvalid')
        await h.until(lambda: h.value('reset_required'))
        assert h.value('error') and h.value('error_code') == (9 if fault == 'watchdog' else 7)
        status = await h.reg_read(0x0c)
        assert status & 0x28 == 0x28 and not status & 1
        assert await h.counters() == baseline
        blocked_status = 4 if fault == 'watchdog' else 5
        await h.reg_write(0x10, 1, status=blocked_status)
        await h.read(0x8000, 8, status=blocked_status)
        if fault == 'watchdog':
            assert h.value('host_busy') and h.value('m_axi_arvalid')
            h.force_pause.pop('ar')
        await h.until(lambda: not h.value('host_busy'))
        await h.until(lambda: not h.value('busy'))
        assert h.value('reset_required') and not h.value('ready')
        await h.reg_write(0x10, 1, status=5)
        COVERAGE['faults'].append(fault)
    save_coverage()


@cocotb.test()
async def host_watchdog_threshold_and_progress(dut):
    h = Harness(dut)

    async def pending_read(limit):
        await h.reset()
        await h.reg_write(0x4c, limit)
        h.force_pause.update(ar=True, r=True)
        await h.idle(4)
        check = {'limit': limit, 'idle': 0, 'deadline_progress': []}
        h.watchdog_check = check
        h.sequence += 1
        target = len(h.replies)+1
        COVERAGE['requests'] += 1
        await h.send(packet(4, h.sequence, struct.pack('<IH', 0x8800, 8)))
        return check, target

    for limit in (1, 2, 3, 7, 16):
        check, target = await pending_read(limit)
        await h.until(lambda: 'expired_at' in check)
        assert h.value('host_busy') and h.value('m_axi_arvalid')
        await h.until(lambda: len(h.replies) == target)
        assert h.replies[-1] == (0x84, h.sequence, 9, b'')
        # Releasing a timed-out AR must still drain its accepted read. The
        # latched fault persists even when real progress restarts the timer.
        h.force_pause.update(ar=False, r=False)
        await h.until(lambda: not h.value('host_busy'))
        assert int(dut.control.host_fatal.value) and h.value('reset_required')
        COVERAGE['watchdog_limits'].append(limit)

    check, target = await pending_read(8)
    await h.until(lambda: check['idle'] == 6)
    # Set the RAM pause directly in the low phase for this exact-edge check.
    # Its registered READY rises after the next edge, placing the actual AR
    # handshake on the eighth no-progress candidate edge.
    h.channels['ar'].clear_pause_generator()
    h.channels['ar'].pause = False
    await h.until(lambda: bool(check['deadline_progress']) or 'expired_at' in check)
    assert check['deadline_progress'] and 'expired_at' not in check, check
    deadline_edge = check['deadline_progress'][-1]
    assert h.logs['ar'][-1][0] == deadline_edge
    await h.until(lambda: 'expired_at' in check)
    assert check['expired_at']-deadline_edge == 8
    await h.until(lambda: len(h.replies) == target)
    assert h.replies[-1] == (0x84, h.sequence, 9, b'')
    h.force_pause['r'] = False
    await h.until(lambda: not h.value('host_busy'))
    assert h.value('reset_required')
    COVERAGE['watchdog_progress_at_deadline'] += 1

    h.channels['ar'].set_pause_generator(h.pauses('ar', 9100))
    await h.reset()
    await h.reg_write(0x4c, 0, status=1)
    await h.reg_write(0x4c, 0xffffffff)
    assert await h.reg_read(0x4c) == 0xffffffff
    h.ram.write(0x8800, b'maxlimit')
    assert await h.read(0x8800, 8) == b'maxlimit'
    assert not h.value('reset_required')
    COVERAGE['watchdog_limits'].append(0xffffffff)
    save_coverage()


@cocotb.test()
async def host_memory_completion_fault_priority(dut):
    h = Harness(dut)
    for direction, fault_phase in (('read', 'completion'), ('write', 'completion'),
                                   ('read', 'capture')):
        await h.reset()
        baseline = await h.counters()
        h.force_pause.update({channel: False for channel in h.channels})
        address = 0x9400
        data = bytes((index*43+17) & 255 for index in range(128))
        guard = b'\xa7'*8
        initial = data if direction == 'read' else bytes(len(data))
        h.ram.write(address-8, guard+initial+guard)
        before = {name: len(h.logs[name]) for name in h.logs}
        h.allow_tx = False
        h.sequence += 1
        target = len(h.replies)+1
        opcode = 4 if direction == 'read' else 5
        payload = struct.pack('<IH', address, len(data))
        if direction == 'write':
            payload += data
        COVERAGE['requests'] += 1
        await h.send(packet(opcode, h.sequence, payload))

        if fault_phase == 'completion':
            # This public completion pulse precedes publication of the host
            # reply. All memory obligations have retired, but a newly lost
            # calibration must still take priority over that pending success.
            await h.until(lambda: int(dut.control.memory_complete.value), limit=2000)
            assert not h.value('host_busy') and not h.axi_reads and not h.local_reads
            assert not int(dut.control.rsp_valid.value)
            if direction == 'read':
                observed = int(dut.control.rsp_payload.value).to_bytes(240, 'little')
                assert observed[:len(data)] == data
        else:
            # Lose calibration with the first buffered read word offered for
            # local acceptance. Any data retained on this detecting edge must
            # be discarded before an error response becomes externally valid.
            await h.until(lambda: int(dut.control.rd_data_valid.value) and
                          int(dut.control.rd_data_ready.value), limit=2000)
            assert h.value('host_busy') and h.local_reads
            assert int(dut.control.rd_data_index.value) == 0
            assert int(dut.control.rd_data.value).to_bytes(8, 'little') == data[:8]

        dut.ddr_ready.value = 0
        await h.step()
        assert int(dut.control.host_fatal.value)
        assert int(dut.control.host_fatal_code.value) == 10

        held_response = None
        held_cycles = 0
        saw_response = False
        for _ in range(2000):
            valid, ready = (int(getattr(dut.control, 'rsp_'+name).value)
                            for name in ('valid', 'ready'))
            fields = (int(dut.control.rsp_status.value),
                      int(dut.control.rsp_length.value),
                      int(dut.control.rsp_payload.value))
            if held_response is not None:
                assert valid and fields == held_response, 'Fatal response changed while transport copied it'
            if valid:
                assert fields == (10, 0, 0), ('Completion escaped calibration fault', fields)
                saw_response = True
            held_response = fields if valid and not ready else None
            held_cycles += int(valid and not ready)
            if h.local_reads:
                assert h.value('host_busy'), 'Fault released read ownership before local DONE'
            if h.value('tx_valid'):
                break
            await h.step()
        else:
            raise AssertionError('Calibration fault response did not reach stalled transport')
        assert saw_response and held_cycles > 0
        # Hold the framed reply at its first byte as well as checking the
        # backend response through the transport's payload-copy backpressure.
        stalled_byte = h.value('tx_data')
        await h.idle(20)
        assert h.value('tx_valid') and h.value('tx_data') == stalled_byte
        assert len(h.replies) == target-1
        h.allow_tx = True
        await h.until(lambda: len(h.replies) == target, limit=2000)
        assert h.replies[-1] == (opcode | 0x80, h.sequence, 10, b'')
        await h.until(lambda: not h.value('host_busy') and not h.value('busy'), limit=2000)
        assert not h.axi_reads and not h.local_reads
        assert h.value('reset_required') and h.value('error_code') == 10
        assert not h.value('done') and not h.value('ready')
        assert h.ram.read(address-8, len(data)+16) == guard+data+guard
        assert len(h.logs['accepted']) == before['accepted']
        if direction == 'read':
            assert len(h.logs['ar'])-before['ar'] == 1
            assert len(h.logs['r'])-before['r'] == len(data)//8
            assert len(h.logs['rd_done'])-before['rd_done'] == 1
            assert len(h.logs['aw']) == before['aw'] and len(h.logs['w']) == before['w']
        else:
            assert len(h.logs['aw'])-before['aw'] == 1
            assert len(h.logs['b'])-before['b'] == 1
            beats = h.logs['w'][before['w']:]
            observed = b''.join(fields[0].to_bytes(8, 'little') for _, fields in beats)
            assert observed == data and all(fields[1] == 255 for _, fields in beats)
            assert [fields[2] for _, fields in beats] == [0]*15+[1]
            assert len(h.logs['ar']) == before['ar']

        # Restored calibration cannot erase the first fault or permit reuse.
        dut.ddr_ready.value = 1
        await h.reg_write(0x10, 1, status=5)
        await h.read(address, 8, status=5)
        await h.write(address, b'noeffect', status=5)
        assert h.value('error_code') == 10
        assert await h.counters() == baseline
        assert h.ram.read(address-8, len(data)+16) == guard+data+guard
        COVERAGE['host_memory_completion_fault_priority'].append({
            'direction': direction, 'fault_phase': fault_phase,
            'status': 10, 'empty_reply': True, 'ownership_drained': True,
            'backend_stall_cycles': held_cycles, 'tx_stall_cycles': 20,
            'data_and_guards_preserved': True})
        COVERAGE['faults'].append(f'calibration_during_host_{direction}_{fault_phase}')
    save_coverage()


@cocotb.test()
async def calibration_loss_during_accepted_write(dut):
    h = Harness(dut)
    await h.reset()
    data = bytes((index*37+11) & 255 for index in range(128))
    address = 0x9008
    guards = b'\xa7'*8
    h.ram.write(address-8, guards+bytes(128)+guards)
    baseline = await h.counters()
    h.force_pause.update(aw=True, w=True, b=True)
    await h.idle(4)
    h.drop_calibration_on_write = True
    await h.write(address, data, status=10)
    assert h.calibration_drop_cycle is not None
    assert h.value('host_busy') and h.value('reset_required') and h.value('error_code') == 10
    assert h.value('m_axi_awvalid') and h.value('m_axi_wvalid')
    assert not h.logs['accepted'] and not h.logs['aw'] and not h.logs['w']
    # These new request payloads cannot overwrite data retained by the failed
    # write. AXI offers must stay asserted and unchanged throughout diagnosis.
    for nonce in (0xdeadbeef, 0x12345678):
        assert await h.exchange(1, struct.pack('<I', nonce)) == struct.pack('<III', nonce, 0x314d474e, VERSION)
    status = await h.reg_read(0x0c)
    assert status & 0x28 == 0x28 and not status & 0x11
    assert await h.counters() == baseline
    await h.reg_write(0x10, 1, status=4)
    h.force_pause.pop('aw')
    h.force_pause.pop('w')
    await h.until(lambda: len(h.logs['w']) == 16)
    await h.idle(20)
    assert h.value('host_busy') and not h.logs['b']
    observed = b''.join(fields[0].to_bytes(8, 'little') for _, fields in h.logs['w'])
    assert observed == data and all(fields[1] == 255 for _, fields in h.logs['w'])
    assert [fields[2] for _, fields in h.logs['w']] == [0]*15+[1]
    h.force_pause.pop('b')
    await h.until(lambda: not h.value('host_busy') and not h.value('busy'))
    assert h.ram.read(address-8, 144) == guards+data+guards
    assert len(h.logs['b']) == 1 and h.value('reset_required') and not h.value('done')
    await h.reg_write(0x10, 1, status=5)
    assert await h.counters() == baseline
    COVERAGE['faults'].append('calibration_during_write_collection')
    save_coverage()


@cocotb.test()
async def calibration_loss_cancels_unoffered_job_reads(dut):
    h = Harness(dut)
    await h.reset()
    descriptor, regions, _ = h.matrix(4, 3, 128, 0x420)
    descriptor['a_base'] = 0x1000
    regions['a'] = (descriptor['a_base']-64, regions['a'][1])
    await h.prepare_matrix(descriptor, regions)
    h.force_pause.update({channel: False for channel in h.channels})
    h.force_pause.update(ar=True, r=True)
    await h.idle(4)
    before = {name: len(h.logs[name]) for name in h.logs}
    await h.reg_write(0x10, 1)
    await h.until(lambda: len(h.local_reads) == READ_SLOTS and h.value('m_axi_arvalid'))
    address = h.value('m_axi_araddr')
    assert address == descriptor['a_base'] and not h.axi_reads
    assert len(h.logs['ar']) == before['ar']
    h.dut.ddr_ready.value = 0
    await h.until(lambda: h.value('reset_required'))
    await h.idle(4)
    assert h.value('error_code') == 10 and not h.value('done')
    assert h.value('busy') and not h.value('host_busy')
    frozen = await h.counters()
    assert frozen[1:5] == (0, 0, 0, 0)
    await h.read(0x600000, 8, status=4)
    await h.write(0x600000, b'noeffect', status=4)
    await h.reg_write(0x10, 1, status=4)
    assert len(h.logs['ar']) == before['ar'] and not h.value('host_busy')
    # The previously offered address must survive; the other accepted local
    # commands own ordered error terminals without creating DDR requests.
    h.force_pause['ar'] = False
    await h.until(lambda: len(h.logs['ar']) == before['ar']+1)
    await h.idle(40)
    assert len(h.logs['ar']) == before['ar']+1
    assert len(h.axi_reads) == 1 and not h.value('m_axi_arvalid')
    assert h.logs['ar'][-1][1][:2] == (address, 15)
    h.force_pause['r'] = False
    await h.until(lambda: not h.value('busy'))
    assert not h.axi_reads and not h.local_reads
    terminals = h.logs['rd_done'][before['rd_done']:]
    assert [entry[1] for entry in terminals] == [0]+[8]*(READ_SLOTS-1)
    assert all(not entry[3] for entry in terminals)
    assert len(h.logs['r'])-before['r'] == 16
    assert len(h.logs['ar']) == before['ar']+1
    assert len(h.logs['aw']) == before['aw'] and len(h.logs['w']) == before['w']
    assert h.value('reset_required') and h.value('error_code') == 10
    assert not h.value('done') and not h.value('ready')
    assert await h.counters() == frozen
    for region_address, original in regions.values():
        assert h.ram.read(region_address, len(original)) == bytes(original)
    await h.read(0x600000, 8, status=5)
    await h.reg_write(0x10, 1, status=5)
    COVERAGE['external_read_cancel'] = {
        'read_slots': READ_SLOTS, 'held_ar_drained': 1,
        'never_offered_canceled': READ_SLOTS-1, 'drained_r_beats': 16,
        'global_error_code': 10, 'fault_counters_frozen': True}
    COVERAGE['faults'].append('calibration_loss_cancels_unoffered_job_reads')
    save_coverage()


@cocotb.test()
async def gemm_read_error_retains_memory_ownership(dut):
    h = Harness(dut)
    await h.reset()
    descriptor, regions, _ = h.matrix(4, 3, 128, 0x410)
    # Full 16-beat rows leave time to halt responses after the first error,
    # independently of the registered fault propagation through the job.
    descriptor['a_base'] = 0x1000
    regions['a'] = (descriptor['a_base']-64, regions['a'][1])
    await h.prepare_matrix(descriptor, regions)
    h.force_pause.update({channel: False for channel in h.channels})
    h.force_pause['r'] = True
    await h.idle(4)
    before = {name: len(h.logs[name]) for name in h.logs}
    h.fail_read = True
    await h.reg_write(0x10, 1)
    await h.until(lambda: len(h.axi_reads) == READ_SLOTS)
    assert len(h.local_reads) == READ_SLOTS
    assert len(h.logs['r']) == before['r']
    assert len(h.logs['accepted']) == before['accepted']+1
    assert h.value('busy') and not h.value('host_busy')
    issued = [(fields[0], fields[1]+1) for _, fields in h.logs['ar'][before['ar']:]]
    assert issued == expected_job_reads(descriptor)[:READ_SLOTS]

    address = 0x600000
    sentinel = b'guarded!'
    h.ram.write(address, sentinel)
    bus_before = tuple(len(h.logs[name]) for name in ('ar', 'aw', 'w'))
    await h.read(address, 8, status=4)
    await h.write(address, b'noeffect', status=4)
    await h.reg_write(0x10, 1, status=4)
    assert tuple(len(h.logs[name]) for name in ('ar', 'aw', 'w')) == bus_before

    h.pause_r_on_error = True
    h.force_pause['r'] = False
    await h.until(lambda: h.value('reset_required'))
    fault_cycle = h.cycle
    fault_r_beats = len(h.logs['r'])-before['r']
    assert not h.pause_r_on_error and h.force_pause['r']
    await h.idle(4)
    assert h.value('busy') and h.axi_reads and h.local_reads
    assert not h.value('host_busy') and not h.value('done')
    assert h.value('error') and h.value('error_code') == 7
    assert not h.value('ready')
    assert len(h.logs['rd_cmd'])-before['rd_cmd'] == READ_SLOTS
    assert len(h.logs['rd_done']) == before['rd_done']
    frozen = await h.counters()
    assert frozen[0] == fault_cycle-h.logs['accepted'][-1]
    assert frozen[1:5] == (0, fault_r_beats, 0, 0)
    nonce = 0xabad1dea
    assert await h.exchange(1, struct.pack('<I', nonce)) == struct.pack('<III', nonce, 0x314d474e, VERSION)
    await h.read(address, 8, status=4)
    await h.write(address, b'noeffect', status=4)
    await h.reg_write(0x10, 1, status=4)
    assert not h.value('host_busy') and h.value('busy')
    assert tuple(len(h.logs[name]) for name in ('ar', 'aw', 'w')) == bus_before
    assert await h.counters() == frozen

    h.force_pause['r'] = False
    await h.until(lambda: not h.value('busy'))
    assert not h.axi_reads and not h.local_reads
    assert len(h.logs['rd_done'])-before['rd_done'] == READ_SLOTS
    assert all(not terminal[3] for terminal in h.logs['rd_done'][before['rd_done']:])
    assert len(h.logs['r'])-before['r'] == sum(beats for _, beats in issued)
    assert len(h.logs['aw']) == before['aw'] and len(h.logs['w']) == before['w']
    assert len(h.logs['accepted']) == before['accepted']+1
    assert h.value('reset_required') and not h.value('done') and not h.value('ready')
    await h.read(address, 8, status=5)
    await h.write(address, b'noeffect', status=5)
    await h.reg_write(0x10, 1, status=5)
    assert h.ram.read(address, 8) == sentinel
    for region_address, original in regions.values():
        assert h.ram.read(region_address, len(original)) == bytes(original)
    assert await h.counters() == frozen, 'Private drain changed frozen fault counters'
    COVERAGE['gemm_read_error_ownership'] = {
        'read_slots': READ_SLOTS, 'ar_before_first_r': len(issued),
        'ordered_terminals': READ_SLOTS, 'drained_r_beats': sum(beats for _, beats in issued),
        'fault_snapshot_r_beats': fault_r_beats, 'host_commands_excluded': 9,
        'c_unchanged': True, 'fault_counters_frozen': True}
    COVERAGE['faults'].append('gemm_read_error_retains_memory_ownership')

    # A coordinated reset permits a fresh, fully compared job. No aborted
    # input residency or buffered response is carried into the new operation.
    await h.reset()
    descriptor, regions, expected = h.matrix(5, 3, 9, 0x411)
    await h.prepare_matrix(descriptor, regions)
    ar_before = len(h.logs['ar'])
    await h.reg_write(0x10, 1)
    await h.until(lambda: h.value('done') or h.value('error'))
    assert h.value('done') and not h.value('error') and not h.value('reset_required')
    counters = await h.counters()
    assert counters[0] == h.logs['b'][-1][0]-h.logs['accepted'][-1]
    assert counters[1:5] == (((5+P-1)//P)*((3+P-1)//P)*(9+3*P-1), 16, 10, 60)
    assert [(fields[0], fields[1]+1) for _, fields in h.logs['ar'][ar_before:]] == expected_job_reads(descriptor)
    await h.check_matrix(descriptor, regions, expected)
    assert await h.counters() == counters
    save_coverage()
