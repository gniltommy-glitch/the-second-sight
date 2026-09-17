"""Pi UART bridge: 16 kHz mono PCM, ToF telemetry, acknowledged button events.

One Bridge owns the serial port. YOLO can import this and consume bridge.buttons;
speech can call play_wav in another thread. CLI keeps receiving after playback.
"""
import argparse
import binascii
import json
import math
from pathlib import Path
import queue
import secrets
import struct
import threading
import time
import wave

START, PCM, STOP, BUTTON_ACK = 0x10, 0x11, 0x12, 0x20
ACK, BUTTON, TOF, STATUS, DONE, BOOT = range(0x80, 0x86)
MAX_PAYLOAD = 1024

def encode(kind, seq, payload=b''):
    if len(payload) > MAX_PAYLOAD:
        raise ValueError('payload too large')
    body = struct.pack('<BHH', kind, seq, len(payload)) + payload
    return b'\xa5\x5a' + body + struct.pack('<H', binascii.crc_hqx(body, 0xffff))

class Decoder:
    def __init__(self):
        self.buf = bytearray()
        self.last_byte = 0.0
        self.errors = 0

    def feed(self, data, now=None):
        now = time.monotonic() if now is None else now
        if self.buf and now - self.last_byte > 0.1:
            self.buf.clear()
        if data:
            self.last_byte = now
        self.buf.extend(data)
        result = []
        while self.buf:
            pos = self.buf.find(b'\xa5\x5a')
            if pos < 0:
                self.buf[:] = b'\xa5' if self.buf[-1] == 0xa5 else b''
                break
            del self.buf[:pos]
            if len(self.buf) < 7:
                break
            kind, seq, n = struct.unpack_from('<BHH', self.buf, 2)
            if n > MAX_PAYLOAD:
                del self.buf[0]
                self.errors += 1
                continue
            if len(self.buf) < n + 9:
                break
            crc, = struct.unpack_from('<H', self.buf, n + 7)
            if crc != binascii.crc_hqx(self.buf[2:n+7], 0xffff):
                del self.buf[0]
                self.errors += 1
                continue
            result.append((kind, seq, bytes(self.buf[7:n+7])))
            del self.buf[:n+9]
        return result

