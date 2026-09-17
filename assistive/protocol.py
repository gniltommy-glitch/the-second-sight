"""Wire protocol of the supplied, unmodified STM32 ``firmware/app.c``.

All integer fields are little endian. CRC is CRC-16/CCITT-FALSE over
``type + sequence + payload_length + payload`` (the sync bytes are excluded).
"""

from __future__ import annotations

import binascii
import struct
import time
from dataclasses import dataclass

SYNC = b"\xa5\x5a"
MAX_PAYLOAD = 1024
FRAME_OVERHEAD = 9
MSG_START, MSG_PCM, MSG_STOP = 0x10, 0x11, 0x12
MSG_BUTTON_ACK = 0x20
MSG_ACK, MSG_BUTTON, MSG_TOF, MSG_STATUS, MSG_DONE, MSG_BOOT = range(0x80, 0x86)
ACK_OK, ACK_BUSY, ACK_BAD, ACK_NO_AUDIO = range(4)


@dataclass(frozen=True)
class Packet:
    type: int
    seq: int
    payload: bytes


def crc16(data: bytes | bytearray) -> int:
    return binascii.crc_hqx(data, 0xFFFF)


def encode_packet(message_type: int, seq: int, payload: bytes = b"") -> bytes:
    if not 0 <= message_type <= 255 or not 0 <= seq <= 65535:
        raise ValueError("message type or sequence is out of range")
    if len(payload) > MAX_PAYLOAD:
        raise ValueError("STM32 payload cannot exceed 1024 bytes")
    body = struct.pack("<BHH", message_type, seq, len(payload)) + bytes(payload)
    return SYNC + body + struct.pack("<H", crc16(body))


class PacketParser:
    """Incremental parser with bounded memory, CRC recovery and inter-byte expiry."""

    def __init__(self, inter_byte_timeout: float = 0.1):
        if inter_byte_timeout <= 0:
            raise ValueError("inter_byte_timeout must be positive")
        self.inter_byte_timeout = inter_byte_timeout
        self._buffer = bytearray()
        self._last_rx: float | None = None
        self.crc_errors = 0
        self.length_errors = 0

    @property
    def buffered_bytes(self) -> int:
        return len(self._buffer)

    def feed(self, data: bytes, now: float | None = None) -> list[Packet]:
        now = time.monotonic() if now is None else now
        if self._last_rx is not None and now - self._last_rx > self.inter_byte_timeout:
            self._buffer.clear()
        if not data:
            return []
        self._last_rx = now
        result = []
        # Even a caller supplying megabytes cannot grow the working buffer without bound.
        stride = MAX_PAYLOAD + FRAME_OVERHEAD
        for offset in range(0, len(data), stride):
            self._buffer.extend(data[offset:offset + stride])
            while self._buffer:
                start = self._buffer.find(SYNC)
                if start < 0:
                    self._buffer[:] = b"\xa5" if self._buffer[-1] == 0xA5 else b""
                    break
                if start:
                    del self._buffer[:start]
                if len(self._buffer) < 7:
                    break
                message_type, seq, length = struct.unpack_from("<BHH", self._buffer, 2)
                if length > MAX_PAYLOAD:
                    self.length_errors += 1
                    del self._buffer[0]
                    continue
                end = length + FRAME_OVERHEAD
                if len(self._buffer) < end:
                    break
                expected = struct.unpack_from("<H", self._buffer, end - 2)[0]
                if crc16(self._buffer[2:end - 2]) != expected:
                    self.crc_errors += 1
                    del self._buffer[0]
                    continue
                result.append(Packet(message_type, seq, bytes(self._buffer[7:end - 2])))
                del self._buffer[:end]
        return result
