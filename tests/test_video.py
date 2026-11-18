import json
from datetime import datetime, timedelta
from pathlib import Path

import cv2
import numpy as np
import pytest

from vision.run_video import main
from vision.sources.file_source import FileVideoSource, VideoSourceError


@pytest.fixture
def recording(tmp_path: Path) -> Path:
    path = tmp_path / "sample.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 12.5, (96, 64))
    assert writer.isOpened()
    try:
        for value in (0, 40, 80, 120, 160):
            writer.write(np.full((64, 96, 3), value, dtype=np.uint8))
    finally:
        writer.release()
    return path


def test_reads_metadata_and_recording_timeline(recording: Path) -> None:
    start = datetime.fromisoformat("2026-11-18T10:30:00+05:30")
    with FileVideoSource(recording, start) as source:
        assert (source.metadata.width, source.metadata.height) == (96, 64)
        assert source.metadata.fps == 12.5
        assert source.metadata.frame_count == 5
        frames = list(source)
        assert list(source) == []
    assert [frame.number for frame in frames] == list(range(5))
    assert [frame.elapsed_seconds for frame in frames] == pytest.approx([0, 0.08, 0.16, 0.24, 0.32])
    assert [frame.timestamp for frame in frames] == [
        start + timedelta(seconds=index / 12.5) for index in range(5)
    ]
    assert frames[0].image.shape == (64, 96, 3)


def test_timestamp_is_absent_without_recording_start(recording: Path) -> None:
    with FileVideoSource(recording) as source:
        assert next(iter(source)).timestamp is None


@pytest.mark.parametrize("exists", [False, True])
def test_unreadable_file_returns_failure(tmp_path: Path, exists: bool) -> None:
    path = tmp_path / "bad.mp4"
    if exists:
        path.write_bytes(b"not a video")
    assert main([str(path)]) == 1


def test_timezone_required(recording: Path) -> None:
    with pytest.raises(ValueError, match="UTC offset"):
        FileVideoSource(recording, datetime(2026, 11, 18, 10, 30))


def test_reports_diagnostics_and_writes_mp4(recording: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    output = tmp_path / "copy.mp4"
    report = tmp_path / "report.json"
    assert main([str(recording), "--max-frames", "3", "--output", str(output), "--report", str(report)]) == 0
    diagnostics = json.loads(capsys.readouterr().out)
    assert diagnostics == json.loads(report.read_text(encoding="utf-8"))
    assert diagnostics["stop_reason"] == "frame_limit"
    assert diagnostics["decoded_frames"] == diagnostics["written_frames"] == 3
    assert diagnostics["last_frame"]["number"] == 2
    with FileVideoSource(output) as video:
        assert video.metadata.fps == 12.5
        assert len(list(video)) == 3


def test_output_cannot_overwrite_input(recording: Path) -> None:
    with pytest.raises(SystemExit) as result:
        main([str(recording), "--output", str(recording)])
    assert result.value.code == 2


class SimulatedCapture:
    """Inject a decoder failure independently of the platform codec."""

    def __init__(self, readable_frames: int, unreadable_frame: int = -1) -> None:
        self.current = -1
        self.readable_frames = readable_frames
        self.unreadable_frame = unreadable_frame
        self.released = False

    def get(self, property_id: int) -> int:
        return {
            cv2.CAP_PROP_FRAME_WIDTH: 96,
            cv2.CAP_PROP_FRAME_HEIGHT: 64,
            cv2.CAP_PROP_FPS: 25,
            cv2.CAP_PROP_FRAME_COUNT: 3,
        }[property_id]

    def isOpened(self) -> bool:
        return True

    def grab(self) -> bool:
        self.current += 1
        return self.current < self.readable_frames

    def retrieve(self) -> tuple[bool, np.ndarray | None]:
        if self.current == self.unreadable_frame:
            return False, None
        return True, np.zeros((64, 96, 3), dtype=np.uint8)

    def release(self) -> None:
        self.released = True


def test_recoverable_corrupt_frame_keeps_timeline(recording: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    capture = SimulatedCapture(3, unreadable_frame=1)
    monkeypatch.setattr(cv2, "VideoCapture", lambda path: capture)
    with FileVideoSource(recording) as source:
        frames = list(source)
        assert [frame.number for frame in frames] == [0, 2]
        assert frames[1].elapsed_seconds == pytest.approx(0.08)
        assert source.dropped_frames == 1
    assert capture.released


def test_premature_decode_failure_is_reported(recording: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    capture = SimulatedCapture(2)
    monkeypatch.setattr(cv2, "VideoCapture", lambda path: capture)
    with pytest.raises(VideoSourceError, match="Decoding stopped at frame 2"):
        with FileVideoSource(recording) as source:
            list(source)
    assert capture.released
