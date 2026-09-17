"""Fake STM32 tests: no pyserial, models, numpy, camera or board required."""

import struct
import threading
import time
import unittest

from assistive.protocol import (
    ACK_BAD, ACK_BUSY, ACK_OK, MSG_ACK, MSG_BOOT, MSG_BUTTON, MSG_BUTTON_ACK,
    MSG_DONE, MSG_PCM, MSG_START, MSG_STATUS, MSG_STOP, MSG_TOF,
    PacketParser, encode_packet,
)
from assistive.serial_link import SerialLink, SerialLinkError


class FakeSerial:
    """Models firmware deduplication and backpressure, not wall-time audio output."""

    def __init__(self):
        self.condition = threading.Condition()
        self.rx = bytearray()
        self.closed = False
        self.disconnected = False
        self.parser = PacketParser()
        self.sent = []
        self.last_accepted = None
        self.pcm = bytearray()
        self.expected = 0
        self.busy_remaining = 0
        self.drop_pcm_ack = False
        self.button_during_pcm = False
        self.reset_during_pcm = False
        self.cancel_during_pcm = None
        self.done_enabled = True
        self.heartbeats = True
        self.last_heartbeat = time.monotonic()
        self.epoch = self.last_heartbeat
        self.emit(MSG_STATUS, 0, self.status())

    def status(self, uptime=None, audio_ok=1, tof_ready=1):
        uptime = int((time.monotonic() - self.epoch) * 1000) + 10000 if uptime is None else uptime
        return bytes([1, audio_ok, tof_ready, bool(self.expected)]) + struct.pack("<11I", uptime, *([0] * 10))

    def emit(self, message_type, seq, payload=b""):
        with self.condition:
            self.rx.extend(encode_packet(message_type, seq, payload))
            self.condition.notify_all()

    @property
    def in_waiting(self):
        if self.disconnected:
            raise OSError("USB disconnected")
        with self.condition:
            return len(self.rx)

    def read(self, size):
        with self.condition:
            if not self.rx and not self.closed:
                self.condition.wait(0.01)
            if self.heartbeats and time.monotonic() - self.last_heartbeat > 0.05:
                self.last_heartbeat = time.monotonic()
                self.emit(MSG_STATUS, 0, self.status())
            result = bytes(self.rx[:size])
            del self.rx[:size]
            return result

    def write(self, data):
        if self.disconnected or self.closed:
            raise OSError("Port is closed")
        for packet in self.parser.feed(data):
            self.sent.append(packet)
            if packet.type == MSG_BUTTON_ACK:
                continue
            fingerprint = packet.type, packet.seq, packet.payload
            if fingerprint == self.last_accepted:
                self.emit(MSG_ACK, packet.seq, bytes([ACK_OK]))
                continue
            if packet.type == MSG_STOP:
                self.expected = 0
            elif packet.type == MSG_START:
                self.expected = struct.unpack("<I", packet.payload)[0]
                self.pcm.clear()
            elif packet.type == MSG_PCM:
                if self.reset_during_pcm:
                    self.reset_during_pcm = False
                    self.emit(MSG_BOOT, 0)
                    continue
                if self.busy_remaining:
                    self.busy_remaining -= 1
                    self.emit(MSG_ACK, packet.seq, bytes([ACK_BUSY]))
                    continue
                if self.button_during_pcm:
                    self.button_during_pcm = False
                    self.emit(MSG_BUTTON, 1, struct.pack("<I", 1))
                    self.emit(MSG_BUTTON, 1, struct.pack("<I", 1))
                self.pcm.extend(packet.payload)
                if self.cancel_during_pcm is not None:
                    self.cancel_during_pcm.set()
                    self.cancel_during_pcm = None
                if len(self.pcm) == self.expected * 2 and self.done_enabled:
                    self.emit(MSG_DONE, 0, struct.pack("<I", self.expected))
                    self.expected = 0
            else:
                self.emit(MSG_ACK, packet.seq, bytes([ACK_BAD]))
                continue
            self.last_accepted = fingerprint
            if packet.type == MSG_PCM and self.drop_pcm_ack:
                self.drop_pcm_ack = False
                continue
            self.emit(MSG_ACK, packet.seq, bytes([ACK_OK]))
        return len(data)

    def close(self):
        with self.condition:
            self.closed = True
            self.condition.notify_all()


class SerialLinkTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeSerial()
        self.link = SerialLink({
            "port": "FAKE", "ack_timeout": 0.04, "retries": 2,
            "status_timeout": 0.4, "tof_timeout": 0.04,
        }, serial_factory=lambda **kwargs: self.fake)
        self.link.start()
        self.addCleanup(self.link.close)

    def wait_for(self, predicate, timeout=1):
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() > deadline:
                self.fail("Timed out waiting for reader")
            time.sleep(0.005)

    def test_audio_retries_same_sequence_without_duplicate_samples_and_acks_button(self):
        self.fake.drop_pcm_ack = True
        self.fake.button_during_pcm = True
        pcm = b"\x23\xfe" * 1500
        self.link.play_pcm(pcm)
        self.assertEqual(self.fake.pcm, pcm)
        attempts = [p for p in self.fake.sent if p.type == MSG_PCM]
        self.assertEqual(attempts[0].seq, attempts[1].seq)
        self.assertEqual(attempts[0].payload, attempts[1].payload)
        self.assertEqual(self.link.poll_button(), 1)
        self.assertIsNone(self.link.poll_button())
        acknowledgements = [p for p in self.fake.sent if p.type == MSG_BUTTON_ACK]
        self.assertEqual(len(acknowledgements), 2)
        self.assertTrue(all(p.payload == struct.pack("<I", 1) for p in acknowledgements))

    def test_busy_does_not_exhaust_dropped_ack_retry_budget(self):
        self.fake.busy_remaining = 6
        self.link.play_pcm(b"\x00\x00" * 512)
        attempts = [p for p in self.fake.sent if p.type == MSG_PCM]
        self.assertEqual(len(attempts), 7)
        self.assertEqual(len({p.seq for p in attempts}), 1)
        self.assertEqual(len(self.fake.pcm), 1024)

    def test_cancel_sends_stop_and_allows_next_audio(self):
        cancel = threading.Event()
        self.fake.cancel_during_pcm = cancel
        self.link.play_pcm(b"\x00\x00" * 2000, cancel)
        self.assertEqual(self.fake.sent[-1].type, MSG_STOP)
        self.assertEqual(self.fake.expected, 0)
        self.link.play_pcm(b"\x00\x00" * 100)
        self.assertEqual(len(self.fake.pcm), 200)

    def test_waits_for_done_and_cancellation_stops_wait(self):
        self.fake.done_enabled = False
        cancel = threading.Event()
        completed = threading.Event()
        errors = []

        def play():
            try:
                self.link.play_pcm(b"\x00\x00" * 100, cancel)
            except Exception as exc:
                errors.append(exc)
            finally:
                completed.set()

        thread = threading.Thread(target=play)
        thread.start()
        self.wait_for(lambda: len(self.fake.pcm) == 200)
        self.assertFalse(completed.wait(0.04))
        cancel.set()
        self.assertTrue(completed.wait(0.5))
        thread.join()
        self.assertFalse(errors)
        self.assertEqual(self.fake.sent[-1].type, MSG_STOP)

    def test_reset_during_audio_is_fatal_and_discards_tof(self):
        self.fake.emit(MSG_TOF, 1, struct.pack("<I64H", 1, *([500] * 64)))
        self.wait_for(lambda: self.link.get_tof() is not None)
        self.fake.reset_during_pcm = True
        with self.assertRaises(SerialLinkError):
            self.link.play_pcm(b"\x00\x00" * 100)
        with self.assertRaises(SerialLinkError):
            self.link.get_tof()

    def test_uptime_reset_without_boot_is_detected(self):
        self.fake.emit(MSG_STATUS, 0, self.fake.status(uptime=50))
        self.wait_for(lambda: self.link._failure is not None)
        with self.assertRaisesRegex(SerialLinkError, "uptime reset"):
            self.link.check_health()

    def test_tof_uses_latest_frame_and_expires(self):
        self.fake.emit(MSG_TOF, 1, struct.pack("<I64H", 1, *([501] * 64)))
        self.fake.emit(MSG_TOF, 2, struct.pack("<I64H", 2, *([502] * 64)))
        self.wait_for(lambda: (self.link.get_tof() or (None, ()))[1] == (502,) * 64)
        time.sleep(0.05)
        self.assertIsNone(self.link.get_tof())

    def test_disconnected_reader_and_missing_heartbeat_fail_closed(self):
        self.fake.disconnected = True
        self.wait_for(lambda: self.link._failure is not None)
        with self.assertRaises(SerialLinkError):
            self.link.poll_button()

    def test_missing_heartbeat_fails_even_while_other_packets_arrive(self):
        self.fake.heartbeats = False
        self.link._last_status = time.monotonic() - 1
        with self.assertRaisesRegex(SerialLinkError, "heartbeat"):
            self.link.check_health()

    def test_codec_failure_is_fatal(self):
        self.fake.emit(MSG_STATUS, 0, self.fake.status(audio_ok=0))
        self.wait_for(lambda: self.link._failure is not None)
        with self.assertRaisesRegex(SerialLinkError, "codec/DMA"):
            self.link.check_health()


if __name__ == "__main__":
    unittest.main()
