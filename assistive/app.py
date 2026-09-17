"""Controller state machine. Only this thread may change the AI process."""
from __future__ import annotations

from enum import Enum, auto
import logging
import os
import socket
import threading
import time

from .audio import SpeechWorker
from .camera import CameraWorker
from .navigation import NavigationPolicy
from .worker import ModelProcess

log = logging.getLogger(__name__)


class Mode(Enum):
    STARTING = auto()
    NAVIGATING = auto()
    OCR_LOADING = auto()
    OCR_RUNNING = auto()
    OCR_SPEAKING = auto()
    STOPPED = auto()


def notify_systemd(message):
    address = os.environ.get("NOTIFY_SOCKET")
    if not address or not hasattr(socket, "AF_UNIX"):
        return
    if address.startswith("@"):
        address = "\0" + address[1:]
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as client:
        client.sendto(message.encode(), address)


class AssistiveApp:
    def __init__(self, config, mock=False):
        self.config = config
        self.runtime = config["runtime"]
        self.mock = mock
        if mock:
            from .mock import MockLink
            self.link = MockLink()
        else:
            from .serial_link import SerialLink
            self.link = SerialLink(config["serial"])
        self.camera = CameraWorker(dict(config["camera"], **({"backend": "mock"} if mock else {})))
        self.speech = SpeechWorker(dict(config["tts"], mock=mock), self.link)
        self.models = ModelProcess()
        self.navigation = NavigationPolicy(config["navigation"], config["tof"])
        # Separate policy prevents ToF-only polling from resetting crossing history.
        self.tof_policy = NavigationPolicy(config["navigation"], config["tof"])
        self.stop_event = threading.Event()
        self.mode = Mode.STOPPED
        self.history = []
        self.snapshot = None
        self.ocr_ticket = None
        self.last_frame_id = -1
        self.last_submit = 0.
        self.last_camera_seen = time.monotonic()
        self.last_tof_seen = time.monotonic()
        self.mode_since = time.monotonic()
        self.started = False
        self.yolo_failures = 0
        self.closed = False

    def _set_mode(self, mode):
        self.mode, self.mode_since = mode, time.monotonic()
        self.history.append(mode.name)
        self.history[:] = self.history[-32:]
        log.info("Mode: %s", mode.name)

    def _start_yolo(self):
        self.models.close()
        self.snapshot = None
        self.ocr_ticket = None
        self.navigation = NavigationPolicy(self.config["navigation"], self.config["tof"])
        self.models.start("yolo", dict(self.config["yolo"], mock=self.mock),
                          self.runtime.get("model_load_timeout", 90.))
        self._set_mode(Mode.STARTING)

    def _start_ocr(self, snapshot):
        # snapshot captured before shutdown/loading; never OCR a later unrelated frame.
        self.snapshot = snapshot
        self.speech.speak("Đang đọc chữ. Bạn hãy đứng yên.", priority=5,
                          key="ocr-start", ttl=30., repeat_after=0., replace=True)
        self.models.close()
        self.models.start("ocr", dict(self.config["ocr"], mock=self.mock),
                          self.runtime.get("model_load_timeout", 90.))
        self._set_mode(Mode.OCR_LOADING)

    def _say_ocr(self, text):
        self.models.close()  # Return OCR memory while Piper reads; YOLO remains unloaded.
        self.snapshot = None
        self.ocr_ticket = self.speech.speak(text or "Không đọc được chữ rõ ràng. Bạn hãy thử lại.",
            priority=5, key="ocr-result", ttl=60., repeat_after=0.)
        self._set_mode(Mode.OCR_SPEAKING)

    def run(self, duration=None, mock_button_after=None):
        started_at = time.monotonic()
        button_sent = False
        try:
            self.link.start()
            self.camera.start()
            self.speech.start()
            self._start_yolo()
            while not self.stop_event.is_set():
                now = time.monotonic()
                if duration is not None and now - started_at >= duration:
                    break
                self.link.check_health()
                self.camera.check_health()
                self.speech.check_health()
                frame = self.camera.latest(self.runtime.get("frame_max_age", 1.))
                if frame is not None:
                    self.last_camera_seen = now
                elif now - self.last_camera_seen > self.runtime.get("camera_timeout", 5.):
                    raise TimeoutError("No fresh camera frame")
                tof = self.link.get_tof()
                if tof is not None:
                    self.last_tof_seen = now
                    alert = self.tof_policy.analyze([], tof, now=time.monotonic())
                    if alert["priority"] == 0 and alert["text"]:
                        self.speech.speak(alert["text"], priority=0, key=alert["key"],
                                          repeat_after=3., ttl=1.)
                elif now - self.last_tof_seen > 3.:
                    self.speech.speak("Chưa có dữ liệu khoảng cách. Bạn hãy thận trọng.",
                                      priority=3, key="tof-unavailable", ttl=5., repeat_after=20.)
                if self.mock and mock_button_after is not None and not button_sent:
                    if now - started_at >= mock_button_after and self.mode == Mode.NAVIGATING:
                        self.link.press()
                        button_sent = True
                # Firmware retransmissions deduped in SerialLink; busy presses coalesce.
                button = self.link.poll_button()
                if button is not None:
                    if self.mode in (Mode.STARTING, Mode.NAVIGATING) and frame is not None:
                        self._start_ocr(frame)
                    elif self.mode in (Mode.OCR_LOADING, Mode.OCR_RUNNING, Mode.OCR_SPEAKING):
                        log.info("Button acknowledged while OCR busy; request coalesced")
                    else:
                        self.speech.speak("Camera chưa sẵn sàng. Bạn hãy bấm lại.", priority=5)
                try:
                    event = self.models.poll()
                    if event:
                        self._handle_model_event(event, now)
                except (RuntimeError, TimeoutError) as exc:
                    log.exception("AI worker failed")
                    if self.mode in (Mode.OCR_LOADING, Mode.OCR_RUNNING):
                        self._say_ocr("Không đọc được văn bản. Bạn hãy thử lại.")
                    else:
                        self.yolo_failures += 1
                        if self.yolo_failures >= 3:
                            raise RuntimeError("YOLO repeatedly failed") from exc
                        self.speech.speak("Nhận diện tạm gián đoạn. Bạn hãy dừng lại.",
                                          priority=1, key="ai-failed", repeat_after=0.)
                        self._start_yolo()
                if self.mode == Mode.NAVIGATING and frame is not None:
                    interval = 1. / self.runtime.get("max_inference_fps", 5.)
                    if frame[0] != self.last_frame_id and now - self.last_submit >= interval:
                        if self.models.submit(*frame, self.runtime.get("yolo_timeout", 15.)):
                            self.last_frame_id, self.last_submit = frame[0], now
                elif self.mode == Mode.OCR_SPEAKING:
                    if self.ocr_ticket.done.is_set():
                        if self.ocr_ticket.error:
                            raise RuntimeError("OCR audio output failed") from self.ocr_ticket.error
                        if self.ocr_ticket.cancelled.is_set():
                            log.info("OCR speech interrupted by higher priority alert")
                        self._start_yolo()
                    elif now - self.mode_since > self.runtime.get("ocr_speech_timeout", 300.):
                        raise TimeoutError("OCR speech did not finish")
                if not self.started and self.mode == Mode.NAVIGATING:
                    self.started = True
                    notify_systemd("READY=1")
                notify_systemd(f"WATCHDOG=1\nSTATUS={self.mode.name}")
                self.stop_event.wait(.02)
        finally:
            self.close()

    def _handle_model_event(self, event, now):
        kind, value = event
        if kind == "ready":
            if self.mode == Mode.STARTING:
                self._set_mode(Mode.NAVIGATING)
            elif self.mode == Mode.OCR_LOADING:
                self.models.submit(*self.snapshot, self.runtime.get("ocr_timeout", 60.))
                self._set_mode(Mode.OCR_RUNNING)
        elif kind == "result":
            _, captured, result = value
            if self.mode == Mode.OCR_RUNNING:
                self._say_ocr(result)
            elif self.mode == Mode.NAVIGATING:
                if now - captured > self.runtime.get("result_max_age", 1.5):
                    self.speech.speak("Hình ảnh xử lý chậm. Bạn hãy dừng lại.", priority=2,
                                      key="vision-stale", repeat_after=10.)
                    return
                self.yolo_failures = 0
                tof = self.link.get_tof()
                # Arrival timestamps only: firmware supplies no capture timestamp.
                if tof and abs(tof[0] - captured) > self.runtime.get("fusion_max_skew", .3):
                    tof = None
                alert = self.navigation.analyze(result, tof, now=time.monotonic())
                if alert["text"]:
                    self.speech.speak(alert["text"], priority=alert["priority"],
                        key=alert["key"], ttl=2., repeat_after=self.runtime.get("repeat_seconds", 5.))

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.stop_event.set()
        failures = []
        for component in (self.models, self.camera, self.speech, self.link):
            try:
                component.close()
            except Exception as exc:
                failures.append(exc)
                log.exception("Cleanup failed")
        self._set_mode(Mode.STOPPED)
        if failures:
            raise RuntimeError("Not all workers stopped cleanly") from failures[0]