class Bridge:
    def __init__(self, port='/dev/ttyAMA0', baud=1000000, transport=None):
        if transport is None:
            import serial
            transport = serial.Serial(port, baud, timeout=0.01, write_timeout=1)
        self.serial = transport
        self.decoder = Decoder()
        self.buttons = queue.Queue(maxsize=128)
        self.acks = queue.Queue()
        self.latest_tof = None
        self.status = None
        self.status_time = 0.0
        self.failure = None
        self.last_button = None
        self.boots = 0
        self.seq = secrets.randbelow(65536)
        self.write_lock = threading.Lock()
        self.command_lock = threading.Lock()
        self.play_lock = threading.Lock()
        self.closed = threading.Event()
        self.done = threading.Event()
        self.thread = threading.Thread(target=self._reader, daemon=True)
        self.thread.start()

    def _write(self, kind, seq, payload=b''):
        data = encode(kind, seq, payload)
        with self.write_lock:
            offset = 0
            while offset < len(data):
                sent = self.serial.write(data[offset:])
                if not sent:
                    raise OSError('UART write made no progress')
                offset += sent

    def _handle_ack(self, seq, p):
        if len(p) == 1:
            self.acks.put((seq, p[0]))

    def _handle_button(self, seq, p):
        if len(p) == 4:
            count, = struct.unpack('<I', p)
            if count != self.last_button:
                try:
                    self.buttons.put_nowait({'event': 'capture', 'count': count,
                                             'received_ns': time.time_ns()})
                except queue.Full:
                    return  # No ACK: STM32 retries until YOLO has queue space.
                self.last_button = count
            self._write(BUTTON_ACK, seq, p)

    def _handle_tof(self, seq, p):
        if len(p) == 132:
            values = struct.unpack('<I64H', p)
            self.latest_tof = (values[0], [list(values[1+i*8:9+i*8]) for i in range(8)])

    def _handle_status(self, seq, p):
        if len(p) == 48:
            values = struct.unpack('<4B11I', p)
            names = ['version', 'audio_ok', 'tof_ready', 'audio_active', 'uptime_ms',
                     'tof_frames', 'tof_errors', 'underruns', 'uart_errors', 'rx_overflows',
                     'crc_errors', 'button_count', 'exti_edges', 'telemetry_drops', 'samples_played']
            update = dict(zip(names, values))
            if self.status and update['uptime_ms'] < self.status['uptime_ms']:
                self.boots += 1
                self.last_button = None
            self.status = update
            self.status_time = time.monotonic()

    def _handle_done(self, seq, p):
        if len(p) == 4:
            self.done.set()

    def _handle_boot(self, seq, p):
        self.boots += 1
        self.last_button = None
        self.status = None

    def _dispatch(self, kind, seq, p):
        if kind == ACK:
            self._handle_ack(seq, p)
        elif kind == BUTTON:
            self._handle_button(seq, p)
        elif kind == TOF:
            self._handle_tof(seq, p)
        elif kind == STATUS:
            self._handle_status(seq, p)
        elif kind == DONE:
            self._handle_done(seq, p)
        elif kind == BOOT:
            self._handle_boot(seq, p)

    def _reader(self):
        try:
            while not self.closed.is_set():
                for frame in self.decoder.feed(self.serial.read(2048)):
                    self._dispatch(*frame)
        except Exception as exc:
            if not self.closed.is_set():
                self.failure = exc

    def _check(self):
        if self.failure:
            raise OSError('UART receiver failed') from self.failure
        if self.closed.is_set():
            raise OSError('Bridge is closed')

    def command(self, kind, payload=b'', timeout=3.0):
        with self.command_lock:
            self.seq = (self.seq + 1) & 0xffff
            seq, boot = self.seq, self.boots
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                self._check()
                if self.boots != boot:
                    raise OSError('STM32 reset during command; restart playback')
                self._write(kind, seq, payload)
                attempt = min(deadline, time.monotonic() + 0.15)
                while time.monotonic() < attempt:
                    try:
                        got_seq, status = self.acks.get(timeout=max(0.001, attempt-time.monotonic()))
                    except queue.Empty:
                        break
                    if got_seq != seq:
                        continue
                    if status == 0:
                        return
                    if status == 1:
                        time.sleep(0.008)  # Audio ring full: retry SAME sequence.
                        break
                    raise OSError(f'STM32 rejected 0x{kind:02x}: status={status} (2=invalid, 3=codec unavailable)')
            raise TimeoutError(f'No ACK for command 0x{kind:02x}; check UART/wiring/status')

    def wait_ready(self, timeout=5.0):
        deadline = time.monotonic() + timeout
        while self.status is None and time.monotonic() < deadline:
            self._check()
            time.sleep(0.01)
        if self.status is None:
            raise TimeoutError('No STM32 heartbeat; check port, 1 Mbaud, clock and wiring')
        if self.status['version'] != 1:
            raise OSError('Unsupported firmware protocol')

    def play_pcm(self, data):
        if not data or len(data) % 2:
            raise ValueError('Need nonempty signed 16-bit little-endian mono PCM at 16000 Hz')
        with self.play_lock:
            self.wait_ready()
            self.command(STOP)
            time.sleep(0.04)  # Drain previous DMA halves before accepting another clip.
            self.done.clear()
            boot = self.boots
            try:
                self.command(START, struct.pack('<I', len(data)//2))
                for offset in range(0, len(data), MAX_PAYLOAD):
                    if self.boots != boot:
                        raise OSError('STM32 reset during playback')
                    self.command(PCM, data[offset:offset+MAX_PAYLOAD])
                deadline = time.monotonic() + 4.0
                while not self.done.wait(0.05):
                    self._check()
                    if self.boots != boot:
                        raise OSError('STM32 reset during playback')
                    # DONE may be lost; heartbeat confirms complete after DMA drained.
                    if (self.status and time.monotonic()-self.status_time < 2
                            and not self.status['audio_active']
                            and self.status['samples_played'] == len(data)//2):
                        break
                    if time.monotonic() > deadline:
                        raise TimeoutError('Audio completion timeout')
            except Exception:
                try:
                    self.command(STOP, timeout=0.3)
                except Exception:
                    pass
                raise

    def play_wav(self, path):
        with wave.open(str(path), 'rb') as wav:
            if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getcomptype()) != (1,2,16000,'NONE'):
                raise ValueError('Convert WAV first: ffmpeg -i input.wav -ac 1 -ar 16000 -c:a pcm_s16le speech16.wav')
            data = wav.readframes(wav.getnframes())
        self.play_pcm(data)

    def close(self):
        self.closed.set()
        self.thread.join(timeout=1.2)
        self.serial.close()

def make_tone(seconds=1):
    return b''.join(struct.pack('<h', int(4000*math.sin(2*math.pi*440*i/16000)))
                    for i in range(int(16000*seconds)))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', default='/dev/ttyAMA0')
    parser.add_argument('--wav', type=Path)
    parser.add_argument('--tone', action='store_true')
    parser.add_argument('--show-matrix', action='store_true')
    args = parser.parse_args()
    bridge = Bridge(args.port)
    playback = None
    def play():
        try:
            if args.tone:
                bridge.play_pcm(make_tone())
            if args.wav:
                bridge.play_wav(args.wav)
            print('Audio completed', flush=True)
        except Exception as exc:
            print(f'Audio failed: {exc}', flush=True)
    try:
        bridge.wait_ready()
        if args.tone or args.wav:
            playback = threading.Thread(target=play, daemon=True)
            playback.start()
        last = 0.0
        while True:
            bridge._check()
            try:
                event = bridge.buttons.get(timeout=0.05)
                print(json.dumps(event), flush=True)
            except queue.Empty:
                pass
            if time.monotonic() - last >= 1:
                last = time.monotonic()
                print(json.dumps(bridge.status), flush=True)
                if time.monotonic() - bridge.status_time > 3:
                    print('UART heartbeat stale', flush=True)
                if args.show_matrix and bridge.latest_tof:
                    for row in bridge.latest_tof[1]:
                        print(' '.join(f'{v:5d}' for v in row))
    except KeyboardInterrupt:
        try:
            bridge.command(STOP, timeout=0.3)
        except Exception:
            pass
    finally:
        bridge.close()

if __name__ == '__main__':
    main()
