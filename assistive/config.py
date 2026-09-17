import json
from pathlib import Path


def load_config(filename):
    source = Path(filename).resolve()
    with source.open(encoding="utf-8") as stream:
        config = json.load(stream)
    # Paths are relative to project root, explicitly declared in config.
    root = (source.parent / config.get("project_root", "..")).resolve()
    path_fields = {"yolo": ("path",), "tts": ("model",),
                   "ocr": ("det_model_dir", "rec_model_dir", "vietocr_config", "vietocr_weights")}
    for section, fields in path_fields.items():
        for key in fields:
            value = config.get(section, {}).get(key)
            if value:
                config[section][key] = str((root / value).resolve())
    for section in ("camera", "serial", "yolo", "ocr", "tts", "navigation", "tof", "runtime"):
        if not isinstance(config.get(section), dict):
            raise ValueError(f"Missing config object: {section}")
    if config["serial"].get("baudrate") != 1000000:
        raise ValueError("Provided app.c requires baudrate=1000000")
    if config["tts"].get("queue_size", 3) < 1:
        raise ValueError("tts.queue_size must be positive")
    if config["camera"].get("fps", 15) <= 0:
        raise ValueError("camera.fps must be positive")
    return config
