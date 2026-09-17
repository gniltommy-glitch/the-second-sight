import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Make sure assistive is available for imports
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from assistive.config import load_config


class TestConfig(unittest.TestCase):
    def setUp(self):
        self.valid_config = {
            "camera": {"fps": 15},
            "serial": {"baudrate": 1000000},
            "yolo": {"path": "models/yolo.pt"},
            "ocr": {
                "det_model_dir": "models/det",
                "rec_model_dir": "models/rec",
                "vietocr_config": "config.yml",
                "vietocr_weights": "weights.pth"
            },
            "tts": {"model": "models/tts", "queue_size": 3},
            "navigation": {},
            "tof": {},
            "runtime": {}
        }

    def write_temp_config(self, config_data):
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, 'w') as f:
            json.dump(config_data, f)
        return path

    def test_valid_config(self):
        config_path = self.write_temp_config(self.valid_config)
        try:
            config = load_config(config_path)

            # Paths should be resolved relative to parent of project_root (default "..")
            source = Path(config_path).resolve()
            root = (source.parent / "..").resolve()

            self.assertEqual(config["yolo"]["path"], str((root / "models/yolo.pt").resolve()))
            self.assertEqual(config["ocr"]["det_model_dir"], str((root / "models/det").resolve()))
            self.assertEqual(config["ocr"]["rec_model_dir"], str((root / "models/rec").resolve()))
            self.assertEqual(config["ocr"]["vietocr_config"], str((root / "config.yml").resolve()))
            self.assertEqual(config["ocr"]["vietocr_weights"], str((root / "weights.pth").resolve()))
            self.assertEqual(config["tts"]["model"], str((root / "models/tts").resolve()))

            # Non-path values should be intact
            self.assertEqual(config["camera"]["fps"], 15)
            self.assertEqual(config["serial"]["baudrate"], 1000000)
            self.assertEqual(config["tts"]["queue_size"], 3)

        finally:
            os.remove(config_path)

    def test_missing_section(self):
        invalid_config = self.valid_config.copy()
        del invalid_config["camera"]
        config_path = self.write_temp_config(invalid_config)
        try:
            with self.assertRaisesRegex(ValueError, "Missing config object: camera"):
                load_config(config_path)
        finally:
            os.remove(config_path)

    def test_invalid_baudrate(self):
        invalid_config = self.valid_config.copy()
        invalid_config["serial"] = {"baudrate": 115200}
        config_path = self.write_temp_config(invalid_config)
        try:
            with self.assertRaisesRegex(ValueError, "Provided app.c requires baudrate=1000000"):
                load_config(config_path)
        finally:
            os.remove(config_path)

    def test_invalid_tts_queue_size(self):
        invalid_config = self.valid_config.copy()
        invalid_config["tts"] = {"model": "models/tts", "queue_size": 0}
        config_path = self.write_temp_config(invalid_config)
        try:
            with self.assertRaisesRegex(ValueError, "tts.queue_size must be positive"):
                load_config(config_path)
        finally:
            os.remove(config_path)

    def test_invalid_camera_fps(self):
        invalid_config = self.valid_config.copy()
        invalid_config["camera"] = {"fps": 0}
        config_path = self.write_temp_config(invalid_config)
        try:
            with self.assertRaisesRegex(ValueError, "camera.fps must be positive"):
                load_config(config_path)
        finally:
            os.remove(config_path)


if __name__ == '__main__':
    unittest.main()
