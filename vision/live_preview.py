"""Publish a bounded-rate, annotated JPEG and simple frame telemetry."""

import json
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic

import cv2

from vision.sources.base_source import Frame


class LivePreview:
    def __init__(self, directory: Path, camera: str) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self.image_path = directory / f"{camera}.jpg"
        self.metadata_path = directory / f"{camera}.json"
        self.next_preview = 0.0

    def ready(self) -> bool:
        return monotonic() >= self.next_preview

    def publish(self, frame: Frame, tracks: int, crossings: int) -> None:
        image = frame.image
        height, width = image.shape[:2]
        scale = min(1, 960 / max(height, width))
        if scale < 1:
            image = cv2.resize(image, (max(1, round(width * scale)), max(1, round(height * scale))),
                               interpolation=cv2.INTER_AREA)
        ok, jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 82])
        if not ok:
            raise RuntimeError("Could not encode the camera preview.")
        metadata = {
            "number": frame.number, "timestamp": frame.timestamp.isoformat() if frame.timestamp else None,
            "elapsed_seconds": frame.elapsed_seconds, "published_at": datetime.now(timezone.utc).isoformat(),
            "tracks": tracks, "crossings": crossings,
        }
        for path, data in ((self.image_path, jpeg.tobytes()),
                           (self.metadata_path, json.dumps(metadata).encode("utf-8"))):
            temporary = path.with_suffix(".tmp-" + path.suffix[1:])
            temporary.write_bytes(data)
            temporary.replace(path)
        self.next_preview = monotonic() + 0.25
