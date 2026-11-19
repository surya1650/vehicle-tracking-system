"""Score annotated recording observations against the vision telemetry and outbox."""

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from vision.recognition import normalize


def coords(box: dict) -> list[float]:
    return [box[key] for key in ("x1", "y1", "x2", "y2")]


def iou(first: list[float], second: list[float]) -> float:
    x1, y1, x2, y2 = first
    a1, b1, a2, b2 = second
    intersection = max(0, min(x2, a2) - max(x1, a1)) * max(0, min(y2, b2) - max(y1, b1))
    union = (x2 - x1) * (y2 - y1) + (a2 - a1) * (b2 - b1) - intersection
    return intersection / union if union > 0 else 0


def edit_distance(first: str, second: str) -> int:
    previous = list(range(len(second) + 1))
    for index, one in enumerate(first, start=1):
        current = [index]
        for column, two in enumerate(second, start=1):
            current.append(min(current[-1] + 1, previous[column] + 1,
                               previous[column - 1] + (one != two)))
        previous = current
    return previous[-1]


def ratio(part: int, whole: int) -> dict:
    return {"matched": part, "total": whole, "rate": part / whole if whole else None}


def evaluate(truth: dict, tracking: list[dict], events: list[dict],
             overlap: float = 0.5, tolerance: int = 6) -> dict:
    tracks = {row["number"]: row["vehicles"] for row in tracking}
    labels = defaultdict(list)
    for label in truth["vehicles"]:
        labels[label["frame_number"]].append(label)

    matched_detections = 0
    identities = defaultdict(Counter)
    for frame, expected in labels.items():
        detections = tracks.get(frame, [])
        pairs = sorted([
            (iou(label["bbox"], coords(detection["bbox"])), left, right)
            for left, label in enumerate(expected)
            for right, detection in enumerate(detections)
            if label["vehicle_type"] == detection["class_name"]
        ], reverse=True)
        matched_truth, matched_pred = set(), set()
        for score, left, right in pairs:
            if score < overlap or left in matched_truth or right in matched_pred:
                continue
            matched_truth.add(left)
            matched_pred.add(right)
            matched_detections += 1
            track = detections[right]["track_id"]
            if track is not None:
                identities[expected[left]["object_id"]][track] += 1
    dominant = {key: ids.most_common(1)[0][0] for key, ids in identities.items()}
    stable = sum(max(ids.values()) for ids in identities.values())
    tracked = sum(sum(ids.values()) for ids in identities.values())

    seen_plates = associated = 0
    for target in truth["plates"]:
        matches = [
            (observation, event) for event in events for observation in event["observations"]
            if observation["frame_number"] == target["frame_number"]
            and iou(target["bbox"], coords(observation["bbox"])) >= overlap
        ]
        seen_plates += bool(matches)
        associated += any(event["track_id"] == dominant.get(target["object_id"])
                          and observation["association"] == "MATCHED"
                          for observation, event in matches)

    target_events = truth["crossings"]
    labeled_objects = {label["object_id"] for label in truth["vehicles"]}
    pairs = sorted([
        (abs(label["frame_number"] - event["frame_number"]), left, right)
        for left, label in enumerate(target_events)
        for right, event in enumerate(events)
        if label["direction"] == event["direction"]
        and (
            label["object_id"] not in labeled_objects
            or event["track_id"] in identities.get(label["object_id"], {})
        )
    ])
    matched_left, matched_right = set(), set()
    exact = characters = total_characters = 0
    automatic_correct = automatic_wrong = 0
    for distance, left, right in pairs:
        if distance > tolerance or left in matched_left or right in matched_right:
            continue
        matched_left.add(left)
        matched_right.add(right)
        expected = normalize(target_events[left]["plate_number"])
        if expected:
            predicted = normalize(events[right].get("plate_number"))
            exact += predicted == expected
            if events[right]["verification_status"] == "AUTO":
                automatic_correct += predicted == expected
                automatic_wrong += predicted != expected
            characters += max(0, len(expected) - edit_distance(expected, predicted))
            total_characters += len(expected)
    for index, label in enumerate(target_events):
        if index not in matched_left:
            total_characters += len(normalize(label["plate_number"]))

    complete = truth["crossings_complete"]
    count = len(matched_left)
    automatic_total = sum(event["verification_status"] == "AUTO" for event in events)
    automatic_extra = sum(event["verification_status"] == "AUTO" and index not in matched_right
                          for index, event in enumerate(events)) if complete else 0
    return {
        "ground_truth_source": truth["source"],
        "crossings_fully_labeled": complete,
        "vehicle_detection_recall": ratio(matched_detections, len(truth["vehicles"])),
        "tracking_continuity_on_matched_tracks": ratio(stable, tracked),
        "crossing_recall": ratio(count, len(target_events)),
        "crossing_precision": ratio(count, len(events)) if complete else None,
        "false_movement_rate": ratio(len(events) - count, len(events)) if complete else None,
        "plate_detection_recall": ratio(seen_plates, len(truth["plates"])),
        "plate_association_accuracy_on_detected_plates": ratio(associated, seen_plates),
        "exact_plate_accuracy": ratio(exact, sum(bool(normalize(row["plate_number"])) for row in target_events)),
        "character_accuracy": ratio(characters, total_characters),
        "automatic_accepts": {
            "total": automatic_total,
            "correct_plate": automatic_correct,
            "wrong_plate": automatic_wrong,
            "false_movement": automatic_extra if complete else None,
            "unscored": automatic_total - automatic_correct - automatic_wrong - automatic_extra,
        },
        "manual_review_rate": ratio(sum(event["verification_status"] == "PENDING" for event in events), len(events)),
    }


def score_files(truth_path: Path, tracks_path: Path, outbox: Path,
                overlap: float = 0.5, tolerance: int = 6) -> dict:
    truth = json.loads(truth_path.read_text(encoding="utf-8"))
    tracking = [json.loads(line) for line in tracks_path.read_text(encoding="utf-8").splitlines()]
    paths = {path.name: path for root in (outbox, outbox / ".sent") for path in root.glob("*.json")}
    events = [json.loads(paths[name].read_text(encoding="utf-8")) for name in sorted(paths)]
    return evaluate(truth, tracking, events, overlap, tolerance)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("truth", type=Path)
    parser.add_argument("tracks", type=Path)
    parser.add_argument("outbox", type=Path)
    parser.add_argument("--iou", type=float, default=0.5)
    parser.add_argument("--frame-tolerance", type=int, default=6)
    args = parser.parse_args()
    if not 0 < args.iou <= 1 or args.frame_tolerance < 0:
        parser.error("Use an IoU in (0, 1] and a nonnegative frame tolerance.")
    print(json.dumps(score_files(args.truth, args.tracks, args.outbox, args.iou, args.frame_tolerance), indent=2))


if __name__ == "__main__":
    main()
