"""PostgreSQL schema for events, evidence, and the derived visit history."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class Gate(Base):
    __tablename__ = "gates"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))


class Camera(Base):
    __tablename__ = "cameras"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    gate_id: Mapped[str] = mapped_column(ForeignKey("gates.id"), index=True)
    direction: Mapped[str] = mapped_column(String(5))
    entry_side: Mapped[str] = mapped_column(String(8), server_default="positive")
    source_binding: Mapped[str | None] = mapped_column(String(64))
    width: Mapped[int | None]
    height: Mapped[int | None]
    tripwire: Mapped[list | None] = mapped_column(JSONB)
    detection_threshold: Mapped[float]
    enabled: Mapped[bool] = mapped_column(default=True)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stream_state: Mapped[str] = mapped_column(String(20), default="OFFLINE")


class Vehicle(Base):
    __tablename__ = "vehicles"
    registration_number: Mapped[str] = mapped_column(String(24), primary_key=True)
    vehicle_type: Mapped[str | None] = mapped_column(String(20))
    owner_or_department: Mapped[str | None] = mapped_column(String(120))
    authorised_status: Mapped[bool | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Event(Base):
    __tablename__ = "vehicle_events"
    __table_args__ = (
        CheckConstraint("direction IN ('IN', 'OUT')", name="event_direction"),
        CheckConstraint("verification_status IN ('AUTO', 'PENDING', 'VERIFIED')", name="event_verification"),
    )
    event_uuid: Mapped[UUID] = mapped_column(primary_key=True)
    registration_number: Mapped[str | None] = mapped_column(ForeignKey("vehicles.registration_number"), index=True)
    plate_number: Mapped[str | None] = mapped_column(String(24))
    vehicle_type: Mapped[str | None] = mapped_column(String(20))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.id"), index=True)
    gate_id: Mapped[str] = mapped_column(ForeignKey("gates.id"), index=True)
    direction: Mapped[str] = mapped_column(String(3))
    track_id: Mapped[int | None]
    frame_number: Mapped[int | None]
    elapsed_seconds: Mapped[float | None]
    ocr_confidence: Mapped[float | None]
    plate_confidence: Mapped[float | None]
    verification_status: Mapped[str] = mapped_column(String(12), index=True)
    recognition_status: Mapped[str | None] = mapped_column(String(40))
    reason: Mapped[str | None] = mapped_column(String(120))
    submission_hash: Mapped[str] = mapped_column(String(64))


class VehicleSession(Base):
    __tablename__ = "vehicle_sessions"
    __table_args__ = (
        Index("uq_active_vehicle_session", "registration_number", unique=True,
              postgresql_where=text("status = 'INSIDE'")),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    registration_number: Mapped[str] = mapped_column(ForeignKey("vehicles.registration_number"), index=True)
    entry_event_id: Mapped[UUID | None] = mapped_column(ForeignKey("vehicle_events.event_uuid"))
    exit_event_id: Mapped[UUID | None] = mapped_column(ForeignKey("vehicle_events.event_uuid"))
    entry_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exit_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[float | None]
    entry_camera_id: Mapped[str | None] = mapped_column(ForeignKey("cameras.id"))
    exit_camera_id: Mapped[str | None] = mapped_column(ForeignKey("cameras.id"))
    status: Mapped[str] = mapped_column(String(12), index=True)


class Detection(Base):
    __tablename__ = "detection_records"
    __table_args__ = (UniqueConstraint("event_uuid", "frame_number"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    event_uuid: Mapped[UUID] = mapped_column(ForeignKey("vehicle_events.event_uuid"), index=True)
    frame_number: Mapped[int]
    track_id: Mapped[int | None]
    bbox: Mapped[dict | None] = mapped_column(JSONB)
    quality: Mapped[float | None]


class Observation(Base):
    __tablename__ = "plate_recognition_observations"
    id: Mapped[int] = mapped_column(primary_key=True)
    detection_id: Mapped[int] = mapped_column(ForeignKey("detection_records.id"), index=True)
    raw_text: Mapped[str | None] = mapped_column(Text)
    normalized_text: Mapped[str] = mapped_column(String(128))
    plate_confidence: Mapped[float]
    ocr_confidence: Mapped[float | None]
    association: Mapped[str] = mapped_column(String(12))
    valid_format: Mapped[bool]
    bbox: Mapped[dict] = mapped_column(JSONB)


class Snapshot(Base):
    __tablename__ = "snapshots"
    __table_args__ = (UniqueConstraint("event_uuid", "kind"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    event_uuid: Mapped[UUID] = mapped_column(ForeignKey("vehicle_events.event_uuid"), index=True)
    kind: Mapped[str] = mapped_column(String(12))
    path: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[UUID] = mapped_column(primary_key=True)
    event_uuid: Mapped[UUID] = mapped_column(ForeignKey("vehicle_events.event_uuid"), index=True)
    registration_number: Mapped[str] = mapped_column(ForeignKey("vehicles.registration_number"), index=True)
    kind: Mapped[str] = mapped_column(String(24), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class User(Base):
    __tablename__ = "users"
    username: Mapped[str] = mapped_column(String(64), primary_key=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(12))
    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor: Mapped[str] = mapped_column(ForeignKey("users.username"), index=True)
    action: Mapped[str] = mapped_column(String(40))
    details: Mapped[dict] = mapped_column(JSONB)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class SystemSetting(Base):
    __tablename__ = "system_settings"
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB)
