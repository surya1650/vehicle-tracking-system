from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from secrets import token_hex
from uuid import uuid4

import os
import pytest

URL = os.environ.get("ANPR_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="Set ANPR_TEST_DATABASE_URL to a migrated PostgreSQL test DB")


@pytest.fixture
def api(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from backend.auth import PASSWORDS
    from backend.database import make_sessions
    from backend.main import create_app
    from backend.models import User

    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("API_SIGNING_KEY", token_hex(32))
    monkeypatch.setenv("ANPR_EVIDENCE", str(tmp_path))
    marker = token_hex(5)
    credentials = {"username": f"admin_{marker}", "password": token_hex(16)}
    viewer_credentials = {"username": f"viewer_{marker}", "password": token_hex(16)}
    with make_sessions().begin() as db:
        db.add(User(username=credentials["username"], role="ADMIN",
                    password_hash=PASSWORDS.hash(credentials["password"])))
        db.add(User(username=viewer_credentials["username"], role="VIEWER",
                    password_hash=PASSWORDS.hash(viewer_credentials["password"])))

    with TestClient(create_app()) as client:
        headers = {}
        for role, login in [("admin", credentials), ("viewer", viewer_credentials)]:
            result = client.post("/api/v1/auth/login", json=login)
            assert result.status_code == 200
            headers[role] = {"Authorization": "Bearer " + result.json()["access_token"]}
        assert client.get("/health").json() == {"status": "ok"}
        camera = {
            "id": f"cam_{marker}", "gate_id": f"gate_{marker}", "name": "Validation camera",
            "source_binding": "CAMERA_SOURCE_VALIDATION", "tripwire": [1, 2, 100, 2],
        }
        route = "/api/v1/cameras/" + camera["id"]
        assert client.put(route, json=camera, headers=headers["viewer"]).status_code == 403
        configured = client.put(route, json=camera, headers=headers["admin"])
        assert configured.status_code == 200, configured.text
        yield client, headers, camera, tmp_path


def event(camera, plate=None, direction="IN", seconds=0, status="AUTO", **kwargs):
    return {
        "event_uuid": str(uuid4()), "direction": direction, "plate_number": plate,
        "camera_id": camera["id"], "gate_id": camera["gate_id"],
        "timestamp": (datetime(2026, 3, 12, 0, 0, tzinfo=timezone.utc)
                      + timedelta(seconds=seconds)).isoformat(),
        "vehicle_type": "car", "verification_status": status, **kwargs,
    }


def test_entry_exit_reentry_and_exceptions(api):
    client, headers, camera, _ = api
    auth = headers["admin"]
    plate = "AP" + token_hex(6).upper()
    for index, direction in enumerate(("OUT", "IN", "IN", "OUT", "IN")):
        payload = event(camera, plate, direction, seconds=index * 90)
        result = client.post("/api/v1/events", json=payload, headers=auth)
        assert result.status_code == 201, result.text
    inside = client.get("/api/v1/vehicles/inside", headers=auth).json()
    assert len([row for row in inside if row["registration_number"] == plate]) == 1
    sessions = client.get("/api/v1/vehicles/sessions", headers=auth).json()
    visits = [row for row in sessions if row["registration_number"] == plate]
    assert {row["status"] for row in visits} == {"INSIDE", "EXITED", "EXCEPTION"}
    assert next(row for row in visits if row["status"] == "EXITED")["duration_seconds"] == 180
    alerts = client.get("/api/v1/alerts", headers=auth).json()
    assert {row["kind"] for row in alerts if row["registration_number"] == plate} == {
        "UNMATCHED_EXIT", "DUPLICATE_ENTRY",
    }


def test_simultaneous_events_retries_and_conflicts(api):
    client, headers, camera, _ = api
    auth = headers["admin"]
    plate = "KA" + token_hex(6).upper()
    first, second = event(camera, plate), event(camera, plate, seconds=2)

    def send(row):
        result = client.post("/api/v1/events", json=row, headers=auth)
        return result.status_code

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sorted(pool.map(send, [first, first, second, second])) == [200, 200, 201, 201]
    assert send({**first, "direction": "OUT"}) == 409
    inside = client.get("/api/v1/vehicles/inside", headers=auth).json()
    assert len([row for row in inside if row["registration_number"] == plate]) == 1


def test_verification_evidence_and_audit(api):
    client, headers, camera, evidence = api
    auth = headers["admin"]
    plate = "TS" + token_hex(6).upper()
    candidate = event(camera, "AP21AB1234", status="PENDING")
    image = candidate["event_uuid"] + ".jpg"
    (evidence / image).write_bytes(b"test fixture")
    candidate["snapshot_path"] = image
    candidate["observations"] = [{
        "frame_number": 1, "bbox": {"x1": 0, "y1": 0, "x2": 30, "y2": 10},
        "raw_text": "AP21 AB1234", "normalized_text": "AP21AB1234",
        "plate_confidence": 0.8, "ocr_confidence": 0.5, "quality": 3,
        "association": "MATCHED", "valid_format": True,
    }]
    created = client.post("/api/v1/events", json=candidate, headers=auth)
    assert created.status_code == 201, created.text
    assert created.json()["registration_number"] is None
    endpoint = f"/api/v1/events/{candidate['event_uuid']}/evidence/vehicle"
    assert client.get(endpoint).status_code == 401
    assert client.get(endpoint, headers=headers["viewer"]).content == b"test fixture"
    correction = {"event_uuid": candidate["event_uuid"], "plate_number": plate, "notes": "Manual check"}
    assert client.post("/api/v1/events/manual-verify", json=correction, headers=headers["viewer"]).status_code == 403
    result = client.post("/api/v1/events/manual-verify", json=correction, headers=auth)
    assert result.status_code == 200, result.text
    assert result.json()["registration_number"] == plate
    assert result.json()["plate_number"] == "AP21AB1234"
    assert result.json()["observations"][0]["raw_text"] == "AP21 AB1234"
    audit = client.get("/api/v1/audit", headers=auth).json()
    assert any(row["action"] == "plate_verified" and row["details"]["registration"] == plate for row in audit)


def test_toggle_replay_and_late_event(api):
    client, headers, camera, _ = api
    auth = headers["admin"]
    plate = "MH" + token_hex(6).upper()
    exit_event = event(camera, plate, "OUT", seconds=120)
    assert client.post("/api/v1/events", json=exit_event, headers=auth).status_code == 201
    assert client.post("/api/v1/events", json=event(camera, plate), headers=auth).status_code == 201
    alerts = client.get("/api/v1/alerts", headers=auth).json()
    assert not any(row["registration_number"] == plate for row in alerts)
    next_entry = event(camera, plate, seconds=240)
    for item in ("direction", "verification_status"):
        del next_entry[item]
    first = client.post("/api/v1/events/toggle", json=next_entry, headers=auth)
    retry = client.post("/api/v1/events/toggle", json=next_entry, headers=auth)
    assert (first.status_code, retry.status_code) == (201, 200)
    assert first.json() == retry.json()
    assert first.json()["direction"] == "IN"


def test_summary_and_history_filters(api):
    client, headers, camera, _ = api
    auth = headers["admin"]
    payload = event(camera, "DL" + token_hex(6).upper())
    assert client.post("/api/v1/events", json=payload, headers=auth).status_code == 201
    history = client.get("/api/v1/vehicles/history", params={
        "gate": camera["gate_id"], "direction": "IN", "verification": "AUTO",
    }, headers=auth)
    assert history.status_code == 200 and history.json()[0]["event_uuid"] == payload["event_uuid"]
    result = client.get("/api/v1/dashboard/summary", params={"day": "2026-03-12"}, headers=auth)
    assert result.status_code == 200, result.text
    assert result.json()["entries"] >= 1
    assert len(result.json()["hourly"]) == 24 and len(result.json()["daily"]) == 7


def test_retention_keeps_the_event_ledger(api, monkeypatch):
    from backend import retention
    from backend.database import make_sessions
    from backend.models import Snapshot
    from sqlalchemy import select

    client, headers, camera, evidence = api
    item = event(camera, "TN" + token_hex(6).upper())
    name = item["event_uuid"] + ".jpg"
    image = evidence / name
    image.write_bytes(b"test evidence")
    result = client.post("/api/v1/events", json={**item, "snapshot_path": name},
                         headers=headers["admin"])
    assert result.status_code == 201, result.text
    with make_sessions().begin() as db:
        snapshot = db.scalar(select(Snapshot).where(Snapshot.path == name))
        snapshot.created_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr("sys.argv", ["retention", "--days", "30", "--apply"])
    retention.main()
    assert not image.exists()
    result = client.get(f"/api/v1/events/{item['event_uuid']}", headers=headers["viewer"])
    assert result.status_code == 200
    assert result.json()["event_uuid"] == item["event_uuid"] and result.json()["evidence"] == []
