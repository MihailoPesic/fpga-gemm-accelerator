import random
import cocotb
from common import pack, tick


@cocotb.test()
async def result_addressing(dut):
    p, t = len(dut.wr_mask), 1 << len(dut.wr_row)
    rng = random.Random(20261000+p+t)
    expected = [[[rng.getrandbits(32) for _ in range(t)] for _ in range(t)] for _ in range(2)]
    for name in ('clk', 'wr_en', 'wr_buf', 'wr_row', 'wr_group', 'wr_mask',
                 'wr_data', 'rd_en', 'rd_buf', 'rd_row', 'rd_pair'):
        getattr(dut, name).value = 0
    for buf in range(2):
        for row in range(t):
            for group in range(t//p):
                dut.wr_en.value = 1
                dut.wr_buf.value, dut.wr_row.value, dut.wr_group.value = buf, row, group
                dut.wr_mask.value = (1 << p)-1
                dut.wr_data.value = pack(expected[buf][row][group*p:group*p+p], 32)
                await tick(dut)
    dut.wr_en.value = 0

    async def read(buf, row, pair):
        dut.rd_en.value = 1
        dut.rd_buf.value, dut.rd_row.value, dut.rd_pair.value = buf, row, pair
        await tick(dut)
        assert int(dut.rd_data.value) == pack(expected[buf][row][pair*2:pair*2+2], 32)

    for buf in range(2):
        for row in range(t):
            for pair in range(t//2):
                await read(buf, row, pair)
    for _ in range(200):
        buf, row, group = rng.randrange(2), rng.randrange(t), rng.randrange(t//p)
        mask = rng.randrange(1 << p)
        values = [rng.getrandbits(32) for _ in range(p)]
        dut.wr_en.value = 1
        dut.wr_buf.value, dut.wr_row.value, dut.wr_group.value = buf, row, group
        dut.wr_mask.value, dut.wr_data.value = mask, pack(values, 32)
        for col in range(p):
            if mask & (1 << col):
                expected[buf][row][group*p+col] = values[col]
        await read(1-buf, rng.randrange(t), rng.randrange(t//2))
        dut.wr_en.value = 0
        for pair in range(t//2):
            await read(buf, row, pair)
    for buf in range(2):
        for row in range(t):
            for pair in range(t//2):
                await read(buf, row, pair)
    dut._log.info('PASS: P=%d T=%d result addressing, masks and independent ports', p, t)
