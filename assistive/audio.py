"""Bounded speech scheduling, cancellation, and PCM output through STM32."""
from __future__ import annotations

from dataclasses import dataclass, field
from collections import OrderedDict
import io
import logging
import math
from pathlib import Path
import re
import threading
import time
import wave

log = logging.getLogger(__name__)


def split_text(text, limit=220):
    """Keep synthesis buffers short, including OCR without punctuation."""
    for sentence in re.split(r"(?<=[.!?;])\s+|\n+", text.strip()):
        words, chunk = sentence.split(), ""
        for word in words:
            if chunk and len(chunk) + len(word) + 1 > limit:
                yield chunk
                chunk = ""
            while len(word) > limit:
                if chunk:
                    yield chunk
                    chunk = ""
                yield word[:limit]
                word = word[limit:]
            chunk = f"{chunk} {word}".strip()
        if chunk:
            yield chunk


def wav_to_pcm(data):
    import numpy as np
    from scipy.signal import resample_poly
    with wave.open(io.BytesIO(data), "rb") as wav:
        if wav.getsampwidth() != 2 or wav.getnchannels() != 1:
            raise ValueError("Piper must output mono signed 16-bit WAV")
        rate = wav.getframerate()
        samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2")
    if rate != 16000:
        common = math.gcd(rate, 16000)
        samples = resample_poly(samples.astype(np.float32), 16000 // common, rate // common)
    return np.clip(np.rint(samples), -32768, 32767).astype("<i2").tobytes()


class PiperSynthesizer:
    def __init__(self, config):
        import json
        import onnxruntime as ort
        from piper import PiperVoice
        from piper.config import PiperConfig
        path = Path(config["model"])
        if not path.is_file() or not Path(str(path) + ".json").is_file():
            raise FileNotFoundError(f"Need Piper ONNX and ONNX.json: {path}")
        # PiperVoice.load uses ORT's default CPU pool. Build the same public
        # dataclass with an explicit small pool so YOLO/serial retain CPU time.
        options = ort.SessionOptions()
        options.intra_op_num_threads = int(config.get("threads", 1))
        options.inter_op_num_threads = 1
        with Path(str(path) + ".json").open(encoding="utf-8") as stream:
            voice_config = PiperConfig.from_dict(json.load(stream))
        self.voice = PiperVoice(config=voice_config, session=ort.InferenceSession(
            str(path), sess_options=options, providers=["CPUExecutionProvider"]))
        self.cache = OrderedDict()
        self.cache_limit = int(config.get("cache_bytes", 2_000_000))
        self.cache_bytes = 0

    def synthesize(self, text):
        if text in self.cache:
            self.cache.move_to_end(text)
            return self.cache[text]
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            self.voice.synthesize_wav(text, wav)
        pcm = wav_to_pcm(buffer.getvalue())
        if len(pcm) <= self.cache_limit:
            self.cache[text] = pcm
            self.cache_bytes += len(pcm)
            while self.cache_bytes > self.cache_limit:
                _, old = self.cache.popitem(last=False)
                self.cache_bytes -= len(old)
        return pcm


class MockSynthesizer:
    def synthesize(self, text):
        log.info("MOCK speech: %s", text)
        return b"\x00\x00" * 800


@dataclass
class SpeechRequest:
    text: str
    priority: int = 10
    key: str = ""
    ttl: float = 5.
    repeat_after: float = 4.
    replace: bool = False

@dataclass
class SpeechTicket:
    text: str
    priority: int
    key: str
    expires: float
    done: threading.Event = field(default_factory=threading.Event)
    cancelled: threading.Event = field(default_factory=threading.Event)
    error: BaseException | None = None


class SpeechWorker:
    def __init__(self, config, link):
        self.config, self.link = config, link
        self._condition = threading.Condition()
        self._pending = []
        self._active = None
        self._stop = False
        self._recent = OrderedDict()
        self.error = None
        self.heartbeat = time.monotonic()
        self.thread = threading.Thread(target=self._run, name="speech", daemon=True)

    def start(self):
        self.thread.start()

    def speak(self, request: SpeechRequest):
        now = time.monotonic()
        ticket = SpeechTicket(request.text, request.priority, request.key or request.text, now + request.ttl)
        with self._condition:
            if self._stop:
                ticket.cancelled.set()
                ticket.done.set()
                return ticket
            if not request.replace and now - self._recent.get(ticket.key, -1e9) < request.repeat_after:
                ticket.cancelled.set()
                ticket.done.set()
                return ticket
            self._recent[ticket.key] = now
            self._recent.move_to_end(ticket.key)
            while len(self._recent) > 128:
                self._recent.popitem(last=False)
            # Urgent alerts or a mode change interrupt old speech, including PCM.
            if self._active and (request.priority < self._active.priority or
                                 (request.replace and request.priority <= self._active.priority)):
                self._active.cancelled.set()
            for old in self._pending[:]:
                if (request.replace and request.priority <= old.priority) or request.priority < old.priority or old.key == ticket.key:
                    self._pending.remove(old)
                    old.cancelled.set()
                    old.done.set()
            capacity = int(self.config.get("queue_size", 3))
            if len(self._pending) >= capacity:
                worst = max(self._pending, key=lambda t: t.priority)
                if worst.priority >= request.priority:
                    self._pending.remove(worst)
                    worst.cancelled.set()
                    worst.done.set()
                else:
                    ticket.cancelled.set()
                    ticket.done.set()
                    return ticket
            self._pending.append(ticket)
            self._pending.sort(key=lambda t: t.priority)
            self._condition.notify()
        return ticket

    def check_health(self):
        if self.error:
            raise RuntimeError("Speech worker failed") from self.error
        if not self.thread.is_alive() and not self._stop:
            raise RuntimeError("Speech worker stopped")
        if time.monotonic() - self.heartbeat > self.config.get("worker_timeout_seconds", 90.):
            raise TimeoutError("Speech synthesis/output stalled")

    def _run(self):
        try:
            synth = MockSynthesizer() if self.config.get("mock") else PiperSynthesizer(self.config)
            while True:
                with self._condition:
                    self.heartbeat = time.monotonic()
                    while not self._stop and not self._pending:
                        self._condition.wait(.25)
                        self.heartbeat = time.monotonic()
                    if self._stop:
                        return
                    ticket = self._pending.pop(0)
                    self._active = ticket
                try:
                    if time.monotonic() > ticket.expires:
                        ticket.cancelled.set()
                    for chunk in split_text(ticket.text):
                        if ticket.cancelled.is_set():
                            break
                        pcm = synth.synthesize(chunk)
                        self.heartbeat = time.monotonic()
                        if not ticket.cancelled.is_set():
                            self.link.play_pcm(pcm, cancel=ticket.cancelled)
                        self.heartbeat = time.monotonic()
                except BaseException as exc:
                    ticket.error = exc
                    raise
                finally:
                    ticket.done.set()
                    with self._condition:
                        self._active = None
        except BaseException as exc:
            self.error = exc
        finally:
            with self._condition:
                for ticket in self._pending:
                    ticket.error = self.error
                    ticket.cancelled.set()
                    ticket.done.set()
                self._pending.clear()

    def close(self):
        with self._condition:
            self._stop = True
            if self._active:
                self._active.cancelled.set()
            self._condition.notify_all()
        if self.thread.ident is not None:
            self.thread.join(3.)
        if self.thread.is_alive():
            raise RuntimeError("Speech worker did not stop")
