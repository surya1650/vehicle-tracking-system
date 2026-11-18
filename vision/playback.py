"""Desktop video playback scheduled against the recording timeline."""

import math
from time import perf_counter
from types import TracebackType

import cv2

from vision.sources.base_source import Frame


class PlaybackWindow:
    def __init__(self, title: str, speed: float) -> None:
        self._title = title
        self._speed = speed
        self._started_at: float | None = None
        self._first_elapsed = 0.0

    def __enter__(self) -> "PlaybackWindow":
        cv2.namedWindow(self._title, cv2.WINDOW_NORMAL | cv2.WINDOW_GUI_NORMAL)
        return self

    def _is_open(self, key: int) -> bool:
        if (key & 0xFF) in (27, ord("q")):
            return False
        try:
            return cv2.getWindowProperty(self._title, cv2.WND_PROP_VISIBLE) >= 1
        except cv2.error:
            return False

    def show(self, frame: Frame) -> bool:
        if self._started_at is None:
            height, width = frame.image.shape[:2]
            scale = min(1.0, 960 / width, 720 / height)
            cv2.resizeWindow(self._title, round(width * scale), round(height * scale))
            self._started_at = perf_counter()
            self._first_elapsed = frame.elapsed_seconds
        target = self._started_at + (frame.elapsed_seconds - self._first_elapsed) / self._speed
        delay = target - perf_counter()
        while delay > 0:
            key = cv2.waitKey(min(20, math.ceil(delay * 1000)))
            if not self._is_open(key):
                return False
            delay = target - perf_counter()

        cv2.imshow(self._title, frame.image)
        return self._is_open(cv2.waitKey(1))

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            cv2.destroyWindow(self._title)
        except cv2.error:
            # The user may have already closed the native window.
            pass
