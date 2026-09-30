"""BRAM preview command checks with independent whole-matrix arithmetic."""
import json
import os
from pathlib import Path
import random
import struct

import cocotb
from cocotb.handle import Force, Release
from common import matmul, tick


@cocotb.test()
async def preview_commands(dut):
    rng = random.Random(20261001)
    coverage = dict(seed=20261001, jobs=0, output_values=0, rejected_commands=0,
                    stalled_response_cycles=0, reset_cases=0, protocol_faults=0, shapes=[])
    for name in ('clk', 'req_valid', 'req_opcode', 'req_length', 'req_payload', 'rsp_ready'):
        getattr(dut, name).value = 0

    async def reset():
        dut.rst.value = 1
        dut.req_valid.value = 0
        dut.rsp_ready.value = 0
        await tick(dut)
        dut.rst.value = 0
        await tick(dut)
        assert int(dut.req_ready.value)
        assert not int(dut.busy.value) and not int(dut.done.value) and not int(dut.error.value)
        coverage['reset_cases'] += 1

    await reset()

    async def request(opcode, payload=b'', expected=0, stall=None, declared=None):
        assert len(payload) <= 256
        assert int(dut.req_ready.value)
        dut.req_opcode.value = opcode
        dut.req_length.value = len(payload) if declared is None else declared
        dut.req_payload.value = int.from_bytes(payload, 'little')
        dut.req_valid.value = 1
        assert await tick(dut, lambda: int(dut.req_ready.value))
        dut.req_valid.value = 0
        # Header is captured at acceptance; the payload is held as contracted.
        dut.req_opcode.value = 255
        dut.req_length.value = 511
        for _ in range(150):
            if int(dut.rsp_valid.value):
                break
            assert not int(dut.req_ready.value)
            await tick(dut)
        else:
            raise AssertionError('command backend timeout')
        frozen = tuple(int(x.value) for x in (dut.rsp_status, dut.rsp_length, dut.rsp_payload))
        assert frozen[0] == expected, (opcode, payload.hex(), frozen[:2], expected)
        for _ in range(rng.randrange(5) if stall is None else stall):
            # A second request cannot be accepted while this reply is blocked.
            dut.req_valid.value = 1
            assert not int(dut.req_ready.value)
            await tick(dut)
            assert int(dut.rsp_valid.value)
            assert frozen == tuple(int(x.value) for x in (dut.rsp_status, dut.rsp_length, dut.rsp_payload))
            coverage['stalled_response_cycles'] += 1
        dut.req_valid.value = 0
        dut.rsp_ready.value = 1
        await tick(dut)
        dut.rsp_ready.value = 0
        assert not int(dut.rsp_valid.value) and int(dut.req_ready.value)
        dut.req_payload.value = 0
        assert 0 <= frozen[1] <= 240
        if expected:
            assert frozen[1:] == (0, 0)
            coverage['rejected_commands'] += 1
        return frozen[2].to_bytes(240, 'little')[:frozen[1]]

    async def read_reg(address, expected=0, stall=None):
        result = await request(2, struct.pack('<H', address), expected, stall)
        return int.from_bytes(result, 'little')

    async def write_reg(address, value, expected=0, stall=None):
        assert await request(3, struct.pack('<HI', address, value), expected, stall) == b''

    async def read_mem(address, length, expected=0, stall=None):
        return await request(4, struct.pack('<IH', address, length), expected, stall)

    async def write_mem(address, data, expected=0):
        assert await request(5, struct.pack('<IH', address, len(data)) + data, expected) == b''

    async def configure(m, n, k, job_id):
        for address, value in ((0x14, job_id), (0x18, m), (0x1c, n), (0x20, k)):
            await write_reg(address, value)

    async def counters():
        return (await read_reg(0x80) | (await read_reg(0x84) << 32),
                await read_reg(0x88) | (await read_reg(0x8c) << 32))

    async def load(a, bt):
        for base, rows in ((0, a), (0x2000, bt)):
            for row, values in enumerate(rows):
                data = bytes(x & 255 for x in values)
                data += bytes(rng.choice((1, 127, 128, 255)) for _ in range(-len(data) % 8))
                for offset in range(0, len(data), 240):
                    await write_mem(base + 256*row + offset, data[offset:offset+240])

    previous_counters = (0, 0)

    async def job(a, bt, job_id, do_load=True, hostile=False):
        nonlocal previous_counters
        m, n, k = len(a), len(bt), len(a[0])
        oracle = matmul(a, bt, m, n)
        if do_load:
            await load(a, bt)
        await configure(m, n, k, job_id)
        assert await counters() == previous_counters
        await write_reg(0x10, 1, stall=0)
        if hostile:
            assert int(dut.busy.value) and not int(dut.done.value)
            assert await counters() == previous_counters
            await write_reg(0x10, 1, 4)
            await write_reg(0x10, 2, 4)
            await write_reg(0x14, 0xdeadbeef, 4)
            await write_reg(0x18, 1, 4)
            await write_reg(0x3c, 0, 4)
            await write_mem(0, b'\0'*8, 4)
            await read_mem(0x4000, 8, 4)
            assert await read_reg(0x44) == job_id
            assert not int(dut.error.value)
        for _ in range(18000):
            if not int(dut.busy.value):
                break
            await tick(dut)
        else:
            raise AssertionError('matrix job timeout')
        assert int(dut.done.value) and not int(dut.error.value)
        assert await read_reg(0x0c) == 5  # READY + DONE; DDR_READY remains clear.
        assert await read_reg(0x44) == job_id
        tiles = ((m+3)//4)*((n+3)//4)
        previous_counters = (tiles*(k+15), tiles*(k+11))
        assert await counters() == previous_counters
        for row, values in enumerate(oracle):
            raw = await read_mem(0x4000 + row*128, ((n+1)//2)*8)
            assert list(struct.unpack('<'+'i'*n, raw[:4*n])) == values
            assert raw[4*n:] == b'\0'*(4*(n % 2))
        # Modifying future descriptor fields must not change completed C bounds.
        await write_reg(0x1c, 1)
        assert await read_mem(0x4000, ((n+1)//2)*8) == b''.join(struct.pack('<i', x) for x in oracle[0]) + b'\0'*(4*(n % 2))
        await write_reg(0x1c, n)
        if m < 32:
            await read_mem(0x4000 + m*128, 8, 2)
        if n < 31:
            await read_mem(0x4000 + ((n+1)//2)*8, 8, 2)
        await read_mem(0x4078, 16, 2)  # row crossing, also at full width
        assert await counters() == previous_counters
        coverage['jobs'] += 1
        coverage['output_values'] += m*n
        coverage['shapes'].append([m, n, k])

    nonce = 0xa5910083
    assert await request(1, struct.pack('<I', nonce)) == struct.pack('<III', nonce, 0x3142474e, 0x100)
    constants = {0: 0x3142474e, 4: 0x100, 8: 0x01002004, 0x0c: 1, 0x10: 0,
                 0x24: 0, 0x28: 0x2000, 0x2c: 0x4000, 0x30: 256, 0x34: 256,
                 0x38: 128, 0x3c: 0, 0x40: 0, 0x44: 0, 0x48: 100000000, 0x50: 0x12345678}
    for address, value in constants.items():
        assert await read_reg(address) == value
    for address in (0, 4, 8, 0x0c, 0x24, 0x28, 0x2c, 0x30, 0x34, 0x38,
                    0x40, 0x44, 0x48, 0x50, 0x80, 0x84, 0x88, 0x8c):
        await write_reg(address, 0, 1)
    for address in (1, 0x12, 0x4c, 0x54, 0x90, 0xb8, 0xfffc):
        await read_reg(address, 2)
        await write_reg(address, 0, 2)
    for value in (0, 3, 0xffffffff):
        await write_reg(0x10, value, 1)
    await write_reg(0x3c, 1, 1)
    await write_reg(0x3c, 0)
    for opcode, payload in ((1, b''), (1, b'12345'), (2, b'\0'), (2, b'\0'*3),
                            (3, b'\0'*5), (3, b'\0'*7), (4, b'\0'*5), (0xff, b'')):
        await request(opcode, payload, 1)
    await read_mem(0x4000, 8, 5)
    for m, n, k in ((0, 1, 1), (1, 0, 1), (1, 1, 0), (33, 1, 1), (1, 33, 1),
                    (1, 1, 257), (0x10000001, 1, 1), (1, 0xffffffff, 1), (1, 1, 0x10001)):
        await configure(m, n, k, 10)
        await write_reg(0x10, 1, 3)
        assert int(dut.error.value) and not int(dut.busy.value)
        assert await read_reg(0x40) == 3
        assert await read_reg(0x44) == 0
    await write_reg(0x10, 2)
    assert await read_reg(0x40) == 0 and not int(dut.error.value)

    for shape in ((1, 1, 1), (3, 5, 7), (5, 3, 9), (4, 4, 8), (9, 17, 33), (32, 32, 256)):
        m, n, k = shape
        a = [[rng.randrange(-128, 128) for _ in range(k)] for _ in range(m)]
        bt = [[rng.randrange(-128, 128) for _ in range(k)] for _ in range(n)]
        await job(a, bt, 100+coverage['jobs'], hostile=shape == (32, 32, 256))

    # Entire invalid writes must have zero effects, including valid prefixes.
    # Reuse the max-size operands; a row/region crossing would corrupt this result.
    for address in (1, 0xf8, 0x1ff8, 0x3ff8, 0x4000, 0xfffffff8):
        await write_mem(address, b'\0'*16, 2)
    for length in (0, 1, 7, 9, 241, 248, 65535):
        await request(5, struct.pack('<IH', 0, length) + b'\0'*8, 1)
        await read_mem(0x4000, length, 1)
    await request(5, struct.pack('<IH', 0, 16) + b'\0'*8, 1)  # truncated data
    await request(5, struct.pack('<IH', 0, 8) + b'\0'*16, 1)  # trailing data
    for address in (0, 0x2000, 0x4001, 0x5000, 0xfffffff8):
        await read_mem(address, 8, 2)
    await job(a, bt, 200, do_load=False)
    await job([[-128]*256 for _ in range(3)], [[-128]*256 for _ in range(5)], 201)
    for _ in range(12):
        m, n, k = rng.randrange(1, 33), rng.randrange(1, 33), rng.choice((1, 2, 7, 8, 9, 31, 32, 33, 255, 256))
        await job([[rng.randrange(-128, 128) for _ in range(k)] for _ in range(m)],
                  [[rng.randrange(-128, 128) for _ in range(k)] for _ in range(n)], 300+coverage['jobs'])

    # Clearing public DONE does not destroy completed data or counter snapshots.
    await write_reg(0x10, 2)
    assert not int(dut.done.value)
    assert await counters() == previous_counters
    await read_mem(0x4000, 8)

    # Inject an otherwise unreachable internal rejection during a legal job.
    # A failed job must not replace the previous successful snapshot or expose C.
    await load([[1]*256 for _ in range(4)], [[2]*256 for _ in range(4)])
    await configure(4, 4, 256, 998)
    await write_reg(0x10, 1, stall=0)
    assert int(dut.busy.value)
    dut.engine_error.value = Force(1)
    await tick(dut)
    dut.engine_error.value = Release()
    await tick(dut)
    assert await read_reg(0x0c) == 0x2a  # BUSY + ERROR + RESET_REQUIRED
    assert await read_reg(0x40) == 8
    await write_reg(0x10, 2, 4)
    for _ in range(300):
        if not int(dut.busy.value):
            break
        await tick(dut)
    assert not int(dut.busy.value) and not int(dut.done.value)
    assert await read_reg(0x0c) == 0x28
    assert await counters() == previous_counters
    await read_mem(0x4000, 8, 8)
    await write_mem(0, b'\0'*8, 8)
    await write_reg(0x10, 1, 8)
    await write_reg(0x10, 2, 8)
    await write_reg(0x18, 1, 8)
    await write_reg(0x3c, 0, 8)
    assert await request(1, struct.pack('<I', nonce)) == struct.pack('<III', nonce, 0x3142474e, 0x100)
    coverage['protocol_faults'] += 1
    await reset()
    previous_counters = (0, 0)
    assert await read_reg(0x0c) == 1
    await read_mem(0x4000, 8, 5)

    # Unexpected result-port failures also invalidate externally readable C.
    await job([[3]], [[-7]], 997)
    dut.engine_response_error.value = Force(1)
    await read_mem(0x4000, 8, 8)
    dut.engine_response_error.value = Release()
    await tick(dut)
    assert await read_reg(0x0c) == 0x28
    assert not int(dut.done.value)
    await read_mem(0x4000, 8, 8)
    coverage['protocol_faults'] += 1
    await reset()

    await configure(32, 32, 256, 999)
    await write_reg(0x10, 1, stall=0)
    assert int(dut.busy.value)
    await reset()
    assert await counters() == (0, 0)
    assert await read_reg(0x44) == 0
    await read_mem(0x4000, 8, 5)

    # Reset discards a pending backend response; no stale reply survives.
    dut.req_opcode.value = 1
    dut.req_length.value = 4
    dut.req_payload.value = nonce
    dut.req_valid.value = 1
    await tick(dut)
    dut.req_valid.value = 0
    await tick(dut)
    assert int(dut.rsp_valid.value)
    await reset()
    assert not int(dut.rsp_valid.value)
    assert await request(1, struct.pack('<I', nonce)) == struct.pack('<III', nonce, 0x3142474e, 0x100)

    target = os.environ.get('GEMM_COVERAGE')
    if target:
        Path(target).write_text(json.dumps(coverage, indent=2)+'\n')
    dut._log.info('Preview command coverage: %s', coverage)
