"""Local CPU inference adapters, imported only inside the model worker process.

The module itself has no heavy imports: utilities and configuration validation can
be tested without Torch, Paddle, OpenCV, or attached hardware. ``close`` releases
Python references; the owner must EXIT/JOIN the process to reclaim native RAM.
"""
from __future__ import annotations

from dataclasses import dataclass
import gc
import math
import os
from pathlib import Path
import re
import unicodedata
from typing import Any


def clean_text(text: str) -> str:
    """Preserve Vietnamese accents; never infer missing words or punctuation."""
    text = unicodedata.normalize("NFC", str(text))
    text = "".join(c for c in text if not unicodedata.category(c).startswith("C") or c in "\n\t")
    return "\n".join(re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip())


@dataclass(frozen=True)
class OCRLine:
    text: str
    confidence: float
    bbox: tuple[float, float, float, float]  # Pixel coordinates on OCR input.


def _height(line: OCRLine) -> float:
    return max(1.0, line.bbox[3] - line.bbox[1])


def _related(a: OCRLine, b: OCRLine) -> bool:
    ax1, ay1, ax2, ay2 = a.bbox
    bx1, by1, bx2, by2 = b.bbox
    gap_y = max(0.0, by1 - ay2, ay1 - by2)
    overlap = min(ax2, bx2) - max(ax1, bx1)
    # Vertical continuity plus a common column: avoids joining unrelated signs.
    return gap_y <= 2.0 * max(_height(a), _height(b)) and overlap >= 0.1 * min(ax2 - ax1, bx2 - bx1)


def select_main_text(
    lines: list[OCRLine], image_width: int, image_height: int, *,
    confidence: float = 0.55, region_mode: str = "main", max_lines: int = 40,
    max_chars: int = 1800,
) -> str:
    """Choose a spatial text block, order rows, and bound the spoken content.

    ``main`` is a geometric heuristic, not a claim to understand document meaning.
    ``all`` retains all confident text. Exact repeated boxes are deduplicated,
    but repeated words at distinct positions are preserved.
    """
    if region_mode not in {"main", "all"}:
        raise ValueError("ocr.region_mode must be 'main' or 'all'")
    if max_chars < 1 or max_lines < 1 or image_width < 1 or image_height < 1:
        raise ValueError("OCR dimensions and limits must be positive")
    retained: list[OCRLine] = []
    seen = set()
    for line in lines:
        text = clean_text(line.text)
        if not text or not math.isfinite(line.confidence) or line.confidence < confidence:
            continue
        if len(line.bbox) != 4 or not all(math.isfinite(v) for v in line.bbox):
            continue
        x1, y1, x2, y2 = line.bbox
        if x2 <= x1 or y2 <= y1:
            continue
        key = (text, tuple(round(v, 1) for v in line.bbox))
        if key not in seen:
            seen.add(key)
            retained.append(OCRLine(text, line.confidence, line.bbox))
    if not retained:
        return ""
    if region_mode == "main":
        clusters: list[list[OCRLine]] = []
        remaining = list(retained)
        while remaining:
            cluster = [remaining.pop(0)]
            index = 0
            while index < len(cluster):
                linked = [line for line in remaining if _related(cluster[index], line)]
                cluster.extend(linked)
                remaining = [line for line in remaining if line not in linked]
                index += 1
            clusters.append(cluster)

        def score(cluster: list[OCRLine]) -> float:
            cx = sum((line.bbox[0] + line.bbox[2]) / 2 for line in cluster) / len(cluster)
            cy = sum((line.bbox[1] + line.bbox[3]) / 2 for line in cluster) / len(cluster)
            center_weight = 1.0 / (1.0 + abs(cx / image_width - 0.5) + abs(cy / image_height - 0.5))
            return sum(len(line.text) * _height(line) * line.confidence for line in cluster) * center_weight

        retained = max(clusters, key=score)
    rows: list[list[OCRLine]] = []
    for line in sorted(retained, key=lambda item: (item.bbox[1], item.bbox[0])):
        cy = (line.bbox[1] + line.bbox[3]) / 2
        row = next((r for r in rows if abs(cy - sum((v.bbox[1] + v.bbox[3]) / 2 for v in r) / len(r)) <= 0.5 * min(_height(line), _height(r[0]))), None)
        if row is None:
            rows.append([line])
        else:
            row.append(line)
    ordered = [" ".join(item.text for item in sorted(row, key=lambda item: item.bbox[0])) for row in rows[:max_lines]]
    text = "\n".join(ordered)
    if len(text) <= max_chars:
        return text
    # Stop on a word boundary without inventing an ellipsis in recognized text.
    cut = text[:max_chars]
    return cut.rsplit(" ", 1)[0] if " " in cut else cut


