import time
import random
from assistive.navigation import ToFFusion

def benchmark():
    config = {
        "bbox_inset": 0.1,
        "calibrated": True,
        "projection": {
            "validated_range_mm": [100, 4000],
            "mode": "homography",
            "matrix": [1, 0, 0, 0, 1, 0, 0, 0, 1]
        }
    }
    fusion = ToFFusion(config)

    # Create mock objects
    objects = []
    for _ in range(50):
        left = random.uniform(0, 0.8)
        top = random.uniform(0, 0.8)
        right = left + random.uniform(0.1, 0.2)
        bottom = top + random.uniform(0.1, 0.2)
        objects.append({"bbox": [left, top, right, bottom]})

    # Create mock values (64 zones)
    values = tuple(random.uniform(200, 3000) for _ in range(64))

    # Warmup
    for _ in range(10):
        fusion.distances(objects, values)

    start_time = time.perf_counter()
    iterations = 1000
    for _ in range(iterations):
        fusion.distances(objects, values)
    end_time = time.perf_counter()

    print(f"Time for {iterations} iterations: {end_time - start_time:.4f} seconds")

if __name__ == "__main__":
    benchmark()