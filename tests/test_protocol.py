import struct
import unittest

from assistive.protocol import (
    MAX_PAYLOAD, MSG_BUTTON, MSG_PCM, MSG_STOP, Packet, PacketParser, crc16, encode_packet,
)


class ProtocolTests(unittest.TestCase):
    def test_ccitt_false_known_vector(self):
        self.assertEqual(crc16(b"123456789"), 0x29B1)

    def test_header_and_independent_firmware_crc(self):
        payload = struct.pack("<I", 1234)
        body = b"\x81\x34\x12\x04\x00" + payload
        crc = 0xFFFF
        for byte in body:
            crc ^= byte << 8
            for _ in range(8):
                crc = ((crc << 1) ^ (0x1021 if crc & 0x8000 else 0)) & 0xFFFF
        self.assertEqual(encode_packet(MSG_BUTTON, 0x1234, payload), b"\xa5\x5a" + body + struct.pack("<H", crc))

    def test_arbitrary_fragments_and_sync_in_payload(self):
        original = Packet(MSG_PCM, 31, b"\xa5\x5a\x00\xff" * 256)
        parser = PacketParser()
        result = []
        for byte in encode_packet(original.type, original.seq, original.payload):
            result.extend(parser.feed(bytes([byte]), now=1.0))
        self.assertEqual(result, [original])
        self.assertEqual(parser.buffered_bytes, 0)

    def test_recovers_from_noise_bad_crc_and_impossible_length(self):
        parser = PacketParser()
        bad_crc = bytearray(encode_packet(MSG_BUTTON, 2, b"\x01\x00\x00\x00"))
        bad_crc[-1] ^= 0x40
        bad_length = b"\xa5\x5a\x80\x00\x00\xff\xff"
        result = parser.feed(b"noise" + bad_crc + bad_length + encode_packet(MSG_STOP, 4))
        self.assertEqual(result, [Packet(MSG_STOP, 4, b"")])
        self.assertEqual(parser.crc_errors, 1)
        self.assertEqual(parser.length_errors, 1)

    def test_truncated_frame_expires_and_partial_sync_survives(self):
        parser = PacketParser(inter_byte_timeout=0.1)
        self.assertEqual(parser.feed(encode_packet(MSG_PCM, 0, b"1" * 1024)[:10], now=1), [])
        parser.feed(b"", now=1.2)
        self.assertEqual(parser.buffered_bytes, 0)
        frame = encode_packet(MSG_STOP, 8)
        parser.feed(b"noise\xa5", now=2)
        self.assertEqual(parser.feed(frame[1:], now=2.01), [Packet(MSG_STOP, 8, b"")])

    def test_noise_is_bounded_and_large_payload_is_rejected(self):
        parser = PacketParser()
        parser.feed(b"\x00" * 100_000)
        self.assertLessEqual(parser.buffered_bytes, MAX_PAYLOAD + 9)
        with self.assertRaises(ValueError):
            encode_packet(MSG_PCM, 0, b"x" * 1025)


if __name__ == "__main__":
    unittest.main()
