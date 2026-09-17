"""Single-reader STM32 connection, reliable PCM transfer and bounded telemetry.

The reader only parses packets and updates small queues. It never runs inference,
TTS, or user callbacks. Audio commands use stop-and-wait, and all writers share
one packet lock so button acknowledgements cannot split a PCM frame.
"""

from __future__ import annotations

import collections
import logging
import struct
import threading
import time
from typing import Callable

from .protocol import (
    ACK_BAD, ACK_BUSY, ACK_NO_AUDIO, ACK_OK, MAX_PAYLOAD,
    MSG_ACK, MSG_BOOT, MSG_BUTTON, MSG_BUTTON_ACK, MSG_DONE, MSG_PCM,
    MSG_START, MSG_STATUS, MSG_STOP, MSG_TOF, Packet, PacketParser, encode_packet,
)

LOG = logging.getLogger(__name__)


class SerialLinkError(RuntimeError):
    """Fatal connection/hardware error; restart the application to recover."""


class PlaybackCancelled(Exception):
    """Internal control signal: cancellation must always issue STOP."""


class SerialLink:
    """Connect to STM32. ``serial_factory`` is injectable for hardware-free tests.

    ``start`` waits for a valid healthy STATUS, then stops any audio left by a
    previous Pi process. A boot or MCU uptime reset after startup is fatal: no
    pre-reset telemetry or audio acknowledgement is reused.
    """

    def __init__(self, config: dict, *, serial_factory: Callable | None = None):
        self.config = dict(config)
        self.port = str(config.get("port", "/dev/serial0"))
        self.baudrate = int(config.get("baudrate", 1_000_000))
        self.ack_timeout = float(config.get("ack_timeout", 0.25))
        self.retries = int(config.get("retries", 4))
        self.status_timeout = float(config.get("status_timeout", 4.0))
        self.tof_timeout = float(config.get("tof_timeout", 0.75))
        if self.baudrate <= 0 or min(self.ack_timeout, self.status_timeout, self.tof_timeout) <= 0 or self.retries < 0:
            raise ValueError("Invalid serial timing, retries or baudrate")
        self._factory = serial_factory
        self._serial = None
        self._reader: threading.Thread | None = None
        self._shutdown = threading.Event()
        self._condition = threading.Condition()
        self._write_lock = threading.Lock()
        self._command_lock = threading.Lock()
        self._play_lock = threading.Lock()
        self._failure: SerialLinkError | None = None
        self._ready = False
        self._started = False
        self._last_status = 0.0
        self._status: dict | None = None
        self._tof: tuple[float, tuple[int, ...]] | None = None
        self._button_last: int | None = None
        self._buttons: collections.deque[int] = collections.deque(maxlen=8)
        self._pending_seq: int | None = None
        self._acks: collections.deque[int] = collections.deque(maxlen=8)
        self._sequence = time.time_ns() & 0xFFFF
        self._expected_samples: int | None = None
        self._done_samples: int | None = None

    def start(self) -> None:
        if self._started:
            raise SerialLinkError("SerialLink instances may only be started once")
        self._started = True
        factory = self._factory
        if factory is None:
            import serial  # Optional dependency: import only when hardware is opened.
            factory = serial.Serial
        try:
            self._serial = factory(
                port=self.port, baudrate=self.baudrate, timeout=0.05,
                write_timeout=max(0.1, self.ack_timeout),
            )
            self._last_status = time.monotonic()
            self._reader = threading.Thread(target=self._read_loop, name="stm32-reader", daemon=True)
            self._reader.start()
            deadline = time.monotonic() + self.status_timeout
            with self._condition:
                while self._status is None:
                    self.check_health()
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise SerialLinkError("No valid STM32 STATUS; check UART wiring, port and 1 Mbaud")
                    self._condition.wait(min(0.05, remaining))
                self._ready = True
            self._send_command(MSG_STOP)
        except Exception:
            self.close()
            raise

    def _fail(self, message: str) -> None:
        with self._condition:
            if self._failure is None:
                self._failure = SerialLinkError(message)
                self._tof = None
                self._buttons.clear()
                LOG.error(message)
            self._condition.notify_all()

    def check_health(self) -> None:
        with self._condition:
            if self._failure is not None:
                raise self._failure
            if self._shutdown.is_set() or self._serial is None:
                raise SerialLinkError("STM32 connection is closed")
            if self._reader is None or not self._reader.is_alive():
                raise SerialLinkError("STM32 serial reader is not running")
            if time.monotonic() - self._last_status > self.status_timeout:
                self._fail("STM32 STATUS heartbeat timed out")
                raise self._failure

    def get_tof(self) -> tuple[float, tuple[int, ...]] | None:
        self.check_health()
        with self._condition:
            if self._tof is None or time.monotonic() - self._tof[0] > self.tof_timeout:
                return None
            return self._tof

    def poll_button(self) -> int | None:
        self.check_health()
        with self._condition:
            return self._buttons.popleft() if self._buttons else None

    def get_status(self) -> dict | None:
        """Return a diagnostic snapshot; values are from the most recent STATUS."""
        self.check_health()
        with self._condition:
            return dict(self._status) if self._status is not None else None

    def _write_packet(self, message_type: int, seq: int, payload: bytes = b"") -> None:
        frame = encode_packet(message_type, seq, payload)
        try:
            with self._write_lock:
                if self._serial is None or self._shutdown.is_set():
                    raise SerialLinkError("STM32 serial port closed during write")
                # pyserial can report a short write. Finish this packet before releasing the lock.
                sent = 0
                deadline = time.monotonic() + max(0.1, self.ack_timeout)
                while sent < len(frame):
                    count = self._serial.write(frame[sent:])
                    if count is None or count <= 0 or time.monotonic() > deadline:
                        raise SerialLinkError("STM32 UART write timed out")
                    sent += count
        except Exception as exc:
            self._fail(f"STM32 UART write failed: {exc}")
            raise SerialLinkError(f"STM32 UART write failed: {exc}") from exc

    def _read_loop(self) -> None:
        parser = PacketParser()
        try:
            while not self._shutdown.is_set():
                waiting = max(1, min(int(self._serial.in_waiting), 4096))
                data = self._serial.read(waiting)
                for packet in parser.feed(data):
                    self._dispatch(packet)
                if time.monotonic() - self._last_status > self.status_timeout:
                    raise SerialLinkError("STM32 STATUS heartbeat timed out")
                if self._failure is not None:
                    return
        except Exception as exc:
            if not self._shutdown.is_set():
                self._fail(f"STM32 serial reader failed: {exc}")

    def _dispatch(self, packet: Packet) -> None:
        now = time.monotonic()
        p = packet.payload
        with self._condition:
            if packet.type == MSG_BOOT:
                if p:
                    raise SerialLinkError("Malformed STM32 BOOT")
                self._tof = None
                self._button_last = None
                self._buttons.clear()
                if self._ready:
                    raise SerialLinkError("STM32 rebooted; discard audio and restart application")
                self._status = None
            elif packet.type == MSG_ACK:
                if len(p) != 1 or p[0] not in (ACK_OK, ACK_BUSY, ACK_BAD, ACK_NO_AUDIO):
                    raise SerialLinkError("Malformed STM32 ACK")
                if packet.seq == self._pending_seq:
                    self._acks.append(p[0])
            elif packet.type == MSG_BUTTON:
                if len(p) != 4:
                    raise SerialLinkError("Malformed STM32 BUTTON")
                count = struct.unpack("<I", p)[0]
                # Acknowledge every retransmission, even if application work is busy.
                self._write_packet(MSG_BUTTON_ACK, packet.seq, p)
                if self._button_last is None or 0 < ((count - self._button_last) & 0xFFFFFFFF) < 0x80000000:
                    self._button_last = count
                    self._buttons.append(count)
            elif packet.type == MSG_TOF:
                if len(p) != 132:
                    raise SerialLinkError("Malformed STM32 TOF")
                self._tof = now, struct.unpack_from("<64H", p, 4)
            elif packet.type == MSG_STATUS:
                if len(p) != 48 or p[0] != 1:
                    raise SerialLinkError("Unsupported STM32 STATUS format/version")
                values = struct.unpack_from("<11I", p, 4)
                keys = ("uptime_ms", "tof_frames", "tof_errors", "underruns", "uart_errors",
                        "rx_overflows", "crc_errors", "button_count", "exti_edges",
                        "telemetry_drops", "samples_played")
                status = dict(zip(keys, values))
                status.update(audio_ok=bool(p[1]), tof_ready=bool(p[2]), audio_active=bool(p[3]))
                old = self._status
                if old is not None:
                    delta = (status["uptime_ms"] - old["uptime_ms"]) & 0xFFFFFFFF
                    if delta >= 0x80000000:
                        raise SerialLinkError("STM32 uptime reset without BOOT; restart application")
                    if any(status[key] != old[key] for key in ("uart_errors", "rx_overflows")):
                        raise SerialLinkError("STM32 reported a new UART error or receive overflow")
                if not status["audio_ok"]:
                    raise SerialLinkError("STM32 audio codec/DMA is not ready")
                if not status["tof_ready"]:
                    self._tof = None
                self._status = status
                self._last_status = now
            elif packet.type == MSG_DONE:
                if len(p) != 4:
                    raise SerialLinkError("Malformed STM32 DONE")
                count = struct.unpack("<I", p)[0]
                if self._expected_samples is not None:
                    if count != self._expected_samples:
                        raise SerialLinkError("STM32 DONE sample count differs from requested audio")
                    self._done_samples = count
            self._condition.notify_all()

    def _next_sequence(self) -> int:
        self._sequence = (self._sequence + 1) & 0xFFFF
        return self._sequence

    def _wait_for_ack(self, deadline: float, cancel: threading.Event | None = None) -> int | None:
        with self._condition:
            while not self._acks:
                self.check_health()
                if cancel is not None and cancel.is_set():
                    raise PlaybackCancelled()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(min(0.025, remaining))
            return self._acks.popleft() if self._acks else None

    def _send_command(self, message_type: int, payload: bytes = b"", cancel: threading.Event | None = None) -> None:
        with self._command_lock:
            seq = self._next_sequence()
            with self._condition:
                self._pending_seq = seq
                self._acks.clear()
            missed = 0
            busy_deadline = time.monotonic() + max(2.0, self.status_timeout)
            try:
                while True:
                    self.check_health()
                    if cancel is not None and cancel.is_set():
                        raise PlaybackCancelled()
                    self._write_packet(message_type, seq, payload)
                    deadline = time.monotonic() + self.ack_timeout

                    ack = self._wait_for_ack(deadline, cancel)

                    if ack == ACK_OK:
                        return
                    if ack == ACK_BUSY:
                        # Ring-buffer backpressure is normal; do not spend the dropped-ACK budget.
                        if time.monotonic() > busy_deadline:
                            raise SerialLinkError("STM32 stayed ACK_BUSY too long")
                        with self._condition:
                            self._condition.wait(0.01)
                        continue
                    if ack is None:
                        missed += 1
                        if missed <= self.retries:
                            continue  # Same seq + payload: STM32 deduplicates accepted commands.
                        raise SerialLinkError(f"No STM32 ACK for command 0x{message_type:02x}")
                    if ack == ACK_NO_AUDIO:
                        raise SerialLinkError("STM32 rejected audio: codec/DMA unavailable")
                    raise SerialLinkError(f"STM32 rejected command 0x{message_type:02x}: ACK_BAD")
            finally:
                with self._condition:
                    self._pending_seq = None
                    self._acks.clear()

    def play_pcm(self, pcm: bytes, cancel: threading.Event | None = None) -> None:
        """Synchronously play raw mono signed little-endian PCM16 at 16 kHz.

        Return only after matching DONE (hardware drained), or after acknowledged
        STOP on cancellation. A cancellation is a normal return, other failures
        are fatal SerialLinkError exceptions.
        """
        if len(pcm) % 2:
            raise ValueError("PCM16 must contain an even number of bytes")
        samples = len(pcm) // 2
        if samples > 0xFFFFFFFF:
            raise ValueError("PCM exceeds the firmware sample-count range")
        with self._play_lock:
            self.check_health()
            if not samples or (cancel is not None and cancel.is_set()):
                return
            with self._condition:
                self._expected_samples = samples
                self._done_samples = None
            try:
                self._send_command(MSG_START, struct.pack("<I", samples), cancel)
                for offset in range(0, len(pcm), MAX_PAYLOAD):
                    self._send_command(MSG_PCM, pcm[offset:offset + MAX_PAYLOAD], cancel)
                # Remaining queued sound <= ring size, but allow full duration for slow links.
                deadline = time.monotonic() + samples / 16_000 + self.status_timeout
                with self._condition:
                    while self._done_samples != samples:
                        self.check_health()
                        if cancel is not None and cancel.is_set():
                            raise PlaybackCancelled()
                        if time.monotonic() >= deadline:
                            raise SerialLinkError("STM32 DONE timeout; audio completion is unknown")
                        self._condition.wait(0.025)
            except PlaybackCancelled:
                self._send_command(MSG_STOP)
            except Exception as exc:
                try:
                    self._send_command(MSG_STOP)
                except Exception:
                    pass
                self._fail(f"STM32 playback failed: {exc}")
                raise
            finally:
                with self._condition:
                    self._expected_samples = None
                    self._done_samples = None

    def close(self) -> None:
        """Best-effort STOP then close; normal playback should be cancelled first."""
        if self._shutdown.is_set():
            return
        if self._serial is not None:
            try:
                self._write_packet(MSG_STOP, self._next_sequence())
            except Exception:
                pass
        self._shutdown.set()
        with self._condition:
            self._condition.notify_all()
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:
                pass
        if self._reader is not None and self._reader is not threading.current_thread():
            self._reader.join(timeout=max(1.0, self.ack_timeout + 0.2))
        self._serial = None
