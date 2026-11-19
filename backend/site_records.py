"""Operator parking confirmations and site-maintained vehicle records."""

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from backend.models import Event, ParkingConfirmation, Vehicle, VehicleSession
from backend.movements import audit, ensure_vehicle, lock
from backend.schemas import ParkingIn, RegistryService


def save_profile(db: Session, plate: str, values: dict, actor: str, *,
                 service: RegistryService | None = None,
                 checked_at: datetime | None = None) -> Vehicle:
    if not values:
        raise HTTPException(422, "Enter at least one vehicle detail.")
    lock(db, [f"plate:{plate}"])
    ensure_vehicle(db, plate, None)
    vehicle = db.get(Vehicle, plate)
    now = datetime.now(timezone.utc)
    checked = checked_at.astimezone(timezone.utc) if checked_at else now
    if checked > now:
        raise HTTPException(422, "The check time must be at or before the current time.")
    source = {"source": service or "SITE", "checked_at": checked.isoformat()}
    sources = dict(vehicle.profile_sources or {})
    changed = []
    for field, value in values.items():
        previous = sources.get(field)
        if service and previous:
            previous_check = datetime.fromisoformat(previous["checked_at"])
            if checked == previous_check and previous == source and getattr(vehicle, field) == value:
                continue
            if checked <= previous_check:
                raise HTTPException(409, f"A newer or conflicting record exists for {field}.")
        setattr(vehicle, field, value)
        sources[field] = source
        changed.append(field)
    if changed:
        vehicle.profile_sources = sources
        vehicle.profile_updated_at = now
        action = "vehicle_registry_check_recorded" if service else "vehicle_profile_updated"
        audit(db, actor, action, registration=plate, fields=sorted(changed), source=source["source"])
    return vehicle


def save_parking(db: Session, plate: str, data: ParkingIn, actor: str) -> None:
    keys = [f"plate:{plate}"]
    if data.bay is not None:
        keys.append(f"parking:{data.zone.lower()}:{data.bay.lower()}")
    lock(db, keys)
    visit = db.scalar(select(VehicleSession).where(
        VehicleSession.registration_number == plate, VehicleSession.status == "INSIDE",
    ))
    if visit is None or visit.entry_event_id != data.entry_event_id:
        raise HTTPException(409, "The open visit changed. Search again before updating parking.")

    current = db.get(ParkingConfirmation, data.entry_event_id)
    if data.zone is None:
        if current is not None and current.registration_number == plate:
            db.delete(current)
            audit(db, actor, "parking_cleared", registration=plate,
                  entry_event_id=str(data.entry_event_id))
        return

    if data.bay is not None:
        newer_sighting = select(Event.event_uuid).where(
            Event.registration_number == ParkingConfirmation.registration_number,
            Event.timestamp > ParkingConfirmation.confirmed_at,
        ).exists()
        occupied = db.scalar(
            select(ParkingConfirmation.entry_event_id)
            .join(VehicleSession, and_(
                VehicleSession.entry_event_id == ParkingConfirmation.entry_event_id,
                VehicleSession.registration_number == ParkingConfirmation.registration_number,
            ))
            .where(
                VehicleSession.status == "INSIDE",
                ParkingConfirmation.registration_number != plate,
                func.lower(ParkingConfirmation.zone) == data.zone.lower(),
                func.lower(ParkingConfirmation.bay) == data.bay.lower(),
                ~newer_sighting,
            ).limit(1)
        )
        if occupied is not None:
            raise HTTPException(409, "This bay is already confirmed for another vehicle inside.")

    if current is None:
        current = ParkingConfirmation(entry_event_id=data.entry_event_id)
        db.add(current)
    current.registration_number = plate
    current.zone = data.zone
    current.bay = data.bay
    current.confirmed_at = datetime.now(timezone.utc)
    audit(db, actor, "parking_confirmed", registration=plate, entry_event_id=str(data.entry_event_id),
          zone=data.zone, bay=data.bay)
