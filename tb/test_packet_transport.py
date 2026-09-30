"""Transport oracle uses Python's CRC implementation and packet-level checks."""
import json
import os
from pathlib import Path
import random
import struct
import zlib

import cocotb
from common import tick


def cobs_encode(data):
    output = bytearray([0])
    code_at, code = 0, 1
    for value in data:
        if value:
            output.append(value)
            code += 1
            if code == 255:
                output[code_at] = code
                code_at, code = len(output), 1
                output.append(0)
        else:
            output[code_at] = code
            code_at, code = len(output), 1
            output.append(0)
    output[code_at] = code
    return bytes(output)


def cobs_decode(encoded):
    output, offset = bytearray(), 0
    while offset < len(encoded):
        code = encoded[offset]
        assert code and offset+code <= len(encoded)
        output.extend(encoded[offset+1:offset+code])
        offset += code
        if code < 255 and offset < len(encoded):
            output.append(0)
    return bytes(output)


def raw_packet(opcode, sequence, payload, version=1, length=None):
    body = struct.pack('<BBHH', version, opcode, sequence, len(payload) if length is None else length) + payload
    return body + struct.pack('<I', zlib.crc32(body))


def packet(opcode, sequence, payload=b'', **kwargs):
    return cobs_encode(raw_packet(opcode, sequence, payload, **kwargs)) + b'\0'


def response(encoded):
    decoded = cobs_decode(encoded)
    assert len(decoded) >= 12 and decoded[0] == 1
    assert zlib.crc32(decoded[:-4]) == int.from_bytes(decoded[-4:], 'little')
    version, opcode, sequence, length = struct.unpack('<BBHH', decoded[:6])
    assert len(decoded) == length+10 and length <= 242
    return opcode, sequence, int.from_bytes(decoded[6:8], 'little'), decoded[8:-4]


def backend(opcode, payload):
    if opcode == 0xfe:
        return 1, b''
    if opcode == 0x55:
        return 0, bytes(range(240))
    return 0, payload[::-1][:240]


class Harness:
    def __init__(self, dut):
        self.dut = dut
        self.rng = random.Random(712901)
        self.requests, self.replies = [], []
        self.tx_partial = bytearray()
        self.pending = None
        self.request_held = self.tx_held = self.active_request = None
        self.backend_enabled = True
        self.reported_response_length = None
        self.allow_request = self.allow_tx = True
        self.coverage = dict(requests=0, responses=0, stalled_tx_cycles=0,
                             stalled_request_cycles=0, malformed_frames=0, resets=0,
                             active_duplicate=0, collector_release_header=0,
                             collector_release_drop=0, oversized_backend_response=0)

    async def reset(self):
        for name in ('clk', 'rx_valid', 'rx_error', 'rx_data', 'tx_ready', 'req_ready',
                     'rsp_valid', 'rsp_status', 'rsp_length', 'rsp_payload'):
            getattr(self.dut, name).value = 0
        self.dut.rst.value = 1
        await tick(self.dut)
        self.dut.rst.value = 0
        await tick(self.dut)
        self.pending = self.request_held = self.tx_held = self.active_request = None
        self.tx_partial.clear()
        self.coverage['resets'] += 1

    async def step(self, byte=None, error=False):
        dut = self.dut
        dut.rx_valid.value = int(byte is not None)
        dut.rx_data.value = 0 if byte is None else byte
        dut.rx_error.value = int(error)
        dut.tx_ready.value = int(self.allow_tx and self.rng.randrange(4) != 0)
        dut.req_ready.value = int(self.allow_request and self.rng.randrange(3) != 0)
        dut.rsp_valid.value = 0
        if self.pending is not None:
            delay, status, data = self.pending
            if delay:
                self.pending = (delay-1, status, data)
            elif self.backend_enabled:
                dut.rsp_valid.value = 1
                dut.rsp_status.value = status
                dut.rsp_length.value = (len(data) if self.reported_response_length is None
                                        else self.reported_response_length)
                dut.rsp_payload.value = int.from_bytes(data, 'little')

        def observe():
            req = (int(dut.req_opcode.value), int(dut.req_length.value), int(dut.req_payload.value))
            return (int(dut.req_valid.value), int(dut.req_ready.value), req,
                    int(dut.rsp_valid.value), int(dut.rsp_ready.value),
                    int(dut.tx_valid.value), int(dut.tx_ready.value),
                    int(dut.tx_data.value) if int(dut.tx_valid.value) else 0)
        valid, ready, req, sv, sr, tv, tr, td = await tick(dut, observe)
        if self.request_held is not None:
            assert valid and req == self.request_held, 'Stalled backend request changed'
        self.request_held = req if valid and not ready else None
        if self.active_request is not None:
            assert req == self.active_request, 'Request changed before response acceptance'
        if valid and not ready:
            self.coverage['stalled_request_cycles'] += 1
        if valid and ready:
            assert self.pending is None and self.active_request is None
            opcode, length, value = req
            assert length <= 256
            payload = value.to_bytes(256, 'little')[:length]
            self.requests.append((opcode, payload))
            self.active_request = req
            status, data = backend(opcode, payload)
            self.pending = (self.rng.randrange(1, 40), status, data)
            self.coverage['requests'] += 1
        if sv and sr:
            assert self.pending is not None
            self.pending = self.active_request = None
        if self.tx_held is not None:
            assert tv and td == self.tx_held, 'Stalled TX byte changed'
        self.tx_held = td if tv and not tr else None
        if tv and not tr:
            self.coverage['stalled_tx_cycles'] += 1
        if tv and tr:
            if td:
                self.tx_partial.append(td)
            else:
                assert self.tx_partial, 'Empty response frame'
                encoded = bytes(self.tx_partial)
                self.replies.append((encoded, response(encoded)))
                self.tx_partial.clear()
                self.coverage['responses'] += 1

    async def send(self, data, error_at=None, gaps=True):
        for offset, value in enumerate(data):
            await self.step(value, error=offset == error_at)
            if gaps:
                for _ in range(self.rng.randrange(3)):
                    await self.step()

    async def idle(self, cycles=3000):
        for _ in range(cycles):
            await self.step()

    async def wait_reply(self, target):
        for _ in range(10000):
            if len(self.replies) >= target:
                return self.replies[target-1]
            await self.step()
        raise AssertionError(f'Response timeout, target={target}, received={len(self.replies)}')

    async def exchange(self, opcode, sequence, payload=b'', execute=True, status=None):
        req_count, reply_count = len(self.requests), len(self.replies)
        await self.send(packet(opcode, sequence, payload))
        encoded, actual = await self.wait_reply(reply_count+1)
        expected_status, expected_data = backend(opcode, payload)
        if status is not None:
            expected_status, expected_data = status, b''
        assert actual == (opcode | 0x80, sequence, expected_status, expected_data)
        assert len(self.requests) == req_count+int(execute)
        if execute:
            assert self.requests[-1] == (opcode, payload)
        return encoded