def _local_path(value: Any, name: str, *, directory: bool = False) -> Path:
    if not value or str(value).startswith(("http://", "https://")):
        raise ValueError(f"{name} must name an existing local {'directory' if directory else 'file'}")
    path = Path(str(value)).expanduser().resolve()
    if not (path.is_dir() if directory else path.is_file()):
        raise FileNotFoundError(f"{name} not found: {path}; prepare models before starting the service")
    return path


class YoloEngine:
    """One YOLO detection OR segmentation checkpoint, CPU only, local assets."""

    def __init__(self, config: dict[str, Any]):
        self.config = dict(config)
        path = Path(str(config.get("path", ""))).expanduser().resolve()
        if not config.get("path") or not path.exists():
            raise FileNotFoundError(f"YOLO checkpoint/directory missing: {path}")
        if path.is_dir():
            if not list(path.glob("*.param")) or not list(path.glob("*.bin")):
                raise ValueError("NCNN directory must contain exported .param and .bin files")
            if not (path / "metadata.yaml").is_file():
                raise ValueError("NCNN directory requires metadata.yaml to preserve class names and task")
        elif path.suffix.lower() != ".pt":
            raise ValueError("Use a local .pt checkpoint or an Ultralytics NCNN export directory")
        task = str(config.get("task", "segment"))
        if task not in {"segment", "detect"}:
            raise ValueError("yolo.task must be segment or detect")
        threads = max(1, int(config.get("threads", 2)))
        os.environ["YOLO_AUTOINSTALL"] = "false"
        os.environ["YOLO_OFFLINE"] = "true"
        os.environ["OMP_NUM_THREADS"] = str(threads)
        import torch
        from ultralytics import YOLO
        torch.set_num_threads(threads)
        torch.set_num_interop_threads(1)
        self.model = YOLO(str(path), task=task)
        if self.model.task != task:
            self.close()
            raise ValueError(f"Checkpoint task differs from configured yolo.task={task}")
        self.names = dict(self.model.names)
        if path.is_dir():
            # Ultralytics creates the NCNN backend when resolving exported names.
            backend = getattr(getattr(self.model, "predictor", None), "model", None)
            net = getattr(backend, "net", None)
            if net is not None:
                net.opt.num_threads = threads

    def infer(self, frame: Any) -> list[dict[str, Any]]:
        result = self.model.predict(
            source=frame, imgsz=int(self.config.get("imgsz", 416)),
            conf=float(self.config.get("confidence", 0.45)),
            iou=float(self.config.get("iou", 0.45)), device="cpu", half=False,
            max_det=int(self.config.get("max_det", 60)), verbose=False,
        )[0]
        if result.boxes is None:
            return []
        boxes = result.boxes.xyxyn.cpu().tolist()
        classes = result.boxes.cls.cpu().tolist()
        confidences = result.boxes.conf.cpu().tolist()
        masks = result.masks.xyn if result.masks is not None else []
        output = []
        for index, (box, class_id, confidence) in enumerate(zip(boxes, classes, confidences)):
            item = {
                "label": str(result.names[int(class_id)]), "confidence": float(confidence),
                "bbox": [max(0.0, min(1.0, float(v))) for v in box],
            }
            if index < len(masks) and len(masks[index]) >= 3:
                item["polygon"] = [[max(0.0, min(1.0, float(x))), max(0.0, min(1.0, float(y)))] for x, y in masks[index]]
            output.append(item)
        return output

    def close(self) -> None:
        self.model = None
        gc.collect()


