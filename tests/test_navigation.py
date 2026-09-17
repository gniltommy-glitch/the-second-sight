import unittest

from assistive.navigation import NavigationPolicy, ToFFusion


def detection(label, box=(0.40, 0.4, 0.60, 0.6), polygon=None):
    result = {"label": label, "confidence": 0.9, "bbox": list(box)}
    if polygon is not None:
        result["polygon"] = polygon
    return result


def mask(label, left=0, top=0.5, right=1, bottom=1):
    return detection(label, (left, top, right, bottom), [[left, top], [right, top], [right, bottom], [left, bottom]])


def calibrated_tof(**extra):
    return {"calibrated": True, "emergency_mm": 500, "projection": {
        "mode": "homography", "matrix": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        "validated_range_mm": [600, 3000]}, **extra}


class NavigationTests(unittest.TestCase):
    def test_four_objects_are_detailed_and_five_are_summarized(self):
        policy = NavigationPolicy()
        four = policy.analyze([detection("person") for _ in range(4)], now=10)
        five = policy.analyze([detection("person") for _ in range(5)], now=10)
        self.assertEqual(four["text"].count("người trước mặt"), 4)
        self.assertNotIn("nhiều chướng ngại vật", four["text"])
        self.assertIn("Phía trước có nhiều chướng ngại vật", five["text"])

    def test_semantic_classes_and_cash_are_not_obstacles(self):
        policy = NavigationPolicy({"cash_labels": {"10000_VND": "mười nghìn đồng"}})
        result = policy.analyze([mask("sidewalk"), detection("10000_VND"), detection("pedestrian_red")], now=10)
        self.assertIn("1 tờ mười nghìn đồng", result["text"])
        self.assertNotIn("nhiều chướng ngại vật", result["text"])
        self.assertNotIn("pedestrian red", result["text"])

    def test_direction_grid(self):
        self.assertEqual(NavigationPolicy.direction([0, 0, 0.2, 0.2]), "góc trên bên trái")
        self.assertEqual(NavigationPolicy.direction([0.8, 0.8, 1, 1]), "góc dưới bên phải")
        self.assertEqual(NavigationPolicy.direction([0.4, 0.4, 0.6, 0.6]), "trước mặt")

    def test_no_mask_and_no_objects_does_not_claim_clear_path(self):
        result = NavigationPolicy().analyze([], now=10)
        self.assertIn("Chưa xác định được lối đi bộ", result["text"])
        self.assertIn("dừng", result["text"])

    def test_sidewalk_box_without_segmentation_is_insufficient(self):
        result = NavigationPolicy().analyze([detection("sidewalk", [0, 0.5, 1, 1])], now=10)
        self.assertIn("lane_unknown", result["key"])

    def test_supported_left_lane_with_road_on_right(self):
        scene = [mask("sidewalk", right=0.32), mask("road", left=0.34), detection("person", [0.4, 0.4, 0.6, 0.9])]
        result = NavigationPolicy().analyze(scene, now=10)
        self.assertIn("lệch bên trái", result["text"])
        self.assertIn("dừng và kiểm tra", result["text"])

    def test_road_wins_over_conflicting_sidewalk_masks(self):
        result = NavigationPolicy().analyze([mask("sidewalk"), mask("road")], now=10)
        self.assertIn("lane_blocked", result["key"])

    def test_road_without_mask_conservatively_blocks_its_box(self):
        result = NavigationPolicy().analyze([mask("sidewalk"), detection("road", [0, 0.4, 1, 1])], now=10)
        self.assertIn("lane_blocked", result["key"])

    def test_thin_road_between_sampling_rows_still_blocks(self):
        result = NavigationPolicy().analyze([mask("sidewalk"), mask("road", top=0.59, bottom=0.591)], now=10)
        self.assertIn("lane_blocked", result["key"])

    def test_obstacle_inflation_blocks_narrow_corridor(self):
        result = NavigationPolicy().analyze([mask("sidewalk", right=0.32), detection("pole", [0.31, 0.55, 0.34, 1])], now=10)
        self.assertIn("lane_blocked", result["key"])

    def test_uncalibrated_tof_does_not_attach_object_distance(self):
        result = NavigationPolicy().analyze([detection("person")], (10, (1800,) * 64), now=10)
        self.assertNotIn("cách khoảng", result["text"])

    def test_stale_future_and_bad_tof_are_ignored(self):
        policy = NavigationPolicy({}, calibrated_tof())
        for sample in [(9, (1800,) * 64), (11, (1800,) * 64), (10, (1800,) * 63), (float("nan"), (1800,) * 64)]:
            self.assertNotIn("cách khoảng", policy.analyze([detection("person")], sample, now=10)["text"])

    def test_calibrated_fusion_adds_supported_distance(self):
        result = NavigationPolicy({}, calibrated_tof()).analyze([detection("person", [0.3, 0.3, 0.7, 0.7])], (10, (1800,) * 64), now=10)
        self.assertIn("người trước mặt, cách khoảng 1,8 mét", result["text"])

    def test_overlap_ambiguity_does_not_attach_range_to_two_objects(self):
        result = NavigationPolicy({}, calibrated_tof()).analyze([detection("person"), detection("car")], (10, (1800,) * 64), now=10)
        self.assertNotIn("cách khoảng", result["text"])

    def test_single_return_is_insufficient_for_object_range(self):
        values = [0] * 64
        values[27] = 1500
        result = NavigationPolicy({}, calibrated_tof()).analyze([detection("person")], (10, tuple(values)), now=10)
        self.assertNotIn("cách khoảng", result["text"])

    def test_emergency_does_not_require_camera_calibration(self):
        values = [0] * 64
        values[27] = 430
        result = NavigationPolicy().analyze([], (10, tuple(values)), now=10)
        self.assertEqual(result["priority"], 0)
        self.assertEqual(result["key"], "tof_emergency")
        self.assertIn("0,4 mét", result["text"])

    def test_invalid_range_does_not_trigger_emergency(self):
        for value in [0, 65535, -1, float("nan"), 50]:
            result = NavigationPolicy().analyze([], (10, (value,) * 64), now=10)
            self.assertNotEqual(result["priority"], 0)

    def test_low_confidence_and_invalid_boxes_are_ignored(self):
        scene = [{"label": "person", "confidence": 0.1, "bbox": [0, 0, 1, 1]}, detection("car", [0, 0, 2, 1]), detection("dog", [0.2, 0.2, 0.1, 0.1])]
        self.assertNotIn("Có ", NavigationPolicy().analyze(scene, now=10)["text"])

    def test_red_overrides_green_immediately(self):
        scene = [detection("zebra_crossing", [0.25, 0.6, 0.75, 1]), detection("pedestrian_green"), detection("pedestrian_red")]
        result = NavigationPolicy().analyze(scene, now=10)
        self.assertIn("crossing_red", result["key"])
        self.assertIn("chưa sang đường", result["text"])

    def test_green_needs_temporal_persistence_and_never_certifies_safety(self):
        policy = NavigationPolicy()
        scene = [detection("zebra_crossing", [0.25, 0.6, 0.75, 1]), detection("pedestrian_green")]
        self.assertIn("crossing_unknown", policy.analyze(scene, now=10)["key"])
        self.assertIn("crossing_unknown", policy.analyze(scene, now=10.3)["key"])
        result = policy.analyze(scene, now=10.7)
        self.assertIn("crossing_green_observed", result["key"])
        self.assertIn("Chưa xác nhận được xe đang dừng", result["text"])

    def test_crossing_gap_resets_stability(self):
        policy = NavigationPolicy()
        scene = [detection("zebra_crossing", [0.25, 0.6, 0.75, 1]), detection("pedestrian_green")]
        for timestamp in [10, 10.3, 10.7]:
            policy.analyze(scene, now=timestamp)
        self.assertIn("crossing_unknown", policy.analyze(scene, now=12)["key"])

    def test_zebra_only_and_generic_light_do_not_imply_green(self):
        scene = [detection("zebra_crossing", [0.25, 0.6, 0.75, 1]), detection("traffic_light")]
        result = NavigationPolicy().analyze(scene, now=10)
        self.assertIn("crossing_unknown", result["key"])

    def test_side_zebra_does_not_enable_crossing_guidance(self):
        scene = [detection("zebra_crossing", [0, 0.6, 0.2, 1]), detection("pedestrian_green")]
        self.assertNotIn("crossing", NavigationPolicy().analyze(scene, now=10)["key"])


