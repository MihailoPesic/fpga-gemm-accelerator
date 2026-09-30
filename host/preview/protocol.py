"""Bounded COBS/CRC transport used by the BRAM preview."""

from collections import deque
from dataclasses import dataclass
import secrets
import struct
import time
import zlib


VERSION = 1
MAX_PAYLOAD = 256
MAX_ENCODED = 269  # Excludes delimiter; receive storage is bounded to 270 bytes.
HEADER = struct.Struct("<BBHH")
STATUS_NAMES = {
    0: "OK", 1: "BAD_CMD", 2: "BAD_ADDR", 3: "BAD_DESC", 4: "BUSY",
    5: "NOT_READY", 6: "SEQ_CONFLICT", 7: "MEM_RESP", 8: "PROTOCOL",
    9: "WATCHDOG", 10: "CALIB_LOST",
}


class ProtocolError(RuntimeError):
    """Malformed frame or an unexpected response."""


class TransportError(RuntimeError):
    """The request outcome is uncertain; reconnect and reload inputs."""


class DeviceError(RuntimeError):
    def __init__(self, status, opcode):
        self.status = status
        self.opcode = opcode
        super().__init__(f"opcode 0x{opcode:02x}: {STATUS_NAMES.get(status, status)}")


@dataclass(frozen=True)
class Packet:
    opcode: int
    sequence: int
    payload: bytes


def cobs_encode(data):
    """Encode bytes without a delimiter. A terminal empty block is permitted."""
    result = bytearray(b"\x00")
    code_index, code = 0, 1
    for value in data:
        if value == 0:
            result[code_index] = code
            code_index = len(result)
            result.append(0)
            code = 1
        else:
            result.append(value)
            code += 1
            if code == 255:
                result[code_index] = code
                code_index = len(result)
                result.append(0)
                code = 1
    result[code_index] = code
    return bytes(result)


def cobs_decode(data):
    if not data or 0 in data:
        raise ProtocolError("empty COBS encoding or embedded delimiter")
    result = bytearray()
    index = 0
    while index < len(data):
        code = data[index]
        index += 1
        end = index + code - 1
        if end > len(data):
            raise ProtocolError("truncated COBS block")
        result.extend(data[index:end])
        index = end
        if code != 255 and index < len(data):
            result.append(0)
    return bytes(result)


def encode_packet(opcode, sequence, payload=b""):
    payload = bytes(payload)
    if not 0 <= opcode <= 255 or not 0 <= sequence <= 65535:
        raise ValueError("opcode/sequence out of range")
    if len(payload) > MAX_PAYLOAD:
        raise ValueError("payload exceeds 256 bytes")
    raw = HEADER.pack(VERSION, opcode, sequence, len(payload)) + payload
    return cobs_encode(raw + struct.pack("<I", zlib.crc32(raw))) + b"\x00"


def decode_packet(encoded):
    if len(encoded) > MAX_ENCODED:
        raise ProtocolError("oversized frame")
    raw = cobs_decode(encoded)
    if len(raw) < HEADER.size + 4:
        raise ProtocolError("short packet")
    version, opcode, sequence, length = HEADER.unpack_from(raw)
    if version != VERSION or length > MAX_PAYLOAD or len(raw) != 10 + length:
        raise ProtocolError("invalid version or payload length")
    if zlib.crc32(raw[:-4]) != struct.unpack_from("<I", raw, len(raw) - 4)[0]:
        raise ProtocolError("CRC mismatch")
    return Packet(opcode, sequence, raw[6:-4])


class FrameDecoder:
    """Discard malformed/oversized frames through the next zero delimiter."""

    def __init__(self):
        self.buffer = bytearray()
        self.discarding = False
        self.rejected = 0

    def feed(self, data):
        packets = []
        for value in data:
            if value == 0:
                if self.discarding:
                    self.discarding = False
                elif self.buffer:
                    try:
                        packets.append(decode_packet(self.buffer))
                    except ProtocolError:
                        self.rejected += 1
                self.buffer.clear()
            elif not self.discarding:
                if len(self.buffer) == MAX_ENCODED:
                    self.buffer.clear()
                    self.discarding = True
                    self.rejected += 1
                else:
                    self.buffer.append(value)
        return packets


class Link:
    """One outstanding command, with byte-identical retries in one session.

    `serial_port` needs read(size) and write(bytes); reads must have a short
    finite timeout. Exhausting retries poisons this Link: its last command may
    have executed, so a new session must identify the device and reload inputs.
    """

    def __init__(self, serial_port, timeout=2.0, retries=2, sequence=None):
        if timeout <= 0 or retries < 0:
            raise ValueError("positive timeout and nonnegative retries required")
        self.serial = serial_port
        self.timeout = timeout
        self.retries = retries
        self.sequence = secrets.randbelow(65536) if sequence is None else sequence
        if not 0 <= self.sequence <= 65535:
            raise ValueError("sequence out of range")
        self.decoder = FrameDecoder()
        self.pending = deque()
        self.poisoned = False
        self.active = False
        self.retry_count = 0

    def request(self, opcode, payload=b""):
        if self.poisoned:
            raise TransportError("connection outcome uncertain: reconnect, identify, reload")
        if self.active:
            raise RuntimeError("only one command may be outstanding")
        sequence = self.sequence
        # A leading delimiter resynchronizes a previously truncated frame.
        wire = b"\x00" + encode_packet(opcode, sequence, payload)
        self.sequence = (self.sequence + 1) & 65535
        self.active = True
        try:
            for attempt in range(self.retries + 1):
                if attempt:
                    self.retry_count += 1
                deadline = time.monotonic() + self.timeout
                sent = 0
                while sent < len(wire):
                    count = self.serial.write(wire[sent:])
                    if not count or time.monotonic() >= deadline:
                        raise TransportError("UART write did not complete")
                    sent += count
                while time.monotonic() < deadline:
                    while self.pending:
                        response = self.pending.popleft()
                        if response.sequence != sequence:
                            continue
                        if response.opcode != (opcode | 0x80):
                            raise ProtocolError("response opcode does not match request")
                        if not 2 <= len(response.payload) <= 242:
                            raise ProtocolError("invalid response length")
                        status = struct.unpack_from("<H", response.payload)[0]
                        if status:
                            raise DeviceError(status, opcode)
                        return response.payload[2:]
                    # Read already buffered bytes without waiting to fill a
                    # 270-byte request when a short register response arrives.
                    available = max(1, min(270, getattr(self.serial, "in_waiting", 1)))
                    chunk = self.serial.read(available)
                    if chunk:
                        self.pending.extend(self.decoder.feed(chunk))
            raise TransportError(
                f"opcode 0x{opcode:02x} timed out; outcome uncertain, reconnect and reload"
            )
        except DeviceError:
            raise
        except (OSError, ProtocolError, TransportError) as exc:
            self.poisoned = True
            if isinstance(exc, OSError):
                raise TransportError(f"UART I/O failed: {exc}") from exc
            raise
        finally:
            self.active = False
