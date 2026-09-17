"""Fit a measured ToF-to-camera homography; never enable calibration automatically.

Usage: python scripts/calibrate_tof.py measurements.json --output tof-candidate.json
See docs/CALIBRATION.md for the measurement format and physical verification.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys


def _pair(value, field):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{field}: expected two normalized coordinates")
    pair = [float(number) for number in value]
    if not all(math.isfinite(n) and 0 <= n <= 1 for n in pair):
        raise ValueError(f"{field}: coordinates must be finite and within [0, 1]")
    return pair


def measurement_points(pairs, orientation):
    source, target = [], []
    if not isinstance(pairs, list):
        raise ValueError("pairs must be a JSON list")
    for number, pair in enumerate(pairs):
        if not isinstance(pair, dict):
            raise ValueError(f"pair {number}: expected an object")
        if ("zone" in pair) == ("tof_uv" in pair):
            raise ValueError(f"pair {number}: provide exactly one of zone or tof_uv")
        if "zone" in pair:
            zone = pair["zone"]
            if type(zone) is not int or not 0 <= zone < 64:
                raise ValueError(f"pair {number}: zone must be an integer 0..63")
            u, v = (zone % 8 + 0.5) / 8, (zone // 8 + 0.5) / 8
        else:
            u, v = _pair(pair["tof_uv"], f"pair {number} tof_uv")
        # Input tof_uv, like zone, refers to the RAW matrix before orientation.
        if orientation["flip_x"]:
            u = 1 - u
        if orientation["flip_y"]:
            v = 1 - v
        for _ in range(orientation["rotate_quarters"]):
            u, v = 1 - v, u
        source.append([u, v])
        target.append(_pair(pair.get("camera_xy"), f"pair {number} camera_xy"))
    return source, target


def fit_calibration(data, ransac_threshold=0.02):
    import cv2
    import numpy as np

    if not isinstance(data, dict):
        raise ValueError("Measurement file must be a JSON object")
    if not math.isfinite(ransac_threshold) or not 0 < ransac_threshold <= 0.2:
        raise ValueError("RANSAC threshold must be within (0, 0.2]")
    orientation = {"flip_x": data.get("flip_x", False), "flip_y": data.get("flip_y", False), "rotate_quarters": data.get("rotate_quarters", 0)}
    if any(type(orientation[name]) is not bool for name in ("flip_x", "flip_y")):
        raise ValueError("flip_x and flip_y must be booleans")
    if type(orientation["rotate_quarters"]) is not int or not 0 <= orientation["rotate_quarters"] <= 3:
        raise ValueError("rotate_quarters must be an integer 0..3")
    bounds = data.get("validated_range_mm")
    if not isinstance(bounds, list) or len(bounds) != 2:
        raise ValueError("validated_range_mm must contain [minimum_mm, maximum_mm]")
    bounds = [float(n) for n in bounds]
    if not all(math.isfinite(n) for n in bounds) or not 0 < bounds[0] < bounds[1]:
        raise ValueError("validated_range_mm must be finite, positive, and increasing")
    source, target = measurement_points(data.get("pairs"), orientation)
    if len(source) < 6 or len({tuple(point) for point in source}) < 4:
        raise ValueError("Provide at least 6 measurements with at least 4 distinct ToF points")
    source, target = np.asarray(source, dtype=np.float64), np.asarray(target, dtype=np.float64)
    if np.linalg.matrix_rank(source - source.mean(axis=0)) < 2 or np.linalg.matrix_rank(target - target.mean(axis=0)) < 2:
        raise ValueError("Calibration points cannot all lie on a line")
    matrix, inliers = cv2.findHomography(source, target, cv2.RANSAC, ransac_threshold)
    if matrix is None or inliers is None or not np.isfinite(matrix).all() or abs(float(np.linalg.det(matrix))) < 1e-12:
        raise ValueError("Could not fit a nonsingular homography")
    accepted = inliers.reshape(-1).astype(bool)
    if int(accepted.sum()) < max(4, math.ceil(len(source) * 0.6)):
        raise ValueError("Fewer than 60% of measurements agree; inspect orientation, depth and correspondences")

    def residuals(src, dst):
        coordinates = np.asarray(src, dtype=np.float64)
        denominator = coordinates[:, 0] * matrix[2, 0] + coordinates[:, 1] * matrix[2, 1] + matrix[2, 2]
        if np.any(np.abs(denominator) < 1e-10):
            raise ValueError("Homography projects a measurement to infinity")
        predicted = cv2.perspectiveTransform(coordinates.reshape(-1, 1, 2), matrix).reshape(-1, 2)
        result = np.linalg.norm(predicted - np.asarray(dst, dtype=np.float64), axis=1)
        if not np.isfinite(result).all():
            raise ValueError("Nonfinite projection residual")
        return result

    errors = residuals(source, target)
    report = {"measurement_count": len(source), "inlier_count": int(accepted.sum()),
              "fit_rmse_normalized": float(np.sqrt(np.mean(errors[accepted] ** 2))),
              "fit_max_error_normalized": float(errors[accepted].max()),
              "fit_errors_normalized": errors.tolist(), "inliers": accepted.tolist(),
              "warnings": ["Candidate only: inspect independent measurements before setting calibrated=true."]}
    validation = data.get("validation_pairs", [])
    if validation:
        validation_source, validation_target = measurement_points(validation, orientation)
        validation_errors = residuals(validation_source, validation_target)
        report["validation_count"] = len(validation)
        report["validation_rmse_normalized"] = float(np.sqrt(np.mean(validation_errors ** 2)))
        report["validation_max_error_normalized"] = float(validation_errors.max())
        report["validation_errors_normalized"] = validation_errors.tolist()
        if validation_errors.max() > ransac_threshold:
            report["warnings"].append("Independent validation exceeds the fitting threshold; do not enable this calibration yet.")
    else:
        report["warnings"].append("No independent validation_pairs supplied; fit residual is not an accuracy estimate.")
    candidate = {"tof": {"calibrated": False, "projection": {"mode": "homography", "matrix": matrix.tolist(), "validated_range_mm": bounds, **orientation}}}
    return candidate, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("measurements", type=Path)
    parser.add_argument("--output", type=Path, help="Write a candidate JSON snippet; default stdout")
    parser.add_argument("--report", type=Path, help="Also write residual report to this path")
    parser.add_argument("--ransac-threshold", type=float, default=0.02)
    args = parser.parse_args()
    try:
        for path in (args.output, args.report):
            if path and path.resolve() == args.measurements.resolve():
                raise ValueError("Output paths must not overwrite input measurements")
        if args.output and args.report and args.output.resolve() == args.report.resolve():
            raise ValueError("Candidate and report paths must be different")
        candidate, report = fit_calibration(json.loads(args.measurements.read_text(encoding="utf-8-sig")), args.ransac_threshold)
        snippet = json.dumps(candidate, ensure_ascii=False, indent=2) + "\n"
        report_text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            args.output.write_text(snippet, encoding="utf-8")
        else:
            print(snippet, end="")
        if args.report:
            args.report.write_text(report_text, encoding="utf-8")
        print(report_text, file=sys.stderr, end="")
    except (OSError, ValueError, TypeError, ImportError) as error:
        parser.exit(1, f"Calibration failed: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
