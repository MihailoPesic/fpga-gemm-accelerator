"""Disjoint bank access tests; the DDR scheduler remains serial."""
import json
import os
from pathlib import Path
import random

import cocotb

from common import matmul, pack, tick


class Engine:
    def __init__(self, dut):
        self.dut = dut
        self.p = len(dut.drain_mask)
        self.t = 1 << len(dut.load_q)
        self.rng = random.Random(20261003 + self.p * 100 + self.t)
        self.job = None
        self.offered = None
        self.saved = {}
        self.coverage = dict(p=self.p, t=self.t, seed=20261003 + self.p * 100 + self.t,
                             jobs=0, output_values=0, compute_load_edges=0,
                             compute_read_edges=0, compute_load_read_edges=0,
                             active_load_blocks=0, active_read_blocks=0,
                             launch_gap_blocks=0, stalled_response_edges=0,
                             disjoint_start_loads=0, disjoint_start_reads=0,
                             pending_disjoint_starts=0, held_disjoint_starts=0,
                             response_owned_start_blocks=0, resets=0)

    async def reset(self):
        for name in ('clk', 'load_valid', 'load_bt', 'load_buf', 'load_q', 'load_word',
                     'load_data', 'start', 'input_buf', 'output_buf', 'm', 'n', 'k',
                     'read_valid', 'read_buf', 'read_row', 'read_pair', 'response_ready'):
            getattr(self.dut, name).value = 0
        self.dut.rst.value = 1
        await tick(self.dut)
        self.job, self.offered = None, None
        self.saved.clear()
        self.dut.rst.value = 0
        await tick(self.dut)
        assert not int(self.dut.busy.value) and not int(self.dut.response_valid.value)
        assert not int(self.dut.result_valid.value)
        self.coverage['resets'] += 1

    def matrices(self, m, n, k):
        return ([[self.rng.randrange(-128, 128) for _ in range(k)] for _ in range(m)],
                [[self.rng.randrange(-128, 128) for _ in range(k)] for _ in range(n)])

    def response(self):
        return tuple(int(getattr(self.dut, name).value) for name in
                     ('response_valid', 'response_error', 'response_strb', 'response_data'))

    async def step(self):
        names = ('busy', 'start', 'start_ready', 'input_buf', 'output_buf',
                 'load_valid', 'load_ready', 'load_buf', 'read_valid', 'read_ready',
                 'read_buf', 'response_valid', 'response_ready', 'tile_start',
                 'result_write', 'result_write_row', 'col_group', 'drain_mask', 'drain_data')
        def observe():
            values = {name: int(getattr(self.dut, name).value) for name in names}
            values['response'] = self.response()
            return values
        s = await tick(self.dut, observe)
        load_fire = s['load_valid'] and s['load_ready']
        read_fire = s['read_valid'] and s['read_ready']
        start_fire = s['start'] and s['start_ready'] and self.offered is not None
        if s['response_valid'] and not s['response_ready']:
            assert self.response() == s['response'], 'Stalled response changed'
            self.coverage['stalled_response_edges'] += 1
        if s['busy']:
            self.coverage['compute_load_edges'] += int(load_fire)
            self.coverage['compute_read_edges'] += int(read_fire)
            self.coverage['compute_load_read_edges'] += int(load_fire and read_fire)
            assert self.job is not None
            if s['load_valid'] and s['load_buf'] == self.job['ib']:
                assert not s['load_ready'], 'Active operand buffer accepted a write'
                self.coverage['active_load_blocks'] += 1
                self.coverage['launch_gap_blocks'] += int(s['tile_start'])
            if s['read_valid'] and s['read_buf'] == self.job['ob']:
                assert not s['read_ready'], 'Active result buffer accepted a read'
                self.coverage['active_read_blocks'] += 1
            self.job['cycles'] += 1
            if s['result_write']:
                row, group = s['result_write_row'], s['col_group']
                for lane in range(self.p):
                    if s['drain_mask'] & (1 << lane):
                        col = group * self.p + lane
                        key = (row, col)
                        assert row < self.job['m'] and col < self.job['n'], key
                        assert key not in self.job['writes'], ('duplicate result', key)
                        actual = (s['drain_data'] >> (32 * lane)) & 0xffffffff
                        assert actual == self.job['golden'][row][col] & 0xffffffff, key
                        self.job['writes'].add(key)
            assert int(self.dut.job_cycles.value) == self.job['cycles']
            if not int(self.dut.busy.value):
                job = self.job
                count = ((job['m'] + self.p - 1) // self.p) * ((job['n'] + self.p - 1) // self.p)
                assert int(self.dut.done.value)
                assert job['cycles'] == count * (job['k'] + 3 * self.p + 3)
                assert int(self.dut.compute_cycles.value) == count * (job['k'] + 3 * self.p - 1)
                assert int(self.dut.microtiles.value) == count
                assert job['writes'] == {(r, c) for r in range(job['m']) for c in range(job['n'])}
                self.saved[job['ob']] = job['golden']
                self.coverage['jobs'] += 1
                self.coverage['output_values'] += job['m'] * job['n']
                self.job = None
        if start_fire:
            assert self.job is None and int(self.dut.busy.value)
            assert not int(self.dut.cmd_error.value)
            self.job, self.offered = self.offered, None
            self.saved.pop(self.job['ob'], None)
            self.coverage['disjoint_start_loads'] += int(load_fire)
            self.coverage['disjoint_start_reads'] += int(read_fire)
        return s

    def offer(self, a, bt, ib, ob):
        m, n, k = len(a), len(bt), len(a[0])
        self.offered = dict(m=m, n=n, k=k, ib=ib, ob=ob, cycles=0,
                            golden=matmul(a, bt, m, n), writes=set())
        self.dut.m.value, self.dut.n.value, self.dut.k.value = m, n, k
        self.dut.input_buf.value, self.dut.output_buf.value = ib, ob
        self.dut.start.value = 1

    async def start(self, a, bt, ib, ob):
        self.offer(a, bt, ib, ob)
        s = await self.step()
        assert s['start_ready']
        self.dut.start.value = 0

    async def finish(self):
        for _ in range(100000):
            if self.job is None:
                return
            await self.step()
        raise AssertionError('Local compute did not finish')

    async def load(self, buf, a, bt):
        for plane, matrix in enumerate((a, bt)):
            for q, row in enumerate(matrix):
                for word in range((len(row) + 7) // 8):
                    values = row[word * 8:word * 8 + 8]
                    values += [self.rng.choice((-128, 127, -1, 1)) for _ in range(8 - len(values))]
                    self.dut.load_valid.value = 1
                    self.dut.load_bt.value, self.dut.load_buf.value = plane, buf
                    self.dut.load_q.value, self.dut.load_word.value = q, word
                    self.dut.load_data.value = pack(values)
                    assert (await self.step())['load_ready']
        self.dut.load_valid.value = 0

    def request_read(self, buf, row=0, pair=0):
        self.dut.read_valid.value = 1
        self.dut.read_buf.value, self.dut.read_row.value, self.dut.read_pair.value = buf, row, pair
        matrix = self.saved.get(buf)
        if matrix is None or row >= len(matrix) or pair * 2 >= len(matrix[0]):
            return (1, 1, 0, 0)
        values = matrix[row][pair * 2:pair * 2 + 2]
        return (1, 0, 0xff if len(values) == 2 else 0x0f, pack(values, 32))

    async def read(self, buf, row=0, pair=0, stall=0):
        expected = self.request_read(buf, row, pair)
        assert (await self.step())['read_ready']
        self.dut.read_valid.value = 0
        self.dut.read_buf.value = 1 - buf
        await self.step()
        assert self.response() == expected
        for _ in range(stall):
            await self.step()
            assert self.response() == expected
        self.dut.response_ready.value = 1
        await self.step()
        self.dut.response_ready.value = 0
        assert not int(self.dut.response_valid.value)

    async def read_all(self, buf, stall=0):
        matrix = self.saved[buf]
        for row in range(len(matrix)):
            for pair in range((len(matrix[0]) + 1) // 2):
                await self.read(buf, row, pair, stall)

    def save(self, name):
        path = Path(os.environ['GEMM_COVERAGE_DIR']) / (name + '.json')
        path.write_text(json.dumps(self.coverage, indent=2) + '\n', encoding='utf-8')


@cocotb.test()
async def disjoint_load_compute_read(dut):
    e = Engine(dut)
    await e.reset()
    previous = e.matrices(3, 3, 9)
    await e.load(1, *previous)
    await e.start(*previous, 1, 0)
    await e.finish()
    active = e.matrices(e.t, e.t, 33)
    await e.load(0, *active)
    e.offer(*active, 0, 1)
    dut.load_valid.value = 1
    dut.load_buf.value, dut.load_data.value = 0, 0
    dut.read_valid.value, dut.read_buf.value = 1, 1
    s = await e.step()
    assert s['start_ready'] and not s['load_ready'] and not s['read_ready']
    dut.start.value = 0
    # The lower microtile engine is idle on this launch edge; the macrotile
    # still owns its buffers, so wrapper admission must protect them.
    await e.step()
    dut.load_valid.value, dut.read_valid.value = 0, 0
    # Exercise all three ports on one edge, with an inactive-buffer write.
    dut.load_valid.value, dut.load_buf.value, dut.load_bt.value = 1, 1, 0
    dut.load_q.value, dut.load_word.value, dut.load_data.value = 0, 0, pack([-128] * 8)
    await e.read(0, 0, 1, stall=7)  # Odd result tail, response held during compute.
    dut.load_valid.value = 0
    following = e.matrices(e.t - 1, min(e.p + 1, e.t), 17)
    await e.load(1, *following)
    assert e.job is not None, 'Inactive loading failed to overlap active compute'
    await e.read_all(0, stall=2)
    # Hold destructive requests to the active buffers through every remaining
    # launch gap and the final drain edge. Neither is allowed to handshake.
    dut.load_valid.value, dut.load_buf.value, dut.load_data.value = 1, 0, 0
    dut.read_valid.value, dut.read_buf.value = 1, 1
    await e.finish()
    dut.load_valid.value, dut.read_valid.value = 0, 0
    # The next job starts while a read of the preceding output is pending.
    expected = e.request_read(1)
    assert (await e.step())['read_ready']
    dut.read_valid.value = 0
    e.offer(*following, 1, 0)
    assert (await e.step())['start_ready']
    dut.start.value = 0
    assert e.response() == expected
    e.coverage['pending_disjoint_starts'] += 1
    for _ in range(5):
        await e.step()
        assert e.response() == expected
    dut.response_ready.value = 1
    await e.step()
    dut.response_ready.value = 0
    await e.read_all(1, stall=1)
    await e.finish()
    await e.read_all(0, stall=1)
    assert e.coverage['compute_load_read_edges'] > 0
    assert e.coverage['launch_gap_blocks'] > 0
    assert e.coverage['compute_read_edges'] > 0 and e.coverage['compute_load_edges'] > 0
    e.save('disjoint_load_compute_read')


@cocotb.test()
async def start_response_ownership(dut):
    e = Engine(dut)
    await e.reset()
    matrix = e.matrices(1, 3, 9)
    await e.load(0, *matrix)
    await e.load(1, *matrix)
    await e.start(*matrix, 0, 0)
    await e.finish()
    await e.read_all(0)
    # A pending read owns buffer 0; changing live read_buf must not redirect it.
    expected = e.request_read(0, 0, 1)
    assert (await e.step())['read_ready']
    dut.read_valid.value, dut.read_buf.value = 0, 1
    e.offer(*matrix, 0, 0)
    assert not (await e.step())['start_ready']
    assert int(dut.cmd_error.value) and e.response() == expected
    e.coverage['response_owned_start_blocks'] += 1
    for _ in range(4):
        assert not (await e.step())['start_ready']
        assert int(dut.cmd_error.value) and e.response() == expected
        e.coverage['response_owned_start_blocks'] += 1
    # Even the consume edge remains reserved. Reuse is accepted the next edge.
    dut.response_ready.value = 1
    assert not (await e.step())['start_ready']
    assert int(dut.cmd_error.value) and not int(dut.response_valid.value)
    e.coverage['response_owned_start_blocks'] += 1
    dut.response_ready.value = 0
    assert (await e.step())['start_ready']
    dut.start.value = 0
    await e.finish()
    await e.read_all(0)
    expected = e.request_read(0)
    assert (await e.step())['read_ready']
    dut.read_valid.value = 0
    await e.step()
    assert e.response() == expected
    # A held response from buffer 0 does not block compute into buffer 1.
    e.offer(*matrix, 1, 1)
    assert (await e.step())['start_ready']
    dut.start.value = 0
    e.coverage['held_disjoint_starts'] += 1
    await e.finish()
    assert e.response() == expected
    dut.response_ready.value = 1
    await e.step()
    dut.response_ready.value = 0
    await e.read_all(0)
    await e.read_all(1)
    # Invalid read replies also retain their accepted buffer until consumed.
    expected = e.request_read(1, e.t - 1)
    assert (await e.step())['read_ready']
    dut.read_valid.value = 0
    e.offer(*matrix, 1, 1)
    assert not (await e.step())['start_ready']
    assert e.response() == expected == (1, 1, 0, 0)
    dut.start.value = 0
    e.offered = None
    await e.reset()
    e.save('start_response_ownership')


@cocotb.test()
async def start_edge_and_reset(dut):
    e = Engine(dut)
    await e.reset()
    matrix = e.matrices(e.t - 1, e.t - 1, 9)
    await e.load(0, *matrix)
    await e.start(*matrix, 0, 0)
    await e.finish()
    await e.load(1, *matrix)
    # Accepted START, a load and a read use disjoint sets on the same edge.
    expected = e.request_read(0)
    dut.load_valid.value, dut.load_buf.value, dut.load_bt.value = 1, 0, 0
    dut.load_q.value, dut.load_word.value, dut.load_data.value = 0, 0, pack([127] * 8)
    e.offer(*matrix, 1, 1)
    s = await e.step()
    assert s['start_ready'] and s['load_ready'] and s['read_ready']
    dut.start.value = 0
    dut.load_valid.value, dut.read_valid.value = 0, 0
    await e.step()
    assert e.response() == expected
    await e.finish()
    assert e.response() == expected
    dut.response_ready.value = 1
    await e.step()
    dut.response_ready.value = 0
    await e.read_all(0)
    await e.read_all(1)
    # Reset invalidates a held read and both result-valid bits without clearing
    # BRAM. A subsequent job must load fresh operands and produce fresh data.
    expected = e.request_read(0)
    assert (await e.step())['read_ready']
    dut.read_valid.value = 0
    await e.step()
    assert e.response() == expected
    await e.reset()
    await e.read(0)
    await e.read(1)
    fresh = e.matrices(1, 1, 1)
    fresh[0][0][0], fresh[1][0][0] = -128, -128
    await e.load(0, *fresh)
    await e.start(*fresh, 0, 0)
    await e.finish()
    await e.read_all(0, stall=3)
    assert e.coverage['disjoint_start_loads'] == 1 and e.coverage['disjoint_start_reads'] == 1
    e.save('start_edge_and_reset')
