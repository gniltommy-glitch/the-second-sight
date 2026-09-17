import threading
import time
import unittest
from unittest.mock import patch

from assistive.audio import SpeechWorker, SpeechRequest, split_text


class TextSynthesizer:
    def synthesize(self, text):
        return text.encode("utf-8")


class ControlledLink:
    def __init__(self):
        self.calls = []
        self.first_started = threading.Event()
        self.release_first = threading.Event()

    def play_pcm(self, pcm, cancel=None):
        self.calls.append(pcm.decode("utf-8"))
        if len(self.calls) == 1:
            self.first_started.set()
            deadline = time.monotonic() + 2
            while not self.release_first.is_set() and not cancel.is_set():
                if time.monotonic() > deadline:
                    raise TimeoutError("Test did not release audio")
                cancel.wait(0.005)


class SpeechWorkerTests(unittest.TestCase):
    def make_worker(self, link=None):
        worker = SpeechWorker({"mock": True, "queue_size": 2}, link or ControlledLink())
        self.addCleanup(worker.close)
        return worker

    def test_urgent_alert_cancels_active_audio_and_obsolete_pending_speech(self):
        link = ControlledLink()
        worker = self.make_worker(link)
        with patch("assistive.audio.MockSynthesizer", TextSynthesizer):
            worker.start()
            old = worker.speak(SpeechRequest("old navigation", key="old", ttl=30))
            self.assertTrue(link.first_started.wait(1))
            queued = worker.speak(SpeechRequest("queued navigation", key="queued", ttl=30))
            urgent = worker.speak(SpeechRequest("stop immediately", priority=0, key="urgent", ttl=30))
            self.assertTrue(urgent.done.wait(1))
        self.assertTrue(old.cancelled.is_set())
        self.assertTrue(old.done.is_set())
        self.assertTrue(queued.cancelled.is_set())
        self.assertTrue(queued.done.is_set())
        self.assertEqual(link.calls, ["old navigation", "stop immediately"])
        worker.check_health()

    def test_mode_replacement_cannot_cancel_higher_priority_emergency(self):
        link = ControlledLink()
        worker = self.make_worker(link)
        with patch("assistive.audio.MockSynthesizer", TextSynthesizer):
            worker.start()
            urgent = worker.speak(SpeechRequest("stop immediately", priority=0, key="urgent", ttl=30))
            self.assertTrue(link.first_started.wait(1))
            replacement = worker.speak(SpeechRequest("reading text", priority=5, key="ocr", replace=True, ttl=30))
            self.assertFalse(urgent.cancelled.is_set(), "Mode switching must preserve emergency speech")
            link.release_first.set()
            self.assertTrue(replacement.done.wait(1))
        self.assertFalse(urgent.cancelled.is_set())
        self.assertEqual(link.calls, ["stop immediately", "reading text"])

    def test_queue_and_dedup_are_bounded_and_expired_speech_is_not_played(self):
        link = ControlledLink()
        link.release_first.set()
        worker = self.make_worker(link)
        first = worker.speak(SpeechRequest("first", key="first", ttl=30))
        duplicate = worker.speak(SpeechRequest("first", key="first", ttl=30))
        second = worker.speak(SpeechRequest("second", key="second", ttl=-1))
        third = worker.speak(SpeechRequest("third", key="third", ttl=30))
        self.assertTrue(duplicate.cancelled.is_set())
        self.assertTrue(duplicate.done.is_set())
        self.assertTrue(first.done.is_set())
        with patch("assistive.audio.MockSynthesizer", TextSynthesizer):
            worker.start()
            self.assertTrue(third.done.wait(1))
        self.assertTrue(second.cancelled.is_set())
        self.assertEqual(link.calls, ["third"])

    def test_hardware_failure_finishes_ticket_and_fails_health(self):
        class BrokenLink:
            def play_pcm(self, pcm, cancel=None):
                raise OSError("UART disconnected")
        worker = self.make_worker(BrokenLink())
        with patch("assistive.audio.MockSynthesizer", TextSynthesizer):
            worker.start()
            ticket = worker.speak(SpeechRequest("hello", ttl=30))
            self.assertTrue(ticket.done.wait(1))
            worker.thread.join(1)
        self.assertIsInstance(ticket.error, OSError)
        with self.assertRaisesRegex(RuntimeError, "Speech worker failed"):
            worker.check_health()

    def test_text_chunking_bounds_unpunctuated_ocr_and_long_tokens(self):
        text = "Một câu tiếng Việt có dấu. " + "từ " * 500 + "z" * 700
        chunks = list(split_text(text, limit=100))
        self.assertTrue(chunks)
        self.assertTrue(all(0 < len(chunk) <= 100 for chunk in chunks))
        self.assertEqual("".join("".join(chunks).split()), "".join(text.split()))


if __name__ == "__main__":
    unittest.main()
