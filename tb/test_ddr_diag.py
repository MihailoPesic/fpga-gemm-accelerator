"""DDR diagnostic tests through the production AXI engine and byte-addressed RAM."""
import json
import logging
import os
from pathlib import Path
import random

import cocotb
from cocotbext.axi import AxiBus, AxiRam
from common import tick

SLOTS = [0, 0x1000, 0x10000, 0x80000, 0x100000, 0x200000, 0x400000,
         0x800000, 0x1000000, 0x2000000, 0x4000000, 0x6000000, 0x7000000,
         0x7800000, 0x7f00000, 0x7ffff00]
MASKS = [0x0f, 0xf0, 0x55, 0xaa, 0x01, 0x80, 0x00, 0xff]
COVERAGE = {'successful_runs': 0, 'compared_read_words': 0, 'checked_write_words': 0,
            'faults': [], 'stalled_axi_cycles': {}, 'repeated_busy_start': 0}


def pattern(address, seed):
    rotated = ((address << 7) | (address >> 25)) & 0xffffffff
    low = rotated ^ (seed ^ 0xffffffff) ^ 0xa5c39e17
    high = address ^ seed ^ 0x3c6ef372
    return low | (high << 32)


def planned_writes(seed):
    commands = []
    for base in SLOTS:
        for offset in (0, 128):
            commands.append((base+offset, [(pattern(base+offset+8*i, seed), 0xff) for i in range(16)]))
    for slot, base in enumerate(SLOTS):
        commands.append((base+8, [(pattern(base+8+8*i, seed) ^ 0xffffffffffffffff,
                                  MASKS[(slot+i) % 8]) for i in range(slot+1)]))
    return commands


def memory_image(seed, overlays):
    image = {}
    for base in SLOTS:
        for offset in range(0, 256, 8):
            for lane, byte in enumerate(pattern(base+offset, seed).to_bytes(8, 'little')):
                image[base+offset+lane] = byte
    if overlays:
        for slot, base in enumerate(SLOTS):
            for beat in range(slot+1):
                for lane in range(8):
                    if MASKS[(slot+beat) % 8] & (1 << lane):
                        image[base+8+8*beat+lane] ^= 0xff
    return image


def pauses(seed):
    rng = random.Random(seed)
    while True:
        yield rng.randrange(4) == 0


