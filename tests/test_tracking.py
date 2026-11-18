import json
import os
from collections import defaultdict
from pathlib import Path

import pytest

from vision.run_video import main
from vision.sources.file_source import FileVideoSource
from vision.tracks import BoundingBox, TrackHistory, Vehicle


def car(track_id: int | None, offset: int = 0) -> Vehicle:
    return Vehicle(2, "car", 0.91, BoundingBox(offset, 10, offset + 20, 40), track_id)


def test_tracks_keep_distinct_trajectories_and_anchors() -> None:
    history = TrackHistory(history_size=2, max_misses=2)
    history.update(0, [car(1), car(2, 40), car(None, 70)])
    history.update(3, [car(1, 4), car(2, 43)])
    history.update(6, [car(1, 8), car(2, 46)])

    assert set(history.tracks) == {1, 2}
    first = history.tracks[1]
    assert first.first_seen == 0
    assert [sample.frame_number for sample in first.trajectory] == [3, 6]
    assert first.previous is not None
    assert first.previous.anchor == (14, 40)
    assert first.current.anchor == (18, 40)
    assert first.current.vehicle.bbox.center == (18, 25)
    assert history.tracks[2].current.anchor == (56, 40)


def test_history_survives_temporary_miss_and_expires() -> None:
    history = TrackHistory(history_size=4, max_misses=2)
    history.update(3, [car(12)])
    history.update(6, [])
    history.update(9, [])
    history.update(12, [car(12, 5)])
    assert [sample.frame_number for sample in history.tracks[12].trajectory] == [3, 12]
    history.update(15, [])
    history.update(18, [])
    history.update(21, [])
    history.update(24, [car(13, 10)])
    assert set(history.tracks) == {13}
    assert history.tracks[13].previous is None


def test_real_yolo_and_bytetrack_demo(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    demo = os.environ.get("ANPR_TEST_VIDEO")
    if demo is None:
        pytest.skip("Set ANPR_TEST_VIDEO to the Ultralytics anpr-demo-video.mp4 recording.")

    weights = os.environ.get("ANPR_TEST_WEIGHTS", "data/models/yolov8n.pt")
    telemetry = tmp_path / "tracks.jsonl"
    output = tmp_path / "annotated.mp4"
    result = main([
        demo, "--track", "--device", "cpu", "--weights", weights,
        "--max-frames", "24", "--inference-interval", "2",
        "--tracks-jsonl", str(telemetry), "--output", str(output),
    ])
    assert result == 0
    report = json.loads(capsys.readouterr().out)
    tracking = report["tracking"]
    assert tracking["inference_calls"] == report["written_frames"] == 12
    assert {"car", "truck"} <= tracking["boxes_by_class"].keys()
    assert tracking["tracked_boxes"] > 0
    assert tracking["mean_inference_ms"] > 0

    rows = [json.loads(line) for line in telemetry.read_text(encoding="utf-8").splitlines()]
    assert [row["number"] for row in rows] == list(range(0, 24, 2))
    observed = defaultdict(list)
    for row in rows:
        for vehicle in row["vehicles"]:
            if vehicle["track_id"] is not None:
                observed[vehicle["track_id"]].append(vehicle["bbox"])
    assert any(
        len(boxes) == 12 and abs(boxes[0]["y2"] - boxes[-1]["y2"]) > 2
        for boxes in observed.values()
    )
    with FileVideoSource(output) as annotated:
        assert annotated.metadata.fps == pytest.approx(report["metadata"]["fps"] / 2, abs=0.01)
        assert len(list(annotated)) == 12
