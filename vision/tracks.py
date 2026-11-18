"""Vehicle observations and bounded, per-stream trajectory history."""

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass

Point = tuple[float, float]


@dataclass(frozen=True, slots=True)
class BoundingBox:
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def center(self) -> Point:
        return (self.x1 + self.x2) / 2, (self.y1 + self.y2) / 2

    @property
    def bottom_center(self) -> Point:
        return (self.x1 + self.x2) / 2, self.y2


@dataclass(frozen=True, slots=True)
class Vehicle:
    class_id: int
    class_name: str
    confidence: float
    bbox: BoundingBox
    track_id: int | None = None


@dataclass(frozen=True, slots=True)
class TrackSample:
    frame_number: int
    vehicle: Vehicle

    @property
    def anchor(self) -> Point:
        return self.vehicle.bbox.bottom_center


@dataclass(slots=True)
class TrackState:
    first_seen: int
    last_step: int
    trajectory: deque[TrackSample]

    @property
    def current(self) -> TrackSample:
        return self.trajectory[-1]

    @property
    def previous(self) -> TrackSample | None:
        return self.trajectory[-2] if len(self.trajectory) >= 2 else None


class TrackHistory:
    """Maintain history in tracker updates, which can be sparser than source frames."""

    def __init__(self, history_size: int, max_misses: int) -> None:
        self._history_size = history_size
        self._max_misses = max_misses
        self._step = 0
        self.tracks: dict[int, TrackState] = {}

    def update(self, frame_number: int, vehicles: Sequence[Vehicle]) -> None:
        self._step += 1
        for vehicle in vehicles:
            track_id = vehicle.track_id
            if track_id is None:
                continue
            state = self.tracks.get(track_id)
            if state is None:
                state = TrackState(frame_number, self._step, deque(maxlen=self._history_size))
                self.tracks[track_id] = state
            state.last_step = self._step
            state.trajectory.append(TrackSample(frame_number, vehicle))

        expired = [
            track_id for track_id, state in self.tracks.items()
            if self._step - state.last_step > self._max_misses
        ]
        for track_id in expired:
            del self.tracks[track_id]
