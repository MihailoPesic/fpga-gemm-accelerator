import json
import os
import random
from pathlib import Path
import cocotb
from common import matmul, pack, tick


@cocotb.test()
async def local_matrix_jobs(dut):
    t = 1 << len(dut.load_q)
    p = len(dut.drain_mask)
    rng = random.Random(20261030+p+t)
    coverage = dict(p=p, t=t, seed=20261030+p+t, jobs=0, output_values=0,
                    microtiles=0, invalid_starts=0, rejected_reads=0, stalled_responses=0,
                    resets=0, shapes=[])
    for name in ('clk', 'load_valid', 'load_bt', 'load_buf', 'load_q', 'load_word',
                 'load_data', 'start', 'input_buf', 'output_buf', 'm', 'n', 'k',
                 'read_valid', 'read_buf', 'read_row', 'read_pair', 'response_ready'):
        getattr(dut, name).value = 0
    dut.rst.value = 1
    await tick(dut)
    dut.rst.value = 0
    await tick(dut)

    def response():
        return tuple(int(s.value) for s in (dut.response_valid, dut.response_error,
                                          dut.response_strb, dut.response_data))

    async def read(buf, row, pair, values=None, stall=0, reject_start=False):
        dut.read_valid.value = 1
        dut.read_buf.value, dut.read_row.value, dut.read_pair.value = buf, row, pair
        assert await tick(dut, lambda: int(dut.read_ready.value))
        assert not int(dut.response_valid.value)
        dut.read_valid.value = 0
        dut.read_buf.value = 1-buf
        dut.read_row.value, dut.read_pair.value = t-1, t//2-1
        if reject_start:
            dut.m.value, dut.n.value, dut.k.value = 1, 1, 1
        dut.start.value = int(reject_start)
        await tick(dut)
        assert int(dut.cmd_error.value) == int(reject_start)
        dut.start.value = 0
        expected = (1, 1, 0, 0) if values is None else (
            1, 0, 0xff if len(values) == 2 else 0x0f, pack(values, 32))
        assert response() == expected, (buf, row, pair, response(), expected)
        for _ in range(stall):
            assert not int(dut.start_ready.value) and not int(dut.read_ready.value)
            await tick(dut)
            assert response() == expected
            coverage['stalled_responses'] += 1
        dut.response_ready.value = 1
        await tick(dut)
        dut.response_ready.value = 0
        assert not int(dut.response_valid.value)
        coverage['rejected_reads'] += int(values is None)

    async def load(buf, a, bt, k):
        for plane, data in enumerate((a, bt)):
            for q, row in enumerate(data):
                for word in range((k+7)//8):
                    values = row[word*8:word*8+8]
                    values += [rng.choice((-128, 127, -1, 1)) for _ in range(8-len(values))]
                    dut.load_valid.value = 1
                    dut.load_buf.value, dut.load_bt.value, dut.load_q.value = buf, plane, q
                    dut.load_word.value, dut.load_data.value = word, pack(values)
                    assert await tick(dut, lambda: int(dut.load_ready.value))
        dut.load_valid.value = 0

    def observe():
        return {name: int(getattr(dut, name).value) for name in (
            'tile_start', 'micro_ready', 'row_group', 'col_group', 'result_write',
            'result_write_row', 'drain_mask', 'drain_data', 'load_ready', 'read_ready')}

    saved = {}

    async def job(m, n, k, ib, ob, reset_edge=None, extremum=False):
        a = [[-128 if extremum else rng.randrange(-128, 128) for _ in range(k)] for _ in range(m)]
        bt = [[-128 if extremum else rng.randrange(-128, 128) for _ in range(k)] for _ in range(n)]
        golden = matmul(a, bt, m, n)
        await load(ib, a, bt, k)
        dut.m.value, dut.n.value, dut.k.value = m, n, k
        dut.input_buf.value, dut.output_buf.value = ib, ob
        dut.start.value = 1
        # Exercise start priority over load/read commands, then hold them busy.
        dut.load_valid.value, dut.read_valid.value = 1, 1
        dut.load_buf.value, dut.load_bt.value, dut.load_q.value = ib, 0, 0
        dut.load_word.value, dut.load_data.value = 0, 0
        dut.read_buf.value, dut.read_row.value, dut.read_pair.value = ob, 0, 0
        assert int(dut.start_ready.value)
        signals = await tick(dut, observe)
        assert not signals['load_ready'] and not signals['read_ready']
        assert int(dut.busy.value) and not int(dut.done.value)
        assert not (int(dut.result_valid.value) & (1 << ob))
        dut.start.value = 0
        dut.m.value, dut.n.value, dut.k.value = 1, 1, 1
        dut.input_buf.value, dut.output_buf.value = 1-ib, 1-ob
        count = ((m+p-1)//p)*((n+p-1)//p)
        final = count*(k+3*p+3)
        writes, launches = set(), []
        for edge in range(1, final+1):
            dut.start.value = int(edge == 2)
            dut.rst.value = int(edge == reset_edge)
            signals = await tick(dut, observe)
            if edge == reset_edge:
                assert not int(dut.busy.value) and not int(dut.done.value)
                assert not int(dut.result_valid.value) and not int(dut.response_valid.value)
                dut.rst.value, dut.start.value = 0, 0
                dut.load_valid.value, dut.read_valid.value = 0, 0
                await tick(dut)
                saved.clear()
                coverage['resets'] += 1
                return
            assert int(dut.cmd_error.value) == int(edge == 2)
            assert not signals['load_ready'] and not signals['read_ready']
            if signals['tile_start'] and signals['micro_ready']:
                launches.append((signals['row_group'], signals['col_group']))
            if signals['result_write']:
                row, group = signals['result_write_row'], signals['col_group']
                for lane in range(p):
                    if signals['drain_mask'] & (1 << lane):
                        col = group*p+lane
                        assert row < m and col < n, ('out-of-bounds write', row, col)
                        assert (row, col) not in writes, ('duplicate write', row, col)
                        actual = (signals['drain_data'] >> (32*lane)) & 0xffffffff
                        assert actual == golden[row][col] & 0xffffffff
                        writes.add((row, col))
            assert int(dut.busy.value) == int(edge != final)
            assert int(dut.done.value) == int(edge == final)
            assert int(dut.job_cycles.value) == edge
        dut.start.value = 0
        assert writes == {(r, c) for r in range(m) for c in range(n)}
        assert launches == [(r, c) for r in range((m+p-1)//p) for c in range((n+p-1)//p)]
        assert int(dut.microtiles.value) == count
        assert int(dut.compute_cycles.value) == count*(k+3*p-1)
        assert int(dut.result_valid.value) & (1 << ob)
        saved[ob] = golden
        # Both held host requests may complete on the first idle edge.
        accepted = await tick(dut, observe)
        assert accepted['load_ready'] and accepted['read_ready']
        dut.load_valid.value, dut.read_valid.value = 0, 0
        await tick(dut)
        assert response() == (1, 0, 0xff if n > 1 else 0x0f, pack(golden[0][:2], 32))
        dut.response_ready.value = 1
        await tick(dut)
        dut.response_ready.value = 0
        for row in range(m):
            for pair in range((n+1)//2):
                await read(ob, row, pair, golden[row][pair*2:pair*2+2], stall=rng.randrange(3))
        if m < t:
            await read(ob, m, 0)
        if n < t-1:
            await read(ob, 0, (n+1)//2)
        if 1-ob in saved:
            await read(1-ob, 0, 0, saved[1-ob][0][:2])
        assert int(dut.done.value) and int(dut.job_cycles.value) == final
        assert int(dut.compute_cycles.value) == count*(k+3*p-1)
        coverage['jobs'] += 1
        coverage['output_values'] += m*n
        coverage['microtiles'] += count
        coverage['shapes'].append([m, n, k])

    await read(0, 0, 0, stall=3, reject_start=True)
    for m, n, k in ((0, 1, 1), (1, 0, 1), (t+1, 1, 1), (1, t+1, 1), (1, 1, 0), (1, 1, 257)):
        dut.start.value = 1
        dut.m.value, dut.n.value, dut.k.value = m, n, k
        await tick(dut)
        assert int(dut.cmd_error.value) and not int(dut.busy.value)
        assert not int(dut.result_valid.value) and not int(dut.microtiles.value)
        coverage['invalid_starts'] += 1
    dut.start.value = 0
    shapes = [(1, 1, 1), (1, t, 256), (t, 1, 255), (p-1, min(p+1, t), 9),
              (min(p+1, t), p-1, 8), (t-1, t, 17), (t, t-1, 31),
              (t, t, 256), (t, t, 1), (min(p+1, t), min(p+1, t), 33)]
    for idx, shape in enumerate(shapes):
        await job(*shape, idx % 2, (idx//2) % 2, extremum=idx == 7)
    for edge in (1, 2, 4, 5, 17+2*p+4, 17+3*p+3):
        await job(p, p, 17, 0, 0, reset_edge=edge)
        await read(0, 0, 0)
        await job(1, 1, 1, 1, 1)
    for _ in range(25):
        await job(rng.randrange(1, t+1), rng.randrange(1, t+1),
                  rng.choice((1, 2, 7, 8, 9, 16, 17, 32, 33, 255, 256)),
                  rng.randrange(2), rng.randrange(2))
    # Reset discards a pending read and a stalled response, invalidating results.
    for wait in (0, 1):
        dut.read_valid.value = 1
        dut.read_buf.value, dut.read_row.value, dut.read_pair.value = 1, 0, 0
        assert await tick(dut, lambda: int(dut.read_ready.value))
        dut.read_valid.value = 0
        for _ in range(wait):
            await tick(dut)
        dut.rst.value = 1
        await tick(dut)
        dut.rst.value = 0
        await tick(dut)
        assert not int(dut.response_valid.value) and not int(dut.result_valid.value)
        assert not int(dut.done.value) and not int(dut.job_cycles.value)
        coverage['resets'] += 1
    Path(os.environ['GEMM_COVERAGE']).write_text(json.dumps(coverage, indent=2)+'\n')
    dut._log.info('PASS: P=%d T=%d, %d full jobs, %d outputs', p, t, coverage['jobs'], coverage['output_values'])
