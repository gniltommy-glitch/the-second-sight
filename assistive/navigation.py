"""Conservative, dependency-free scene interpretation and calibrated 8x8 ToF fusion.

Inputs use normalized camera coordinates and a shared monotonic clock. This policy
offers observations, never a certification that a crossing or route is safe.
"""
from __future__ import annotations

import math
import time
from collections import Counter
from typing import Any


def _label(value: Any) -> str:
    return str(value).strip().lower().replace("-", "_").replace(" ", "_")


DEFAULT_LABELS = {
    "sidewalk": ["sidewalk", "walkway", "pavement", "footpath", "pedestrian_lane"],
    "road": ["road", "street", "vehicle_lane", "roadway"],
    "zebra": ["zebra_crossing", "crosswalk", "pedestrian_crossing"],
    "pedestrian_red": ["pedestrian_red", "pedestrian_light_red"],
    "pedestrian_green": ["pedestrian_green", "pedestrian_light_green"],
    "ignore": ["traffic_light", "pedestrian_light", "traffic_sign"],
}

VI_NAMES = {
    "person": "người", "people": "người", "bicycle": "xe đạp", "bike": "xe đạp",
    "motorcycle": "xe máy", "motorbike": "xe máy", "car": "ô tô", "bus": "xe buýt",
    "truck": "xe tải", "chair": "ghế", "bench": "ghế dài", "dog": "chó", "cat": "mèo",
    "pole": "cột", "tree": "cây", "potted_plant": "chậu cây", "pothole": "ổ gà",
    "stairs": "bậc thang", "stair": "bậc thang", "obstacle": "vật cản",
    "barrier": "rào chắn", "bollard": "cọc chắn", "trash_bin": "thùng rác",
    "fire_hydrant": "trụ cứu hỏa", "backpack": "ba lô", "suitcase": "va li",
}


def point_in_polygon(x: float, y: float, polygon: list) -> bool:
    """Ray casting, with edges included; malformed masks are not walkable."""
    if len(polygon) < 3:
        return False
    inside = False
    previous = polygon[-1]
    for current in polygon:
        x1, y1 = previous
        x2, y2 = current
        cross = (x - x1) * (y2 - y1) - (y - y1) * (x2 - x1)
        if abs(cross) < 1e-9 and min(x1, x2) <= x <= max(x1, x2) and min(y1, y2) <= y <= max(y1, y2):
            return True
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
        previous = current
    return inside


def polygon_intersects_box(polygon: list, box: tuple) -> bool:
    """Include thin road polygons that could fall between coverage samples."""
    left, top, right, bottom = box
    corners = [(left, top), (right, top), (right, bottom), (left, bottom)]
    if any(left <= x <= right and top <= y <= bottom for x, y in polygon):
        return True
    if any(point_in_polygon(x, y, polygon) for x, y in corners):
        return True
    for first, second in zip(polygon, polygon[1:] + polygon[:1]):
        for a, b in zip(corners, corners[1:] + corners[:1]):
            def orientation(p, q, r):
                return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
            o1, o2 = orientation(first, second, a), orientation(first, second, b)
            o3, o4 = orientation(a, b, first), orientation(a, b, second)
            if o1 * o2 < 0 and o3 * o4 < 0:
                return True
    return False


def _vector(value: Any, length: int) -> list[float] | None:
    try:
        # Accept a flattened vector or a JSON matrix.
        flattened = [number for row in value for number in row] if value and isinstance(value[0], (list, tuple)) else value
        result = [float(number) for number in flattened]
        return result if len(result) == length and all(math.isfinite(n) for n in result) else None
    except (ValueError, TypeError, IndexError):
        return None


