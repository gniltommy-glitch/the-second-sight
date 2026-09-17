"""Controller integration tests with deterministic hardware/model substitutes."""

import collections
import time
import unittest
from unittest.mock import Mock, patch

from assistive.app import AssistiveApp, Mode
from assistive.audio import SpeechTicket, SpeechRequest


def config_fixture():
    return {
        "runtime": {"max_inference_fps": 100, "model_load_timeout": 2,
                    "result_max_age": 1.5, "fusion_max_skew": 0.3},
        "serial": {}, "camera": {}, "tts": {}, "navigation": {},
        "tof": {}, "yolo": {}, "ocr": {},
    }


class FakeModels:
    def __init__(self, trace, fail_ocr=False):
        self.trace = trace
        self.active = None
        self.events = collections.deque()
        self.requests = []
        self.fail_ocr = fail_ocr
        self.yolo_starts = 0

    def start(self, kind, config, timeout):
        if self.active is not None:
            raise AssertionError("Concurrent model residency")
        self.active = kind
        self.trace.append(("start", kind))
        self.yolo_starts += kind == "yolo"
        self.events.append(("ready", None))

    def close(self):
        self.trace.append(("close", self.active))
        self.active = None
        self.events.clear()

    def submit(self, ident, captured, frame, timeout):
        self.requests.append((self.active, ident, captured, frame))
        if self.active == "ocr" and self.fail_ocr:
            self.events.append(RuntimeError("OCR initialization/inference failed"))
        else:
            value = "Nội dung chính của văn bản." if self.active == "ocr" else []
            self.events.append(("result", (ident, captured, value)))
        return True

    def poll(self):
        if not self.events:
            return None
        event = self.events.popleft()
        if isinstance(event, Exception):
            raise event
        return event


class FakeCamera:
    def __init__(self, app):
        self.app = app
        self.ident = 0

    def start(self):
        pass

    def close(self):
        pass

    def check_health(self):
        if self.app.models.yolo_starts == 2 and self.app.mode == Mode.NAVIGATING:
            self.app.stop_event.set()

    def latest(self, max_age):
        self.ident += 1
        return self.ident, time.monotonic(), f"frame-{self.ident}"


class FakeSpeech:
    def __init__(self, app, trace):
        self.app, self.trace = app, trace
        self.calls = []
        self.result_ticket = None
        self.wait_ticks = 0
        self.resident_models_during_speech = []

    def start(self):
        pass

    def close(self):
        pass

    def speak(self, request):
        self.calls.append(request)
        key = request.key
        ticket = SpeechTicket(request.text, request.priority, key, time.monotonic() + 30)
        if key == "ocr-result":
            self.result_ticket = ticket
        else:
            ticket.done.set()
        return ticket

    def check_health(self):
        if self.result_ticket is not None and not self.result_ticket.done.is_set():
            self.resident_models_during_speech.append(self.app.models.active)
            self.wait_ticks += 1
            if self.wait_ticks >= 4:
                self.trace.append(("speech_done", None))
                self.result_ticket.done.set()


class FakeLink:
    def __init__(self, app, buttons=True, distance=2000):
        self.app, self.buttons, self.distance = app, buttons, distance
        self.sent_modes = set()

    def start(self):
        pass

    def close(self):
        pass

    def check_health(self):
        pass

    def get_tof(self):
        return time.monotonic(), (self.distance,) * 64

    def poll_button(self):
        if self.buttons and self.app.mode in (Mode.NAVIGATING, Mode.OCR_RUNNING, Mode.OCR_SPEAKING):
            if self.app.mode not in self.sent_modes:
                self.sent_modes.add(self.app.mode)
                return len(self.sent_modes)
        return None


class AppTests(unittest.TestCase):
    def make_app(self, fail_ocr=False, buttons=True, distance=2000):
        app = AssistiveApp(config_fixture(), mock=True)
        trace = []
        app.models = FakeModels(trace, fail_ocr)
        app.camera = FakeCamera(app)
        app.speech = FakeSpeech(app, trace)
        app.link = FakeLink(app, buttons, distance)
        return app, trace

    def test_snapshot_model_exclusion_playback_wait_and_busy_button_coalescing(self):
        app, trace = self.make_app()
        with patch("assistive.app.notify_systemd"):
            app.run(duration=1)
        starts = [entry for entry in trace if entry[0] == "start"]
        self.assertEqual(starts, [("start", "yolo"), ("start", "ocr"), ("start", "yolo")])
        ocr_start = trace.index(("start", "ocr"))
        self.assertEqual(trace[ocr_start - 1], ("close", "yolo"))
        second_yolo = max(i for i, entry in enumerate(trace) if entry == ("start", "yolo"))
        self.assertLess(trace.index(("speech_done", None)), second_yolo)
        self.assertEqual(app.speech.resident_models_during_speech, [None] * 4)
        ocr_requests = [request for request in app.models.requests if request[0] == "ocr"]
        self.assertEqual(len(ocr_requests), 1)
        self.assertEqual(ocr_requests[0][1], 2)
        self.assertEqual(ocr_requests[0][3], "frame-2")
        self.assertEqual(len(app.link.sent_modes), 3)
        self.assertEqual(app.history, ["STARTING", "NAVIGATING", "OCR_LOADING", "OCR_RUNNING",
                                      "OCR_SPEAKING", "STARTING", "NAVIGATING", "STOPPED"])

    def test_ocr_failure_is_spoken_then_yolo_resumes(self):
        app, trace = self.make_app(fail_ocr=True)
        with patch("assistive.app.notify_systemd"), self.assertLogs("assistive.app", "ERROR"):
            app.run(duration=1)
        self.assertEqual(app.models.yolo_starts, 2)
        self.assertIn("speech_done", [entry[0] for entry in trace])
        failure_messages = [req.text for req in app.speech.calls if req.key == "ocr-result"]
        self.assertEqual(len(failure_messages), 1)
        self.assertIn("Không đọc được", failure_messages[0])

    def test_stale_vision_result_is_not_used_for_navigation(self):
        app, _ = self.make_app()
        app.mode = Mode.NAVIGATING
        app.navigation = Mock()
        now = time.monotonic()
        app._handle_model_event(("result", (1, now - 10, [])), now)
        app.navigation.analyze.assert_not_called()
        self.assertEqual(app.speech.calls[-1].key, "vision-stale")

    def test_fresh_tof_is_not_fused_with_temporally_distant_camera_frame(self):
        app, _ = self.make_app()
        app.mode = Mode.NAVIGATING
        app.navigation = Mock()
        app.navigation.analyze.return_value = {"text": "", "priority": 10, "key": ""}
        now = time.monotonic()
        app._handle_model_event(("result", (1, now - .6, [])), now)
        self.assertIsNone(app.navigation.analyze.call_args.args[1])

    def test_new_tof_arriving_during_tick_still_triggers_emergency(self):
        app, _ = self.make_app(buttons=False, distance=400)
        with patch("assistive.app.notify_systemd"):
            app.run(duration=.08)
        emergencies = [req for req in app.speech.calls if req.key == "tof_emergency"]
        self.assertTrue(emergencies)
        self.assertTrue(all(req.priority == 0 for req in emergencies))


if __name__ == "__main__":
    unittest.main()
