"""Read a local recording on its nominal frame timeline."""

import logging
import math
from datetime import datetime, timedelta
from pathlib import Path
from types import TracebackType
from typing import Iterator, Self

import cv2

from vision.sources.base_source import Frame, VideoMetadata

logger = logging.getLogger(__name__)


class VideoSourceError(RuntimeError):
    """A video cannot be opened or read reliably."""


class FileVideoSource:
    def __init__(self, path: Path, start_time: datetime | None = None) -> None:
        if not path.is_file():
            raise VideoSourceError(f"Video file does not exist: {path}")
        if start_time is not None and start_time.utcoffset() is None:
            raise ValueError("The recording start time must include a UTC offset.")

        self._capture = cv2.VideoCapture(str(path))
        self._start_time = start_time
        self._next_frame = 0
        self._finished = False
        self._dropped_frames = 0

        try:
            if not self._capture.isOpened():
                raise VideoSourceError(f"OpenCV could not open the video: {path}")

            width = self._capture.get(cv2.CAP_PROP_FRAME_WIDTH)
            height = self._capture.get(cv2.CAP_PROP_FRAME_HEIGHT)
            fps = self._capture.get(cv2.CAP_PROP_FPS)
            count = self._capture.get(cv2.CAP_PROP_FRAME_COUNT)
            if not all(math.isfinite(value) and value > 0 for value in (width, height, fps)):
                raise VideoSourceError("The video must report positive dimensions and an FPS.")
            frame_count = round(count) if math.isfinite(count) and count >= 1 else None
            self._metadata = VideoMetadata(int(width), int(height), fps, frame_count)
        except Exception:
            self.close()
            raise

    @property
    def metadata(self) -> VideoMetadata:
        return self._metadata

    @property
    def dropped_frames(self) -> int:
        return self._dropped_frames

    def __iter__(self) -> Iterator[Frame]:
        while not self._finished:
            if not self._capture.grab():
                self._finished = True
                expected = self.metadata.frame_count
                if expected is not None and self._next_frame < expected:
                    raise VideoSourceError(
                        f"Decoding stopped at frame {self._next_frame}; "
                        f"the container reports {expected} frames."
                    )
                break

            number = self._next_frame
            self._next_frame += 1
            ok, image = self._capture.retrieve()
            if not ok or image is None or image.size == 0:
                self._dropped_frames += 1
                logger.warning("frame_unavailable", extra={"frame_number": number})
                continue

            elapsed = number / self.metadata.fps
            timestamp = None
            if self._start_time is not None:
                timestamp = self._start_time + timedelta(seconds=elapsed)
            yield Frame(image, number, elapsed, timestamp)

    def close(self) -> None:
        self._finished = True
        self._capture.release()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
