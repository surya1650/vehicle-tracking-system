"""Generate and validate a staged three-camera replay of one vehicle."""

import argparse
import json
import os
import shutil
import subprocess
import sys
from contextlib import ExitStack
from datetime import date
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import cv2
from dotenv import load_dotenv

from validation.fetch_assets import ASSETS, fetch
from vision.deliver import DeliveryClient
from vision.sources.file_source import FileVideoSource

PLATE = "3066MP4"
PLATE_FORMAT = "[0-9]{4}[A-Z]{2}[0-9]"
FIRST_FRAME, END_FRAME = 60, 111
CAMERAS = (
    ("demo-gate-in", "Gate IN", "ENTRY", "IN", 0, (0, 0, 1080, 1920)),
    ("demo-yard", "Yard Camera", "TRACKING", "SIGHTING", 4, (40, 180, 1060, 1780)),
    ("demo-gate-out", "Gate OUT", "EXIT", "OUT", 11, (0, 260, 1080, 1840)),
)


def generate(source: Path, output: Path, day: str, review: bool) -> list[dict]:
    output.mkdir(parents=True, exist_ok=True)
    manifest = []
    with FileVideoSource(source) as video, ExitStack() as stack:
        writers = []
        for camera_id, name, mode, direction, minute, crop in CAMERAS:
            if review:
                camera_id = camera_id.replace("demo-", "demo-review-", 1)
            review_yard = review and mode == "TRACKING"
            left, top, right, bottom = crop
            width, height = right - left, bottom - top
            wire = 1320 - top
            writer = cv2.VideoWriter(
                str(output / f"{camera_id}.mp4"), cv2.VideoWriter_fourcc(*"mp4v"),
                video.metadata.fps, (width, height),
            )
            stack.callback(writer.release)
            if not writer.isOpened():
                raise RuntimeError(f"Could not write {camera_id}.mp4")
            writers.append((writer, crop, name, review_yard))
            replay = {
                "file": f"{camera_id}.mp4",
                "start_time": f"{day}T{11 if review else 10}:{minute:02d}:00+05:30",
                "expected_direction": direction,
                "camera": {
                    "id": camera_id, "name": f"{name} ({'Review Replay' if review else 'Replay'})",
                    "gate_id": camera_id,
                    "direction": mode, "width": width, "height": height,
                    "tripwire": [0, wire, width - 1, wire],
                },
            }
            if review_yard:
                replay.update(expected_status="PENDING", expected_reason="insufficient_agreement")
            manifest.append(replay)
        frames_written = 0
        for frame in video:
            if frame.number < FIRST_FRAME:
                continue
            if frame.number >= END_FRAME:
                break
            for writer, (left, top, right, bottom), name, review_yard in writers:
                image = frame.image[top:bottom, left:right].copy()
                if review_yard and frame.number != 78:
                    # Keep one sharp plate observation for the operator to inspect.
                    plate_area = image[880:1110, 350:870]
                    plate_area[:] = cv2.blur(plate_area, (111, 15))
                cv2.rectangle(image, (24, 24), (550, 75), (24, 39, 51), -1)
                cv2.putText(image, f"STAGED REPLAY  /  {name}", (43, 57),
                            cv2.FONT_HERSHEY_SIMPLEX, .82, (243, 252, 255), 2, cv2.LINE_AA)
                writer.write(image)
            frames_written += 1
    if frames_written != END_FRAME - FIRST_FRAME:
        raise RuntimeError("The source did not contain all replay frames.")
    return manifest


def replay_event(outbox: Path, replay: dict) -> dict:
    records = {path.name: path for path in (outbox / ".sent").glob("*.json")}
    records.update({path.name: path for path in outbox.glob("*.json")})
    events = [json.loads(path.read_text(encoding="utf-8")) for path in records.values()]
    expected = (replay["expected_direction"], PLATE, replay.get("expected_status", "AUTO"))
    actual = [(item["direction"], item["plate_number"], item["verification_status"])
              for item in events]
    if actual != [expected]:
        raise RuntimeError(f"{outbox.name} expected one {expected} event, got {actual}. "
                           f"See {outbox.parents[2] / (outbox.name + '.json')}.")
    if reason := replay.get("expected_reason"):
        if events[0]["reason"] != reason or not events[0]["plate_crop_path"]:
            raise RuntimeError(f"{outbox.name} expected {reason} with plate evidence.")
    return events[0]


def process(output: Path, manifest: list[dict], weights: Path) -> list[dict]:
    events = []
    for replay in manifest:
        camera = replay["camera"]
        camera_id = camera["id"]
        recording_id = str(uuid5(NAMESPACE_URL, f"anpr-staged-route-v1:{replay}"))
        outbox = output / "outbox" / recording_id / camera_id
        subprocess.run([
            sys.executable, "-m", "vision.run_video", str(output / replay["file"]),
            "--track", "--weights", str(weights), "--anpr", "--inference-interval", "3",
            "--tripwire", ",".join(map(str, camera["tripwire"])), "--tripwire-mode", camera["direction"],
            "--start-time", replay["start_time"], "--recording-id", recording_id,
            "--plate-format", PLATE_FORMAT, "--ocr-post", "1", "--anpr-outbox", str(outbox),
            "--anpr-evidence", str(output / "evidence"), "--live-camera", camera_id,
            "--live-dir", str(output / "live"), "--report", str(output / f"{camera_id}.json"),
        ], check=True, stdout=subprocess.DEVNULL)
        events.append({"replay": replay, "outbox": outbox,
                       "event": replay_event(outbox, replay)})
    return events


