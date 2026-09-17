"""One camera owner; latest frame replaces previous frame instead of queueing."""
from __future__ import annotations

import threading
import time


class CameraWorker:
    def __init__(self, config):
        self.config = config
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._frame = None
        self.error = None
        self.thread = threading.Thread(target=self._run, name="camera", daemon=True)

    def start(self):
        self.thread.start()

    def latest(self, max_age=1.):
        with self._lock:
            item = self._frame
            if item is None or time.monotonic() - item[1] > max_age:
                return None
            return item[0], item[1], item[2].copy()

    def check_health(self):
        if self.error:
            raise RuntimeError("Camera worker failed") from self.error
        if not self.thread.is_alive() and not self._stop.is_set():
            raise RuntimeError("Camera worker stopped")

    def _run(self):
        camera = None
        backend = self.config.get("backend", "picamera2")
        try:
            width, height = self.config.get("width", 1280), self.config.get("height", 720)
            fps = self.config.get("fps", 15)
            if backend == "picamera2":
                from picamera2 import Picamera2
                from libcamera import controls
                camera = Picamera2()
                # Picamera2 RGB888 is byte-ordered B,G,R, compatible with OpenCV.
                camera.configure(camera.create_video_configuration(
                    main={"size": (width, height), "format": "RGB888"},
                    controls={"FrameRate": fps, "AfMode": controls.AfModeEnum.Continuous},
                    buffer_count=3))
                camera.start()
            elif backend == "opencv":
                import cv2
                camera = cv2.VideoCapture(self.config.get("device", 0))
                camera.set(cv2.CAP_PROP_FRAME_WIDTH, width)
                camera.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
                camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                if not camera.isOpened():
                    raise RuntimeError("Cannot open camera")
            elif backend != "mock":
                raise ValueError(f"Unknown camera backend: {backend}")
            ident = 0
            while not self._stop.is_set():
                if backend == "picamera2":
                    frame = camera.capture_array("main")
                elif backend == "opencv":
                    ok, frame = camera.read()
                    if not ok:
                        raise RuntimeError("Camera stopped delivering frames")
                else:
                    import numpy as np
                    frame = np.zeros((height, width, 3), dtype=np.uint8)
                ident += 1
                with self._lock:
                    self._frame = ident, time.monotonic(), frame
                if backend != "picamera2":
                    self._stop.wait(1. / fps)
        except BaseException as exc:
            self.error = exc
        finally:
            if camera is not None:
                if backend == "picamera2":
                    camera.stop()
                    camera.close()
                else:
                    camera.release()

    def close(self):
        self._stop.set()
        if self.thread.ident is not None:
            self.thread.join(2.)
        if self.thread.is_alive():
            raise RuntimeError("Camera did not stop; supervisor must restart application")
