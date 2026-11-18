"""Plate association and confidence-weighted voting over distinct source frames."""

import re
from collections import defaultdict
from dataclasses import dataclass, replace

from vision.tracks import BoundingBox

INDIAN_FORMATS = (r"[A-Z]{2}\d{1,2}[A-Z]{1,3}\d{4}", r"\d{2}BH\d{4}[A-Z]{1,2}")


@dataclass(frozen=True, slots=True)
class PlateObservation:
    frame_number: int
    bbox: BoundingBox
    raw_text: str | None
    normalized_text: str
    plate_confidence: float
    ocr_confidence: float | None
    quality: float
    association: str
    valid_format: bool

    @property
    def vote_weight(self) -> float:
        return self.plate_confidence * (self.ocr_confidence or 0)


def normalize(value: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def contains(box: BoundingBox, point: tuple[float, float]) -> bool:
    x, y = point
    return box.x1 <= x <= box.x2 and box.y1 <= y <= box.y2


def associate(
    *, detections: list[dict], vehicle: BoundingBox, neighbors: tuple[BoundingBox, ...],
    frame_number: int, offset: tuple[int, int], quality: float,
    formats: tuple[re.Pattern, ...], scale: tuple[float, float] = (1, 1),
) -> list[PlateObservation]:
    observations = []
    for detection in detections:
        x1, y1, x2, y2 = detection["bbox"]
        x, y = offset
        sx, sy = scale
        box = BoundingBox(x + x1 * sx, y + y1 * sy, x + x2 * sx, y + y2 * sy)
        raw = detection["raw_text"]
        text = normalize(raw)
        if x2 <= x1 or y2 <= y1 or not contains(vehicle, box.center):
            association = "REJECTED"
        elif any(contains(other, box.center) for other in neighbors):
            association = "AMBIGUOUS"
        else:
            association = "MATCHED"
        observations.append(PlateObservation(
            frame_number, box, raw, text, detection["plate_confidence"],
            detection["ocr_confidence"], quality, association,
            any(rule.fullmatch(text) for rule in formats),
        ))
    possible = sum(item.association != "REJECTED" for item in observations)
    if possible > 1:
        observations = [
            replace(item, association="AMBIGUOUS") if item.association == "MATCHED" else item
            for item in observations
        ]
    return observations


def aggregate(
    observations: list[PlateObservation], minimum: float, agreement: int,
) -> tuple[PlateObservation | None, str]:
    votes: dict[str, dict[int, PlateObservation]] = defaultdict(dict)
    ambiguous = False
    for item in observations:
        if item.association == "AMBIGUOUS":
            ambiguous = True
        if (item.association != "MATCHED" or not item.normalized_text
                or min(item.plate_confidence, item.ocr_confidence or 0) < minimum):
            continue
        per_frame = votes[item.normalized_text]
        best = per_frame.get(item.frame_number)
        if best is None or item.vote_weight > best.vote_weight:
            per_frame[item.frame_number] = item
    ranked = sorted(votes.values(), key=lambda frames: sum(
        item.vote_weight for item in frames.values()
    ), reverse=True)
    best = max(ranked[0].values(), key=lambda item: item.vote_weight) if ranked else None
    if ambiguous:
        return best, "ambiguous_association"
    if len(ranked) > 1:
        return best, "conflicting_readings"
    if best is None:
        return None, "no_confident_plate"
    if not best.valid_format:
        return best, "unrecognized_format"
    if len(ranked[0]) < agreement:
        return best, "insufficient_agreement"
    return best, "accepted"
