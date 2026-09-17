import binascii
from pathlib import Path
import queue
import random
import struct
import sys
import threading
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'pi'))
from stm32_bridge import (Decoder, encode, Bridge, START, PCM, STOP, BUTTON_ACK,
                          ACK, BUTTON, TOF, STATUS, DONE, BOOT, make_tone)

class FakeSerial:
    """Device simulator: dropped ACK, BUSY, duplicate retry, short writes."""
    def __init__(self):
        self.incoming = queue.Queue()
        self.decoder = Decoder()
        self.lock = threading.Lock()
        self.accepted = []
        self.last = None
        self.drop_once = True
        self.busy_once = True
        self.total = 0
        self.samples = 0
        self.retries = 0
        self.button_acks = 0

    def emit(self, kind, seq, payload=b''):
        self.incoming.put(encode(kind, seq, payload))

    def read(self, n):
        try:
            return self.incoming.get(timeout=0.005)
        except queue.Empty:
            return b''

    def write(self, data):
        n = min(17, len(data))
        with self.lock:
            for kind, seq, payload in self.decoder.feed(data[:n]):
                key = (kind, seq, payload)
                if kind == BUTTON_ACK:
                    self.button_acks += 1
                    continue
                if key == self.last:
                    self.retries += 1
                    self.emit(ACK, seq, b'\0')
                    continue
                if kind == PCM and self.busy_once:
                    self.busy_once = False
                    self.emit(ACK, seq, b'\1')
                    continue
                self.last = key
                if kind == START:
                    self.total, = struct.unpack('<I', payload)
                    self.samples = 0
                elif kind == PCM:
                    self.accepted.append(payload)
                    self.samples += len(payload)//2
                    self.emit(BUTTON, 1, struct.pack('<I', 1))
                    if self.samples == self.total:
                        self.emit(DONE, 0, struct.pack('<I', self.samples))
                if self.drop_once and kind == PCM:
                    self.drop_once = False
                else:
                    self.emit(ACK, seq, b'\0')
        return n

    def close(self):
        pass

class ProtocolTests(unittest.TestCase):
    def test_crc_standard_vector(self):
        self.assertEqual(binascii.crc_hqx(b'123456789', 0xffff), 0x29b1)

    def test_fragmented_full_size_binary_payload(self):
        rng = random.Random(7)
        payload = bytes(range(256))*4
        stream = encode(PCM, 0xffff, payload)
        decoder, result = Decoder(), []
        while stream:
            n = rng.randint(1, 31)
            result += decoder.feed(stream[:n], now=0)
            stream = stream[n:]
        self.assertEqual(result, [(PCM, 65535, payload)])

    def test_noise_crc_and_oversize_recovery(self):
        broken = bytearray(encode(PCM, 1, b'abcd'))
        broken[-1] ^= 1
        invalid = b'\xa5\x5a' + struct.pack('<BHH', PCM, 2, 65535)
        good = encode(BUTTON, 3, struct.pack('<I', 3))
        decoder = Decoder()
        self.assertEqual(decoder.feed(b'noise'+broken+invalid+good, now=0),
                         [(BUTTON, 3, struct.pack('<I', 3))])
        self.assertGreaterEqual(decoder.errors, 2)

    def test_truncated_frame_timeout(self):
        decoder = Decoder()
        decoder.feed(encode(PCM, 1, b'x'*80)[:15], now=0)
        self.assertEqual(decoder.feed(encode(STOP, 2), now=0.2), [(STOP, 2, b'')])

    def test_empty_payload_and_merged_frames(self):
        decoder = Decoder()
        self.assertEqual(decoder.feed(encode(STOP, 1)+encode(START, 2, b'abcd')),
                         [(STOP,1,b''),(START,2,b'abcd')])

    def test_payload_limit(self):
        with self.assertRaises(ValueError):
            encode(PCM, 1, bytes(1025))

class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.device = FakeSerial()
        self.bridge = Bridge(transport=self.device)
        self.device.emit(STATUS, 0, struct.pack('<4B11I',1,1,1,0,1000,*([0]*10)))
        self.bridge.wait_ready(1)

    def tearDown(self):
        self.bridge.close()

    def test_pcm_retry_busy_and_button_during_audio(self):
        data = make_tone(0.13)
        self.bridge.play_pcm(data)
        self.assertEqual(b''.join(self.device.accepted), data)
        self.assertGreaterEqual(self.device.retries, 1)
        self.assertGreater(self.device.button_acks, 0)
        event = self.bridge.buttons.get(timeout=1)
        self.assertEqual(event['count'], 1)
        self.assertTrue(self.bridge.buttons.empty())

    def test_matrix_little_endian(self):
        self.bridge._dispatch(TOF, 7, struct.pack('<I64H',7,*range(64)))
        seq, matrix = self.bridge.latest_tof
        self.assertEqual(seq,7)
        self.assertEqual(matrix[0],list(range(8)))
        self.assertEqual(matrix[7],list(range(56,64)))

    def test_boot_clears_button_dedup(self):
        self.bridge._dispatch(BUTTON,1,struct.pack('<I',1))
        self.bridge._dispatch(BOOT,0,b'')
        self.bridge._dispatch(BUTTON,1,struct.pack('<I',1))
        self.assertEqual(self.bridge.buttons.qsize(),2)

    def test_full_event_queue_does_not_ack(self):
        for i in range(128):
            self.bridge.buttons.put(i)
        self.bridge._dispatch(BUTTON,1,struct.pack('<I',1))
        self.assertEqual(self.device.button_acks,0)
        self.assertIsNone(self.bridge.last_button)

    def test_empty_or_odd_pcm_rejected(self):
        for data in [b'',b'1']:
            with self.assertRaises(ValueError):
                self.bridge.play_pcm(data)

if __name__ == '__main__':
    unittest.main()
