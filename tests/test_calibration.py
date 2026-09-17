import importlib.util
import unittest

from scripts.calibrate_tof import fit_calibration, measurement_points


@unittest.skipUnless(importlib.util.find_spec("cv2") and importlib.util.find_spec("numpy"), "OpenCV/NumPy not installed")
class CalibrationTests(unittest.TestCase):
    @staticmethod
    def measurements():
        source = [[0.1, 0.1], [0.5, 0.1], [0.9, 0.1], [0.1, 0.9], [0.5, 0.9], [0.9, 0.9]]
        return {"validated_range_mm": [600, 2000], "pairs": [
            {"tof_uv": [u, v], "camera_xy": [0.8 * u + 0.1, 0.8 * v + 0.1]} for u, v in source],
            "validation_pairs": [{"tof_uv": [0.3, 0.3], "camera_xy": [0.34, 0.34]}]}

    def test_recovers_transform_without_enabling_it(self):
        candidate, report = fit_calibration(self.measurements())
        self.assertIs(candidate["tof"]["calibrated"], False)
        matrix = candidate["tof"]["projection"]["matrix"]
        self.assertAlmostEqual(matrix[0][0], 0.8, places=5)
        self.assertAlmostEqual(matrix[1][2], 0.1, places=5)
        self.assertLess(report["validation_rmse_normalized"], 1e-6)

    def test_bad_independent_measurement_is_reported(self):
        data = self.measurements()
        data["validation_pairs"][0]["camera_xy"] = [0.8, 0.8]
        candidate, report = fit_calibration(data)
        self.assertIs(candidate["tof"]["calibrated"], False)
        self.assertTrue(any("exceeds" in text for text in report["warnings"]))

    def test_collinear_measurements_are_rejected(self):
        data = self.measurements()
        for pair in data["pairs"]:
            pair["tof_uv"][1] = 0.5
        with self.assertRaises(ValueError):
            fit_calibration(data)

    def test_invalid_depth_range_and_coordinates_rejected(self):
        for bounds in ([2000, 500], [float("nan"), 2000], [0, 2000]):
            data = self.measurements()
            data["validated_range_mm"] = bounds
            with self.assertRaises(ValueError):
                fit_calibration(data)
        data = self.measurements()
        data["pairs"][0]["camera_xy"] = [1.1, 0.5]
        with self.assertRaises(ValueError):
            fit_calibration(data)

    def test_zone_orientation_matches_runtime(self):
        source, target = measurement_points([{"zone": 0, "camera_xy": [0.5, 0.5]}],
                                            {"flip_x": True, "flip_y": False, "rotate_quarters": 1})
        self.assertEqual(source, [[0.9375, 0.9375]])
        self.assertEqual(target, [[0.5, 0.5]])


if __name__ == "__main__":
    unittest.main()
