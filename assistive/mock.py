"""Explicit simulated hardware; never selected implicitly on a real device."""
import queue
import time


class MockLink:
    def __init__(self):
        self.buttons = queue.Queue(maxsize=1)
        self.played = 0

    def start(self):
        pass

    def close(self):
        pass

    def check_health(self):
        pass

    def get_tof(self):
        return time.monotonic(), (2000,) * 64

    def poll_button(self):
        try:
            return self.buttons.get_nowait()
        except queue.Empty:
            return None

    def press(self):
        try:
            self.buttons.put_nowait(1)
        except queue.Full:
            pass

    def play_pcm(self, pcm, cancel=None):
        self.played += 1
        duration = min(.1, len(pcm) / 32000.)
        if cancel is not None:
            cancel.wait(duration)
        else:
            time.sleep(duration)