@cocotb.test()
async def framing_cache_and_adversarial_streams(dut):
    assert zlib.crc32(b'123456789') == 0xcbf43926
    h = Harness(dut)
    await h.reset()
    await h.exchange(1, 1)
    await h.exchange(0x55, 2, bytes(range(256)))
    await h.exchange(3, 3, b'\xa5'*256)
    await h.exchange(0xfe, 4, b'unknown opcode')
    for sequence, length in enumerate((1, 7, 8, 31, 240, 255, 256, 0), start=5):
        await h.exchange(2, sequence, bytes(h.rng.randrange(256) for _ in range(length)))
    for sequence in range(20, 45):
        length = h.rng.randrange(257)
        await h.exchange(h.rng.randrange(1, 0x70), sequence,
                         bytes(h.rng.randrange(256) for _ in range(length)))

    original = await h.exchange(3, 100, b'once\0only')
    for _ in range(3):
        assert await h.exchange(3, 100, b'once\0only', execute=False) == original
    await h.exchange(3, 100, b'changed', execute=False, status=6)
    await h.exchange(4, 100, b'once\0only', execute=False, status=6)
    await h.exchange(3, 100, b'', execute=False, status=6)
    assert await h.exchange(3, 100, b'once\0only', execute=False) == original
    await h.exchange(3, 101, b'new cache')
    await h.exchange(3, 100, b'once\0only')  # Only the immediately previous request is cached.

    good = packet(2, 200, b'no side effects')
    damaged_crc = bytearray(raw_packet(2, 200, b'no side effects'))
    damaged_crc[-1] ^= 0x80
    malformed = [b'\x05x\0', b'\x01\0', cobs_encode(bytes(damaged_crc))+b'\0',
                 packet(2, 200, b'x', version=2), packet(2, 200, b'x', length=2),
                 packet(2, 200, b'\x55'*257), b'\x01'*280+b'\0', b'\x55'*400+b'\0',
                 good[:-3]+b'\0', good[:5]+b'\0'+good[5:], good[:5]+good[6:],
                 good[:-1]+good]
    for data in malformed:
        counts = len(h.requests), len(h.replies)
        await h.send(data)
        await h.idle()
        assert (len(h.requests), len(h.replies)) == counts, data.hex()
        h.coverage['malformed_frames'] += 1
    for error_at in (0, 1, len(good)//2, len(good)-2, len(good)-1):
        counts = len(h.requests), len(h.replies)
        await h.send(good, error_at=error_at)
        await h.idle()
        assert (len(h.requests), len(h.replies)) == counts
        h.coverage['malformed_frames'] += 1
    await h.exchange(2, 201, b'parser recovered')

    # Withhold request acceptance and TX readiness for long intervals.
    h.allow_request = False
    await h.send(packet(3, 202, b'hold me'))
    await h.idle(1200)
    assert int(dut.req_valid.value)
    h.allow_tx = False
    h.allow_request = True
    target = len(h.replies)+1
    await h.idle(1200)
    assert int(dut.tx_valid.value)
    h.allow_tx = True
    await h.wait_reply(target)

    # Keep collecting one bounded pending frame during a slow backend command.
    h.backend_enabled = False
    first_request = len(h.requests)
    first_reply = len(h.replies)
    await h.send(packet(3, 203, b'active'))
    for _ in range(2000):
        if len(h.requests) > first_request:
            break
        await h.step()
    assert len(h.requests) == first_request+1
    await h.send(packet(3, 204, b'pending'), gaps=False)
    await h.send(packet(3, 205, b'excess must drop'), gaps=False)
    h.backend_enabled = True
    await h.wait_reply(first_reply+2)
    await h.idle()
    assert len(h.requests) == first_request+2
    assert [r[1][1] for r in h.replies[-2:]] == [203, 204]
    await h.exchange(3, 205, b'retry after drop')

    # A retry received before the original backend response must execute once
    # and replay that response after the first exchange completes.
    h.backend_enabled = False
    first_request, first_reply = len(h.requests), len(h.replies)
    duplicate = packet(3, 210, b'active exact duplicate')
    await h.send(duplicate)
    for _ in range(2000):
        if len(h.requests) > first_request:
            break
        await h.step()
    assert len(h.requests) == first_request+1
    await h.send(duplicate, gaps=False)
    h.backend_enabled = True
    await h.wait_reply(first_reply+2)
    await h.idle(100)
    assert len(h.requests) == first_request+1, 'Active duplicate executed twice'
    assert h.replies[-2] == h.replies[-1], 'Active duplicate did not replay identical bytes'
    h.coverage['active_duplicate'] += 1

    # Once an excess frame has begun while the collector is locked, releasing
    # the previous frame must not let its remaining suffix become a new frame.
    # Exercise release from both successful COBS decoding and an early drop.
    for locked, executes, name, sequence in (
        (packet(3, 220, b'locked until header'), True, 'header', 221),
        (b'\x05x\0', False, 'drop', 222),
    ):
        first_request, first_reply = len(h.requests), len(h.replies)
        excess = packet(3, sequence, b'excess crossing the release edge')
        await h.send(locked, gaps=False)
        await h.step(excess[0])
        assert int(dut.dropping.value), 'Excess frame did not begin while locked'
        for _ in range(1000):
            if int(dut.release_frame.value):
                break
            await h.step()
        assert int(dut.release_frame.value) and int(dut.dropping.value)
        await h.send(excess[1:], gaps=False)
        if executes:
            await h.wait_reply(first_reply+1)
        await h.idle(1000)
        assert len(h.requests) == first_request+int(executes), 'Released collector accepted excess suffix'
        assert len(h.replies) == first_reply+int(executes)
        h.coverage[f'collector_release_{name}'] += 1
        await h.exchange(3, sequence, b'excess crossing the release edge')

    # Fault injection at the backend boundary: neither 241 nor 256 declared
    # bytes may overrun the 240-byte response store or produce malformed COBS.
    for sequence, length in ((230, 241), (231, 256)):
        h.reported_response_length = length
        encoded = await h.exchange(3, sequence, b'backend length fault', status=1)
        h.reported_response_length = None
        assert await h.exchange(3, sequence, b'backend length fault', execute=False, status=1) == encoded
        h.coverage['oversized_backend_response'] += 1

    # Reset abandons incomplete RX, backend and TX state and clears replay cache.
    await h.send(packet(3, 300, b'partial')[:5])
    await h.reset()
    await h.exchange(3, 300, b'after RX reset')
    await h.exchange(3, 300, b'after RX reset', execute=False)
    await h.reset()
    await h.exchange(3, 300, b'after RX reset')
    h.backend_enabled = False
    await h.send(packet(3, 301, b'waiting'))
    await h.idle(1000)
    assert h.pending is not None and h.active_request is not None
    assert not int(dut.req_valid.value), 'Accepted request was issued again before reset'
    await h.reset()
    h.backend_enabled = True
    await h.exchange(3, 302, b'after backend reset')
    h.allow_tx = False
    await h.send(packet(3, 303, b'unsent reply'))
    await h.idle(1500)
    assert int(dut.tx_valid.value)
    await h.reset()
    h.allow_tx = True
    await h.exchange(3, 303, b'after TX reset')
    await h.idle(100)
    assert not int(dut.req_valid.value) and not int(dut.tx_valid.value)
    path = os.environ.get('GEMM_COVERAGE')
    if path:
        Path(path).write_text(json.dumps(h.coverage, indent=2)+'\n')
