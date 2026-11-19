"""Ultralytics YOLOv8 detector with one persistent ByteTrack instance per source."""

import os
from collections import Counter
from pathlib import Path
from time import perf_counter

import cv2
import numpy as np
from numpy.typing import NDArray

from vision.sources.base_source import Frame
from vision.tracks import BoundingBox, TrackHistory, Vehicle

COCO_VEHICLES = {2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}


class VehicleTracker:
    def __init__(
        self,
        *,
        weights: Path,
        tracker_config: Path,
        device: str,
        image_size: int,
        confidence: float,
        iou: float,
        history_size: int,
        max_misses: int,
        agnostic_nms: bool = False,
    ) -> None:
        if not weights.is_file():
            raise ValueError(
                f"Model weights not found: {weights}. See README.md for the download."
            )
        if not tracker_config.is_file():
            raise ValueError(f"Tracker configuration not found: {tracker_config}")

        if "YOLO_CONFIG_DIR" not in os.environ:
            config_dir = Path(".cache/ultralytics").resolve()
            config_dir.mkdir(parents=True, exist_ok=True)
            os.environ["YOLO_CONFIG_DIR"] = str(config_dir)
        os.environ.setdefault("YOLO_AUTOINSTALL", "false")
        try:
            from ultralytics import YOLO
        except ImportError as exc:
            raise RuntimeError(
                "Install the tracking packages from requirements-tracking.txt."
            ) from exc

        self._model = YOLO(str(weights), task="detect")
        if any(self._model.names.get(index) != name for index, name in COCO_VEHICLES.items()):
            raise ValueError(
                "The vehicle detector must use the COCO vehicle class IDs and labels."
            )

        self._tracker_config = str(tracker_config)
        self._device = device
        self._image_size = image_size
        self._confidence = confidence
        self._iou = iou
        self._agnostic_nms = agnostic_nms
        self.history = TrackHistory(history_size, max_misses)
        self._class_counts: Counter[str] = Counter()
        self._samples = 0
        self._tracked_boxes = 0
        self._first_call_ms = 0.0
        self._total_call_ms = 0.0
        self._total_inference_ms = 0.0
        self._max_inference_ms = 0.0

    def reset(self) -> None:
        predictor = self._model.predictor
        if predictor is not None:
            for tracker in predictor.trackers:
                tracker.reset()
        self.history.tracks.clear()

    def process(self, frame: Frame) -> list[Vehicle]:
        started = perf_counter()
        result = self._model.track(
            source=frame.image,
            persist=True,
            tracker=self._tracker_config,
            classes=list(COCO_VEHICLES),
            conf=self._confidence,
            iou=self._iou,
            agnostic_nms=self._agnostic_nms,
            imgsz=self._image_size,
            device=self._device,
            verbose=False,
        )[0]
        call_ms = (perf_counter() - started) * 1000
        if self._samples == 0:
            self._first_call_ms = call_ms
        self._total_call_ms += call_ms
        self._samples += 1
        inference_ms = result.speed["inference"]
        self._total_inference_ms += inference_ms
        self._max_inference_ms = max(self._max_inference_ms, inference_ms)

        boxes = result.boxes.cpu()
        ids = boxes.id
        track_ids = [None] * len(boxes) if ids is None else [int(item) for item in ids.tolist()]
        vehicles = []
        for coords, class_id, confidence, track_id in zip(
            boxes.xyxy.tolist(), boxes.cls.tolist(), boxes.conf.tolist(), track_ids, strict=True
        ):
            class_id = int(class_id)
            if class_id not in COCO_VEHICLES:
                continue
            name = COCO_VEHICLES[class_id]
            vehicles.append(Vehicle(class_id, name, confidence, BoundingBox(*coords), track_id))
            self._class_counts[name] += 1
            if track_id is not None:
                self._tracked_boxes += 1

        self.history.update(frame.number, vehicles)
        return vehicles

    def diagnostics(self) -> dict[str, object]:
        samples = self._samples
        return {
            "device": self._device,
            "configured_image_size": self._image_size,
            "agnostic_nms": self._agnostic_nms,
            "inference_calls": samples,
            "boxes_by_class": dict(self._class_counts),
            "tracked_boxes": self._tracked_boxes,
            "mean_inference_ms": round(self._total_inference_ms / samples, 2) if samples else None,
            "max_inference_ms": round(self._max_inference_ms, 2) if samples else None,
            "mean_track_call_ms": round(self._total_call_ms / samples, 2) if samples else None,
            "first_track_call_ms": round(self._first_call_ms, 2) if samples else None,
        }


def draw_tracks(image: NDArray[np.uint8], vehicles: list[Vehicle]) -> None:
    height, width = image.shape[:2]
    for vehicle in vehicles:
        box = vehicle.bbox
        x1 = min(max(0, round(box.x1)), width - 1)
        y1 = min(max(0, round(box.y1)), height - 1)
        x2 = min(max(0, round(box.x2)), width - 1)
        y2 = min(max(0, round(box.y2)), height - 1)
        color = (0, 205, 90) if vehicle.track_id is not None else (0, 180, 255)
        track_label = f" #{vehicle.track_id}" if vehicle.track_id is not None else ""
        label = f"{vehicle.class_name}{track_label} {vehicle.confidence:.2f}"

        cv2.rectangle(image, (x1, y1), (x2, y2), color, 2)
        text_size, baseline = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
        label_y = min(max(y1, text_size[1] + 4), height - 1)
        cv2.rectangle(
            image, (x1, max(0, label_y - text_size[1] - 4)),
            (min(x1 + text_size[0] + 6, width - 1), min(label_y + baseline, height - 1)),
            color, cv2.FILLED,
        )
        cv2.putText(
            image, label, (x1 + 3, label_y - 2), cv2.FONT_HERSHEY_SIMPLEX,
            0.55, (20, 20, 20), 1, cv2.LINE_AA,
        )
        cv2.circle(image, ((x1 + x2) // 2, y2), 4, color, cv2.FILLED)
