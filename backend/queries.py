from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session, aliased

from backend.models import Alert, Camera, Detection, Event, Observation, ParkingConfirmation, Snapshot, Vehicle, VehicleSession
from backend.schemas import (
    CameraOut, EventDetail, EventOut, ObservationIn, ParkingOut, RouteEvent, SessionOut,
    VehicleProfileIn, VehicleProfileOut, VehicleRoute,
)


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


def profile_out(vehicle: Vehicle, show_owner: bool) -> VehicleProfileOut:
    values = {field: getattr(vehicle, field) for field in VehicleProfileIn.model_fields}
    sources = dict(vehicle.profile_sources or {})
    if not show_owner:
        values["owner_or_department"] = None
        sources.pop("owner_or_department", None)
    return VehicleProfileOut(**values, vehicle_type=vehicle.vehicle_type,
                             updated_at=vehicle.profile_updated_at, sources=sources)


def visit_duration(visit: VehicleSession, now: datetime) -> float | None:
    if visit.status == "INSIDE" and visit.entry_time is not None:
        return max(0, (now - visit.entry_time).total_seconds())
    return visit.duration_seconds


def vehicle_route(db: Session, vehicle: Vehicle, limit: int, now: datetime,
                  show_owner: bool) -> VehicleRoute:
    plate = vehicle.registration_number
    visit_id = (
        select(VehicleSession.id)
        .where(VehicleSession.registration_number == plate)
        .order_by(func.coalesce(VehicleSession.entry_time, VehicleSession.exit_time).desc(),
                  VehicleSession.id).limit(1).correlate(None).scalar_subquery()
    )
    rows = db.execute(
        select(Event, Camera.name, Camera.area, VehicleSession, ParkingConfirmation)
        .select_from(Event).join(Camera)
        .outerjoin(VehicleSession, VehicleSession.id == visit_id)
        .outerjoin(ParkingConfirmation, and_(
            ParkingConfirmation.entry_event_id == VehicleSession.entry_event_id,
            ParkingConfirmation.registration_number == plate,
        ))
        .where(Event.registration_number == plate)
        .order_by(Event.timestamp.desc(), Event.event_uuid).limit(limit)
    ).all()
    events = events_out(db, [row[0] for row in rows])
    visit, confirmation = rows[0][3:] if rows else (None, None)
    parking = None
    if confirmation is not None:
        current = visit.status == "INSIDE" and events[0].timestamp <= confirmation.confirmed_at
        parking = ParkingOut(zone=confirmation.zone, bay=confirmation.bay,
                             confirmed_at=confirmation.confirmed_at, current=current)
    return VehicleRoute(
        registration_number=plate,
        visit_status=visit.status if visit and visit.status in ("INSIDE", "EXITED") else "UNKNOWN",
        entry_event_id=visit.entry_event_id if visit else None,
        entry_time=visit.entry_time if visit else None,
        exit_time=visit.exit_time if visit else None,
        duration_seconds=visit_duration(visit, now) if visit else None,
        site_date=now.date(), profile=profile_out(vehicle, show_owner), parking=parking,
        history=[RouteEvent(**event.model_dump(), camera_name=row[1], camera_area=row[2])
                 for event, row in zip(events, rows, strict=True)],
    )


def sessions_out(db: Session, inside: bool, limit: int, offset: int,
                 plate: str | None = None) -> list[SessionOut]:
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
    if plate is not None:
        query = query.where(VehicleSession.registration_number == plate)
    now = datetime.now(timezone.utc)
    results = []
    for item, category, gate in db.execute(query):
        values = {field: getattr(item, field) for field in SessionOut.model_fields
                  if field not in {"vehicle_type", "entry_gate", "duration_seconds"}}
        results.append(SessionOut(**values, vehicle_type=category, entry_gate=gate,
                                  duration_seconds=visit_duration(item, now)))
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
            Event.direction.in_(("IN", "OUT")),
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
