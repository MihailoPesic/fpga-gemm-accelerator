import json
import os
import random
from pathlib import Path

import cocotb

from common import matmul, pack, signed, tick

BOUNDARIES = (1, 2, 7, 8, 9, 31, 32, 33, 255, 256)
SEED = 20260914


@cocotb.test()
async def microtile_contract(dut):
    p = len(dut.a_vector) // 8
    rng = random.Random(SEED + p)
    coverage = {"p": p, "seed": SEED + p, "jobs": 0, "random_jobs": 0,
                "k": set(), "tails": set(), "reset_edges": [],
                "busy_start_rejected": 0, "invalid_rejected": 0}
    dut.clk.value = 0
    dut.rst.value = 1
    dut.start.value = 0
    dut.k.value = 0
    dut.rows.value = 0
    dut.cols.value = 0
    dut.a_vector.value = 0
    dut.bt_vector.value = 0
    await tick(dut)
    dut.rst.value = 0
    await tick(dut)

    def observe():
        return tuple(int(signal.value) for signal in
                     (dut.feed_valid, dut.feed_index, dut.drain_valid,
                      dut.drain_row, dut.drain_mask, dut.drain_data))

    async def job(k, rows, cols, kind="random", busy_start=False, reset_edge=None):
        # Fill even invalid lanes with arbitrary nonzero bytes to catch masking.
        a = [[rng.choice((-128, 127, -1, 1)) if kind == "extreme" else
              rng.randrange(-128, 128) for _ in range(k)] for _ in range(p)]
        bt = [[rng.choice((-128, 127, -1, 1)) if kind == "extreme" else
               rng.randrange(-128, 128) for _ in range(k)] for _ in range(p)]
        if kind == "zero":
            a = [[0] * k for _ in range(p)]
        elif kind == "positive_limit":
            a = [[-128] * k for _ in range(p)]
            bt = [[-128] * k for _ in range(p)]
        elif kind == "negative_limit":
            a = [[-128] * k for _ in range(p)]
            bt = [[127] * k for _ in range(p)]
        elif kind == "isolated":
            a = [[0] * k for _ in range(p)]
            bt = [[0] * k for _ in range(p)]
            r, c, index = rng.randrange(rows), rng.randrange(cols), rng.randrange(k)
            a[r][index], bt[c][index] = -128, 127
        golden = matmul(a, bt, rows, cols)
        assert int(dut.start_ready.value) == 1
        dut.start.value = 1
        dut.k.value, dut.rows.value, dut.cols.value = k, rows, cols
        await tick(dut)  # launch edge 0
        dut.start.value = 0
        assert int(dut.busy.value) == 1
        assert int(dut.done.value) == 0
        assert int(dut.cmd_error.value) == 0
        # Change the public config while busy: acceptance must have snapshotted it.
        dut.k.value, dut.rows.value, dut.cols.value = 0, 0, 0
        drained = []
        final_edge = k + 3 * p - 1
        for edge in range(1, final_edge + 1):
            if edge <= k:
                dut.a_vector.value = pack([row[edge - 1] for row in a])
                dut.bt_vector.value = pack([row[edge - 1] for row in bt])
            else:
                dut.a_vector.value = rng.getrandbits(p * 8)
                dut.bt_vector.value = rng.getrandbits(p * 8)
            dut.start.value = int(busy_start and edge == 2)
            if reset_edge == edge:
                dut.rst.value = 1
            feed, index, valid, row, mask, data = await tick(dut, observe)
            if reset_edge == edge:
                assert not int(dut.busy.value)
                assert not int(dut.done.value)
                assert not int(dut.drain_valid.value)
                dut.rst.value = 0
                dut.start.value = 0
                await tick(dut)
                coverage["reset_edges"].append(edge)
                return
            assert feed == (edge <= k), (p, k, edge, "feed valid")
            if feed:
                assert index == edge - 1, (p, k, edge, "feed index")
            expected_drain = edge >= k + 2 * p
            assert valid == expected_drain, (p, k, edge, "drain edge")
            if valid:
                expected_row = edge - (k + 2 * p)
                assert row == expected_row
                expected_mask = (1 << cols) - 1 if row < rows else 0
                assert mask == expected_mask, (p, k, row, mask, expected_mask)
                values = [signed((data >> (32 * c)) & 0xFFFFFFFF) for c in range(p)]
                expected = [golden[row][c] if row < rows and c < cols else 0
                            for c in range(p)]
                assert values == expected, (coverage["jobs"], p, k, rows, cols,
                                             edge, row, values, expected)
                drained.append(row)
            assert int(dut.done.value) == (edge == final_edge), (p, k, edge, "done")
            assert int(dut.busy.value) == (edge != final_edge)
            assert int(dut.cmd_error.value) == (busy_start and edge == 2)
        dut.start.value = 0
        assert drained == list(range(p))
        coverage["jobs"] += 1
        coverage["k"].add(k)
        coverage["tails"].add((rows, cols))
        coverage["busy_start_rejected"] += int(busy_start)

    # Illegal commands must stay idle, with no feed, drain or completion.
    for k, rows, cols in ((0, p, p), (257, p, p), (511, p, p),
                         (1, 0, p), (1, p, 0), (1, p + 1, p), (1, p, p + 1)):
        dut.start.value = 1
        dut.k.value, dut.rows.value, dut.cols.value = k, rows, cols
        await tick(dut)
        assert int(dut.cmd_error.value) == 1
        assert int(dut.busy.value) == int(dut.done.value) == 0
        assert int(dut.feed_valid.value) == int(dut.drain_valid.value) == 0
        coverage["invalid_rejected"] += 1
    dut.start.value = 0
    await tick(dut)

    for k in BOUNDARIES:
        await job(k, p, p, "extreme", busy_start=True)
        await job(k, 1, p - 1, "isolated")
    for rows in range(1, p + 1):
        for cols in range(1, p + 1):
            await job(7 + (rows + cols) % 3, rows, cols, "isolated")
    for kind in ("positive_limit", "negative_limit", "zero"):
        await job(256, p, p, kind)
    for edge in (1, 2, 9, 9 + 2 * p, 9 + 3 * p - 1):
        await job(9, p, p, reset_edge=edge)
        await job(1, p - 1, 1, "extreme")
    for _ in range(500):
        await job(rng.randrange(1, 257), rng.randrange(1, p + 1), rng.randrange(1, p + 1))
        coverage["random_jobs"] += 1
    # Observe idle after the final operation and rule out duplicated completion.
    for _ in range(4):
        await tick(dut)
        assert int(dut.done.value) == int(dut.feed_valid.value) == int(dut.drain_valid.value) == 0
    assert set(BOUNDARIES) <= coverage["k"]
    assert len(coverage["tails"]) == p * p
    coverage["k"] = sorted(coverage["k"])
    coverage["tails"] = sorted(coverage["tails"])
    Path(os.environ["GEMM_COVERAGE"]).write_text(json.dumps(coverage, indent=2) + "\n")
    dut._log.info("PASS: P=%d, %d jobs, 500 seeded random, every tail class", p, coverage["jobs"])
