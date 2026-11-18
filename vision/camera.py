"""Run one configured camera with independent preview health and outbox delivery."""

import argparse
import logging
import os
import re
import signal
import subprocess
import sys
from pathlib import Path
from time import sleep, time

import httpx
from dotenv import load_dotenv

from vision.deliver import DeliveryClient
from vision.logging_config import configure_logging

logger = logging.getLogger(__name__)


def start_camera(camera: dict, source: str, outbox: Path, interval: int,
                 video_start: str | None) -> subprocess.Popen:
    command = [sys.executable, "-m", "vision.run_video", "--track", "--anpr",
               "--tripwire", ",".join(map(str, camera["tripwire"])),
               "--tripwire-mode", camera["direction"], "--entry-side", camera["entry_side"],
               "--live-camera", camera["id"], "--anpr-outbox", str(outbox),
               "--confidence", str(camera["detection_threshold"]),
               "--inference-interval", str(interval)]
    if source.lower().startswith(("rtsp://", "rtsps://")):
        command += ["--rtsp-source", camera["source_binding"]]
    elif video_start:
        command += [source, "--start-time", video_start]
    else:
        raise ValueError("A video file requires --video-start-time with its recording start and UTC offset.")
    process_options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {}
    return subprocess.Popen(
        command, env={**os.environ, "OPENCV_FFMPEG_LOGLEVEL": "0", "OPENCV_LOG_LEVEL": "OFF"},
        **process_options,
    )


def stop_camera(child: subprocess.Popen) -> None:
    if child.poll() is not None:
        return
    child.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)
    try:
        child.wait(45)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait()


def send_heartbeat(client: DeliveryClient, camera: str, status: str) -> None:
    try:
        client.request("POST", f"cameras/{camera}/heartbeat", json={"state": status}).raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("camera_heartbeat_failed camera=%s %s", camera, type(exc).__name__)


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--camera", required=True)
    parser.add_argument("--video-start-time")
    parser.add_argument("--interval", type=int, default=int(os.environ.get("YOLO_INFERENCE_INTERVAL", 3)))
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", args.camera) or args.interval < 1:
        parser.error("Supply a valid camera ID and positive sampling interval.")
    username = os.environ.get("ANPR_INGEST_USERNAME")
    password = os.environ.get("ANPR_INGEST_PASSWORD")
    if not username or not password:
        parser.error("Set ANPR_INGEST_USERNAME and ANPR_INGEST_PASSWORD.")
    configure_logging("INFO")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    client = DeliveryClient(os.environ.get("ANPR_API_URL", "http://127.0.0.1:8010"), username, password)
    outbox = Path(os.environ.get("ANPR_OUTBOX", "data/outbox")) / args.camera
    preview = Path(os.environ.get("ANPR_LIVE", "data/live")) / f"{args.camera}.json"
    child = None
    try:
        response = client.request("GET", f"cameras/{args.camera}")
        response.raise_for_status()
        camera = response.json()
        binding = camera["source_binding"]
        source = os.environ.get(binding) if binding else None
        if not camera["enabled"] or not camera["tripwire"] or not source:
            raise ValueError("An enabled camera, a configured tripwire, and its source variable are required.")
        started_at = time()
        child = start_camera(camera, source, outbox, args.interval, args.video_start_time)
        while child.poll() is None:
            recent = preview.is_file() and preview.stat().st_mtime >= max(started_at, time() - 8)
            send_heartbeat(client, args.camera, "ACTIVE" if recent else "RECONNECTING")
            client.send_dir(outbox, args.camera, camera["gate_id"], limit=20)
            sleep(2)
        submitted = client.send_dir(outbox, args.camera, camera["gate_id"])
        return child.returncode or int(not submitted)
    except KeyboardInterrupt:
        return 130
    except (httpx.HTTPError, OSError) as exc:
        logger.error("camera_worker_failed camera=%s %s", args.camera, type(exc).__name__)
        return 1
    except ValueError as exc:
        logger.error("%s", exc)
        return 1
    finally:
        if child is not None:
            stop_camera(child)
            send_heartbeat(client, args.camera, "OFFLINE")
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
