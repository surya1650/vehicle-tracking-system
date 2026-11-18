from threading import Event

import cv2
import numpy as np

from vision.sources.rtsp_source import RTSPVideoSource


class Capture:
    def __init__(self, wait: Event, value: int):
        self.wait = wait
        self.value = value
        self.started = False

    def isOpened(self) -> bool:
        return True

    def get(self, prop: int) -> float:
        return 25 if prop == cv2.CAP_PROP_FPS else 1

    def read(self):
        if self.started:
            self.wait.wait(1)
            return False, None
        self.started = True
        return True, np.full((10, 20, 3), self.value, dtype=np.uint8)

    def release(self) -> None:
        pass


def test_reconnect_changes_epoch_and_keeps_monotonic_clock(monkeypatch):
    disconnect = Event()
    finish = Event()
    captures = iter([Capture(disconnect, 1), Capture(finish, 2)])
    monkeypatch.setattr(cv2, "VideoCapture", lambda *_: next(captures))
    source = RTSPVideoSource("rtsp://example.test/camera", retry_min=0.01, retry_max=0.01)
    with source:
        try:
            stream = iter(source)
            first = next(stream)
            disconnect.set()
            second = next(stream)
            assert second.number == first.number + 1
            assert second.stream_epoch == first.stream_epoch + 1
            assert second.timestamp >= first.timestamp
            assert second.elapsed_seconds >= first.elapsed_seconds
            assert second.image[0, 0, 0] == 2
            assert source.metadata.width == 20
        finally:
            finish.set()


def test_buffer_keeps_recent_frames():
    from vision.sources.base_source import Frame

    source = RTSPVideoSource("rtsp://example.test/camera", buffer_size=1)
    for index in range(3):
        source._push(Frame(np.zeros((1, 1, 3), np.uint8), index, index, None))
    assert source.dropped_frames == 2
    assert source._queue.get_nowait().number == 2
