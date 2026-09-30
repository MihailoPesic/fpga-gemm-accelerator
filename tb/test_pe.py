import random

import cocotb

from common import signed, tick


@cocotb.test()
async def arithmetic_and_pipeline(dut):
    dut.clk.value = 0
    accumulated = 0
    pending = None
    cycles = 0

    async def step(a=0, b=0, av=0, bv=0, clear=0):
        nonlocal accumulated, pending, cycles
        dut.a.value = a & 255
        dut.b.value = b & 255
        dut.a_valid.value = av
        dut.b_valid.value = bv
        dut.clear.value = clear
        if clear:
            accumulated = 0
        elif pending is not None:
            accumulated += pending
        pending = a * b if av and bv and not clear else None
        await tick(dut)
        actual = signed(int(dut.sum.value))
        assert actual == accumulated, (cycles, a, b, actual, accumulated)
        assert signed(int(dut.a_out.value), 8) == a
        assert signed(int(dut.b_out.value), 8) == b
        assert int(dut.a_valid_out.value) == (av and not clear)
        assert int(dut.b_valid_out.value) == (bv and not clear)
        cycles += 1

    await step(clear=1)
    for a in range(-128, 128):
        await step(clear=1)
        for b in range(-128, 128):
            await step(a, b, 1, 1)
        await step()
        await step()

    # Full permitted reduction at both extrema and alternating signs.
    for kind in range(4):
        await step(clear=1)
        for k in range(256):
            a, b = [(-128, -128), (-128, 127), (0, -128),
                    (-128, 127 if k % 2 else -128)][kind]
            await step(a, b, 1, 1)
        await step()
        await step()

    rng = random.Random(20260914)
    for _ in range(2000):
        await step(rng.randrange(-128, 128), rng.randrange(-128, 128),
                   rng.randrange(2), rng.randrange(2), rng.randrange(13) == 0)
    # Clearing with a live multiply must discard that product.
    await step(-128, -128, 1, 1)
    await step(127, 127, 1, 1, clear=1)
    await step()
    await step()
    dut._log.info("PASS: 65536 signed pairs, extrema, 2000 bubbles/reset cycles")
