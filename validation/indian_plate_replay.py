"""Exercise FastALPR and the production review rules on rendered Indian plates."""

import argparse
import json
import os
import re
from collections import Counter
from itertools import islice
from pathlib import Path
from tempfile import TemporaryDirectory

import cv2
import numpy as np
from numpy.typing import NDArray

from vision.anpr import FastAlprProcess
from vision.evaluate import iou
from vision.recognition import INDIAN_FORMATS, PlateObservation, aggregate, associate, normalize
from vision.sources.file_source import FileVideoSource
from vision.tracks import BoundingBox

Image = NDArray[np.uint8]
# Rendered test strings. They are labels for the fixture, independent of the video.
LABELS = {"standard": ("AP21AB1234", "MH12XY9087"), "BH": ("24BH7318AX", "25BH6829B")}
CONDITIONS = ("clean", "dim", "glare", "motion_blur", "partial_occlusion")
QUAD = np.float32([(288, 280), (437, 280), (437, 325), (288, 325)])
PLATE_BOX = [288, 280, 437, 325]


def render_vehicle_plate(image: Image, text: str) -> Image:
    width, height = 190, 56
    plate = np.full((height, width, 3), (225, 230, 232), dtype=np.uint8)
    cv2.rectangle(plate, (0, 0), (width - 1, height - 1), (40, 43, 46), 3)
    font = cv2.FONT_HERSHEY_SIMPLEX
    (rough_width, _), _ = cv2.getTextSize(text, font, 1.0, 2)
    scale = (width - 15) / rough_width
    (text_width, text_height), _ = cv2.getTextSize(text, font, scale, 2)
    cv2.putText(plate, text, ((width - text_width) // 2, (height + text_height) // 2),
                font, scale, (23, 25, 27), 2, cv2.LINE_AA)
    source = np.float32([(0, 0), (width - 1, 0), (width - 1, height - 1), (0, height - 1)])
    transform = cv2.getPerspectiveTransform(source, QUAD)
    frame_size = (image.shape[1], image.shape[0])
    changed = cv2.warpPerspective(plate, transform, frame_size)
    mask = cv2.warpPerspective(np.full((height, width), 255, dtype=np.uint8),
                               transform, frame_size)
    output = image.copy()
    output[mask == 255] = changed[mask == 255]
    return output


def distort(image: Image, condition: str, exposure: float) -> Image:
    if condition == "dim":
        exposure *= 0.33
    changed = np.clip(image.astype(np.float32) * exposure, 0, 255).astype(np.uint8)
    if condition == "motion_blur":
        return cv2.filter2D(changed, -1, np.full((1, 15), 1 / 15, np.float32))
    if condition == "glare":
        center = (371, 300)
        cv2.ellipse(changed, center, (30, 17), 0, 0, 360, (255, 255, 255), -1)
    elif condition == "partial_occlusion":
        changed[280:326, 349:395] = 22
    return changed


def run(video: Path, interpreter: Path) -> dict:
    with FileVideoSource(video) as stream:
        frame = next(islice(iter(stream), 60, None), None)
    if frame is None or frame.image.shape[0] < 1240 or frame.image.shape[1] < 620:
        raise ValueError("Use the pinned Ultralytics anpr-demo-video.mp4 recording.")
    crop = frame.image[790:1240, 120:620]
    bounds = BoundingBox(0, 0, crop.shape[1], crop.shape[0])
    formats = tuple(map(re.compile, INDIAN_FORMATS))
    engine = FastAlprProcess(interpreter, "yolo-v9-t-384-license-plate-end2end",
                             "cct-xs-v2-global-model", 30, 0)

    results = []
    try:
        with TemporaryDirectory(prefix="anpr-indian-replay-") as directory:
            path = Path(directory) / "candidate.png"
            for family, labels in LABELS.items():
                for label in labels:
                    rendered = render_vehicle_plate(crop, label)
                    for condition in CONDITIONS:
                        observations: list[PlateObservation] = []
                        located, exact = 0, 0
                        for index, exposure in enumerate((0.94, 1.0, 1.06)):
                            if not cv2.imwrite(str(path), distort(rendered, condition, exposure)):
                                raise OSError("Could not write an Indian plate fixture image.")
                            detections = engine.predict(path)
                            overlapping = [d for d in detections if iou(d["bbox"], PLATE_BOX) >= 0.5]
                            located += bool(overlapping)
                            exact += any(normalize(item["raw_text"]) == label for item in overlapping)
                            observations.extend(associate(
                                detections=detections, vehicle=bounds, neighbors=(),
                                frame_number=index, offset=(0, 0), quality=1, formats=formats,
                            ))
                        best, reason = aggregate(observations, minimum=0.65, agreement=2)
                        proposal = best.normalized_text if best is not None else None
                        state = "REVIEW" if reason != "accepted" else (
                            "CORRECT_AUTO" if proposal == label else "WRONG_AUTO"
                        )
                        results.append({
                            "family": family, "label": label, "condition": condition,
                            "located_frames": located, "exact_read_frames": exact,
                            "proposal": proposal, "status": state, "reason": reason,
                            "reads": [item.raw_text for item in observations],
                        })
    finally:
        engine.close()
    by_condition = {
        condition: dict(Counter(row["status"] for row in results if row["condition"] == condition))
        for condition in CONDITIONS
    }
    return {"cases": len(results), "frames_per_case": 3, "by_condition": by_condition,
            "totals": dict(Counter(row["status"] for row in results)), "results": results}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, default=Path("data/videos/anpr-demo-video.mp4"))
    interpreter = ".venv-ocr/Scripts/python.exe" if os.name == "nt" else ".venv-ocr/bin/python"
    parser.add_argument("--ocr-python", type=Path, default=Path(interpreter))
    args = parser.parse_args()
    print(json.dumps(run(args.video, args.ocr_python), indent=2))


if __name__ == "__main__":
    main()
