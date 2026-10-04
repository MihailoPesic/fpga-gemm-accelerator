"""Selectable overlap through real packet/control/DDR interfaces and AXI RAM."""
import struct

import cocotb

from test_ddr_core import (Harness, COVERAGE, P, T, VERSION, expected_job_reads, split_words,
                           save_coverage)


class OverlapHarness(Harness):
    def __init__(self, dut):
        super().__init__(dut)
        self.overlap_edges = 0

    async def step(self, byte=None, error=False):
        await super().step(byte, error)
        # Passive activity observation. Data, completion and traffic checks
        # below are derived from public bytes and AXI handshakes.
        if (self.value('busy') and not self.value('host_busy') and
                self.value('compute_busy') and
                (self.value('load_busy') or self.value('store_busy'))):
            self.overlap_edges += 1


@cocotb.test()
async def matched_packet_jobs_both_modes(dut):
    assert VERSION == 0x200
    h = OverlapHarness(dut)
    await h.reset()
    assert await h.exchange(1, struct.pack('<I', 123)) == struct.pack('<III', 123, 0x314d474e, VERSION)
    shapes = ((T+1, T+3, 9), (T-1, 2*T+1, 33), (1, T+1, 256), (2*T+1, T-1, 7))
    job = 0
    for m, n, k in shapes:
        descriptor, regions, expected = h.matrix(m, n, k, 0)
        matched_traffic = []
        for mode in (0, 1):
            job += 1
            descriptor.update(job_id=job, mode=mode)
            await h.prepare_matrix(descriptor, regions)
            ar_before = len(h.logs['ar'])
            overlap_before = h.overlap_edges
            await h.reg_write(0x10, 1)
            await h.until(lambda: h.value('done') or h.value('error'), limit=250000)
            assert h.value('done') and not h.value('busy') and not h.value('error')
            counters = await h.counters()
            assert counters[0] == h.logs['b'][-1][0]-h.logs['accepted'][-1]
            assert counters[1] == ((m+P-1)//P)*((n+P-1)//P)*(k+3*P-1)
            assert counters[2] == ((k+7)//8)*(m*((n+T-1)//T)+n*((m+T-1)//T))
            assert counters[3:5] == (m*((n+1)//2), 4*m*n)
            assert [(fields[0], fields[1]+1) for _, fields in h.logs['ar'][ar_before:]] == expected_job_reads(descriptor)
            if mode == 0:
                assert h.overlap_edges == overlap_before
            else:
                assert h.overlap_edges > overlap_before, (m, n, k)
            matched_traffic.append(counters[1:5])
            await h.check_matrix(descriptor, regions, expected)
            assert await h.counters() == counters
            assert await h.reg_read(0x44) == job and await h.reg_read(0x0c) == 0x15
        assert matched_traffic[0] == matched_traffic[1]
    COVERAGE['modes'] = [0, 1]
    COVERAGE['packet_overlap_activity_edges'] = h.overlap_edges
    save_coverage()


@cocotb.test()
async def overlap_busy_replay_and_delayed_final_response(dut):
    h = OverlapHarness(dut)
    await h.reset()
    descriptor, regions, expected = h.matrix(2*T+1, T+1, 33, 0xdecaf)
    descriptor['mode'] = 1
    await h.prepare_matrix(descriptor, regions)
    h.force_pause['b'] = True
    await h.idle(3)
    aw_before = len(h.logs['aw'])
    sequence = h.sequence+1
    await h.reg_write(0x10, 1, sequence=sequence)
    h.sequence = sequence
    await h.until(lambda: len(h.logs['aw']) > aw_before)
    await h.reg_write(0x10, 1, sequence=sequence)
    assert len(h.logs['accepted']) == 1
    COVERAGE['replays'] += 1
    await h.reg_write(0x10, 2, status=6, sequence=sequence)
    COVERAGE['sequence_conflicts'] += 1
    await h.reg_write(0x10, 1, status=4)
    await h.reg_write(0x3c, 0, status=4)
    await h.reg_write(0x10, 2, status=4)
    h.ram.write(0x600000, b'guarded!')
    await h.write(0x600000, b'noeffect', status=4)
    await h.read(0x600000, 8, status=4)
    assert h.ram.read(0x600000, 8) == b'guarded!'
    assert not h.value('host_busy') and h.value('busy') and not h.value('done')
    await h.reg_read(0x80, status=4)
    expected_bursts = sum(len(split_words(
        descriptor['c_base']+row*descriptor['c_stride']+4*j0,
        ((min(T, descriptor['n']-j0)+1)//2)*8))
        for row in range(descriptor['m']) for j0 in range(0, descriptor['n'], T))
    h.pause_final_b_at = aw_before+expected_bursts-1
    h.force_pause.pop('b')
    await h.until(lambda: h.pause_final_b_at is None, limit=250000)
    await h.idle(100)
    assert h.value('busy') and not h.value('done') and len(h.logs['b']) == len(h.logs['aw'])-1
    h.force_pause.pop('b')
    await h.until(lambda: h.value('done') or h.value('error'))
    assert h.value('done') and not h.value('busy') and not h.value('error')
    counters = await h.counters()
    assert counters[0] == h.logs['b'][-1][0]-h.logs['accepted'][-1]
    await h.check_matrix(descriptor, regions, expected)
    assert await h.counters() == counters
    await h.reg_write(0x10, 2)
    assert not h.value('done') and await h.reg_read(0x0c) == 0x11
    assert await h.counters() == counters
    COVERAGE['overlap_final_b_held_cycles'] = 100
    save_coverage()
