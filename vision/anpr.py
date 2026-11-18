"""Bounded crossing-triggered recognition with an isolated FastALPR process."""

import json
import logging
import math
import os
import re
import subprocess
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from queue import Empty, Full, Queue
from tempfile import TemporaryDirectory
from threading import Lock, Thread
from time import perf_counter
from uuid import UUID

import cv2

from vision.recognition import INDIAN_FORMATS, PlateObservation, aggregate, associate
from vision.sources.base_source import Frame
from vision.tracks import BoundingBox, Vehicle
from vision.tripwire import Crossing

logger = logging.getLogger(__name__)


class FastAlprProcess:
    def __init__(self, python: Path, detector: str, ocr: str, timeout: float, retries: int):
        self.command = [str(python.absolute()), "-u", "-m", "vision.anpr_worker",
                        "--detector", detector, "--ocr", ocr]
        self.timeout = timeout
        self.retries = retries
        self.process: subprocess.Popen | None = None

    def _start(self) -> None:
        self.process = subprocess.Popen(
            self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, encoding="utf-8",
        )
        self.responses: Queue[str] = Queue()
        self.reader = Thread(target=self._receive, args=(self.process, self.responses), daemon=True)
        self.reader.start()

    @staticmethod
    def _receive(process: subprocess.Popen, responses: Queue[str]) -> None:
        try:
            for line in process.stdout:
                responses.put(line)
        finally:
            responses.put("")

    def predict(self, path: Path) -> list[dict]:
        for attempt in range(self.retries + 1):
            try:
                if self.process is None:
                    self._start()
                self.process.stdin.write(json.dumps(str(path)) + "\n")
                self.process.stdin.flush()
                line = self.responses.get(timeout=self.timeout)
                if not line:
                    raise RuntimeError("FastALPR exited before answering")
                response = json.loads(line)
                if "error" in response:
                    raise RuntimeError(response["error"])
                return response["plates"]
            except (Empty, OSError, ValueError, RuntimeError) as exc:
                self.close()
                if attempt == self.retries:
                    raise RuntimeError("FastALPR request failed or timed out") from exc
        raise AssertionError("Invalid FastALPR retry count")

    def close(self) -> None:
        process, self.process = self.process, None
        if process is None:
            return
        if process.poll() is None:
            process.kill()
        process.wait()
        self.reader.join()
        process.stdin.close()
        process.stdout.close()


@dataclass(frozen=True, slots=True)
class AnprConfig:
    python: Path
    outbox: Path
    evidence: Path
    detector: str = "yolo-v9-t-384-license-plate-end2end"
    ocr: str = "cct-xs-v2-global-model"
    pre: int = 5
    post: int = 5
    max_candidates: int = 6
    queue_size: int = 8
    minimum: float = 0.65
    agreement: int = 2
    timeout: float = 30
    retries: int = 1
    formats: tuple[str, ...] = INDIAN_FORMATS
    max_tracks: int = 48


@dataclass(frozen=True, slots=True)
class Candidate:
    frame: int
    track: Vehicle
    neighbors: tuple[BoundingBox, ...]
    offset: tuple[int, int]
    scale: tuple[float, float]
    image: bytes
    quality: float


@dataclass(slots=True)
class Pending:
    crossing: Crossing
    candidates: list[Candidate] = field(default_factory=list)
    expires: float = 0
    started: float = field(default_factory=perf_counter)