class OCREngine:
    """Explicit OCR backend; no runtime downloads or silent backend substitution."""

    def __init__(self, config: dict[str, Any]):
        self.config = dict(config)
        self.backend = str(config.get("backend", "tesseract"))
        self.model = self.recognizer = None
        threads = max(1, int(config.get("threads", 2)))
        os.environ["OMP_NUM_THREADS"] = str(threads)
        os.environ["OMP_THREAD_LIMIT"] = str(threads)
        self.confidence = float(config.get("confidence", 0.55))
        if not 0 <= self.confidence <= 1:
            raise ValueError("ocr.confidence must be between 0 and 1")
        if self.backend == "tesseract":
            import pytesseract
            command = config.get("tesseract_cmd")
            if command:
                pytesseract.pytesseract.tesseract_cmd = str(_local_path(command, "ocr.tesseract_cmd"))
            languages = str(config.get("tesseract_lang", "vie+eng")).split("+")
            installed = set(pytesseract.get_languages(config=""))
            missing = set(languages) - installed
            if missing:
                raise RuntimeError(f"Missing Tesseract language data: {', '.join(sorted(missing))}; install tesseract-ocr-vie and tesseract-ocr-eng")
            self.model = pytesseract
        elif self.backend in {"paddle", "paddle_vietocr"}:
            det_dir = self._paddle_model_dir("det_model_dir")
            det_name = str(config.get("det_model_name", "PP-OCRv5_mobile_det"))
            # Disable model source availability probes. All required directories
            # are validated before any Paddle constructor can resolve downloads.
            os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
            common = {"device": "cpu", "cpu_threads": threads, "enable_mkldnn": False}
            if self.backend == "paddle":
                rec_dir = self._paddle_model_dir("rec_model_dir")
                from paddleocr import PaddleOCR
                self.model = PaddleOCR(
                    text_detection_model_name=det_name, text_detection_model_dir=str(det_dir),
                    text_recognition_model_name=str(config.get("rec_model_name", "latin_PP-OCRv5_mobile_rec")),
                    text_recognition_model_dir=str(rec_dir),
                    use_doc_orientation_classify=False, use_doc_unwarping=False,
                    use_textline_orientation=False, text_recognition_batch_size=1,
                    text_det_limit_side_len=int(config.get("detection_max_side", 960)),
                    text_det_limit_type="max", text_rec_score_thresh=self.confidence,
                    **common,
                )
            else:
                # VietOCR recognizes one cropped line, it is not a page detector.
                viet_config_path = _local_path(config.get("vietocr_config"), "ocr.vietocr_config")
                weights = _local_path(config.get("vietocr_weights"), "ocr.vietocr_weights")
                import yaml
                import torch
                from vietocr.tool.predictor import Predictor
                from paddleocr import TextDetection
                with viet_config_path.open(encoding="utf-8") as source:
                    viet_config = yaml.safe_load(source)
                if not isinstance(viet_config, dict) or not all(key in viet_config for key in ("vocab", "dataset", "cnn", "backbone")):
                    raise ValueError("vietocr_config must contain merged base.yml and vgg-seq2seq.yml settings")
                viet_config["device"] = "cpu"
                viet_config["weights"] = str(weights)
                viet_config.setdefault("cnn", {})["pretrained"] = False
                viet_config.setdefault("predictor", {})["beamsearch"] = False
                torch.set_num_threads(threads)
                self.recognizer = Predictor(viet_config)
                self.model = TextDetection(
                    model_name=det_name, model_dir=str(det_dir),
                    limit_side_len=int(config.get("detection_max_side", 960)),
                    limit_type="max", **common,
                )
        else:
            raise ValueError("ocr.backend must be tesseract, paddle, or paddle_vietocr")

    def _paddle_model_dir(self, key: str) -> Path:
        path = _local_path(self.config.get(key), f"ocr.{key}", directory=True)
        if not (path / "inference.pdiparams").is_file() or not (path / "inference.yml").is_file():
            raise ValueError(f"{path} must contain inference.pdiparams and inference.yml")
        if not ((path / "inference.json").is_file() or (path / "inference.pdmodel").is_file()):
            raise ValueError(f"{path} must contain inference.json or inference.pdmodel")
        return path

    def read(self, frame: Any) -> str:
        import cv2
        if frame is None or len(frame.shape) != 3 or frame.shape[2] != 3:
            raise ValueError("OCR input must be a nonempty BGR image")
        height, width = frame.shape[:2]
        max_side = max(64, int(self.config.get("max_side", 1600)))
        if width <= 0 or height <= 0:
            raise ValueError("OCR image is empty")
        if max(width, height) > max_side:
            scale = max_side / max(width, height)
            frame = cv2.resize(frame, (max(1, round(width * scale)), max(1, round(height * scale))), interpolation=cv2.INTER_AREA)
        if self.backend == "tesseract":
            lines = self._tesseract_lines(frame)
        elif self.backend == "paddle":
            lines = self._paddle_lines(frame)
        else:
            lines = self._vietocr_lines(frame)
        return select_main_text(
            lines, frame.shape[1], frame.shape[0], confidence=self.confidence,
            region_mode=str(self.config.get("region_mode", "main")),
            max_lines=int(self.config.get("max_lines", 40)),
            max_chars=int(self.config.get("max_chars", 1800)),
        )

    def _tesseract_lines(self, frame: Any) -> list[OCRLine]:
        import cv2
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        psm = int(self.config.get("tesseract_psm", 6))
        if psm not in {3, 4, 6, 7, 11, 12, 13}:
            raise ValueError("Unsupported tesseract_psm; use 6 for a document or 11 for scene text")
        data = self.model.image_to_data(
            rgb, lang=str(self.config.get("tesseract_lang", "vie+eng")),
            config=f"--oem 1 --psm {psm}", output_type=self.model.Output.DICT,
            timeout=float(self.config.get("tesseract_timeout_s", 40.0)),
        )
        grouped: dict[tuple[int, ...], list[tuple[str, float, float, float, float, float]]] = {}
        for index, text in enumerate(data["text"]):
            confidence = float(data["conf"][index]) / 100.0
            if not clean_text(text) or confidence < self.confidence:
                continue
            key = tuple(int(data[name][index]) for name in ("page_num", "block_num", "par_num", "line_num"))
            x, y = float(data["left"][index]), float(data["top"][index])
            w, h = float(data["width"][index]), float(data["height"][index])
            grouped.setdefault(key, []).append((text, confidence, x, y, x + w, y + h))
        output = []
        for words in grouped.values():
            words.sort(key=lambda word: word[2])
            output.append(OCRLine(
                " ".join(word[0] for word in words), min(word[1] for word in words),
                (min(word[2] for word in words), min(word[3] for word in words), max(word[4] for word in words), max(word[5] for word in words)),
            ))
        return output

    @staticmethod
    def _box(polygon: Any) -> tuple[float, float, float, float]:
        xs, ys = [float(p[0]) for p in polygon], [float(p[1]) for p in polygon]
        return min(xs), min(ys), max(xs), max(ys)

    def _paddle_lines(self, frame: Any) -> list[OCRLine]:
        lines = []
        for result in self.model.predict(frame):
            # PaddleOCR 3.x returns mapping-like OCRResult instances.
            for text, confidence, poly in zip(result.get("rec_texts", []), result.get("rec_scores", []), result.get("rec_polys", [])):
                if len(poly) >= 3:
                    lines.append(OCRLine(str(text), float(confidence), self._box(poly)))
        return lines

    def _vietocr_lines(self, frame: Any) -> list[OCRLine]:
        import cv2
        import numpy as np
        from PIL import Image
        lines = []
        count = 0
        max_crops = int(self.config.get("max_lines", 40))
        for result in self.model.predict(frame):
            polys = result.get("dt_polys", [])
            scores = result.get("dt_scores", [1.0] * len(polys))
            for poly, detection_score in zip(polys, scores):
                if float(detection_score) < self.confidence or len(poly) != 4:
                    continue
                if count >= max_crops:
                    return lines
                count += 1
                # Paddle quadrilaterals are ordered TL, TR, BR, BL.
                points = np.asarray(poly, dtype=np.float32)
                width = max(2, round(max(np.linalg.norm(points[1] - points[0]), np.linalg.norm(points[2] - points[3]))))
                height = max(2, round(max(np.linalg.norm(points[3] - points[0]), np.linalg.norm(points[2] - points[1]))))
                target = np.array([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]], dtype=np.float32)
                transform = cv2.getPerspectiveTransform(points, target)
                crop = cv2.warpPerspective(frame, transform, (width, height), borderMode=cv2.BORDER_REPLICATE)
                text, probability = self.recognizer.predict(Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)), return_prob=True)
                if probability is not None:
                    lines.append(OCRLine(str(text), min(float(probability), float(detection_score)), self._box(points)))
        return lines

    def close(self) -> None:
        self.model = self.recognizer = None
        gc.collect()
