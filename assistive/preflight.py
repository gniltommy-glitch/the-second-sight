"""Fast installation checks without opening hardware or loading large models."""
import importlib.util
from pathlib import Path
import shutil
import subprocess


def check(config, mock=False):
    missing = []
    modules = ["numpy"]
    files = []
    if not mock:
        modules += ["serial", "cv2", "scipy", "piper", "ultralytics"]
        if config["camera"]["backend"] == "picamera2":
            modules += ["picamera2", "libcamera"]
        files += [config["yolo"]["path"], config["tts"]["model"], config["tts"]["model"] + ".json"]
        backend = config["ocr"]["backend"]
        if backend == "tesseract":
            modules += ["pytesseract"]
            if not shutil.which("tesseract"):
                missing.append("executable: tesseract")
            else:
                proc = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True, timeout=10)
                if "vie" not in proc.stdout.split():
                    missing.append("tesseract language: vie")
        else:
            modules += ["paddle", "paddleocr"]
            files += [config["ocr"].get("det_model_dir", "")]
            if backend == "paddle":
                files += [config["ocr"].get("rec_model_dir", "")]
            elif backend == "paddle_vietocr":
                modules += ["vietocr"]
                files += [config["ocr"].get("vietocr_config", ""), config["ocr"].get("vietocr_weights", "")]
            else:
                missing.append(f"unsupported OCR backend: {backend}")
    for module in modules:
        if importlib.util.find_spec(module) is None:
            missing.append(f"Python module: {module}")
    for filename in files:
        if not filename or not Path(filename).exists():
            missing.append(f"model path: {filename!r}")
    for item in missing:
        print(f"MISSING {item}")
    if not missing:
        print("Preflight OK. Hardware, model inference and class coverage still need bench testing.")
    return not missing
