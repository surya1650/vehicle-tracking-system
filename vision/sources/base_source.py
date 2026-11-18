"""Common frame contract shared by recorded and future live sources."""

from dataclasses import dataclass
from datetime import datetime
from typing import Iterator, Protocol

import numpy as np
from numpy.typing import NDArray


@dataclass(frozen=True, slots=True)
class VideoMetadata:
    width: int
    height: int
    fps: float
    frame_count: int | None


@dataclass(frozen=True, slots=True)
class Frame:
    image: NDArray[np.uint8]
    number: int
    elapsed_seconds: float
    timestamp: datetime | None
    stream_epoch: int = 0
    source_elapsed_seconds: float | None = None


class VideoSource(Protocol):
    @property
    def metadata(self) -> VideoMetadata: ...

    def __iter__(self) -> Iterator[Frame]: ...

    def close(self) -> None: ...
