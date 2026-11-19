"""Compare FastALPR on one publicly documented plate under controlled distortions."""

import argparse
import json
import os
from itertools import islice
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

import cv2
import numpy as np

from vision.anpr import FastAlprProcess
from vision.evaluate import iou
from vision.recognition import normalize
from vision.sources.file_source import FileVideoSource

EXPECTED = "3066MP4"
# Approximate visible plate box measured on the frame-60 vehicle crop.
PLATE = [291, 281, 435, 323]


def variants(crop: np.ndarray):
    yield "reference", crop, PLATE

    height, width = crop.shape[:2]
    edges = np.float32([(0, 0), (width - 1, 0), (width - 1, height - 1), (0, height - 1)])
    target = edges * np.float32([0.84, 0.8])
    target += np.float32([(0.12, 0.12), (0.04, 0.02), (0.12, 0.13), (0.04, 0.06)]) * [width, height]
    matrix = cv2.getPerspectiveTransform(edges, target)
    x1, y1, x2, y2 = PLATE
    corners = np.float32([[(x1, y1), (x2, y1), (x2, y2), (x1, y2)]])
    warped = cv2.perspectiveTransform(corners, matrix)[0]
    transformed_box = [float(warped[:, 0].min()), float(warped[:, 1].min()),
                       float(warped[:, 0].max()), float(warped[:, 1].max())]
    yield "perspective_warp", cv2.warpPerspective(crop, matrix, (width, height)), transformed_box

    kernel = np.full((1, 15), 1 / 15, dtype=np.float32)
    yield "horizontal_motion_blur_15px", cv2.filter2D(crop, -1, kernel), PLATE
    yield "brightness_33_percent", (crop * np.float32(0.33)).astype(np.uint8), PLATE

    obscured = crop.copy()
    left, right = x1 + 42, x1 + 86
    obscured[y1:y2, left:right] = 18
    yield "middle_of_plate_obscured", obscured, PLATE


def run(video: Path, interpreter: Path) -> dict:
    with FileVideoSource(video) as source:
        frame = next(islice(iter(source), 60, None))
    crop = frame.image[790:1240, 120:620]
    engine = FastAlprProcess(interpreter, "yolo-v9-t-384-license-plate-end2end",
                             "cct-xs-v2-global-model", 30, 0)
    checks = []
    try:
        with TemporaryDirectory(prefix="anpr-ocr-stress-") as directory:
            image = Path(directory) / "candidate.png"
            for condition, modified, plate_box in variants(crop):
                if not cv2.imwrite(str(image), modified):
                    raise OSError("Failed to write the test crop.")
                started = perf_counter()
                detections = engine.predict(image)
                matches = [row for row in detections if iou(row["bbox"], plate_box) >= 0.5]
                best = max(matches, key=lambda row: row["plate_confidence"], default=None)
                read = normalize(best["raw_text"]) if best else ""
                checks.append({
                    "condition": condition, "plate_detected": best is not None,
                    "exact_match": read == EXPECTED, "ocr_text": read or None,
                    "ocr_confidence": best["ocr_confidence"] if best else None,
                    "elapsed_ms": round((perf_counter() - started) * 1000, 1),
                })
    finally:
        engine.close()
    return {"source": video.name, "frame": 60, "expected_plate": EXPECTED, "checks": checks}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, default=Path("data/videos/anpr-demo-video.mp4"))
    interpreter = ".venv-ocr/Scripts/python.exe" if os.name == "nt" else ".venv-ocr/bin/python"
    parser.add_argument("--ocr-python", type=Path, default=Path(interpreter))
    args = parser.parse_args()
    print(json.dumps(run(args.video, args.ocr_python), indent=2))


if __name__ == "__main__":
    main()
