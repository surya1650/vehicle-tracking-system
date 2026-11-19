from datetime import date, datetime, timezone
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")]
Plate = Annotated[str, StringConstraints(pattern=r"^[A-Z0-9]{1,24}$")]
Confidence = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
VehicleType = Literal["car", "truck", "bus", "motorcycle"]
EventDirection = Literal["IN", "OUT", "SIGHTING"]
EvidenceFile = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,110}\.(jpg|jpeg|png)$")]
FrameNumber = Annotated[int, Field(ge=0)]
Finite = Annotated[float, Field(allow_inf_nan=False)]
Name120 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Name80 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
Name40 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Box(Input):
    x1: Finite
    y1: Finite
    x2: Finite
    y2: Finite


class ObservationIn(Input):
    frame_number: FrameNumber
    bbox: Box
    raw_text: Annotated[str, Field(max_length=256)] | None = None
    normalized_text: Annotated[str, Field(max_length=128)]
    plate_confidence: Confidence
    ocr_confidence: Confidence | None = None
    quality: Finite
    association: Literal["MATCHED", "AMBIGUOUS", "REJECTED"]
    valid_format: bool


class DetectionIn(Input):
    frame_number: FrameNumber
    bbox: Box
    quality: Finite


class MovementIn(Input):
    event_uuid: UUID
    plate_number: Plate | None = None
    timestamp: AwareDatetime
    camera_id: Identifier
    gate_id: Identifier
    vehicle_type: VehicleType | None = None

    @model_validator(mode="after")
    def canonical_timestamp(self):
        self.timestamp = self.timestamp.astimezone(timezone.utc)
        return self


class EventIn(MovementIn):
    direction: EventDirection
    track_id: Annotated[int, Field(ge=0)] | None = None
    frame_number: FrameNumber | None = None
    elapsed_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None = None
    ocr_confidence: Confidence | None = None
    plate_confidence: Confidence | None = None
    snapshot_path: EvidenceFile | None = None
    plate_crop_path: EvidenceFile | None = None
    verification_status: Literal["AUTO", "PENDING"] = "PENDING"
    recognition_status: Annotated[str, Field(max_length=40)] | None = None
    reason: Annotated[str, Field(max_length=120)] | None = None
    observations: list[ObservationIn] = Field(default_factory=list, max_length=120)
    detections: list[DetectionIn] = Field(default_factory=list, max_length=120)

    @model_validator(mode="after")
    def validate_confirmation(self):
        if self.verification_status == "AUTO" and self.plate_number is None:
            raise ValueError("AUTO events require a plate number.")
        frame_numbers = [item.frame_number for item in self.detections]
        if len(set(frame_numbers)) != len(frame_numbers):
            raise ValueError("Detection frame numbers must be unique per event.")
        return self


class ToggleIn(MovementIn):
    plate_number: Plate


class VerifyIn(Input):
    event_uuid: UUID
    plate_number: Plate
    notes: Annotated[str, Field(max_length=500)] = ""


class CameraIn(Input):
    id: Identifier
    name: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    gate_id: Identifier
    gate_name: Annotated[str, StringConstraints(min_length=1, max_length=120)] | None = None
    area: Name120 | None = None
    direction: Literal["AUTO", "ENTRY", "EXIT", "TRACKING"] = "AUTO"
    entry_side: Literal["positive", "negative"] = "positive"
    source_binding: Annotated[str, StringConstraints(pattern=r"^CAMERA_SOURCE_[A-Z0-9_]{1,49}$")] | None = None
    width: Annotated[int, Field(gt=0, le=16384)] | None = None
    height: Annotated[int, Field(gt=0, le=16384)] | None = None
    tripwire: tuple[Finite, Finite, Finite, Finite] | None = None
    detection_threshold: Annotated[float, Field(gt=0, le=1, allow_inf_nan=False)] = 0.1
    enabled: bool = True

    @model_validator(mode="after")
    def validate_line(self):
        if self.tripwire is not None and self.tripwire[:2] == self.tripwire[2:]:
            raise ValueError("Tripwire endpoints must differ.")
        return self


