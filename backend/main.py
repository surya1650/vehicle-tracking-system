import json
import logging
import os
from datetime import date, datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Annotated, Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import AwareDatetime
from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

from backend.auth import PASSWORDS, issue, signing_key, subject, unauthorized
from backend.database import make_sessions
from backend.models import Alert, Camera, Event, Gate, Snapshot, User, Vehicle
from backend.movements import audit, save_event, verify_event
from backend.queries import camera_out, event_detail, events_out, profile_out, sessions_out, summary, vehicle_route
from backend.schemas import (
    AlertOut, CameraIn, CameraOut, EventDetail, EventDirection, EventIn, EventOut, Heartbeat,
    Identifier, Login, ParkingIn, Plate, RegistryCheckIn, SessionOut, ToggleIn, Token,
    VehicleProfileIn, VehicleProfileOut, VehicleRoute, VerifyIn,
)
from backend.site_records import save_parking, save_profile

logger = logging.getLogger("uvicorn.error")


def create_app() -> FastAPI:
    sessions = make_sessions()
    key = signing_key()
    zone = ZoneInfo(os.environ.get("SITE_TIMEZONE", "Asia/Kolkata"))
    evidence = Path(os.environ.get("ANPR_EVIDENCE", "data/snapshots")).resolve()
    live = Path(os.environ.get("ANPR_LIVE", "data/live")).resolve()
    dummy_hash = PASSWORDS.hash("unused-verification-timing-placeholder")
    app = FastAPI(title="Vehicle Tracking API", version="1.0", docs_url="/docs")
    bearer = HTTPBearer(auto_error=False)

    def get_db():
        with sessions() as db:
            yield db

    Db = Annotated[Session, Depends(get_db)]

    def current_user(db: Db, auth: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> User:
        if auth is None:
            raise unauthorized()
        user = db.get(User, subject(auth.credentials, key))
        if user is None or not user.enabled:
            raise unauthorized()
        return user

    def require(*roles):
        def allowed(user: Annotated[User, Depends(current_user)]) -> User:
            if user.role not in roles:
                raise HTTPException(403, "Insufficient permissions.")
            return user
        return allowed

    Reader = Annotated[User, Depends(require("VIEWER", "OPERATOR", "ADMIN"))]
    Operator = Annotated[User, Depends(require("OPERATOR", "ADMIN"))]
    Admin = Annotated[User, Depends(require("ADMIN"))]
    Writer = Annotated[User, Depends(require("INGEST", "ADMIN"))]
    CameraReader = Annotated[User, Depends(require("VIEWER", "OPERATOR", "ADMIN", "INGEST"))]
    Limit = Annotated[int, Query(ge=1, le=200)]
    Offset = Annotated[int, Query(ge=0)]

    @app.middleware("http")
    async def trace(request: Request, call_next):
        request_id = str(uuid4())
        started = perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        if request.scope["path"].startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        route = request.scope.get("route")
        logger.info(json.dumps({
            "request_id": request_id, "method": request.method,
            "route": getattr(route, "path", "unknown"), "status": response.status_code,
            "ms": round((perf_counter() - started) * 1000, 2),
        }))
        return response

    @app.exception_handler(SQLAlchemyError)
    async def database_error(_request, exc):
        logger.error("database_error %s", type(exc).__name__)
        if isinstance(exc, OperationalError):
            return JSONResponse({"detail": "Database unavailable."}, status_code=503)
        if isinstance(exc, IntegrityError):
            return JSONResponse({"detail": "The record conflicts with existing data."}, status_code=409)
        return JSONResponse({"detail": "Database request failed."}, status_code=500)

    @app.get("/health")
    def health(db: Db):
        db.execute(select(Camera.id).limit(1))
        return {"status": "ok"}

    @app.post("/api/v1/auth/login", response_model=Token)
    def login(body: Login, db: Db):
        user = db.get(User, body.username)
        ok = PASSWORDS.verify(body.password, user.password_hash if user else dummy_hash)
        if not user or not user.enabled or not ok:
            raise unauthorized()
        audit(db, user.username, "sign_in")
        db.commit()
        return Token(access_token=issue(user.username, key, user.role), role=user.role, username=user.username)

    @app.get("/api/v1/cameras", response_model=list[CameraOut])
    def cameras(db: Db, _user: CameraReader, limit: Limit = 100, offset: Offset = 0):
        return [camera_out(row) for row in db.scalars(
            select(Camera).order_by(Camera.id).limit(limit).offset(offset)
        )]

    @app.get("/api/v1/cameras/{camera_id}", response_model=CameraOut)
    def get_camera(camera_id: Identifier, db: Db, _user: CameraReader):
        camera = db.get(Camera, camera_id)
        if camera is None:
            raise HTTPException(404, "Camera not found.")
        return camera_out(camera)

    @app.put("/api/v1/cameras/{camera_id}", response_model=CameraOut)
    def put_camera(camera_id: Identifier, body: CameraIn, db: Db, actor: Admin):
        if body.id != camera_id:
            raise HTTPException(422, "Camera identifier must match the path.")
        db.execute(insert(Gate).values(id=body.gate_id, name=body.gate_name or body.gate_id)
                   .on_conflict_do_nothing(index_elements=[Gate.id]))
        values = body.model_dump(mode="json", exclude={"gate_name"})
        if "area" not in body.model_fields_set:
            values.pop("area")
        db.execute(insert(Camera).values(**values).on_conflict_do_update(
            index_elements=[Camera.id], set_={key: value for key, value in values.items() if key != "id"},
        ))
        audit(db, actor.username, "camera_configured", camera_id=camera_id)
        db.commit()
        return camera_out(db.get(Camera, camera_id))

    @app.post("/api/v1/cameras/{camera_id}/heartbeat", response_model=CameraOut)
    def camera_heartbeat(camera_id: Identifier, body: Heartbeat, db: Db, _user: Writer):
        camera = db.get(Camera, camera_id)
        if camera is None:
            raise HTTPException(404, "Camera not found.")
        camera.last_seen = datetime.now(timezone.utc)
        camera.stream_state = body.state
        db.commit()
        return camera_out(camera)

    def file_image(root: Path, name: str) -> FileResponse:
        path = (root / name).resolve()
        if path.parent != root or not path.is_file():
            raise HTTPException(404, "Image unavailable.")
        return FileResponse(path, headers={"Cache-Control": "no-store"})

    @app.get("/api/v1/cameras/{camera_id}/image")
    def camera_image(camera_id: Identifier, db: Db, _user: Reader):
        camera = db.get(Camera, camera_id)
        if camera is None or not camera.enabled:
            raise HTTPException(404, "Camera unavailable.")
        return file_image(live, f"{camera_id}.jpg")

    @app.get("/api/v1/cameras/{camera_id}/live")
    def camera_live(camera_id: Identifier, db: Db, _user: Reader):
        camera = db.get(Camera, camera_id)
        path = (live / f"{camera_id}.json").resolve()
        if camera is None or not camera.enabled or path.parent != live or not path.is_file():
            raise HTTPException(404, "Camera preview unavailable.")
        return JSONResponse(json.loads(path.read_text(encoding="utf-8")),
                            headers={"Cache-Control": "no-store"})

    @app.post("/api/v1/events", response_model=EventOut, status_code=201)
    def post_event(body: EventIn, db: Db, actor: Writer, response: Response):
        event, created = save_event(db, body, actor.username)
        db.commit()
        if not created:
            response.status_code = 200
        return events_out(db, [event])[0]

    @app.post("/api/v1/events/toggle", response_model=EventOut, status_code=201)
    def toggle(body: ToggleIn, db: Db, actor: Operator, response: Response):
        event, created = save_event(db, body, actor.username)
        db.commit()
        if not created:
            response.status_code = 200
        return events_out(db, [event])[0]

    @app.post("/api/v1/events/manual-verify", response_model=EventDetail)
    def manual_verify(body: VerifyIn, db: Db, actor: Operator):
        event = verify_event(db, body, actor.username)
        db.commit()
        return event_detail(db, event)

    @app.get("/api/v1/events", response_model=list[EventOut])
    @app.get("/api/v1/vehicles/history", response_model=list[EventOut])
    def event_list(
        db: Db, _user: Reader, plate: Annotated[str, Query(max_length=24)] = "",
        gate: Identifier | None = None, camera: Identifier | None = None, direction: EventDirection | None = None,
        verification: Literal["AUTO", "PENDING", "VERIFIED"] | None = None,
        since: AwareDatetime | None = None, until: AwareDatetime | None = None,
        limit: Limit = 50, offset: Offset = 0,
    ):
        if since and until and since >= until:
            raise HTTPException(422, "Start time must precede end time.")
        query = select(Event)
        if plate:
            query = query.where(or_(
                Event.registration_number.contains(plate.upper(), autoescape=True),
                Event.plate_number.contains(plate.upper(), autoescape=True),
            ))
        for value, field in ((gate, Event.gate_id), (camera, Event.camera_id),
                             (direction, Event.direction),
                             (verification, Event.verification_status)):
            if value:
                query = query.where(field == value)
        if since:
            query = query.where(Event.timestamp >= since)
        if until:
            query = query.where(Event.timestamp < until)
        query = query.order_by(Event.timestamp.desc(), Event.event_uuid).limit(limit).offset(offset)
        return events_out(db, list(db.scalars(query)))

    @app.get("/api/v1/events/{event_id}", response_model=EventDetail)
    def get_event(event_id: UUID, db: Db, _user: Reader):
        event = db.get(Event, event_id)
        if event is None:
            raise HTTPException(404, "Event not found.")
        return event_detail(db, event)

    @app.get("/api/v1/events/{event_id}/evidence/{kind}")
    def event_image(event_id: UUID, kind: Literal["vehicle", "plate"], db: Db, _user: Reader):
        image = db.scalar(select(Snapshot).where(Snapshot.event_uuid == event_id, Snapshot.kind == kind))
        if image is None:
            raise HTTPException(404, "Image unavailable.")
        return file_image(evidence, image.path)

    @app.get("/api/v1/vehicles/inside", response_model=list[SessionOut])
    def vehicles_inside(db: Db, _user: Reader, limit: Limit = 50, offset: Offset = 0):
        return sessions_out(db, True, limit, offset)

    @app.get("/api/v1/vehicles/sessions", response_model=list[SessionOut])
    def vehicle_sessions(db: Db, _user: Reader, limit: Limit = 50, offset: Offset = 0):
        return sessions_out(db, False, limit, offset)

    @app.get("/api/v1/vehicles/{plate_number}/route", response_model=VehicleRoute)
    def route(plate_number: Plate, db: Db, user: Reader, limit: Limit = 50):
        record = db.get(Vehicle, plate_number)
        if record is None:
            raise HTTPException(404, "Vehicle not found.")
        return vehicle_route(db, record, limit, datetime.now(zone), user.role == "ADMIN")

    @app.get("/api/v1/vehicles/{plate_number}/visits", response_model=list[SessionOut])
    def visits(plate_number: Plate, db: Db, _user: Reader, limit: Limit = 50, offset: Offset = 0):
        return sessions_out(db, False, limit, offset, plate=plate_number)

    @app.patch("/api/v1/vehicles/{plate_number}/profile", response_model=VehicleProfileOut)
    def update_profile(plate_number: Plate, body: VehicleProfileIn, db: Db, actor: Admin):
        record = save_profile(db, plate_number, body.model_dump(exclude_unset=True), actor.username)
        db.commit()
        return profile_out(record, show_owner=True)

    @app.post("/api/v1/vehicles/{plate_number}/registry-check", response_model=VehicleProfileOut)
    def record_registry_check(plate_number: Plate, body: RegistryCheckIn, db: Db, actor: Admin):
        if body.registration_number != plate_number:
            raise HTTPException(422, "The registration on the official record must match the searched vehicle.")
        record = save_profile(
            db, plate_number, body.details.model_dump(exclude_unset=True), actor.username,
            service=body.service, checked_at=body.checked_at,
        )
        db.commit()
        return profile_out(record, show_owner=True)

    @app.put("/api/v1/vehicles/{plate_number}/parking", status_code=204)
    def update_parking(plate_number: Plate, body: ParkingIn, db: Db, actor: Operator):
        save_parking(db, plate_number, body, actor.username)
        db.commit()

    @app.get("/api/v1/vehicles/{plate_number}", response_model=list[EventOut])
    def vehicle(plate_number: Plate, db: Db, _user: Reader, limit: Limit = 50, offset: Offset = 0):
        if db.get(Vehicle, plate_number) is None:
            raise HTTPException(404, "Vehicle not found.")
        events = db.scalars(select(Event).where(Event.registration_number == plate_number)
                            .order_by(Event.timestamp.desc(), Event.event_uuid).limit(limit).offset(offset))
        return events_out(db, list(events))

    @app.get("/api/v1/dashboard/summary")
    def overview(db: Db, _user: Reader, day: date | None = None):
        return summary(db, day or datetime.now(zone).date(), zone)

    @app.get("/api/v1/alerts", response_model=list[AlertOut])
    def alerts(db: Db, _user: Reader, limit: Limit = 50, offset: Offset = 0):
        return list(db.scalars(select(Alert).order_by(Alert.timestamp.desc(), Alert.id)
                               .limit(limit).offset(offset)))

    @app.get("/api/v1/audit")
    def audit_records(db: Db, _user: Admin, limit: Limit = 50, offset: Offset = 0):
        from backend.models import AuditLog

        return [
            {"actor": item.actor, "action": item.action, "details": item.details,
             "timestamp": item.timestamp}
            for item in db.scalars(select(AuditLog).order_by(AuditLog.id.desc())
                                   .limit(limit).offset(offset))
        ]

    frontend = Path(os.environ.get("FRONTEND_DIST", "frontend/dist"))
    if (frontend / "index.html").is_file():
        app.mount("/", StaticFiles(directory=frontend, html=True), name="dashboard")
    return app
