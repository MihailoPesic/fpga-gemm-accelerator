"""End-to-end preview checks via physical UART pins, independent of host code."""
import json
import os
from pathlib import Path
import struct

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, Timer
from test_packet_transport import cobs_encode, packet, raw_packet, response

BIT_NS = 200  # 5 Mbaud, twenty 100 MHz clock periods per bit.
BUILD_ID = 0x7139a2c5


class SerialLink:
    def __init__(self, dut):
        self.dut = dut
        self.replies = []
        self.next_sequence = 1
        self.monitor = None
        self.coverage = dict(rx_bytes=0, tx_bytes=0, commands=0, jobs=0,
                             checked_outputs=0, corrupt_frames=0, resets=0)

    async def watch_tx(self):
        frame = bytearray()
        while True:
            await FallingEdge(self.dut.tx)
            await Timer(BIT_NS//2, unit='ns')
            assert int(self.dut.tx.value) == 0, 'TX start bit did not remain low'
            value = 0
            for bit in range(8):
                await Timer(BIT_NS, unit='ns')
                value |= int(self.dut.tx.value) << bit
            await Timer(BIT_NS, unit='ns')
            assert int(self.dut.tx.value) == 1, 'TX stop bit is invalid'
            self.coverage['tx_bytes'] += 1
            if value:
                frame.append(value)
            else:
                assert frame, 'Empty TX packet'
                encoded = bytes(frame)
                self.replies.append((encoded, response(encoded)))
                frame.clear()
            await Timer(BIT_NS//2-1, unit='ns')

    async def watch_busy(self):
        while True:
            await RisingEdge(self.dut.busy)
            self.coverage['jobs'] += 1

    async def reset(self):
        if self.monitor is not None:
            self.monitor.cancel()
        self.dut.rx.value = 1
        self.dut.rst.value = 1
        await Timer(100, unit='ns')
        self.dut.rst.value = 0
        await Timer(103, unit='ns')
        self.monitor = cocotb.start_soon(self.watch_tx())
        self.coverage['resets'] += 1

    async def send_bytes(self, data, bad_stop=None, bit_ns=BIT_NS):
        # Start off a core clock edge. Per-byte gaps vary without changing 8N1.
        await Timer(3, unit='ns')
        for offset, value in enumerate(data):
            self.dut.rx.value = 0
            await Timer(bit_ns, unit='ns')
            for bit in range(8):
                self.dut.rx.value = (value >> bit) & 1
                await Timer(bit_ns, unit='ns')
            self.dut.rx.value = int(offset != bad_stop)
            await Timer(bit_ns, unit='ns')
            self.dut.rx.value = 1
            if offset == bad_stop:
                await Timer(bit_ns, unit='ns')
            await Timer(offset % 7 + 1, unit='ns')
            self.coverage['rx_bytes'] += 1

    async def exchange(self, opcode, payload=b'', status=0, sequence=None, bit_ns=BIT_NS):
        if sequence is None:
            sequence = self.next_sequence
            self.next_sequence += 1
        target = len(self.replies)+1
        await self.send_bytes(packet(opcode, sequence, payload), bit_ns=bit_ns)
        for _ in range(1000):
            if len(self.replies) >= target:
                break
            await Timer(BIT_NS*10, unit='ns')
        assert len(self.replies) == target, f'UART response timeout: opcode={opcode}, sequence={sequence}'
        encoded, (actual_opcode, actual_sequence, actual_status, data) = self.replies[-1]
        assert (actual_opcode, actual_sequence, actual_status) == (opcode | 0x80, sequence, status)
        self.coverage['commands'] += 1
        return data, encoded, sequence

    async def read_reg(self, address):
        data, _, _ = await self.exchange(2, struct.pack('<H', address))
        assert len(data) == 4
        return int.from_bytes(data, 'little')

    async def write_reg(self, address, value, **kwargs):
        result = await self.exchange(3, struct.pack('<HI', address, value), **kwargs)
        assert result[0] == b''
        return result


@cocotb.test()
async def uart_preview_matrix_and_recovery(dut):
    dut.clk.value = 0
    dut.rx.value = 1
    dut.rst.value = 1
    clock = cocotb.start_soon(Clock(dut.clk, 10, unit='ns').start())
    link = SerialLink(dut)
    await link.reset()
    busy_monitor = cocotb.start_soon(link.watch_busy())
    nonce = 0x00fe7a91
    data, _, _ = await link.exchange(1, struct.pack('<I', nonce), bit_ns=198)
    assert struct.unpack('<III', data) == (nonce, 0x3142474e, 0x100)
    assert await link.read_reg(0x50) == BUILD_ID
    assert await link.read_reg(0x48) == 100_000_000
    assert await link.read_reg(0x08) == 0x01002004
    assert await link.read_reg(0x0c) == 1

    # An invalid physical stop bit and a separately damaged CRC must not write M.
    bad_write = packet(3, 0x7123, struct.pack('<HI', 0x18, 32))
    count = len(link.replies)
    await link.send_bytes(bad_write, bad_stop=5)
    await Timer(100_000, unit='ns')
    assert len(link.replies) == count
    assert await link.read_reg(0x18) == 0
    raw = bytearray(raw_packet(3, 0x7124, struct.pack('<HI', 0x18, 32)))
    raw[-1] ^= 1
    count = len(link.replies)
    await link.send_bytes(cobs_encode(raw)+b'\0')
    await Timer(100_000, unit='ns')
    assert len(link.replies) == count
    assert await link.read_reg(0x18) == 0
    link.coverage['corrupt_frames'] = 2

    a = [[-128, 127, -1, 0, 2, -3, 4, 5, -6],
         [7, -8, 9, 10, -11, 12, 0, 14, -15],
         [-16, 17, 18, -19, 20, 21, -22, 23, 24]]
    bt = [[-128, -1, 2, 3, -4, 5, 6, -7, 8],
          [127, 0, -9, 10, 11, -12, 13, 14, -15],
          [1, -2, 3, -4, 5, -6, 7, -8, 9],
          [0, 0, 0, 0, 0, 0, 0, 0, -128],
          [-3, 5, -7, 9, -11, 13, -15, 17, -19]]
    for base, rows in ((0, a), (0x2000, bt)):
        for row_index, row in enumerate(rows):
            # Exercise a long packet and nonzero reduction padding on A row 0.
            length = 240 if base == 0 and row_index == 0 else 16
            values = bytes(value & 255 for value in row) + bytes([0xa5])*(length-len(row))
            data, _, _ = await link.exchange(5, struct.pack('<IH', base+row_index*256, length)+values)
            assert data == b''
    for address, value in ((0x14, 0xc0011234), (0x18, 3), (0x1c, 5), (0x20, 9)):
        await link.write_reg(address, value)
    _, first_encoded, start_sequence = await link.write_reg(0x10, 1)
    assert link.coverage['jobs'] == 1
    _, duplicate_encoded, _ = await link.write_reg(0x10, 1, sequence=start_sequence)
    assert duplicate_encoded == first_encoded
    assert link.coverage['jobs'] == 1, 'Duplicate START executed a second job'
    assert await link.read_reg(0x0c) == 5
    assert await link.read_reg(0x44) == 0xc0011234
    assert await link.read_reg(0x80) == 48
    assert await link.read_reg(0x84) == 0
    assert await link.read_reg(0x88) == 40
    assert await link.read_reg(0x8c) == 0
    for row_index, row in enumerate(a):
        data, _, _ = await link.exchange(4, struct.pack('<IH', 0x4000+128*row_index, 24))
        actual = struct.unpack('<6i', data)
        expected = tuple(sum(x*y for x, y in zip(row, column)) for column in bt)
        assert actual[:5] == expected
        assert actual[5] == 0, 'Odd output tail was not zero-filled'
        link.coverage['checked_outputs'] += 5
    assert await link.read_reg(0x80) == 48
    assert await link.read_reg(0x40) == 0

    # Reset during a partial UART frame clears the job and packet replay state.
    _, _, cached_sequence = await link.exchange(1, struct.pack('<I', 0x12345678))
    await link.send_bytes(packet(3, 0x7130, struct.pack('<HI', 0x18, 1))[:4])
    await link.reset()
    data, _, _ = await link.exchange(1, struct.pack('<I', 0x87654321), sequence=cached_sequence)
    assert struct.unpack('<III', data) == (0x87654321, 0x3142474e, 0x100)
    assert await link.read_reg(0x0c) == 1
    assert await link.read_reg(0x18) == 0
    assert await link.read_reg(0x80) == 0
    data, _, _ = await link.exchange(4, struct.pack('<IH', 0x4000, 8), status=5)
    assert data == b''
    assert link.coverage['jobs'] == 1
    path = os.environ.get('GEMM_COVERAGE')
    if path:
        Path(path).write_text(json.dumps(link.coverage, indent=2)+'\n')
    link.monitor.cancel()
    busy_monitor.cancel()
    clock.cancel()