def crop(frame: Frame, vehicle: Vehicle, neighbors: tuple[BoundingBox, ...]) -> Candidate | None:
    height, width = frame.image.shape[:2]
    box = vehicle.bbox
    x1, x2 = max(0, math.floor(box.x1)), min(width, math.ceil(box.x2))
    y1, y2 = max(0, math.floor(box.y1)), min(height, math.ceil(box.y2))
    if x2 - x1 < 20 or y2 - y1 < 20:
        return None
    image = frame.image[y1:y2, x1:x2]
    h, w = image.shape[:2]
    scale = min(1, 1080 / max(h, w))
    if scale < 1:
        image = cv2.resize(image, (max(1, round(w * scale)), max(1, round(h * scale))),
                           interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    quality = math.log1p(cv2.Laplacian(gray, cv2.CV_64F).var()) + math.log1p(gray.size) / 4
    ok, jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ok:
        return None
    return Candidate(frame.number, vehicle, neighbors, (x1, y1),
                     (w / image.shape[1], h / image.shape[0]), jpeg.tobytes(), quality)


class RecognitionPipeline:
    def __init__(self, config: AnprConfig) -> None:
        self.config = config
        if not config.python.is_file():
            raise ValueError("OCR interpreter missing. Create .venv-ocr or supply --ocr-python.")
        self.formats = tuple(re.compile(pattern) for pattern in config.formats)
        config.outbox.mkdir(parents=True, exist_ok=True)
        self.checkpoints = config.outbox / ".pending"
        self.checkpoints.mkdir(exist_ok=True)
        for path in self.checkpoints.glob("*.json"):
            if self._has_result(path.name):
                path.unlink()
            else:
                path.replace(config.outbox / path.name)
        self.queues: Queue[Pending | None] = Queue(config.queue_size)
        self.history: dict[int, deque[Candidate]] = {}
        self.pending: dict[UUID, Pending] = {}
        self.engine = FastAlprProcess(
            config.python, config.detector, config.ocr, config.timeout, config.retries,
        )
        self.stats: dict[str, int | float] = {
            "events": 0, "ocr_calls": 0, "ocr_ms": 0, "event_pipeline_ms": 0,
        }
        self.error: BaseException | None = None
        self._lock = Lock()
        self._scratch = TemporaryDirectory(prefix="anpr-")
        self.thread = Thread(target=self._worker, name="recognition-worker")
        self.thread.start()
        self._closed = False

    def process(self, frame: Frame, vehicles: list[Vehicle], crossings: list[Crossing]) -> None:
        if self.error is not None:
            raise RuntimeError("Could not persist ANPR results") from self.error
        active = {vehicle.track_id for vehicle in vehicles if vehicle.track_id is not None}
        # Keep recent crops through temporary misses, while limiting retained track count.
        for vehicle in vehicles:
            track_id = vehicle.track_id
            if track_id is None:
                continue
            neighbors = tuple(other.bbox for other in vehicles if other is not vehicle
                              and other.track_id != track_id
                              and other.bbox.x1 < vehicle.bbox.x2
                              and other.bbox.x2 > vehicle.bbox.x1
                              and other.bbox.y1 < vehicle.bbox.y2
                              and other.bbox.y2 > vehicle.bbox.y1)
            candidate = crop(frame, vehicle, neighbors)
            if candidate is None:
                continue
            if track_id not in self.history and len(self.history) >= self.config.max_tracks:
                stale = min(self.history, key=lambda item: (
                    item in active, self.history[item][-1].frame,
                ))
                del self.history[stale]
            self.history.setdefault(track_id, deque(maxlen=self.config.pre + self.config.post + 24))
            self.history[track_id].append(candidate)

        for crossing in crossings:
            name = f"{crossing.event_uuid}.json"
            if self._has_result(name):
                continue
            checkpoint = {
                **asdict(crossing), "event_uuid": str(crossing.event_uuid),
                "timestamp": crossing.timestamp.isoformat() if crossing.timestamp else None,
                "verification_status": "PENDING", "recognition_status": "RECOGNITION_FAILED",
                "reason": "worker_interrupted", "observations": [],
            }
            temporary = self.checkpoints / f"{name}.tmp"
            temporary.write_text(json.dumps(checkpoint), encoding="utf-8")
            temporary.replace(self.checkpoints / name)
            self.pending[crossing.event_uuid] = Pending(crossing, expires=frame.elapsed_seconds + 2)

        for event_id, job in list(self.pending.items()):
            history = list(self.history.get(job.crossing.track_id, ()))
            before = [item for item in history if item.frame < job.crossing.frame_number]
            after = [item for item in history if item.frame > job.crossing.frame_number]
            at = [item for item in history if item.frame == job.crossing.frame_number]
            job.candidates = before[-self.config.pre:] + at + after[:self.config.post]
            if len(after) >= self.config.post or frame.elapsed_seconds >= job.expires:
                self._enqueue(self.pending.pop(event_id))

    def _has_result(self, name: str) -> bool:
        return ((self.config.outbox / name).exists()
                or (self.config.outbox / ".sent" / name).exists())

    def reset(self) -> None:
        for job in self.pending.values():
            self._enqueue(job)
        self.pending.clear()
        self.history.clear()

    def _enqueue(self, job: Pending) -> None:
        try:
            self.queues.put_nowait(job)
        except Full:
            # Persist a reviewable crossing on overload.
            self._save(job, [], None, "queue_full")

    def _worker(self) -> None:
        path = Path(self._scratch.name) / "candidate.jpg"
        for job in iter(self.queues.get, None):
            observations: list[PlateObservation] = []
            error = False
            candidates = sorted(job.candidates, key=lambda item: item.quality, reverse=True)
            for candidate in candidates[:self.config.max_candidates]:
                started = perf_counter()
                try:
                    path.write_bytes(candidate.image)
                    detections = self.engine.predict(path)
                    observations.extend(associate(
                        detections=detections, vehicle=candidate.track.bbox,
                        neighbors=candidate.neighbors, frame_number=candidate.frame,
                        offset=candidate.offset, quality=candidate.quality,
                        formats=self.formats, scale=candidate.scale,
                    ))
                except Exception as exc:
                    logger.warning("recognition_attempt_failed %s %s", job.crossing.event_uuid,
                                   type(exc).__name__)
                    error = True
                    break
                finally:
                    self.stats["ocr_calls"] += 1
                    self.stats["ocr_ms"] += (perf_counter() - started) * 1000
            best, reason = aggregate(observations, self.config.minimum, self.config.agreement)
            if error:
                reason = "ocr_failed"
            try:
                self._save(job, observations, best, reason)
            except Exception as exc:
                self.error = exc
                logger.error("could_not_save_crossing %s", job.crossing.event_uuid)

    def _save(self, job: Pending, observations: list[PlateObservation],
              best: PlateObservation | None, reason: str) -> None:
        crossing = job.crossing
        evidence_plate = best or max(
            (item for item in observations if item.association != "REJECTED"),
            key=lambda item: item.plate_confidence, default=None,
        )
        candidate = next((item for item in job.candidates if evidence_plate and item.frame == evidence_plate.frame_number),
                         max(job.candidates, key=lambda item: item.quality, default=None))
        snapshot = plate_crop = None
        if candidate:
            self.config.evidence.mkdir(parents=True, exist_ok=True)
            path = self.config.evidence / f"{crossing.event_uuid}.jpg"
            path.write_bytes(candidate.image)
            snapshot = path.name
            if evidence_plate:
                import numpy as np

                x, y = candidate.offset
                sx, sy = candidate.scale
                image = cv2.imdecode(np.frombuffer(candidate.image, dtype=np.uint8), cv2.IMREAD_COLOR)
                box = evidence_plate.bbox
                x1, x2 = max(0, round((box.x1 - x) / sx)), round((box.x2 - x) / sx)
                y1, y2 = max(0, round((box.y1 - y) / sy)), round((box.y2 - y) / sy)
                plate = image[y1:y2, x1:x2]
                path = self.config.evidence / f"{crossing.event_uuid}-plate.jpg"
                if plate.size and cv2.imwrite(str(path), plate):
                    plate_crop = path.name
        failed = reason in ("ocr_failed", "queue_full")
        payload = {
            **asdict(crossing), "event_uuid": str(crossing.event_uuid),
            "timestamp": crossing.timestamp.isoformat() if crossing.timestamp else None,
            "vehicle_type": candidate.track.class_name if candidate else None,
            "plate_number": best.normalized_text if best else None,
            "ocr_confidence": best.ocr_confidence if best else None,
            "plate_confidence": best.plate_confidence if best else None,
            "verification_status": "AUTO" if reason == "accepted" else "PENDING",
            "recognition_status": "RECOGNITION_FAILED" if failed else (
                "RECOGNITION_COMPLETED" if reason == "accepted" else "MANUAL_VERIFICATION_REQUIRED"
            ),
            "reason": reason, "snapshot_path": snapshot, "plate_crop_path": plate_crop,
            "observations": [asdict(item) for item in observations],
            "detections": [
                {"frame_number": item.frame, "bbox": asdict(item.track.bbox), "quality": item.quality}
                for item in job.candidates
            ],
        }
        path = self.config.outbox / f"{crossing.event_uuid}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, path)
        (self.checkpoints / path.name).unlink(missing_ok=True)
        with self._lock:
            self.stats["events"] += 1
            self.stats["event_pipeline_ms"] += (perf_counter() - job.started) * 1000
        logger.info("recognition_saved %s %s", crossing.event_uuid, reason)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.reset()
        finally:
            self.queues.put(None)
            self.thread.join()
            self.engine.close()
            self._scratch.cleanup()
        if self.error is not None:
            raise RuntimeError("Could not persist ANPR results") from self.error
