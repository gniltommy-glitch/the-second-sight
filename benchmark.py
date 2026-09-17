import pytest
import time
import numpy as np

class MockBoxes:
    def __init__(self, n):
        self.boxes = [f"box_{i}" for i in range(n)]

    def __iter__(self):
        return iter(self.boxes)

    def __len__(self):
        return len(self.boxes)

class MockResult:
    def __init__(self, n):
        self.boxes = MockBoxes(n)

def test_append_loop(benchmark):
    results = [MockResult(100)]

    def run_append():
        obstacle_boxes = []
        if len(results) > 0 and results[0].boxes is not None:
            for box in results[0].boxes:
                obstacle_boxes.append(box)
        return obstacle_boxes

    benchmark(run_append)

def test_list_creation(benchmark):
    results = [MockResult(100)]

    def run_list():
        obstacle_boxes = []
        if len(results) > 0 and results[0].boxes is not None:
            obstacle_boxes = list(results[0].boxes)
        return obstacle_boxes

    benchmark(run_list)
