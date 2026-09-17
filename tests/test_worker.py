import multiprocessing
import time
import unittest

from assistive.worker import ModelProcess


class ModelProcessTests(unittest.TestCase):
    def setUp(self):
        self.worker = ModelProcess()
        self.addCleanup(self.worker.close)

    def await_event(self, expected, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            event = self.worker.poll()
            if event is not None:
                self.assertEqual(event[0], expected)
                return event[1]
            time.sleep(0.005)
        self.fail(f"No {expected} event from spawned process")

    def test_real_spawn_serializes_requests_and_reaps_yolo_before_ocr(self):
        self.worker.start("yolo", {"mock": True})
        yolo_pid = self.worker.process.pid
        self.await_event("ready")
        with self.assertRaisesRegex(RuntimeError, "previous model"):
            self.worker.start("ocr", {"mock": True})
        captured = time.monotonic()
        self.assertTrue(self.worker.submit(7, captured, b"frame", 2))
        self.assertFalse(self.worker.submit(8, captured, b"second frame", 2))
        ident, result_time, detections = self.await_event("result")
        self.assertEqual((ident, result_time), (7, captured))
        self.assertEqual(detections[0]["label"], "person")
        self.worker.close()
        self.assertNotIn(yolo_pid, {process.pid for process in multiprocessing.active_children()})
        self.worker.start("ocr", {"mock": True})
        self.await_event("ready")
        self.assertTrue(self.worker.submit(9, captured, b"snapshot", 2))
        ident, result_time, text = self.await_event("result")
        self.assertEqual((ident, result_time), (9, captured))
        self.assertIn("tiếng Việt", text)

    def test_model_load_timeout_is_detected_and_process_can_be_reaped(self):
        self.worker.start("yolo", {"mock": True}, timeout=-1)
        with self.assertRaises(TimeoutError):
            self.worker.poll()
        self.worker.close()
        self.assertIsNone(self.worker.process)

    def test_dead_worker_does_not_silently_keep_navigation_alive(self):
        self.worker.start("yolo", {"mock": True})
        self.await_event("ready")
        self.worker.process.terminate()
        self.worker.process.join(2)
        with self.assertRaisesRegex(RuntimeError, "process exited"):
            self.worker.poll()


if __name__ == "__main__":
    unittest.main()