class Harness:
    def __init__(self, dut, random_stalls=False):
        self.dut = dut
        dut.clk.value = 0
        dut.rst.value = 1
        self.ram = AxiRam(AxiBus.from_prefix(dut, 'm_axi'), dut.clk, dut.rst, size=2**27)
        self.ram.read_if.log.setLevel(logging.WARNING)
        self.ram.write_if.log.setLevel(logging.WARNING)
        channels = (self.ram.read_if.ar_channel, self.ram.read_if.r_channel,
                    self.ram.write_if.aw_channel, self.ram.write_if.w_channel,
                    self.ram.write_if.b_channel)
        if random_stalls:
            for index, channel in enumerate(channels):
                channel.set_pause_generator(pauses(32000+index))
        self.held = {}
        self.aw = self.ar = self.w = self.r = 0
        self.oracle = None

    def value(self, name):
        return int(getattr(self.dut, name).value)

    def put(self, **values):
        for name, value in values.items():
            getattr(self.dut, name).value = value

    async def reset(self):
        self.put(rst=1, start=0, ddr_ready=1, seed=0, inject_r_xor=0, inject_rresp=0, inject_bresp=0)
        for _ in range(4):
            await tick(self.dut)
        self.put(rst=0)
        self.held.clear()
        for _ in range(3):
            await self.step()
        assert not self.value('error') and not self.value('busy') and not self.value('done')

    def configure_oracle(self, seed):
        writes = planned_writes(seed)
        self.oracle = {'aw': [(addr, len(words)) for addr, words in writes],
                       'w': [(data, strobe, index == len(words)-1)
                             for _, words in writes for index, (data, strobe) in enumerate(words)],
                       'ar': [(base+offset, 16) for _ in range(2) for base in SLOTS for offset in (0, 128)],
                       'r': []}
        for overlays in (False, True):
            image = memory_image(seed, overlays)
            self.oracle['r'] += [int.from_bytes(bytes(image[base+offset+lane] for lane in range(8)), 'little')
                                 for base in SLOTS for offset in range(0, 256, 8)]
        self.aw = self.ar = self.w = self.r = 0

    async def step(self, **values):
        self.put(**values)
        def sample():
            result = {}
            for channel in ('aw', 'ar', 'w', 'r', 'b'):
                valid = self.value('m_axi_'+channel+'valid')
                result[channel+'valid'] = valid
                result[channel+'ready'] = self.value('m_axi_'+channel+'ready')
                fields = {'aw': ('addr', 'len'), 'ar': ('addr', 'len'),
                          'w': ('data', 'strb', 'last'), 'r': ('data',), 'b': ()}[channel]
                for field in fields:
                    result[channel+field] = self.value('m_axi_'+channel+field) if valid else 0
            return result
        signals = await tick(self.dut, sample)
        for channel, fields in (('aw', ('addr', 'len')), ('ar', ('addr', 'len')),
                                ('w', ('data', 'strb', 'last'))):
            payload = tuple(signals[channel+field] for field in fields)
            if channel in self.held:
                assert signals[channel+'valid'] and self.held[channel] == payload
            if signals[channel+'valid'] and not signals[channel+'ready']:
                self.held[channel] = payload
                COVERAGE['stalled_axi_cycles'][channel] = COVERAGE['stalled_axi_cycles'].get(channel, 0)+1
            else:
                self.held.pop(channel, None)
        for channel in ('aw', 'ar', 'w', 'r'):
            if signals[channel+'valid'] and signals[channel+'ready']:
                number = getattr(self, channel)
                if self.oracle is not None:
                    actual = ((signals[channel+'addr'], signals[channel+'len']+1) if channel in ('aw', 'ar')
                              else (signals['wdata'], signals['wstrb'], bool(signals['wlast'])) if channel == 'w'
                              else signals['rdata'])
                    assert number < len(self.oracle[channel]), (channel, number)
                    assert actual == self.oracle[channel][number], (channel, number, actual, self.oracle[channel][number])
                setattr(self, channel, number+1)
        return signals

    async def wait(self, condition, limit=16000):
        for _ in range(limit):
            if condition():
                return
            await self.step()
        raise AssertionError('Diagnostic did not reach expected state')

    async def pulse(self, seed):
        await self.step(start=1, seed=seed)
        await self.step(start=0)

    def snapshot(self):
        return tuple(self.value(name) for name in ('cycles', 'read_beats', 'write_beats',
                     'error_code', 'first_fail_addr', 'expected', 'actual'))

    async def finish_error(self, code):
        await self.wait(lambda: bool(self.value('error')))
        assert self.value('error_code') == code and not self.value('done')
        snapshot = self.snapshot()
        await self.wait(lambda: not self.value('busy'))
        for _ in range(10):
            await self.step(start=1, seed=0xffffffff)
        self.put(start=0)
        assert self.snapshot() == snapshot and self.value('error') and not self.value('busy')


@cocotb.test()
async def complete_memory_plan_and_repeated_runs(dut):
    h = Harness(dut, random_stalls=True)
    await h.reset()
    for seed in (0, 0x12345678, 0xffffffff):
        h.configure_oracle(seed)
        guards = {}
        for base in SLOTS:
            for address in (base-8, base+256):
                if 0 <= address < 2**27:
                    guards[address] = b'GUARD123'
                    h.ram.write(address, guards[address])
        await h.pulse(seed)
        assert h.value('busy') and not h.value('done')
        # Live input changes and an extra START cannot replace the active seed.
        for _ in range(10):
            await h.step()
        await h.step(start=1, seed=seed ^ 0xdeadbeef)
        await h.step(start=0)
        COVERAGE['repeated_busy_start'] += 1
        await h.wait(lambda: bool(h.value('done') or h.value('error')))
        assert not h.value('error'), h.snapshot()
        assert not h.value('busy')
        assert (h.aw, h.ar, h.w, h.r) == (48, 64, 648, 1024)
        assert (h.value('write_beats'), h.value('read_beats')) == (648, 1024)
        assert h.value('cycles') > 0
        image = memory_image(seed, True)
        for base in SLOTS:
            assert h.ram.read(base, 256) == bytes(image[base+i] for i in range(256))
        for address, expected_bytes in guards.items():
            assert h.ram.read(address, 8) == expected_bytes
        frozen = h.snapshot()
        for _ in range(10):
            await h.step()
        assert h.snapshot() == frozen and h.value('done')
        COVERAGE['successful_runs'] += 1
        COVERAGE['compared_read_words'] += h.r
        COVERAGE['checked_write_words'] += h.w


