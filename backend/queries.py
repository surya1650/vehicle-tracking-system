from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from backend.models import Alert, Camera, Detection, Event, Observation, Snapshot, Vehicle, VehicleSession
from backend.schemas import CameraOut, EventDetail, EventOut, ObservationIn, SessionOut


def events_out(db: Session, events: list[Event]) -> list[EventOut]:
    media = defaultdict(list)
    if events:
        for event_uuid, kind in db.execute(
            select(Snapshot.event_uuid, Snapshot.kind)
            .where(Snapshot.event_uuid.in_([event.event_uuid for event in events]))
        ):
            media[event_uuid].append(kind)
    return [
        EventOut.model_validate(event).model_copy(update={"evidence": media[event.event_uuid]})
        for event in events
    ]


def event_detail(db: Session, event: Event) -> EventDetail:
    rows = db.execute(
        select(Observation, Detection.frame_number, Detection.quality)
        .join(Detection).where(Detection.event_uuid == event.event_uuid)
        .order_by(Detection.frame_number, Observation.id)
    )
    observations = [ObservationIn(
        frame_number=frame, quality=quality if quality is not None else 0, bbox=item.bbox,
        raw_text=item.raw_text, normalized_text=item.normalized_text, association=item.association,
        plate_confidence=item.plate_confidence, ocr_confidence=item.ocr_confidence,
        valid_format=item.valid_format,
    ) for item, frame, quality in rows]
    return EventDetail(**events_out(db, [event])[0].model_dump(), observations=observations)


def sessions_out(db: Session, inside: bool, limit: int, offset: int) -> list[SessionOut]:
    entry = aliased(Event)
    query = (
        select(VehicleSession, Vehicle.vehicle_type, entry.gate_id)
        .select_from(VehicleSession)
        .join(Vehicle, Vehicle.registration_number == VehicleSession.registration_number)
        .outerjoin(entry, VehicleSession.entry_event_id == entry.event_uuid)
        .order_by(func.coalesce(VehicleSession.entry_time, VehicleSession.exit_time).desc(),
                  VehicleSession.id)
        .limit(limit).offset(offset)
    )
    if inside:
        query = query.where(VehicleSession.status == "INSIDE")
    now = datetime.now(timezone.utc)
    results = []
    for item, category, gate in db.execute(query):
        values = {field: getattr(item, field) for field in SessionOut.model_fields
                  if field not in {"vehicle_type", "entry_gate", "duration_seconds"}}
        duration = item.duration_seconds
        if item.status == "INSIDE":
            duration = max(0, (now - item.entry_time).total_seconds())
        results.append(SessionOut(**values, vehicle_type=category, entry_gate=gate,
                                  duration_seconds=duration))
    return results


def camera_status(camera: Camera, now: datetime) -> str:
    if (not camera.enabled or camera.last_seen is None
            or now - camera.last_seen > timedelta(seconds=30)):
        return "OFFLINE"
    return camera.stream_state


def camera_out(camera: Camera) -> CameraOut:
    return CameraOut(
        **{key: getattr(camera, key) for key in CameraOut.model_fields
           if key not in {"status", "gate_name"}},
        status=camera_status(camera, datetime.now(timezone.utc)),
    )


def summary(db: Session, day: date, zone: ZoneInfo) -> dict:
    today = datetime.combine(day, time.min, zone)
    cutoff = datetime.combine(day + timedelta(days=1), time.min, zone)
    start = datetime.combine(day - timedelta(days=6), time.min, zone)
    events = db.execute(
        select(Event.timestamp, Event.direction, Event.vehicle_type).where(
            Event.timestamp >= start, Event.timestamp < cutoff,
            Event.verification_status != "PENDING",
        )
    )
    hour = [Counter() for _ in range(24)]
    daily = defaultdict(Counter)
    categories: Counter = Counter()
    for timestamp, direction, category in events:
        local = timestamp.astimezone(zone)
        daily[local.date()][direction] += 1
        if local.date() == day:
            hour[local.hour][direction] += 1
            if direction == "IN":
                categories[category or "unknown"] += 1
    count = lambda model, *rules: db.scalar(select(func.count()).select_from(model).where(*rules))
    now = datetime.now(timezone.utc)
    cameras = Counter(camera_status(camera, now) for camera in db.scalars(select(Camera)))
    return {
        "date": day.isoformat(), "timezone": zone.key,
        "entries": daily[day]["IN"], "exits": daily[day]["OUT"],
        "inside": count(VehicleSession, VehicleSession.status == "INSIDE"),
        "pending": count(Event, Event.verification_status == "PENDING"),
        "unmatched_exits": count(Alert, Alert.kind == "UNMATCHED_EXIT",
                                 Alert.timestamp >= today, Alert.timestamp < cutoff),
        "active_cameras": cameras["ACTIVE"], "offline_cameras": cameras["OFFLINE"],
        "reconnecting_cameras": cameras["RECONNECTING"],
        "hourly": [{"label": f"{i:02}:00", "entries": x["IN"], "exits": x["OUT"]}
                   for i, x in enumerate(hour)],
        "daily": [{"label": d.isoformat(), "entries": daily[d]["IN"], "exits": daily[d]["OUT"]}
                  for d in (day - timedelta(days=i) for i in range(6, -1, -1))],
        "categories": categories,
    }
