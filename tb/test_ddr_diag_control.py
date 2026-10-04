"""Exercise the diagnostic packet backend independently of the memory engine."""
import json
import os
from pathlib import Path
import struct

import cocotb
from common import tick


class Harness:
    def __init__(self, dut):
        self.dut = dut
        self.starts = 0
        self.commands = 0
        self.responses_stalled = 0

    def put(self, **values):
        for name, value in values.items():
            getattr(self.dut, name).value = value

    async def step(self):
        start = await tick(self.dut, lambda: int(self.dut.start.value))
        self.starts += start

    async def reset(self):
        for name in ('clk', 'req_valid', 'req_opcode', 'req_length', 'req_payload', 'rsp_ready',
                     'ddr_ready', 'busy', 'done', 'error', 'error_code', 'first_fail_addr',
                     'expected', 'actual', 'cycles', 'read_beats', 'write_beats'):
            getattr(self.dut, name).value = 0
        self.dut.rst.value = 1
        await tick(self.dut)
        self.dut.rst.value = 0
        await self.step()

    async def exchange(self, opcode, payload, status=0, expected=b'', mutate=None):
        self.put(req_valid=1, req_opcode=opcode, req_length=len(payload),
                 req_payload=int.from_bytes(payload, 'little'), rsp_ready=0)
        for _ in range(20):
            accepted = int(self.dut.req_ready.value)
            await self.step()
            if accepted:
                break
        else:
            raise AssertionError('request acceptance timeout')
        self.put(req_valid=0)
        for _ in range(20):
            if int(self.dut.rsp_valid.value):
                break
            await self.step()
        else:
            raise AssertionError('response timeout')
        snapshot = (int(self.dut.rsp_status.value), int(self.dut.rsp_length.value),
                    int(self.dut.rsp_payload.value))
        if mutate:
            self.put(**mutate)
        for _ in range(5):
            await self.step()
            assert int(self.dut.rsp_valid.value)
            assert snapshot == (int(self.dut.rsp_status.value), int(self.dut.rsp_length.value),
                                int(self.dut.rsp_payload.value)), 'response changed while stalled'
            self.responses_stalled += 1
        actual = snapshot[2].to_bytes(240, 'little')[:snapshot[1]]
        assert (snapshot[0], actual) == (status, expected), (opcode, payload.hex(), snapshot[0], actual.hex())
        self.put(rsp_ready=1)
        await self.step()
        self.put(rsp_ready=0)
        self.commands += 1

    async def read(self, address, value=0, status=0, mutate=None):
        await self.exchange(2, struct.pack('<H', address), status,
                            struct.pack('<I', value) if status == 0 else b'', mutate)

    async def write(self, address, value, status=0):
        await self.exchange(3, struct.pack('<HI', address, value), status)


