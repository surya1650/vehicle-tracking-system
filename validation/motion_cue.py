"""Score an adaptive foreground cue on the two fixed-camera Intel benchmark clips."""

import argparse
import json
import os
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import cv2
import numpy as np
import yaml

from vision.evaluate import evaluate
from vision.sources.file_source import FileVideoSource
from vision.tracks import BoundingBox, Vehicle
from vision.tripwire import Tripwire, TripwireConfig


def run(video: Path, truth: dict) -> dict:
    cache = Path(".cache/ultralytics").resolve()
    cache.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("YOLO_CONFIG_DIR", str(cache))
    os.environ.setdefault("YOLO_AUTOINSTALL", "false")
    from ultralytics.engine.results import Boxes
    from ultralytics.trackers.byte_tracker import BYTETracker

    settings = yaml.safe_load(Path("config/bytetrack.yaml").read_text(encoding="utf-8"))
    tracker = BYTETracker(SimpleNamespace(**settings), frame_rate=30)
    x1, y1, x2, y2 = truth["tripwire"]
    tripwire = Tripwire(TripwireConfig((x1, y1), (x2, y2)), uuid4())
    subtractor = cv2.createBackgroundSubtractorMOG2(
        history=100, varThreshold=36, detectShadows=True,
    )
    opening = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    closing = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (13, 13))
    tracking, events = [], []

    with FileVideoSource(video) as source:
        width, height = source.metadata.width, source.metadata.height
        if (width, height) != (768, 432):
            raise ValueError("This benchmark uses the original 768 × 432 Intel clips.")
        for frame in source:
            heatmap = subtractor.apply(
                frame.image, learningRate=0.006 if frame.number >= 20 else -1,
            )
            mask = (heatmap == 255).astype(np.uint8) * 255
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, opening)
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, closing)
            blobs, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            candidates = []
            for blob in blobs:
                area = cv2.contourArea(blob) / (width * height)
                x, y, w, h = cv2.boundingRect(blob)
                if 0.018 <= area <= 0.35 and w >= 60 and h >= 60:
                    # These two fixtures label cars. Motion regions have no class prediction.
                    candidates.append([x, y, x + w, y + h, 0.9, 2])
            raw = np.asarray(candidates, dtype=np.float32).reshape((-1, 6))
            rows = tracker.update(Boxes(raw, (height, width)))
            vehicles = [
                Vehicle(2, "car", float(row[5]), BoundingBox(*map(float, row[:4])), int(row[4]))
                for row in rows
            ]
            tracking.append({"number": frame.number, "vehicles": [asdict(v) for v in vehicles]})
            for event in tripwire.process(frame, vehicles):
                events.append({
                    "frame_number": event.frame_number, "direction": event.direction,
                    "track_id": event.track_id, "point": event.point,
                    "observations": [], "verification_status": "UNMEASURED",
                })

    evaluation = evaluate(truth, tracking, events)
    fields = ("crossing_recall", "crossing_precision", "false_movement_rate")
    return {"events": events, "score": {field: evaluation[field] for field in fields}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("labels", type=Path)
    args = parser.parse_args()
    truth = json.loads(args.labels.read_text(encoding="utf-8"))
    print(json.dumps(run(args.video, truth), indent=2))


if __name__ == "__main__":
    main()