@cocotb.test()
async def response_errors_and_mismatch_details(dut):
    h = Harness(dut)
    for injected, value, code in (('inject_bresp', 2, 7), ('inject_rresp', 3, 7), ('inject_r_xor', 1, 0x100)):
        await h.reset()
        h.aw = h.ar = h.w = h.r = 0
        h.put(**{injected: value})
        await h.pulse(0x13579bdf)
        await h.finish_error(code)
        if injected == 'inject_r_xor':
            expected = pattern(0, 0x13579bdf)
            assert h.value('first_fail_addr') == 0
            assert h.value('expected') == expected and h.value('actual') == expected ^ 1
            assert h.value('read_beats') == 1
        elif injected == 'inject_rresp':
            assert h.value('read_beats') == 0
        assert h.aw == (1 if injected == 'inject_bresp' else 32)
        assert h.ar == (0 if injected == 'inject_bresp' else 1)
        COVERAGE['faults'].append(injected)


@cocotb.test()
async def final_read_word_must_pass_before_completion(dut):
    h = Harness(dut, random_stalls=True)
    seed = 0x2468ace0
    # The final word of the first burst must block the next command; the final
    # word of the whole job must block DONE. Inject only that accepted AXI beat.
    for word_index, address, writes, reads in ((15, 120, 32, 1),
                                              (1023, 0x07fffff8, 48, 64)):
        await h.reset()
        h.configure_oracle(seed)
        await h.pulse(seed)
        await h.wait(lambda: h.r == word_index)
        h.put(inject_r_xor=0x8000000000000001)
        await h.wait(lambda: h.r == word_index+1)
        h.put(inject_r_xor=0)
        for _ in range(100):
            assert not h.value('done'), 'DONE preceded the final read comparison'
            if h.value('error'):
                break
            await h.step()
        else:
            raise AssertionError('Corrupt final word did not produce an error')
        await h.finish_error(0x100)
        image = memory_image(seed, overlays=word_index == 1023)
        expected = int.from_bytes(bytes(image[address+i] for i in range(8)), 'little')
        assert h.value('first_fail_addr') == address
        assert h.value('expected') == expected
        assert h.value('actual') == expected ^ 0x8000000000000001
        assert h.value('read_beats') == word_index+1
        assert (h.aw, h.ar) == (writes, reads), 'Fault allowed a following command'
        COVERAGE['faults'].append('final_burst_word' if word_index == 15 else 'final_job_word')


@cocotb.test()
async def watchdog_and_calibration_preserve_pending_axi(dut):
    h = Harness(dut)
    await h.reset()
    h.ram.write_if.aw_channel.pause = True
    await h.pulse(91)
    await h.wait(lambda: bool(h.value('error')))
    assert h.value('error_code') == 9 and h.value('busy')
    frozen = h.snapshot()
    assert h.value('m_axi_awvalid')
    for _ in range(10):
        await h.step()
    assert h.value('m_axi_awvalid') and h.snapshot() == frozen
    h.ram.write_if.aw_channel.pause = False
    await h.finish_error(9)
    assert h.aw == 1
    COVERAGE['faults'].append('watchdog_stalled_aw')

    await h.reset()
    h.aw = h.ar = h.w = h.r = 0
    h.ram.read_if.ar_channel.pause = True
    await h.pulse(92)
    await h.wait(lambda: bool(h.value('m_axi_arvalid')))
    await h.step(ddr_ready=0)
    assert h.value('error') and h.value('error_code') == 10 and h.value('busy')
    for _ in range(10):
        await h.step()
    assert h.value('m_axi_arvalid')
    h.ram.read_if.ar_channel.pause = False
    await h.finish_error(10)
    assert h.ar == 1 and h.aw == 32
    COVERAGE['faults'].append('calibration_loss_stalled_ar')

    await h.reset()
    h.aw = h.ar = h.w = h.r = 0
    await h.step(ddr_ready=0, start=1)
    await h.step(start=0)
    assert h.value('error_code') == 10 and h.value('error') and not h.value('busy')
    assert not h.value('done') and h.aw == h.ar == 0
    COVERAGE['faults'].append('start_calibration_race')
    path = os.environ.get('GEMM_COVERAGE')
    if path:
        Path(path).write_text(json.dumps(COVERAGE, indent=2)+'\n')
