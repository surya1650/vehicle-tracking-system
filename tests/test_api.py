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


def test_vehicle_route_cameras_and_late_verified_sighting(api):
    client, headers, camera, _ = api
    admin, viewer = headers["admin"], headers["viewer"]
    yard = dict(camera, id=camera["id"] + "_yard", name="Yard camera", direction="TRACKING")
    exit_camera = dict(camera, id=camera["id"] + "_exit", name="Exit camera", direction="EXIT")
    for config in (yard, exit_camera):
        assert client.put(f"/api/v1/cameras/{config['id']}", json=config, headers=admin).status_code == 200

    plate = "AP" + token_hex(6).upper()
    route = f"/api/v1/vehicles/{plate}/route"
    assert client.get(route, headers=viewer).status_code == 404
    assert client.get(route).status_code == 401
    entry = event(camera, plate)
    pending = event(yard, plate, "SIGHTING", seconds=240, status="PENDING")
    exit_event = event(exit_camera, plate, "OUT", seconds=660)
    for payload in (entry, pending):
        assert client.post("/api/v1/events", json=payload, headers=admin).status_code == 201
    data = client.get(route, headers=viewer).json()
    assert data["visit_status"] == "INSIDE"
    assert [row["event_uuid"] for row in data["history"]] == [entry["event_uuid"]]
    assert client.post("/api/v1/events", json=exit_event, headers=admin).status_code == 201
    response = client.post("/api/v1/events/manual-verify", headers=admin, json={
        "event_uuid": pending["event_uuid"], "plate_number": plate,
    })
    assert response.status_code == 200, response.text
    data = client.get(route, headers=viewer).json()
    assert data["visit_status"] == "EXITED"
    assert data["duration_seconds"] == 660
    assert [(row["direction"], row["camera_name"]) for row in data["history"]] == [
        ("OUT", "Exit camera"), ("SIGHTING", "Yard camera"), ("IN", camera["name"]),
    ]
    assert client.get("/api/v1/events", params={"direction": "SIGHTING", "plate": plate},
                      headers=viewer).json()[0]["verification_status"] == "VERIFIED"
    visits = client.get("/api/v1/vehicles/sessions", headers=viewer).json()
    assert [row["status"] for row in visits if row["registration_number"] == plate] == ["EXITED"]
    alerts = client.get("/api/v1/alerts", headers=viewer).json()
    assert all(row["registration_number"] != plate for row in alerts)
    for config, direction in ((yard, "IN"), (camera, "SIGHTING")):
        invalid = client.post("/api/v1/events", json=event(config, plate, direction), headers=admin)
        assert invalid.status_code == 422

    yard_only = "AP" + token_hex(6).upper()
    sighting = event(yard, yard_only, "SIGHTING")
    assert client.post("/api/v1/events", json=sighting, headers=admin).status_code == 201
    assert client.post("/api/v1/events", json=sighting, headers=admin).status_code == 200
    data = client.get(f"/api/v1/vehicles/{yard_only}/route", headers=viewer).json()
    assert data["visit_status"] == "UNKNOWN"
    assert data["history"][0]["camera_id"] == yard["id"]


