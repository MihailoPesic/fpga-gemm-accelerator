import json
import os
import random
from pathlib import Path

import cocotb
from common import matmul, pack, signed, tick

BOUNDARIES = (1, 2, 7, 8, 9, 15, 16, 17, 31, 32, 33, 255, 256)


@cocotb.test()
async def banked_microtile_contract(dut):
    p = len(dut.drain_mask)
    t = 1 << len(dut.load_q)
    groups = t // p
    seed = 20260930 + p + t
    rng = random.Random(seed)
    coverage = dict(p=p, t=t, seed=seed, jobs=0, reset_edges=[],
                    bank_groups_checked=0, active_buffer_blocks=0,
                    concurrent_other_buffer_writes=0, invalid_commands=0)
    # Independent logical matrices. Padding remains arbitrary and nonzero.
    memory = [[[[rng.choice((-128, -1, 1, 127)) for _ in range(256)]
                 for _ in range(t)] for _ in range(2)] for _ in range(2)]
    for name in ('clk', 'start', 'load_valid', 'load_bt', 'load_buf',
                 'load_q', 'load_word', 'load_data', 'buffer_id',
                 'a_group', 'bt_group', 'k', 'rows', 'cols'):
        getattr(dut, name).value = 0
    dut.rst.value = 1
    await tick(dut)
    dut.rst.value = 0
    await tick(dut)

    async def write_word(buf, plane, q, word, values):
        dut.load_valid.value = 1
        dut.load_buf.value = buf
        dut.load_bt.value = plane
        dut.load_q.value = q
        dut.load_word.value = word
        dut.load_data.value = pack(values)
        ready = await tick(dut, lambda: int(dut.load_ready.value))
        assert ready
        dut.load_valid.value = 0

    for buf in range(2):
        for plane in range(2):
            for q in range(t):
                for word in range(32):
                    await write_word(buf, plane, q, word,
                                     memory[buf][plane][q][word*8:word*8+8])

    def observe():
        return tuple(int(s.value) for s in (dut.drain_valid, dut.drain_row,
                     dut.drain_mask, dut.drain_data, dut.load_ready))

    async def job(buf, ag, bg, k, rows, cols, write_mode=None, reset_edge=None):
        a = [row[:k] for row in memory[buf][0][ag*p:ag*p+p]]
        bt = [row[:k] for row in memory[buf][1][bg*p:bg*p+p]]
        golden = matmul(a, bt, rows, cols)
        assert int(dut.start_ready.value)
        dut.buffer_id.value, dut.a_group.value, dut.bt_group.value = buf, ag, bg
        dut.k.value, dut.rows.value, dut.cols.value = k, rows, cols
        dut.start.value = 1
        replacement = [rng.randrange(-128, 128) for _ in range(8)]
        if write_mode:
            dut.load_valid.value = 1
            dut.load_buf.value = buf if write_mode == 'active' else 1-buf
            dut.load_bt.value, dut.load_q.value, dut.load_word.value = 0, ag*p, 0
            dut.load_data.value = pack(replacement)
        _, _, _, _, ready = await tick(dut, observe)  # accepted wrapper start
        assert int(dut.busy.value) and not int(dut.cmd_error.value)
        if write_mode == 'active':
            assert ready == 0  # launch wins over same-edge write
        elif write_mode == 'other':
            assert ready == 1
            memory[1-buf][0][ag*p][:8] = replacement
            # Keep the same write asserted throughout compute; each acceptance
            # is harmless to the active buffer and still tests both ports.
        dut.start.value = 0
        dut.k.value, dut.rows.value, dut.cols.value = 0, 0, 0
        dut.buffer_id.value = 1-buf
        dut.a_group.value, dut.bt_group.value = 0, 0
        final = k + 3*p + 2  # three prefetch/launch edges before core edge 1
        drained = []
        for edge in range(1, final+1):
            dut.start.value = int(edge == 5)  # busy start must not disturb work
            dut.rst.value = int(edge == reset_edge)
            valid, row, mask, data, ready = await tick(dut, observe)
            if edge == reset_edge:
                assert not int(dut.busy.value) and not int(dut.done.value)
                assert not int(dut.drain_valid.value)
                dut.rst.value, dut.start.value, dut.load_valid.value = 0, 0, 0
                await tick(dut)
                coverage['reset_edges'].append(edge)
                return
            assert int(dut.cmd_error.value) == int(edge == 5)
            if write_mode == 'active':
                assert ready == 0
                coverage['active_buffer_blocks'] += 1
            elif write_mode == 'other':
                assert ready == 1
                coverage['concurrent_other_buffer_writes'] += 1
            assert valid == (edge >= 3+k+2*p), (p, t, k, edge, 'drain schedule')
            if valid:
                assert row == edge - (3+k+2*p)
                assert mask == ((1 << cols)-1 if row < rows else 0)
                values = [signed((data >> (32*c)) & 0xffffffff) for c in range(p)]
                expected = [golden[row][c] if row < rows and c < cols else 0
                            for c in range(p)]
                assert values == expected, (buf, ag, bg, k, row, values, expected)
                drained.append(row)
            assert int(dut.done.value) == int(edge == final)
            assert int(dut.busy.value) == int(edge != final)
        assert drained == list(range(p))
        dut.start.value = 0
        if write_mode == 'active':
            # The held request must finally handshake on the first idle edge.
            ready = await tick(dut, lambda: int(dut.load_ready.value))
            assert ready == 1
            memory[buf][0][ag*p][:8] = replacement
        dut.load_valid.value = 0
        coverage['jobs'] += 1

    invalid = [(0, p, p, 0, 0), (257, p, p, 0, 0),
               (1, 0, p, 0, 0), (1, p, 0, 0, 0),
               (1, p+1, p, 0, 0), (1, p, p+1, 0, 0)]
    if groups == 1:  # one group still has a one-bit port; value 1 is invalid
        invalid += [(1, p, p, 1, 0), (1, p, p, 0, 1)]
    for k, rows, cols, ag, bg in invalid:
        dut.start.value = 1
        dut.k.value, dut.rows.value, dut.cols.value = k, rows, cols
        dut.a_group.value, dut.bt_group.value = ag, bg
        await tick(dut)
        assert int(dut.cmd_error.value)
        assert not int(dut.busy.value) and not int(dut.done.value)
        coverage['invalid_commands'] += 1
    dut.start.value = 0
    await tick(dut)

    # Touch every bank word in both buffers and every A/BT group pairing.
    for buf in range(2):
        for ag in range(groups):
            for bg in range(groups):
                await job(buf, ag, bg, 256, p, p)
                coverage['bank_groups_checked'] += 1
    for k in BOUNDARIES:
        await job(k % 2, groups-1, 0, k, p, p, write_mode='other')
        await job(1-k % 2, 0, groups-1, k, 1, p-1, write_mode='active')
    for rows in range(1, p+1):
        for cols in range(1, p+1):
            await job(0, 0, 0, 9, rows, cols)
    for edge in (1, 2, 3, 4, 10, 3+17+2*p, 17+3*p+2):
        await job(1, groups-1, groups-1, 17, p, p, reset_edge=edge)
        await job(0, 0, 0, 1, 1, 1)
    for _ in range(50):
        await job(rng.randrange(2), rng.randrange(groups), rng.randrange(groups),
                  rng.randrange(1, 257), rng.randrange(1, p+1), rng.randrange(1, p+1))
    for _ in range(3):
        await tick(dut)
        assert not int(dut.done.value) and not int(dut.drain_valid.value)
    Path(os.environ['GEMM_COVERAGE']).write_text(json.dumps(coverage, indent=2)+'\n')
    dut._log.info('PASS: banked P=%d T=%d, %d completed microtiles', p, t, coverage['jobs'])
