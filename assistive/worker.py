"""A single disposable AI process. Exiting it releases native model allocators."""
from __future__ import annotations

import multiprocessing as mp
import os
import queue
import time
import traceback


def _model_entry(kind, config, requests, responses):
    threads = str(config.get("threads", 2))
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ[name] = threads
    engine = None
    try:
        if config.get("mock", False):
            engine = MockEngine(kind)
        else:
            from .models import OCREngine, YoloEngine
            engine = YoloEngine(config) if kind == "yolo" else OCREngine(config)
        responses.put(("ready", None))
        while True:
            request = requests.get()
            if request is None:
                return
            ident, captured, frame = request
            value = engine.infer(frame) if kind == "yolo" else engine.read(frame)
            responses.put(("result", (ident, captured, value)))
    except BaseException:
        responses.put(("error", traceback.format_exc()))
    finally:
        if engine is not None:
            engine.close()


class MockEngine:
    def __init__(self, kind):
        self.kind = kind

    def infer(self, frame):
        return [{"label": "person", "confidence": .95, "bbox": [.4, .35, .6, .85]}]

    def read(self, frame):
        return "Đây là văn bản tiếng Việt mô phỏng. Chúc bạn một ngày tốt lành."

    def close(self):
        pass


class ModelProcess:
    """Owned exclusively by the controller. At most one request is in flight."""
    def __init__(self):
        self.context = mp.get_context("spawn")
        self.process = None
        self.requests = self.responses = None
        self.kind = None
        self.ready = False
        self.pending = False
        self.deadline = 0.

    def start(self, kind, config, timeout=90.):
        if self.process is not None:
            raise RuntimeError("Stop and join the previous model before loading another")
        self.requests = self.context.Queue(maxsize=1)
        self.responses = self.context.Queue(maxsize=2)
        self.kind, self.ready, self.pending = kind, False, False
        self.process = self.context.Process(target=_model_entry,
            args=(kind, config, self.requests, self.responses), name=f"ai-{kind}", daemon=True)
        self.process.start()
        self.deadline = time.monotonic() + timeout

    def submit(self, ident, captured, frame, timeout):
        if not self.ready or self.pending:
            return False
        self.requests.put_nowait((ident, captured, frame))
        self.pending = True
        self.deadline = time.monotonic() + timeout
        return True

    def poll(self):
        if self.process is None:
            return None
        try:
            kind, value = self.responses.get_nowait()
        except queue.Empty:
            if not self.process.is_alive():
                raise RuntimeError(f"{self.kind} process exited: {self.process.exitcode}")
            if (not self.ready or self.pending) and time.monotonic() > self.deadline:
                raise TimeoutError(f"{self.kind} model exceeded its deadline")
            return None
        if kind == "error":
            raise RuntimeError(value)
        if kind == "ready":
            self.ready = True
        elif kind == "result":
            self.pending = False
        return kind, value

    def close(self):
        process = self.process
        if process is None:
            return
        # Only request a graceful exit when idle. Inference may be stuck in C++.
        if self.ready and not self.pending:
            try:
                self.requests.put_nowait(None)
            except queue.Full:
                pass
            process.join(.5)
        if process.is_alive():
            process.terminate()
            process.join(2.)
        if process.is_alive():
            process.kill()
            process.join(2.)
        if process.is_alive():
            raise RuntimeError("Cannot reap AI process; refusing to load another model")
        # A killed queue producer may leave a partial frame; never reuse these queues.
        for channel in (self.requests, self.responses):
            channel.cancel_join_thread()
            channel.close()
        process.close()
        self.process = self.kind = None
        self.ready = self.pending = False
