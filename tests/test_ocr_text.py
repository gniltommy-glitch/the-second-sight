from pathlib import Path
import unittest

from assistive.models import OCRLine, OCREngine, YoloEngine, clean_text, select_main_text


class OCRTextTests(unittest.TestCase):
    def test_normalization_preserves_vietnamese_and_strips_controls(self):
        self.assertEqual(clean_text("  Tie\u0302\u0301ng\tVie\u0323\u0302t\x00  \n  có dấu "), "Tiếng Việt\n có dấu".replace("\n ", "\n"))

    def test_confidence_filter_does_not_add_words(self):
        lines = [OCRLine("Nhà thuốc", .91, (10, 10, 200, 40)), OCRLine("Đoán sai", .3, (10, 50, 200, 80))]
        self.assertEqual(select_main_text(lines, 640, 480), "Nhà thuốc")

    def test_reading_order_is_rows_then_left_to_right(self):
        lines = [OCRLine("thứ hai", .9, (10, 50, 200, 70)), OCRLine("phải", .9, (210, 12, 280, 32)), OCRLine("trái", .9, (10, 10, 80, 30))]
        self.assertEqual(select_main_text(lines, 640, 480, region_mode="all"), "trái phải\nthứ hai")

    def test_main_block_excludes_small_distant_background_text(self):
        lines = [OCRLine("CỬA HÀNG", .95, (150, 120, 500, 170)), OCRLine("MỞ CỬA", .95, (150, 180, 500, 230)), OCRLine("A", .8, (5, 5, 15, 15))]
        self.assertEqual(select_main_text(lines, 640, 480), "CỬA HÀNG\nMỞ CỬA")

    def test_repeated_words_at_distinct_positions_are_preserved(self):
        line = OCRLine("có", .9, (10, 10, 30, 30))
        lines = [line, line, OCRLine("có", .9, (40, 10, 60, 30))]
        self.assertEqual(select_main_text(lines, 640, 480, region_mode="all"), "có có")

    def test_reject_nan_confidence_and_invalid_box(self):
        lines = [OCRLine("bad", float("nan"), (1, 1, 5, 5)), OCRLine("bad", .99, (5, 5, 1, 1))]
        self.assertEqual(select_main_text(lines, 640, 480), "")

    def test_bounded_text_stops_before_partial_word(self):
        lines = [OCRLine("một hai ba bốn năm", .9, (10, 10, 100, 30))]
        self.assertEqual(select_main_text(lines, 640, 480, max_chars=12), "một hai ba")

    def test_backend_selection_is_explicit(self):
        with self.assertRaisesRegex(ValueError, "backend"):
            OCREngine({"backend": "auto"})

    def test_local_assets_validated_before_heavy_imports(self):
        directory = Path(__file__).parent
        with self.assertRaisesRegex(FileNotFoundError, "det_model_dir"):
            OCREngine({"backend": "paddle", "det_model_dir": str(directory / "missing-paddle-model")})
        with self.assertRaisesRegex(ValueError, "NCNN"):
            YoloEngine({"path": str(directory)})


if __name__ == "__main__":
    unittest.main()