def test_site_vehicle_profile_and_permissions(api):
    client, headers, camera, _ = api
    admin, viewer = headers["admin"], headers["viewer"]
    plate = "DEMO" + token_hex(5).upper()
    url = f"/api/v1/vehicles/{plate}"
    profile = {
        "owner_or_department": "Facilities Service Fleet",
        "manufacturer": "Example Motors",
        "model": "City Utility",
        "fuel_type": "Electric",
        "puc_certificate_number": "TEST-PUC-100",
        "puc_valid_until": "2026-11-10",
        "insurance_valid_until": "2027-02-03",
    }
    assert client.patch(url + "/profile", json=profile, headers=viewer).status_code == 403
    saved = client.patch(url + "/profile", json=profile, headers=admin)
    assert saved.status_code == 200, saved.text
    assert saved.headers["cache-control"] == "no-store"
    admin_route = client.get(url + "/route", headers=admin).json()
    assert admin_route["history"] == [] and admin_route["visit_status"] == "UNKNOWN"
    assert admin_route["profile"]["owner_or_department"] == profile["owner_or_department"]
    reader_route = client.get(url + "/route", headers=viewer)
    assert profile["owner_or_department"] not in reader_route.text
    assert reader_route.json()["profile"]["puc_valid_until"] == profile["puc_valid_until"]
    assert client.post("/api/v1/events", json=event(camera, plate), headers=admin).status_code == 201
    assert client.get(url + "/route", headers=viewer).json()["profile"]["vehicle_type"] == "car"
    edit = client.patch(url + "/profile", json={"color": "Silver", "puc_certificate_number": None},
                        headers=admin)
    assert edit.status_code == 200, edit.text
    assert edit.json()["manufacturer"] == profile["manufacturer"]
    assert edit.json()["puc_certificate_number"] is None
    assert client.patch(url + "/profile", json={"puc_valid_until": "bad date"}, headers=admin).status_code == 422
    audits = client.get("/api/v1/audit", headers=admin).json()
    assert any(row["action"] == "vehicle_profile_updated" and row["details"]["registration"] == plate
               for row in audits)
    assert all(profile["owner_or_department"] not in str(row) for row in audits)


def test_registry_check_updates_only_permitted_fields_and_preserves_source(api):
    client, headers, _, _ = api
    admin, viewer = headers["admin"], headers["viewer"]
    plate = "DEMO" + token_hex(5).upper()
    url = f"/api/v1/vehicles/{plate}"
    standard = {"registration_number": plate, "service": "VAHAN_RC", "details": {"manufacturer": "Example Motors"}}
    puc = {
        "registration_number": plate, "service": "PARIVAHAN_PUC",
        "checked_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        "details": {"puc_valid_until": "2027-02-03"},
    }
    assert client.post(url + "/registry-check", json=standard, headers=viewer).status_code == 403
    invalid = dict(standard, registration_number="DIFFERENT001")
    assert client.post(url + "/registry-check", json=invalid, headers=admin).status_code == 422
    wrong_fields = dict(standard, details={"owner_or_department": "Site only"})
    assert client.post(url + "/registry-check", json=wrong_fields, headers=admin).status_code == 422
    assert client.post(url + "/registry-check", json=standard, headers=admin).status_code == 200

    first = client.post(url + "/registry-check", json=puc, headers=admin)
    retry = client.post(url + "/registry-check", json=puc, headers=admin)
    assert (first.status_code, retry.status_code) == (200, 200)
    assert first.json() == retry.json()
    profile = client.get(url + "/route", headers=viewer).json()["profile"]
    assert profile["manufacturer"] == "Example Motors" and profile["puc_valid_until"] == "2027-02-03"
    assert profile["sources"]["manufacturer"]["source"] == "VAHAN_RC"
    assert profile["sources"]["puc_valid_until"]["source"] == "PARIVAHAN_PUC"
    site_edit = {"owner_or_department": "Local fleet", "puc_valid_until": None}
    assert client.patch(url + "/profile", json=site_edit, headers=admin).status_code == 200
    stale = dict(puc, details={"puc_certificate_number": "STALE-TEST", "puc_valid_until": "2028-02-03"})
    assert client.post(url + "/registry-check", json=stale, headers=admin).status_code == 409
    updated = client.get(url + "/route", headers=viewer).json()["profile"]
    assert updated["puc_valid_until"] is None and updated["puc_certificate_number"] is None
    assert updated["sources"]["puc_valid_until"]["source"] == "SITE"
    assert updated["owner_or_department"] is None and "owner_or_department" not in updated["sources"]
    audits = client.get("/api/v1/audit", headers=admin).json()
    own_checks = [row for row in audits if row["action"] == "vehicle_registry_check_recorded"
                  and row["details"]["registration"] == plate]
    assert {row["details"]["source"] for row in own_checks} == {"VAHAN_RC", "PARIVAHAN_PUC"}
    assert len(own_checks) == 2


