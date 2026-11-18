"""Submit the durable recognition outbox and retry transient API failures."""

import argparse
import json
import logging
import os
from pathlib import Path
from time import perf_counter, sleep

import httpx
from dotenv import load_dotenv

from vision.logging_config import configure_logging

logger = logging.getLogger(__name__)
UPLOAD_FIELDS = {
    "event_uuid", "track_id", "direction", "frame_number", "elapsed_seconds", "timestamp",
    "vehicle_type", "plate_number", "ocr_confidence", "plate_confidence", "verification_status",
    "recognition_status", "reason", "snapshot_path", "plate_crop_path", "observations", "detections",
}


class DeliveryClient:
    def __init__(self, url: str, username: str, password: str) -> None:
        self.client = httpx.Client(base_url=url.rstrip("/") + "/api/v1/", timeout=10)
        self.credentials = {"username": username, "password": password}

    def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        for _ in range(2):
            if "Authorization" not in self.client.headers:
                login = self.client.post("auth/login", json=self.credentials)
                login.raise_for_status()
                self.client.headers["Authorization"] = "Bearer " + login.json()["access_token"]
            result = self.client.request(method, path, **kwargs)
            if result.status_code != 401:
                return result
            self.client.headers.pop("Authorization")
        return result

    def send_dir(self, outbox: Path, camera: str, gate: str, limit: int | None = None) -> bool:
        complete = True
        for path in sorted(outbox.glob("*.json"))[:limit]:
            started = perf_counter()
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
                if document.get("timestamp") is None:
                    raise ValueError("Recording start timestamp is required before submitting.")
                payload = {**{name: document[name] for name in UPLOAD_FIELDS if name in document},
                           "camera_id": camera, "gate_id": gate}
                response = self.request("POST", "events", json=payload)
                if response.status_code not in (200, 201):
                    logger.warning("delivery_rejected %s status=%d", path.stem, response.status_code)
                    complete = False
                    continue
                receipt_dir = outbox / ".sent"
                receipt_dir.mkdir(exist_ok=True)
                os.replace(path, receipt_dir / path.name)
                logger.info("event_submitted %s latency_ms=%.2f", path.stem,
                            (perf_counter() - started) * 1000)
            except httpx.HTTPError as exc:
                logger.error("delivery_pending %s %s", path.stem, type(exc).__name__)
                return False
            except (OSError, ValueError) as exc:
                logger.error("delivery_pending %s %s", path.stem, type(exc).__name__)
                complete = False
        return complete

    def close(self) -> None:
        self.client.close()


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.environ.get("ANPR_API_URL", "http://127.0.0.1:8010"))
    parser.add_argument("--camera", required=True)
    parser.add_argument("--gate", required=True)
    parser.add_argument("--outbox", type=Path, default=Path(os.environ.get("ANPR_OUTBOX", "data/outbox")))
    parser.add_argument("--watch", action="store_true", help="Keep retrying and delivering new files")
    args = parser.parse_args()
    username = os.environ.get("ANPR_INGEST_USERNAME")
    password = os.environ.get("ANPR_INGEST_PASSWORD")
    if not username or not password:
        parser.error("Set ANPR_INGEST_USERNAME and ANPR_INGEST_PASSWORD.")
    configure_logging("INFO")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    client = DeliveryClient(args.url, username, password)
    try:
        while True:
            complete = client.send_dir(args.outbox, args.camera, args.gate)
            if not args.watch:
                return int(not complete)
            sleep(3)
    except KeyboardInterrupt:
        return 130
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