class Heartbeat(Input):
    state: Literal["ACTIVE", "OFFLINE", "RECONNECTING"]


class Login(Input):
    username: Identifier
    password: Annotated[str, Field(min_length=1, max_length=256)]


class Token(BaseModel):
    access_token: str
    role: str
    username: str
    token_type: Literal["bearer"] = "bearer"


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    event_uuid: UUID
    camera_id: str
    gate_id: str
    plate_number: str | None
    registration_number: str | None
    timestamp: datetime
    direction: EventDirection
    vehicle_type: str | None
    track_id: int | None
    frame_number: int | None
    ocr_confidence: float | None
    plate_confidence: float | None
    verification_status: str
    recognition_status: str | None
    reason: str | None
    evidence: list[str] = Field(default_factory=list)


class EventDetail(EventOut):
    observations: list[ObservationIn]


class RouteEvent(EventOut):
    camera_name: str
    camera_area: str | None


class VehicleProfileIn(Input):
    owner_or_department: Name120 | None = None
    manufacturer: Name80 | None = None
    model: Name80 | None = None
    color: Name40 | None = None
    fuel_type: Name40 | None = None
    registration_valid_until: date | None = None
    insurance_valid_until: date | None = None
    fitness_valid_until: date | None = None
    puc_certificate_number: Name80 | None = None
    puc_valid_until: date | None = None


RegistryService = Literal["VAHAN_RC", "PARIVAHAN_PUC"]
REGISTRY_FIELDS = {
    "VAHAN_RC": frozenset({
        "manufacturer", "model", "color", "fuel_type", "registration_valid_until",
        "insurance_valid_until", "fitness_valid_until",
    }),
    "PARIVAHAN_PUC": frozenset({"puc_certificate_number", "puc_valid_until"}),
}


class RegistryCheckIn(Input):
    registration_number: Plate
    service: RegistryService
    checked_at: AwareDatetime | None = None
    details: VehicleProfileIn

    @model_validator(mode="after")
    def validate_details(self):
        values = self.details.model_dump(exclude_unset=True)
        if not values or any(value is None for value in values.values()):
            raise ValueError("Supply at least one observed field. Omit missing fields.")
        if values.keys() - REGISTRY_FIELDS[self.service]:
            raise ValueError("Use only fields available for the selected service.")
        return self


class ProfileSource(BaseModel):
    source: RegistryService | Literal["SITE"]
    checked_at: datetime


class VehicleProfileOut(VehicleProfileIn):
    vehicle_type: str | None
    updated_at: datetime | None
    sources: dict[str, ProfileSource]


class ParkingIn(Input):
    entry_event_id: UUID
    zone: Name80 | None
    bay: Name40 | None = None

    @field_validator("zone", "bay", mode="before")
    @classmethod
    def spacing(cls, value):
        return " ".join(value.split()) if isinstance(value, str) else value

    @model_validator(mode="after")
    def require_zone(self):
        if self.bay is not None and self.zone is None:
            raise ValueError("Enter a parking zone for the bay.")
        return self


class ParkingOut(BaseModel):
    zone: str
    bay: str | None
    confirmed_at: datetime
    current: bool


class VehicleRoute(BaseModel):
    registration_number: str
    visit_status: Literal["INSIDE", "EXITED", "UNKNOWN"]
    entry_event_id: UUID | None
    entry_time: datetime | None
    exit_time: datetime | None
    duration_seconds: float | None
    site_date: date
    profile: VehicleProfileOut
    parking: ParkingOut | None
    history: list[RouteEvent]


class CameraOut(CameraIn):
    model_config = ConfigDict(from_attributes=True)
    status: str
    last_seen: datetime | None


class SessionOut(BaseModel):
    id: UUID
    registration_number: str
    vehicle_type: str | None
    entry_event_id: UUID | None
    exit_event_id: UUID | None
    entry_time: datetime | None
    exit_time: datetime | None
    entry_camera_id: str | None
    exit_camera_id: str | None
    entry_gate: str | None
    duration_seconds: float | None
    status: str


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    event_uuid: UUID
    registration_number: str
    kind: str
    timestamp: datetime