def share_media(output: Path, event: dict, camera_id: str) -> None:
    for folder, target, filenames in (
        ("evidence", Path(os.getenv("ANPR_EVIDENCE", "data/snapshots")),
         (event["snapshot_path"], event["plate_crop_path"])),
        ("live", Path(os.getenv("ANPR_LIVE", "data/live")),
         (f"{camera_id}.jpg", f"{camera_id}.json")),
    ):
        source = output / folder
        if source.resolve() != target.resolve():
            target.mkdir(parents=True, exist_ok=True)
            for name in filenames:
                if name is not None:
                    shutil.copyfile(source / name, target / name)


def submit_replay(client: DeliveryClient, output: Path, replay: dict) -> None:
    camera = replay["replay"]["camera"]
    camera_id = camera["id"]
    share_media(output, replay["event"], camera_id)
    client.request("PUT", f"cameras/{camera_id}", json=camera).raise_for_status()
    outbox = replay["outbox"]
    name = replay["event"]["event_uuid"] + ".json"
    if not (outbox / name).is_file():
        shutil.copyfile(outbox / ".sent" / name, outbox / name)
    if not client.send_dir(outbox, camera_id, camera["gate_id"]):
        raise RuntimeError(f"Delivery failed for {camera_id}.")


def require_route(client: DeliveryClient, events: list[dict], status: str) -> None:
    response = client.request("GET", f"vehicles/{PLATE}/route")
    response.raise_for_status()
    data = response.json()
    actual = [event["event_uuid"] for event in data["history"][:len(events)]]
    expected = [item["event"]["event_uuid"] for item in reversed(events)]
    if actual != expected or data["visit_status"] != status:
        raise RuntimeError(f"The latest route did not match the staged recordings and {status} visit.")


def load_review(client: DeliveryClient, output: Path, events: list[dict], release_exit: bool) -> None:
    entry, yard, exit_event = events
    if not release_exit:
        for replay in (entry, yard):
            submit_replay(client, output, replay)
    yard_id = yard["event"]["event_uuid"]
    response = client.request("GET", f"events/{yard_id}")
    if response.status_code == 404 and release_exit:
        raise RuntimeError("Load the review replay before releasing Gate OUT.")
    response.raise_for_status()
    sighting = response.json()
    verified = (sighting["registration_number"] == PLATE
                and sighting["verification_status"] == "VERIFIED")
    if release_exit:
        if not verified:
            raise RuntimeError(f"Verify the yard sighting for {PLATE} before releasing Gate OUT.")
        submit_replay(client, output, exit_event)
        require_route(client, events, "EXITED")
        print(f"{PLATE}: Gate IN → Yard Camera → Gate OUT · EXITED")
    elif verified:
        print(f"The yard sighting for {PLATE} is already verified.")
    else:
        require_route(client, [entry], "INSIDE")
        if sighting["verification_status"] != "PENDING":
            raise RuntimeError("Use a fresh replay day to start with a pending yard sighting.")
        print(f"{PLATE} is inside and was last confirmed at Gate IN. "
              "Open Recognition review to verify the yard sighting.")


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--day", default="2026-11-19", type=date.fromisoformat)
    parser.add_argument("--review", action="store_true", help="Stage a yard sighting that needs manual verification")
    parser.add_argument("--release-exit", action="store_true", help="Submit Gate OUT after verifying the yard sighting")
    parser.add_argument("--process", action="store_true", help="Run vehicle detection, tracking, and OCR")
    parser.add_argument("--api-url", help="Load the confirmed replay into a test API using a dashboard admin")
    args = parser.parse_args()
    if args.release_exit and not (args.review and args.api_url):
        parser.error("--release-exit requires --review and --api-url.")
    username, password = os.getenv("ANPR_DEMO_USERNAME"), os.getenv("ANPR_DEMO_PASSWORD")
    if args.api_url and (not args.process or not username or not password):
        parser.error("To load the demo API, use --process and set ANPR_DEMO_USERNAME and ANPR_DEMO_PASSWORD.")
    for name, url, digest in ASSETS if args.process else ASSETS[:1]:
        fetch(args.data_dir / name, url, digest)
    source = args.data_dir / ASSETS[0][0]
    output = args.output or Path("data/replays/multicamera-review" if args.review else "data/replays/multicamera")
    manifest = generate(source, output, args.day.isoformat(), args.review)
    (output / "manifest.json").write_text(json.dumps({
        "source": ASSETS[0][1], "source_frames": [FIRST_FRAME, END_FRAME - 1],
        "expected_plate": PLATE, "cameras": manifest,
    }, indent=2) + "\n", encoding="utf-8")
    if args.process:
        events = process(output, manifest, args.data_dir / ASSETS[1][0])
        if args.api_url:
            client = DeliveryClient(args.api_url, username, password)
            try:
                if args.review:
                    load_review(client, output, events, args.release_exit)
                else:
                    # Send the exit first to exercise late-arrival reconciliation.
                    for replay in (events[2], events[0], events[1]):
                        submit_replay(client, output, replay)
                    require_route(client, events, "EXITED")
                    print(f"{PLATE}: Gate IN → Yard Camera → Gate OUT · EXITED")
            finally:
                client.close()
    print(f"Replay files: {output.resolve()}")


if __name__ == "__main__":
    main()