def test_parking_and_visit_search(api):
    client, headers, camera, _ = api
    admin, viewer = headers["admin"], headers["viewer"]
    yard = dict(camera, id=camera["id"] + "_yard", name="Yard camera", direction="TRACKING",
                area="North car park")
    camera_api = f"/api/v1/cameras/{yard['id']}"
    assert client.put(camera_api, json=yard, headers=admin).status_code == 200
    legacy_camera = {key: value for key, value in yard.items() if key != "area"}
    assert client.put(camera_api, json=legacy_camera, headers=admin).json()["area"] == yard["area"]
    plate, second_plate = ["DEMO" + token_hex(5).upper() for _ in range(2)]
    url = f"/api/v1/vehicles/{plate}"
    second_url = f"/api/v1/vehicles/{second_plate}"
    start = datetime.now(timezone.utc) - timedelta(days=2, hours=3)
    entry = event(camera, plate, timestamp=start.isoformat())
    second_entry = event(camera, second_plate, timestamp=start.isoformat())
    sighting = event(yard, plate, "SIGHTING", timestamp=(start + timedelta(minutes=5)).isoformat())
    for payload in (entry, second_entry, sighting):
        response = client.post("/api/v1/events", json=payload, headers=admin)
        assert response.status_code == 201, response.text
    route = client.get(url + "/route", headers=viewer).json()
    assert route["visit_status"] == "INSIDE"
    assert route["duration_seconds"] >= 2 * 86400 + 3 * 3600
    assert route["history"][0]["camera_area"] == yard["area"]
    assert route["parking"] is None

    bay = {"entry_event_id": entry["event_uuid"], "zone": "North car park", "bay": "B17"}
    assert client.put(url + "/parking", json=bay, headers=viewer).status_code == 403
    confirmed = client.put(url + "/parking", json=bay, headers=admin)
    assert confirmed.status_code == 204, confirmed.text
    parking = client.get(url + "/route", headers=viewer).json()["parking"]
    assert parking["current"] and parking["bay"] == "B17"
    other = dict(bay, entry_event_id=second_entry["event_uuid"], zone="north  car park", bay="b17")
    assert client.put(second_url + "/parking", json=other, headers=admin).status_code == 409
    during_review = event(yard, plate, "SIGHTING", timestamp=datetime.now(timezone.utc).isoformat(),
                          status="PENDING")
    assert client.post("/api/v1/events", json=during_review, headers=admin).status_code == 201
    assert client.get(url + "/route", headers=viewer).json()["parking"]["current"]
    verified = client.post("/api/v1/events/manual-verify", headers=admin, json={
        "event_uuid": during_review["event_uuid"], "plate_number": plate,
    })
    assert verified.status_code == 200, verified.text
    assert not client.get(url + "/route", headers=viewer).json()["parking"]["current"]
    assert client.put(second_url + "/parking", json=other, headers=admin).status_code == 204
    assert client.put(second_url + "/parking", json=dict(other, zone=None, bay=None),
                      headers=admin).status_code == 204

    departure = start + timedelta(days=2, hours=1)
    exit_event = event(camera, plate, "OUT", timestamp=departure.isoformat())
    assert client.post("/api/v1/events", json=exit_event, headers=admin).status_code == 201
    route = client.get(url + "/route", headers=viewer).json()
    assert route["visit_status"] == "EXITED" and route["duration_seconds"] == 2 * 86400 + 3600
    assert datetime.fromisoformat(route["entry_time"]) == start
    assert datetime.fromisoformat(route["exit_time"]) == departure
    assert not route["parking"]["current"]
    assert client.put(url + "/parking", json=bay, headers=admin).status_code == 409
    reentry = event(camera, plate, timestamp=datetime.now(timezone.utc).isoformat())
    assert client.post("/api/v1/events", json=reentry, headers=admin).status_code == 201
    assert client.get(url + "/route", headers=viewer).json()["parking"] is None
    assert client.put(url + "/parking", json=bay, headers=admin).status_code == 409
    visits = client.get(url + "/visits", headers=viewer).json()
    assert [visit["status"] for visit in visits] == ["INSIDE", "EXITED"]
    assert client.get(url + "/visits?limit=1&offset=1", headers=viewer).json() == visits[1:]


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