@cocotb.test()
async def packet_validation_start_guard_and_snapshots(dut):
    h = Harness(dut)
    await h.reset()
    assert int(dut.seed.value) == 0x12345678
    await h.exchange(1, struct.pack('<I', 0x11223344), expected=struct.pack('<III', 0x11223344, 0x3144474e, 0x100))
    for address, value in ((0, 0x3144474e), (4, 0x100), (0x48, 100_000_000),
                           (0x50, 0x1234abcd), (0x14, 0x12345678), (0x10, 0), (0x0c, 0)):
        await h.read(address, value)
    await h.write(0x10, 1, status=5)
    assert h.starts == 0

    # Every wrong packet length is rejected before a seed write or START pulse.
    seed_before = int(dut.seed.value)
    for opcode, malformed in ((1, b''), (1, bytes(5)), (2, b'\x14'), (2, bytes(3)),
                               (3, struct.pack('<HI', 0x14, 0xdeadbeef)[:-1]),
                               (3, struct.pack('<HI', 0x10, 1)+b'\0'),
                               (4, bytes(6)), (5, bytes(14)), (0xff, bytes(256))):
        await h.exchange(opcode, malformed, status=1)
    assert int(dut.seed.value) == seed_before and h.starts == 0
    await h.read(0x15, status=2)
    await h.write(0x15, 0, status=2)
    await h.read(0x08, status=2)
    await h.write(0x08, 0, status=2)
    for address in (0, 4, 0x0c, 0x40, 0x48, 0x50, 0x60, 0x68, 0x6c,
                    0x70, 0x74, 0x80, 0x84, 0x90, 0x94, 0x98, 0x9c):
        await h.write(address, 0, status=1)
    for control in (0, 2, 3, 0xffffffff):
        await h.write(0x10, control, status=1)

    await h.write(0x14, 0xcafef00d)
    await h.read(0x14, 0xcafef00d)
    h.put(ddr_ready=1, done=1)
    await h.read(0x0c, 0x15)
    await h.write(0x10, 1)
    assert h.starts == 1
    # Delay core BUSY deliberately. Pending START must still block another
    # START, suppress old DONE, protect its seed, and block counter reads.
    await h.read(0x0c, 0x12)
    await h.write(0x10, 1, status=4)
    await h.write(0x14, 0, status=4)
    await h.read(0x80, status=4)
    assert h.starts == 1 and int(dut.seed.value) == 0xcafef00d
    h.put(busy=1, done=0)
    await h.step()
    for address in (0x60, 0x68, 0x6c, 0x70, 0x74, 0x80, 0x84, 0x90, 0x94, 0x98, 0x9c):
        await h.read(address, status=4)
    await h.read(0x10, 0)
    await h.read(0x0c, 0x12)

    h.put(busy=0, done=1, first_fail_addr=0x7fffff8, expected=0x1122334455667788,
          actual=0x99aabbccddeeff00, cycles=0x100000023, read_beats=0x200000400,
          write_beats=0x300000288)
    for address, value in ((0x60, 0x7fffff8), (0x68, 0x55667788), (0x6c, 0x11223344),
                           (0x70, 0xddeeff00), (0x74, 0x99aabbcc), (0x80, 0x23),
                           (0x84, 1), (0x90, 0x400), (0x94, 2), (0x98, 0x288), (0x9c, 3)):
        await h.read(address, value)
    await h.read(0x0c, 0x15, mutate={'busy': 1, 'done': 0})
    # Fatal diagnostics are frozen and readable even while AXI is still
    # draining; a stuck slave must not hide the first failing word forever.
    h.put(busy=1, done=0, error=1, error_code=0x0100)
    await h.read(0x0c, 0x3a)
    await h.read(0x60, 0x7fffff8)
    await h.read(0x68, 0x55667788)
    await h.read(0x80, 0x23)
    await h.write(0x10, 1, status=8)
    await h.write(0x14, 0, status=8)
    h.put(busy=0)
    await h.read(0x0c, 0x38)
    await h.read(0x40, 0x0100)
    await h.write(0x10, 1, status=8)
    await h.write(0x14, 0, status=8)
    await h.exchange(1, struct.pack('<I', 7), expected=struct.pack('<III', 7, 0x3144474e, 0x100))
    assert h.starts == 1

    # The core reports a START/calibration race as a fatal error. It must clear
    # pending state so the full frozen diagnostics remain readable afterwards.
    await h.reset()
    h.put(ddr_ready=1)
    await h.write(0x10, 1)
    assert h.starts == 2
    h.put(ddr_ready=0, error=1, error_code=10)
    await h.step()
    await h.read(0x0c, 0x28)
    await h.read(0x80, 0)
    await h.read(0x40, 10)
    await h.write(0x10, 1, status=8)
    path = os.environ.get('GEMM_COVERAGE')
    if path:
        Path(path).write_text(json.dumps({'commands': h.commands, 'starts': h.starts,
                                        'stalled_response_cycles': h.responses_stalled}, indent=2)+'\n')
