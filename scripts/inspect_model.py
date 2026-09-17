"""Inspect checkpoint metadata and dataset ZIPs without unpickling/extraction.

Usage: python scripts/inspect_model.py best.pt --datasets "dataset.zip"
Only pickle opcodes and literal dictionaries are examined. No Torch import, model
execution, GLOBAL/REDUCE execution, or zip extraction occurs. Metadata is a hint,
not proof of accuracy or authenticity of a checkpoint.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
import pickletools
import sys
import zipfile


_MEMO = {"BINPUT", "LONG_BINPUT", "PUT", "MEMOIZE"}
_SCALARS = {"BINUNICODE", "SHORT_BINUNICODE", "UNICODE", "BINUNICODE8", "BININT", "BININT1", "BININT2", "INT", "LONG", "LONG1", "LONG4", "BINFLOAT", "FLOAT"}


def inspect_checkpoint(path: Path) -> dict:
    with zipfile.ZipFile(path) as archive:
        entry = next((item for item in archive.infolist() if item.filename.endswith("/data.pkl") or item.filename == "data.pkl"), None)
        if entry is None or entry.file_size > 16 * 1024 * 1024:
            raise ValueError("No small PyTorch data.pkl found; refusing unsupported checkpoint")
        ops = list(pickletools.genops(archive.read(entry)))
    # Scalar memo values are enough for class names/version references. This is
    # deliberately NOT a pickle virtual machine and does not reconstruct objects.
    memo = {}
    previous = None
    resolved = []
    for opcode, argument, position in ops:
        name = opcode.name
        if name in _SCALARS:
            previous = argument
        elif name in {"BINGET", "LONG_BINGET", "GET"}:
            previous = memo.get(int(argument))
        elif name in _MEMO:
            key = len(memo) if name == "MEMOIZE" else int(argument)
            memo[key] = previous
        else:
            previous = None
        resolved.append((name, previous, position))

    def after(index: int):
        index += 1
        while index < len(resolved) and resolved[index][0] in _MEMO:
            index += 1
        return resolved[index][1] if index < len(resolved) else None

    report = {"file": str(path), "bytes": path.stat().st_size, "inspection": "pickletools only; no unpickling"}
    with path.open("rb") as source:
        report["sha256"] = hashlib.file_digest(source, "sha256").hexdigest()
    for key in ["task", "yaml_file", "date", "version"]:
        values = [after(i) for i, (_, value, _) in enumerate(resolved) if value == key]
        report[key] = next((value for value in values if isinstance(value, str)), None)
    report["head_markers"] = sorted({str(arg) for op, arg, _ in ops if op.name == "GLOBAL" and isinstance(arg, str) and ("Segment" in arg or "DetectionModel" in arg)})
    names = {}
    for start, (_, value, _) in enumerate(resolved):
        if value != "names":
            continue
        index = start + 1
        while index < len(resolved) and resolved[index][0] in _MEMO | {"EMPTY_DICT", "MARK"}:
            index += 1
        pairs = []
        while index < len(resolved):
            opcode, scalar, _ = resolved[index]
            if opcode in _MEMO:
                index += 1
                continue
            if opcode == "SETITEMS":
                break
            if not isinstance(scalar, (str, int)):
                pairs = []
                break
            pairs.append(scalar)
            index += 1
        if pairs and len(pairs) % 2 == 0 and all(isinstance(pairs[i], int) and isinstance(pairs[i + 1], str) for i in range(0, len(pairs), 2)):
            names = dict(zip(pairs[::2], pairs[1::2]))
            break
    report["names"] = names
    report["class_count"] = len(names)
    return report


def _process_yaml_entry(archive: zipfile.ZipFile, entry: zipfile.ZipInfo, report: dict) -> None:
    content = archive.read(entry).decode("utf-8", errors="replace")
    names = None
    for line in content.splitlines():
        if line.strip().startswith("names:"):
            try:
                names = ast.literal_eval(line.partition(":")[2].strip())
            except (ValueError, SyntaxError):
                pass
    report["yaml"].append({"path": entry.filename, "names": names, "text": content})


def _process_txt_entry(archive: zipfile.ZipFile, entry: zipfile.ZipInfo, counts: Counter, report: dict, max_rows: int) -> None:
    for line in archive.read(entry).decode("utf-8", errors="replace").splitlines():
        fields = line.split()
        try:
            values = [float(value) for value in fields]
        except ValueError:
            continue
        if not values or not values[0].is_integer():
            continue
        if len(fields) == 5:
            counts["box_rows"] += 1
        elif len(fields) >= 7 and len(fields) % 2 == 1:
            counts["polygon_rows"] += 1
        else:
            counts["other_numeric_rows"] += 1
        report["label_rows_sampled"] += 1
        if report["label_rows_sampled"] >= max_rows:
            break


def inspect_dataset(path: Path, max_rows: int = 10000) -> dict:
    report = {"file": str(path), "yaml": [], "label_rows_sampled": 0}
    counts = Counter()
    with zipfile.ZipFile(path) as archive:
        seen = set()
        for entry in archive.infolist():
            if entry.filename in seen:
                continue
            seen.add(entry.filename)
            if entry.filename.lower().endswith((".yaml", ".yml")) and entry.file_size <= 65536:
                _process_yaml_entry(archive, entry, report)
            if not entry.filename.lower().endswith(".txt") or entry.file_size > 2 * 1024 * 1024 or report["label_rows_sampled"] >= max_rows:
                continue
            _process_txt_entry(archive, entry, counts, report, max_rows)
    report.update(counts)
    report["mixed_boxes_and_polygons"] = bool(counts["box_rows"] and counts["polygon_rows"])
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkpoint", type=Path)
    parser.add_argument("--datasets", nargs="*", type=Path, default=[])
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        report = {"checkpoint": inspect_checkpoint(args.checkpoint), "datasets": [inspect_dataset(path) for path in args.datasets]}
    except (OSError, ValueError, zipfile.BadZipFile, StopIteration) as error:
        parser.exit(1, f"Inspection failed: {error}\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
