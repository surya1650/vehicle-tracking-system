"""Reconnect a camera stream with bounded, latest-first buffering."""

import logging
import math
from datetime import datetime, timezone
from queue import Empty, Full, Queue
from threading import Event, Thread
from time import monotonic
from typing import Iterator, Self
from urllib.parse import urlsplit

import cv2

from vision.sources.base_source import Frame, VideoMetadata
from vision.sources.file_source import VideoSourceError

logger = logging.getLogger(__name__)


class RTSPVideoSource:
    def __init__(self, url: str, *, timeout_ms: int = 4000, buffer_size: int = 4,
                 retry_min: float = 0.5, retry_max: float = 10, fallback_fps: float = 25) -> None:
        try:
            valid = urlsplit(url)
            if valid.scheme not in {"rtsp", "rtsps"} or not valid.hostname:
                raise ValueError
        except ValueError:
            raise VideoSourceError("Configure a valid RTSP URL in the camera source variable.") from None
        self._url = url
        self._timeout = timeout_ms
        self._min_retry = retry_min
        self._max_retry = retry_max
        self._fallback_fps = fallback_fps
        self._queue: Queue[Frame] = Queue(buffer_size)
        self._stopping = Event()
        self._worker = Thread(target=self._acquire, name="rtsp-ingest", daemon=True)
        self._metadata: VideoMetadata | None = None
        self.state = "OFFLINE"
        self.dropped_frames = 0

    @property
    def metadata(self) -> VideoMetadata:
        if self._metadata is None:
            raise VideoSourceError("Stream metadata becomes available after the first frame.")
        return self._metadata

    def __enter__(self) -> Self:
        self._worker.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._stopping.set()
        if self._worker.is_alive():
            self._worker.join(timeout=self._timeout / 1000 + 1)
        self.state = "OFFLINE"

    def __iter__(self) -> Iterator[Frame]:
        while not self._stopping.is_set():
            try:
                yield self._queue.get(timeout=0.2)
            except Empty:
                if not self._worker.is_alive() and not self._stopping.is_set():
                    raise VideoSourceError("Camera acquisition stopped unexpectedly.") from None

    def _push(self, frame: Frame) -> None:
        try:
            self._queue.put_nowait(frame)
        except Full:
            try:
                self._queue.get_nowait()
                self.dropped_frames += 1
            except Empty:
                pass
            self._queue.put_nowait(frame)

    def _clear(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
                self.dropped_frames += 1
            except Empty:
                return

    def _acquire(self) -> None:
        self.state = "RECONNECTING"
        epoch, number = 0, 0
        origin = monotonic()
        pause = self._min_retry
        while not self._stopping.is_set():
            capture = None
            try:
                capture = cv2.VideoCapture(self._url, cv2.CAP_FFMPEG, [
                    cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, self._timeout,
                    cv2.CAP_PROP_READ_TIMEOUT_MSEC, self._timeout,
                ])
                if capture.isOpened():
                    epoch += 1
                    fps = capture.get(cv2.CAP_PROP_FPS)
                    fps = fps if math.isfinite(fps) and fps > 0 else self._fallback_fps
                    last_pts = None
                    while not self._stopping.is_set():
                        ok, image = capture.read()
                        if not ok or image is None or not image.size:
                            break
                        height, width = image.shape[:2]
                        self._metadata = VideoMetadata(width, height, fps, None)
                        if self.state != "ACTIVE":
                            self.state = "ACTIVE"
                            pause = self._min_retry
                            logger.info("rtsp_connected epoch=%d", epoch)
                        stamp = capture.get(cv2.CAP_PROP_PTS) / fps
                        pts = stamp if math.isfinite(stamp) else None
                        if pts is not None and last_pts is not None and pts + 1 < last_pts:
                            epoch += 1
                            self._clear()
                            logger.info("rtsp_timeline_discontinuity epoch=%d", epoch)
                        last_pts = pts
                        self._push(Frame(
                            image, number, monotonic() - origin, datetime.now(timezone.utc),
                            stream_epoch=epoch, source_elapsed_seconds=pts,
                        ))
                        number += 1
            except cv2.error:
                logger.warning("rtsp_capture_error")
            finally:
                if capture is not None:
                    capture.release()
            self.state = "RECONNECTING"
            self._clear()
            if not self._stopping.is_set():
                logger.warning("rtsp_reconnecting delay_seconds=%.2f", pause)
            self._stopping.wait(pause)
            pause = min(self._max_retry, pause * 2)
