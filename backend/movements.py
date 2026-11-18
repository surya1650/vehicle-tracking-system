"""Transactional writes and deterministic visit reconstruction per registration."""

import hashlib
import json
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from backend.models import Alert, AuditLog, Camera, Detection, Event, Observation, Snapshot, Vehicle, VehicleSession
from backend.schemas import EventIn, ToggleIn, VerifyIn


def lock(db: Session, keys: list[str]) -> None:
    for key in sorted(set(keys)):
        db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key})


def audit(db: Session, actor: str, action: str, **details) -> None:
    db.add(AuditLog(actor=actor, action=action, details=details))


def ensure_vehicle(db: Session, plate: str, category: str | None) -> None:
    db.execute(insert(Vehicle).values(registration_number=plate, vehicle_type=category)
               .on_conflict_do_nothing(index_elements=[Vehicle.registration_number]))


def rebuild_sessions(db: Session, plate: str) -> None:
    # The event ledger and its corrections remain authoritative, including late arrivals.
    db.execute(delete(VehicleSession).where(VehicleSession.registration_number == plate))
    db.execute(delete(Alert).where(Alert.registration_number == plate))
    events = db.scalars(
        select(Event).where(Event.registration_number == plate)
        .order_by(Event.timestamp, Event.event_uuid)
    )
    inside = None
    for event in events:
        if event.direction == "IN":
            if inside is not None:
                db.add(Alert(id=event.event_uuid, event_uuid=event.event_uuid,
                             registration_number=plate, kind="DUPLICATE_ENTRY", timestamp=event.timestamp))
                continue
            inside = VehicleSession(
                id=event.event_uuid, registration_number=plate, entry_event_id=event.event_uuid,
                entry_camera_id=event.camera_id, entry_time=event.timestamp, status="INSIDE",
            )
            db.add(inside)
        elif inside is None:
            db.add(Alert(id=event.event_uuid, event_uuid=event.event_uuid,
                         registration_number=plate, kind="UNMATCHED_EXIT", timestamp=event.timestamp))
            db.add(VehicleSession(
                id=event.event_uuid, registration_number=plate, exit_event_id=event.event_uuid,
                exit_camera_id=event.camera_id, exit_time=event.timestamp, status="EXCEPTION",
            ))
        else:
            inside.exit_event_id = event.event_uuid
            inside.exit_camera_id = event.camera_id
            inside.exit_time = event.timestamp
            inside.duration_seconds = (event.timestamp - inside.entry_time).total_seconds()
            inside.status = "EXITED"
            inside = None


def save_event(db: Session, source: EventIn | ToggleIn, actor: str) -> tuple[Event, bool]:
    toggle = isinstance(source, ToggleIn)
    canonical = json.dumps([type(source).__name__, source.model_dump(mode="json")], sort_keys=True)
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    lock(db, [f"event:{source.event_uuid}"])
    previous = db.get(Event, source.event_uuid)
    if previous is not None:
        if previous.submission_hash != digest:
            raise HTTPException(409, "Event UUID already belongs to another submission.")
        return previous, False

    camera = db.get(Camera, source.camera_id)
    if camera is None or camera.gate_id != source.gate_id:
        raise HTTPException(422, "Camera and gate must be registered and match.")
    plate = source.plate_number if toggle or source.verification_status == "AUTO" else None
    if plate is not None:
        lock(db, [f"plate:{plate}"])
        ensure_vehicle(db, plate, source.vehicle_type)

    if toggle:
        inside = db.scalar(select(VehicleSession.id).where(
            VehicleSession.registration_number == plate, VehicleSession.status == "INSIDE",
        ))
        data = EventIn(**source.model_dump(), direction="OUT" if inside else "IN",
                       verification_status="AUTO")
    else:
        data = source
    values = data.model_dump(exclude={"observations", "detections", "snapshot_path", "plate_crop_path"})
    event = Event(**values, submission_hash=digest, registration_number=plate)
    db.add(event)
    db.flush()

    detections = {
        item.frame_number: Detection(event_uuid=event.event_uuid, track_id=event.track_id,
                                      **item.model_dump())
        for item in data.detections
    }
    for item in data.observations:
        if item.frame_number not in detections:
            detections[item.frame_number] = Detection(
                event_uuid=event.event_uuid, track_id=event.track_id,
                frame_number=item.frame_number, quality=item.quality,
            )
    db.add_all(detections.values())
    db.flush()
    for item in data.observations:
        db.add(Observation(detection_id=detections[item.frame_number].id,
                           **item.model_dump(exclude={"frame_number", "quality"})))
    for kind, path in (("vehicle", data.snapshot_path), ("plate", data.plate_crop_path)):
        if path:
            db.add(Snapshot(event_uuid=event.event_uuid, kind=kind, path=path))
    if plate:
        rebuild_sessions(db, plate)
    audit(db, actor, "event_submitted", event_uuid=str(event.event_uuid))
    return event, True


def verify_event(db: Session, source: VerifyIn, actor: str) -> Event:
    lock(db, [f"event:{source.event_uuid}"])
    event = db.get(Event, source.event_uuid)
    if event is None:
        raise HTTPException(404, "Event not found.")
    old = event.registration_number
    if event.verification_status == "VERIFIED" and old == source.plate_number:
        return event
    plates = sorted({plate for plate in (old, source.plate_number) if plate is not None})
    lock(db, [f"plate:{plate}" for plate in plates])
    ensure_vehicle(db, source.plate_number, event.vehicle_type)
    event.registration_number = source.plate_number
    event.verification_status = "VERIFIED"
    db.flush()
    for plate in plates:
        rebuild_sessions(db, plate)
    audit(db, actor, "plate_verified", event_uuid=str(event.event_uuid),
          previous_registration=old, raw_proposal=event.plate_number,
          registration=source.plate_number, notes=source.notes)
    return event