class ProjectionTests(unittest.TestCase):
    def test_calibrated_flag_without_matrix_is_insufficient(self):
        self.assertIsNone(ToFFusion({"calibrated": True}).project(27, 1000))

    def test_homography_and_orientation(self):
        fusion = ToFFusion(calibrated_tof())
        self.assertEqual(fusion.project(0, 1000), (0.0625, 0.0625))
        fusion.projection["flip_x"] = True
        self.assertEqual(fusion.project(0, 1000), (0.9375, 0.0625))
        fusion.projection["rotate_quarters"] = 1
        self.assertEqual(fusion.project(0, 1000), (0.9375, 0.9375))

    def test_projection_requires_validated_depth_interval(self):
        fusion = ToFFusion(calibrated_tof())
        self.assertIsNone(fusion.project(0, 500))
        self.assertIsNone(fusion.project(0, 3500))

    def test_singular_homography_is_rejected(self):
        fusion = ToFFusion(calibrated_tof())
        fusion.projection["matrix"] = [0] * 9
        self.assertIsNone(fusion.project(0, 1000))

    def test_pinhole_projector_respects_extrinsics(self):
        fusion = ToFFusion({"calibrated": True, "projection": {
            "mode": "pinhole", "validated_range_mm": [500, 3000],
            "camera_intrinsics_normalized": [0.5, 0.5, 0.5, 0.5],
            "rotation": [1, 0, 0, 0, 1, 0, 0, 0, 1],
            "translation_mm": [100, 0, 0], "tof_fov_degrees": [60, 60], "distance_mode": "axial"}})
        x, y = fusion.project(27, 1000)
        self.assertGreater(x, y)
        self.assertAlmostEqual(x - y, 0.05)


if __name__ == "__main__":
    unittest.main()
