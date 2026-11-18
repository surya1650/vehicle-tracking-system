"""Directional crossings of a finite line segment in source pixel coordinates."""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID, uuid5

from vision.sources.base_source import Frame
from vision.tracks import Point, Vehicle


class Direction(StrEnum):
    IN = "IN"
    OUT = "OUT"


@dataclass(frozen=True, slots=True)
class TripwireConfig:
    start: Point
    end: Point
    mode: str = "AUTO"
    positive_is_entry: bool = True
    deadband: float = 4
    min_movement: float = 12
    confirmation: int = 2
    cooldown: float = 1
    max_gap: float = 1

    def __post_init__(self) -> None:
        values = (*self.start, *self.end, self.deadband, self.min_movement,
                  self.cooldown, self.max_gap)
        if not all(map(math.isfinite, values)) or self.start == self.end:
            raise ValueError("Tripwire requires two distinct finite points.")
        if (min(self.deadband, self.min_movement, self.cooldown) < 0
                or self.confirmation < 1 or self.max_gap <= 0):
            raise ValueError("Invalid tripwire confirmation, movement, or timing settings.")
        if self.mode not in ("AUTO", "ENTRY", "EXIT"):
            raise ValueError("Tripwire mode must be AUTO, ENTRY or EXIT.")

    @classmethod
    def from_coordinates(cls, value: str, **settings) -> "TripwireConfig":
        try:
            x1, y1, x2, y2 = map(float, value.split(","))
        except ValueError as exc:
            raise ValueError("Use four comma-separated tripwire coordinates: x1,y1,x2,y2.") from exc
        return cls((x1, y1), (x2, y2), **settings)


@dataclass(frozen=True, slots=True)
class Crossing:
    event_uuid: UUID
    track_id: int
    direction: Direction
    frame_number: int
    confirmed_frame: int
    elapsed_seconds: float
    timestamp: datetime | None
    point: Point


@dataclass(frozen=True, slots=True)
class Observation:
    anchor: Point
    distance: float
    elapsed: float
    timestamp: datetime | None
    frame: int


@dataclass(slots=True)
class CrossingCandidate:
    point: Point
    frame: int
    elapsed: float
    timestamp: datetime | None


@dataclass(slots=True)
class TrackSide:
    previous: Observation
    stable_side: int = 0
    origin_distance: float = 0
    pending: CrossingCandidate | None = None
    confirmations: int = 0
    last_crossing: float = -math.inf


class Tripwire:
    def __init__(self, config: TripwireConfig, recording_id: UUID) -> None:
        self.config = config
        self.recording_id = recording_id
        self._tracks: dict[int, TrackSide] = {}
        self._dx = config.end[0] - config.start[0]
        self._dy = config.end[1] - config.start[1]
        self._length = math.hypot(self._dx, self._dy)

    def reset(self) -> None:
        self._tracks.clear()

    def _distance(self, point: Point) -> float:
        x = point[0] - self.config.start[0]
        y = point[1] - self.config.start[1]
        return (self._dx * y - self._dy * x) / self._length

    def _intersection(self, previous: Observation, current: Observation) -> CrossingCandidate | None:
        fraction = previous.distance / (previous.distance - current.distance)
        x = previous.anchor[0] + fraction * (current.anchor[0] - previous.anchor[0])
        y = previous.anchor[1] + fraction * (current.anchor[1] - previous.anchor[1])
        projection = ((x - self.config.start[0]) * self._dx
                      + (y - self.config.start[1]) * self._dy) / self._length ** 2
        if not -1e-9 <= projection <= 1 + 1e-9:
            return None
        offset = fraction * (current.elapsed - previous.elapsed)
        timestamp = previous.timestamp
        if timestamp is not None:
            timestamp += timedelta(seconds=offset)
        return CrossingCandidate((x, y), current.frame, previous.elapsed + offset, timestamp)

    def _direction(self, side: int) -> Direction:
        if self.config.mode == "ENTRY":
            return Direction.IN
        if self.config.mode == "EXIT":
            return Direction.OUT
        return Direction.IN if (side > 0) == self.config.positive_is_entry else Direction.OUT

    def _observe(self, track_id: int, observation: Observation) -> Crossing | None:
        distance = observation.distance
        side = 0 if abs(distance) <= self.config.deadband else (1 if distance > 0 else -1)
        state = self._tracks.setdefault(track_id, TrackSide(observation))
        previous, state.previous = state.previous, observation

        if state.stable_side == 0 or side == state.stable_side:
            if side:
                if state.stable_side == 0 or abs(distance) > abs(state.origin_distance):
                    state.origin_distance = distance
                state.stable_side = side
            state.pending = None
            state.confirmations = 0
            return None

        if previous.distance * state.stable_side >= 0 > distance * state.stable_side:
            state.pending = self._intersection(previous, observation)
        elif distance * state.stable_side >= 0:
            state.pending = None

        if not side:
            state.confirmations = 0
            return None
        state.confirmations += 1
        if state.confirmations < self.config.confirmation:
            return None

        candidate = state.pending
        movement = abs(distance - state.origin_distance)
        if candidate is not None and movement < self.config.min_movement:
            return None
        state.stable_side, state.origin_distance = side, distance
        state.pending, state.confirmations = None, 0
        if candidate is None or candidate.elapsed - state.last_crossing < self.config.cooldown:
            return None
        state.last_crossing = candidate.elapsed
        return Crossing(
            uuid5(self.recording_id, f"{track_id}:{candidate.frame}"), track_id,
            self._direction(side), candidate.frame, observation.frame, candidate.elapsed,
            candidate.timestamp, candidate.point,
        )

    def process(self, frame: Frame, vehicles: list[Vehicle]) -> list[Crossing]:
        for track_id, state in list(self._tracks.items()):
            if frame.elapsed_seconds - state.previous.elapsed > self.config.max_gap:
                del self._tracks[track_id]

        crossings = []
        seen: set[int] = set()
        for vehicle in vehicles:
            track_id = vehicle.track_id
            if track_id is None or track_id in seen:
                continue
            seen.add(track_id)
            point = vehicle.bbox.bottom_center
            crossing = self._observe(track_id, Observation(
                point, self._distance(point), frame.elapsed_seconds, frame.timestamp, frame.number
            ))
            if crossing is not None:
                crossings.append(crossing)
        return crossings