class ToFFusion:
    """Projection must be explicitly calibrated before attaching a range to YOLO.

    Homography models a calibrated depth interval; pinhole uses sensor rays and a
    measured rigid transform into the camera. Neither assumes identical FOVs.
    """

    def __init__(self, config: dict):
        self.config = config
        self.projection = config.get("projection", {})

    def fresh_values(self, sample: tuple | None, now: float) -> tuple | None:
        if sample is None:
            return None
        try:
            timestamp, values = sample
            age = now - float(timestamp)
            if len(values) != 64 or not math.isfinite(age) or age < 0 or age > float(self.config.get("max_age_seconds", 0.35)):
                return None
            return tuple(values)
        except (TypeError, ValueError):
            return None

    def valid(self, value: Any) -> bool:
        try:
            value = float(value)
            return math.isfinite(value) and value not in (0, 65535) and float(self.config.get("min_mm", 100)) <= value <= float(self.config.get("max_mm", 4000))
        except (TypeError, ValueError):
            return False

    def emergency(self, values: tuple | None) -> float | None:
        if values is None:
            return None
        zones = self.config.get("emergency_zones", [27, 28, 35, 36])
        distances = sorted(float(values[i]) for i in zones if isinstance(i, int) and 0 <= i < 64 and self.valid(values[i]))
        if distances and distances[0] <= float(self.config.get("emergency_mm", 700)):
            # A single nearby valid zone suffices to request a stop, without
            # asserting which visual object generated that return.
            return distances[0]
        return None

    def project(self, index: int, distance_mm: float) -> tuple[float, float] | None:
        if not self.config.get("calibrated", False):
            return None
        bounds = _vector(self.projection.get("validated_range_mm"), 2)
        if not bounds or not 0 < bounds[0] < bounds[1] or not bounds[0] <= distance_mm <= bounds[1]:
            return None
        u, v = (index % 8 + 0.5) / 8, (index // 8 + 0.5) / 8
        if self.projection.get("flip_x", False):
            u = 1 - u
        if self.projection.get("flip_y", False):
            v = 1 - v
        for _ in range(int(self.projection.get("rotate_quarters", 0)) % 4):
            u, v = 1 - v, u
        mode = self.projection.get("mode", "homography")
        if mode == "homography":
            matrix = _vector(self.projection.get("matrix"), 9)
            if matrix is None:
                return None
            a, b, c, d, e, f, g, h, i = matrix
            determinant = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
            denominator = g * u + h * v + i
            if abs(determinant) < 1e-12 or abs(denominator) < 1e-12:
                return None
            x, y = (a * u + b * v + c) / denominator, (d * u + e * v + f) / denominator
        elif mode == "pinhole":
            intrinsics = _vector(self.projection.get("camera_intrinsics_normalized"), 4)
            rotation = _vector(self.projection.get("rotation"), 9)
            translation = _vector(self.projection.get("translation_mm"), 3)
            fov = _vector(self.projection.get("tof_fov_degrees"), 2)
            if intrinsics is None or rotation is None or translation is None or fov is None:
                return None
            fx, fy, cx, cy = intrinsics
            if fx <= 0 or fy <= 0 or not all(0 < angle < 180 for angle in fov):
                return None
            ray = [(2 * u - 1) * math.tan(math.radians(fov[0] / 2)), (2 * v - 1) * math.tan(math.radians(fov[1] / 2)), 1.0]
            # Firmware may expose axial or radial distances. It is not safe to
            # choose for an unidentified sensor/firmware combination.
            mode_range = self.projection.get("distance_mode")
            if mode_range not in {"radial", "axial"}:
                return None
            scale = distance_mm / math.sqrt(sum(a * a for a in ray)) if mode_range == "radial" else distance_mm
            camera = [sum(rotation[row * 3 + col] * ray[col] * scale for col in range(3)) + translation[row] for row in range(3)]
            if camera[2] <= 0:
                return None
            x, y = fx * camera[0] / camera[2] + cx, fy * camera[1] / camera[2] + cy
        else:
            return None
        return (x, y) if math.isfinite(x) and math.isfinite(y) and 0 <= x <= 1 and 0 <= y <= 1 else None

    def distances(self, objects: list[dict], values: tuple | None) -> dict[int, float]:
        if values is None:
            return {}
        support: dict[int, list[float]] = {i: [] for i in range(len(objects))}
        inset = min(0.4, max(0.0, float(self.config.get("bbox_inset", 0.1))))
        for zone, distance in enumerate(values):
            if not self.valid(distance):
                continue
            projected = self.project(zone, float(distance))
            if projected is None:
                continue
            x, y = projected
            candidates = []
            for index, item in enumerate(objects):
                left, top, right, bottom = item["bbox"]
                dx, dy = (right - left) * inset, (bottom - top) * inset
                if left + dx <= x <= right - dx and top + dy <= y <= bottom - dy:
                    polygon = item.get("polygon")
                    if not polygon or point_in_polygon(x, y, polygon):
                        candidates.append(index)
            # Overlapping boxes do not provide a reliable object association.
            if len(candidates) == 1:
                support[candidates[0]].append(float(distance))
        result = {}
        minimum = max(2, int(self.config.get("min_support", 2)))
        for index, numbers in support.items():
            if len(numbers) >= minimum:
                numbers.sort()
                result[index] = numbers[int((len(numbers) - 1) * 0.2)]
        return result


class NavigationPolicy:
    def __init__(self, config: dict | None = None, tof_config: dict | None = None):
        self.config = config or {}
        supplied_labels = self.config.get("labels", {})
        self.labels = {role: {_label(value) for value in supplied_labels.get(role, defaults)} for role, defaults in DEFAULT_LABELS.items()}
        self.names = {**VI_NAMES, **{_label(k): str(v) for k, v in self.config.get("class_names", {}).items()}}
        self.cash = {_label(k): str(v) for k, v in self.config.get("cash_labels", {}).items()}
        self.fusion = ToFFusion(tof_config or {})
        self._cross_state = ""
        self._cross_count = 0
        self._cross_started = 0.0
        self._cross_last = float("-inf")

    @staticmethod
    def direction(bbox: list) -> str:
        x, y = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
        col, row = min(2, int(x * 3)), min(2, int(y * 3))
        return (("góc trên bên trái", "phía trên trước mặt", "góc trên bên phải"),
                ("bên trái", "trước mặt", "bên phải"),
                ("góc dưới bên trái", "phía dưới trước mặt", "góc dưới bên phải"))[row][col]

    def _clean(self, detections: list[dict]) -> list[dict]:
        cleaned = []
        for detection in detections:
            try:
                confidence = float(detection.get("confidence", 0))
                bbox = _vector(detection.get("bbox"), 4)
                if not math.isfinite(confidence) or confidence < float(self.config.get("confidence", 0.45)) or not bbox:
                    continue
                if not all(0 <= number <= 1 for number in bbox) or bbox[0] >= bbox[2] or bbox[1] >= bbox[3]:
                    continue
                item = {"label": _label(detection["label"]), "confidence": confidence, "bbox": bbox}
                polygon = detection.get("polygon", [])
                if polygon:
                    points = [_vector(point, 2) for point in polygon]
                    if len(points) >= 3 and all(point and all(0 <= n <= 1 for n in point) for point in points):
                        item["polygon"] = points
                cleaned.append(item)
            except (KeyError, TypeError, ValueError):
                continue
        return cleaned

    def _lane(self, scene: list[dict], objects: list[dict]) -> tuple[str, str]:
        cfg = self.config.get("lane", {})
        walk = [d["polygon"] for d in scene if d["label"] in self.labels["sidewalk"] and d.get("polygon")]
        road = [d["polygon"] for d in scene if d["label"] in self.labels["road"] and d.get("polygon")]
        # A detected road with no usable mask blocks its entire box conservatively.
        for item in scene:
            if item["label"] in self.labels["road"] and not item.get("polygon"):
                x1, y1, x2, y2 = item["bbox"]
                road.append([[x1, y1], [x2, y1], [x2, y2], [x1, y2]])
        if not walk:
            return "Chưa xác định được lối đi bộ; hãy dừng để kiểm tra.", "lane_unknown"
        y_min = min(0.9, max(0.3, float(cfg.get("min_y", 0.55))))
        margin = max(0.0, float(cfg.get("obstacle_margin", 0.06)))
        candidates = []
        for name, left, right in [("center", 0.38, 0.62), ("left", 0.05, 0.30), ("right", 0.70, 0.95)]:
            points = [(left + (right - left) * (col + 0.5) / 5, y_min + (1 - y_min) * (row + 0.5) / 5) for row in range(5) for col in range(5)]
            walk_coverage = sum(any(point_in_polygon(x, y, p) for p in walk) for x, y in points) / len(points)
            road_overlap = any(polygon_intersects_box(p, (left, y_min, right, 1.0)) for p in road)
            blocked = any(d["bbox"][0] - margin <= right and d["bbox"][2] + margin >= left and d["bbox"][3] + margin >= y_min for d in objects)
            if walk_coverage >= float(cfg.get("sidewalk_coverage", 0.70)) and not road_overlap and not blocked:
                candidates.append((walk_coverage, name))
        if not candidates:
            return "Chưa xác định được hướng tránh trên lối đi bộ; hãy dừng để kiểm tra.", "lane_blocked"
        # Prefer the center when supported; ties preserve center/left/right order.
        chosen = next((name for _, name in candidates if name == "center"), max(candidates, key=lambda pair: pair[0])[1])
        if chosen == "center":
            return "Đang thấy lối đi bộ phía trước; tiếp tục kiểm tra vật cản.", "lane_center"
        side = "trái" if chosen == "left" else "phải"
        return f"Lối đi bộ nhận diện được lệch bên {side}; hãy dừng và kiểm tra trước khi đổi hướng.", f"lane_{chosen}"

    def _crossing(self, scene: list[dict], now: float) -> tuple[str, str] | None:
        zebra = any(d["label"] in self.labels["zebra"] and 1 / 3 <= (d["bbox"][0] + d["bbox"][2]) / 2 <= 2 / 3 and d["bbox"][3] >= 0.5 for d in scene)
        if not zebra:
            self._cross_state, self._cross_count = "", 0
            return None
        red = any(d["label"] in self.labels["pedestrian_red"] for d in scene)
        green = any(d["label"] in self.labels["pedestrian_green"] for d in scene)
        state = "red" if red else "green" if green else "unknown"
        cfg = self.config.get("crossing", {})
        if state != self._cross_state or now < self._cross_last or now - self._cross_last > float(cfg.get("max_gap_seconds", 1.0)):
            self._cross_state, self._cross_count, self._cross_started = state, 1, now
        else:
            self._cross_count = min(1000000, self._cross_count + 1)
        self._cross_last = now
        if red:
            return "Có vạch qua đường phía trước. Đèn người đi bộ màu đỏ; hãy dừng, chưa sang đường.", "crossing_red"
        stable = self._cross_count >= int(cfg.get("min_observations", 3)) and now - self._cross_started >= float(cfg.get("stable_seconds", 0.6))
        if green and stable:
            return "Có vạch qua đường phía trước; nhận diện đèn người đi bộ màu xanh. Chưa xác nhận được xe đang dừng; hãy dừng và nhờ hỗ trợ trước khi sang đường.", "crossing_green_observed"
        return "Có vạch qua đường phía trước. Chưa xác nhận được đèn người đi bộ; hãy dừng, chưa sang đường.", "crossing_unknown"

    @staticmethod
    def _meters(distance: float) -> str:
        # Round down to avoid making an obstacle sound farther away.
        value = max(0.1, math.floor(distance / 100) / 10)
        return f"{value:.1f}".replace(".", ",")

    def analyze(self, detections: list[dict], tof: tuple | None = None, now: float | None = None) -> dict:
        now = time.monotonic() if now is None else now
        scene = self._clean(detections)
        values = self.fusion.fresh_values(tof, now)
        emergency = self.fusion.emergency(values)
        crossing = self._crossing(scene, now)
        if emergency is not None:
            return {"text": f"Dừng lại. Cảm biến báo vật cản phía trước cách khoảng {self._meters(emergency)} mét.", "priority": 0, "key": "tof_emergency"}
        semantics = set().union(*self.labels.values())
        objects = [item for item in scene if item["label"] not in semantics and item["label"] not in self.cash]
        ranges = self.fusion.distances(objects, values)
        parts, keys = [], []
        if crossing:
            parts.append(crossing[0])
            keys.append(crossing[1])
        count = len(objects)
        if count > int(self.config.get("max_details", 4)):
            parts.append("Phía trước có nhiều chướng ngại vật.")
            keys.append("many_obstacles")
        elif objects:
            details = []
            for index, item in enumerate(objects):
                label = item["label"]
                name = self.names.get(label, label.replace("_", " "))
                direction = self.direction(item["bbox"])
                detail = f"{name} {direction}"
                if index in ranges:
                    detail += f", cách khoảng {self._meters(ranges[index])} mét"
                details.append(detail)
                keys.append(f"{label}:{direction}")
            parts.append("Có " + "; ".join(details) + ".")
        if not crossing:
            lane_text, lane_key = self._lane(scene, objects)
            parts.append(lane_text)
            keys.append(lane_key)
        currency = Counter(self.cash[item["label"]] for item in scene if item["label"] in self.cash)
        if currency:
            parts.append("Nhận diện tiền: " + "; ".join(f"{count} tờ {name}" for name, count in sorted(currency.items())) + ".")
            keys.extend(f"cash:{name}:{count}" for name, count in sorted(currency.items()))
        priority = 1 if crossing and crossing[1] in {"crossing_red", "crossing_unknown"} else 10
        return {"text": " ".join(parts), "priority": priority, "key": "|".join(sorted(keys))}
