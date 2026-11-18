from datetime import datetime, timedelta, timezone
from uuid import UUID

import numpy as np
import pytest

from vision.sources.base_source import Frame
from vision.tracks import BoundingBox, Vehicle
from vision.tripwire import Direction, Tripwire, TripwireConfig

START = datetime(2026, 1, 1, tzinfo=timezone.utc)
ID = UUID(int=1)


def feed(wire, points):
    return [
        crossing
        for index, anchors in enumerate(points)
        for crossing in wire.process(
            Frame(np.zeros((1, 1, 3), dtype=np.uint8), index, index / 10,
                  START + timedelta(seconds=index / 10)),
            [
                Vehicle(2, "car", 1.0, BoundingBox(x - 5, y - 5, x + 5, y), track_id)
                for track_id, (x, y) in enumerate(anchors)
            ],
        )
    ]


@pytest.mark.parametrize("start,end,points,expected", [
    ((0, 0), (100, 0), [(50, -10), (50, 10), (50, 12)], Direction.IN),
    ((0, 0), (100, 0), [(50, 10), (50, -10), (50, -12)], Direction.OUT),
    ((0, 0), (0, 100), [(10, 50), (-10, 50), (-12, 50)], Direction.IN),
    ((0, 0), (100, 100), [(60, 40), (40, 60), (39, 61)], Direction.IN),
])
def test_crossings(start, end, points, expected):
    wire = Tripwire(TripwireConfig(start, end), ID)
    crossing, = feed(wire, [[point] for point in points])
    assert crossing.direction == expected
    assert crossing.frame_number == 1 and crossing.confirmed_frame == 2
    assert crossing.elapsed_seconds == pytest.approx(0.05)
    assert crossing.timestamp == START + timedelta(seconds=0.05)


def test_slow_crossing_jitter_and_cooldown():
    wire = Tripwire(TripwireConfig((0, 0), (100, 0)), ID)
    ordinates = [-10, -2, 1, -1, 0, 1, 3, 5, 7, 1, -6, -9]
    crossing, = feed(wire, [[(50, y)] for y in ordinates])
    assert crossing.frame_number == 5
    assert crossing.confirmed_frame == 8


def test_segment_end_and_multiple_tracks():
    wire = Tripwire(TripwireConfig((0, 0), (100, 0), positive_is_entry=False), ID)
    events = feed(wire, [[(20, -10), (80, -10), (120, -10)],
                        [(20, 10), (80, 10), (120, 10)],
                        [(20, 12), (80, 12), (120, 12)]])
    assert [(event.track_id, event.direction) for event in events] == [
        (0, Direction.OUT), (1, Direction.OUT),
    ]
    assert len({event.event_uuid for event in events}) == 2


def test_long_gap_does_not_create_crossing():
    wire = Tripwire(TripwireConfig((0, 0), (100, 0), max_gap=0.2), ID)
    assert not feed(wire, [[(10, -10)], [], [], [(10, 10)], [(10, 15)]])


def test_reverse_after_cooldown_is_a_new_event():
    wire = Tripwire(TripwireConfig((0, 0), (100, 0), cooldown=0.2), ID)
    events = feed(wire, [[(10, y)] for y in (-10, 10, 12, 15, -10, -12)])
    assert [event.direction for event in events] == [Direction.IN, Direction.OUT]


def test_line_touch_does_not_cross():
    wire = Tripwire(TripwireConfig((0, 0), (100, 0)), ID)
    assert not feed(wire, [[(10, y)] for y in (-10, -2, 0, -2, -10)])
