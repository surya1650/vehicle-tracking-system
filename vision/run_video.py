"""Inspect, play or annotate a local video recording."""

import argparse
import json
import logging
import math
import os
import re
import signal
import sys
from contextlib import ExitStack, redirect_stdout
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from time import perf_counter, process_time
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import cv2
from dotenv import dotenv_values

from vision.live_preview import LivePreview
from vision.logging_config import configure_logging
from vision.playback import PlaybackWindow
from vision.sources.base_source import Frame, VideoMetadata
from vision.sources.file_source import FileVideoSource, VideoSourceError
from vision.tripwire import Tripwire, TripwireConfig

logger = logging.getLogger(__name__)


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("Expected a positive integer.")
    return number


def positive_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("Expected a finite positive number.")
    return number


def probability(value: str) -> float:
    number = float(value)
    if not 0 < number <= 1:
        raise argparse.ArgumentTypeError("Expected a value greater than 0 and at most 1.")
    return number


def recording_start(value: str) -> datetime:
    start = datetime.fromisoformat(value)
    if start.utcoffset() is None:
        raise argparse.ArgumentTypeError("Include the recording's UTC offset.")
    return start


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    local_env = dotenv_values(".env")

    def setting(name: str, fallback: str | None = None) -> str | None:
        return os.environ.get(name, local_env.get(name, fallback))

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path, nargs="?", help="Path to a local MP4")
    parser.add_argument("--rtsp-source", help="Environment variable that holds an RTSP URL")
    parser.add_argument("--rtsp-timeout-ms", type=positive_int, default=setting("RTSP_TIMEOUT_MS", "4000"))
    parser.add_argument("--rtsp-buffer", type=positive_int, default=setting("RTSP_BUFFER", "4"))
    parser.add_argument("--show", action="store_true", help="Open a playback window")
    parser.add_argument(
        "--speed", type=positive_float, default=setting("VIDEO_PLAYBACK_SPEED", "1.0"),
        help="Playback multiplier for the window; 1 is the recording speed",
    )
    parser.add_argument("--start-time", type=recording_start, default=setting("VIDEO_START_TIME"))
    parser.add_argument(
        "--max-frames", type=positive_int, help="Limit successfully decoded frames",
    )
    parser.add_argument("--report", type=Path, help="Also save the JSON diagnostics to this file")
    parser.add_argument("--output", type=Path, help="Write the processed frames to an MP4")
    parser.add_argument(
        "--log-level", type=str.upper, default=setting("LOG_LEVEL", "INFO"),
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
    )

    parser.add_argument("--live-camera", help="Publish annotated JPEG previews for this camera ID")
    parser.add_argument("--live-dir", type=Path, default=setting("ANPR_LIVE", "data/live"))

    tracking = parser.add_argument_group("vehicle tracking")
    tracking.add_argument("--track", action="store_true", help="Run YOLOv8 and ByteTrack")
    tracking.add_argument(
        "--weights", type=Path, default=setting("YOLO_WEIGHTS", "data/models/yolov8n.pt"),
    )
    tracking.add_argument("--device", default=setting("YOLO_DEVICE", "cpu"))
    tracking.add_argument(
        "--image-size", type=positive_int, default=setting("YOLO_IMAGE_SIZE", "640"),
    )
    tracking.add_argument(
        "--confidence", type=probability, default=setting("YOLO_CONFIDENCE", "0.1"),
    )
    tracking.add_argument("--iou", type=probability, default=setting("YOLO_IOU", "0.7"))
    tracking.add_argument(
        "--inference-interval", type=positive_int, default=setting("YOLO_INFERENCE_INTERVAL", "1"),
        help="Run detection/tracking every N source frames (default: 1)",
    )
    tracking.add_argument(
        "--tracker-config",
        type=Path, default=setting("BYTETRACK_CONFIG", "config/bytetrack.yaml"),
    )
    tracking.add_argument(
        "--history-size", type=positive_int, default=setting("TRACK_HISTORY_SIZE", "30"),
    )
    tracking.add_argument(
        "--history-max-misses",
        type=positive_int, default=setting("TRACK_HISTORY_MAX_MISSES", "30"),
    )
    tracking.add_argument(
        "--tracks-jsonl", type=Path, help="Write per-frame tracked boxes and IDs",
    )

    wire = parser.add_argument_group("tripwire")
    wire.add_argument("--tripwire", default=setting("TRIPWIRE_POINTS"), help="x1,y1,x2,y2")
    wire.add_argument("--tripwire-mode", default=setting("TRIPWIRE_MODE", "AUTO"),
                      choices=("AUTO", "ENTRY", "EXIT"))
    wire.add_argument("--entry-side", default=setting("TRIPWIRE_ENTRY_SIDE", "positive"),
                      choices=("positive", "negative"))
    wire.add_argument("--wire-deadband", type=float, default=setting("TRIPWIRE_DEADBAND", "4"))
    wire.add_argument("--wire-min-movement", type=float, default=setting("TRIPWIRE_MIN_MOVEMENT", "12"))
    wire.add_argument("--wire-confirmation", type=positive_int, default=setting("TRIPWIRE_CONFIRMATION", "2"))
    wire.add_argument("--wire-cooldown", type=float, default=setting("TRIPWIRE_COOLDOWN", "1"))
    wire.add_argument("--wire-max-gap", type=positive_float, default=setting("TRIPWIRE_MAX_GAP", "1"))
    wire.add_argument("--recording-id", type=UUID, default=setting("RECORDING_ID"))
    wire.add_argument("--crossings-jsonl", type=Path)

    anpr = parser.add_argument_group("plate recognition")
    anpr.add_argument("--anpr", action="store_true")
    default_python = ".venv-ocr/Scripts/python.exe" if os.name == "nt" else ".venv-ocr/bin/python"
    anpr.add_argument("--ocr-python", type=Path, default=setting("OCR_PYTHON", default_python))
    anpr.add_argument("--anpr-outbox", type=Path, default=setting("ANPR_OUTBOX", "data/outbox"))
    anpr.add_argument("--anpr-evidence", type=Path, default=setting("ANPR_EVIDENCE", "data/snapshots"))
    anpr.add_argument("--ocr-detector", default=setting("OCR_DETECTOR", "yolo-v9-t-384-license-plate-end2end"))
    anpr.add_argument("--ocr-model", default=setting("OCR_MODEL", "cct-xs-v2-global-model"))
    anpr.add_argument("--ocr-pre", type=positive_int, default=setting("OCR_PRE", "5"))
    anpr.add_argument("--ocr-post", type=positive_int, default=setting("OCR_POST", "5"))
    anpr.add_argument("--ocr-max-candidates", type=positive_int, default=setting("OCR_MAX_CANDIDATES", "6"))
    anpr.add_argument("--ocr-queue-size", type=positive_int, default=setting("OCR_QUEUE_SIZE", "8"))
    anpr.add_argument("--ocr-min-confidence", type=probability, default=setting("OCR_MIN_CONFIDENCE", "0.65"))
    anpr.add_argument("--ocr-agreement", type=positive_int, default=setting("OCR_AGREEMENT", "2"))
    anpr.add_argument("--ocr-timeout", type=positive_float, default=setting("OCR_TIMEOUT", "30"))
    anpr.add_argument("--ocr-retries", type=int, default=setting("OCR_RETRIES", "1"))
    anpr.add_argument("--plate-format", action="append", help="Accepted regex, repeat to supply several")
    anpr.set_defaults(plate_formats_env=setting("PLATE_FORMATS"))

    args = parser.parse_args(argv)
    if bool(args.video) == bool(args.rtsp_source):
        parser.error("Choose a local video or --rtsp-source.")
    if args.rtsp_source and not re.fullmatch(r"CAMERA_SOURCE_[A-Z0-9_]{1,49}", args.rtsp_source):
        parser.error("Supply the camera source variable name, such as CAMERA_SOURCE_MAIN.")
    if args.rtsp_source and args.output:
        parser.error("MP4 export is supported for file playback.")
    args.source_value = setting(args.rtsp_source) if args.rtsp_source else None
    if args.rtsp_source and not args.source_value:
        parser.error("The requested camera source variable is unset.")
    if args.log_level not in ("DEBUG", "INFO", "WARNING", "ERROR"):
        parser.error("LOG_LEVEL must be DEBUG, INFO, WARNING or ERROR.")
    if args.live_camera and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", args.live_camera):
        parser.error("Invalid camera ID.")
    if args.tracks_jsonl and not args.track:
        parser.error("--tracks-jsonl requires --track.")
    if args.output and args.output.suffix.lower() != ".mp4":
        parser.error("--output must be an MP4 filename.")
    if (args.tripwire and not args.track) or (args.crossings_jsonl and not args.tripwire):
        parser.error("A tripwire requires --track; --crossings-jsonl also requires a tripwire.")
    if args.tripwire_mode not in ("AUTO", "ENTRY", "EXIT") or args.entry_side not in ("positive", "negative"):
        parser.error("Invalid tripwire mode or entry side.")
    if args.anpr and not args.tripwire:
        parser.error("--anpr requires a tripwire.")
    if args.ocr_retries < 0 or args.ocr_agreement > args.ocr_max_candidates:
        parser.error("Invalid OCR retry or agreement count.")
    paths = [args.video, args.output, args.report, args.tracks_jsonl, args.crossings_jsonl]
    if args.track:
        paths.extend([args.weights, args.tracker_config])
    destinations = [path.resolve() for path in paths if path is not None]
    if len(destinations) != len(set(destinations)):
        parser.error("Use distinct paths for inputs and outputs.")
    return args


def make_writer(path: Path, metadata: VideoMetadata, interval: int) -> cv2.VideoWriter:
    if metadata.width % 2 or metadata.height % 2:
        raise VideoSourceError("MP4 export requires even frame dimensions.")
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"mp4v"), metadata.fps / interval,
        (metadata.width, metadata.height),
    )
    if not writer.isOpened():
        writer.release()
        raise VideoSourceError(f"OpenCV could not create the output video: {path}")
    return writer


def frame_summary(frame: Frame) -> dict[str, int | float | str | None]:
    return {
        "number": frame.number,
        "elapsed_seconds": frame.elapsed_seconds,
        "timestamp": frame.timestamp.isoformat() if frame.timestamp is not None else None,
        **({"stream_epoch": frame.stream_epoch, "source_elapsed_seconds": frame.source_elapsed_seconds}
           if frame.stream_epoch else {}),
    }


def peak_rss_mib() -> float | None:
    if sys.platform == "win32":
        try:
            import psutil
        except ImportError:
            return None
        return round(psutil.Process().memory_info().peak_wset / 2 ** 20, 2)
    try:
        import resource
    except ImportError:
        return None
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(peak / (2 ** 20 if sys.platform == "darwin" else 2 ** 10), 2)


def run(args: argparse.Namespace) -> dict[str, object]:
    if args.show and sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        raise VideoSourceError(
            "Video playback needs a desktop display. Omit --show for headless mode."
        )

    with ExitStack() as stack:
        if args.rtsp_source:
            from vision.sources.rtsp_source import RTSPVideoSource

            os.environ.setdefault("OPENCV_FFMPEG_LOGLEVEL", "0")
            cv2.setLogLevel(0)
            source = stack.enter_context(RTSPVideoSource(
                args.source_value, timeout_ms=args.rtsp_timeout_ms, buffer_size=args.rtsp_buffer,
            ))
        else:
            source = stack.enter_context(FileVideoSource(args.video, args.start_time))
        tracker = None
        if args.track:
            from vision.tracking import VehicleTracker, draw_tracks

            tracker = VehicleTracker(
                weights=args.weights, tracker_config=args.tracker_config,
                device=args.device, image_size=args.image_size, confidence=args.confidence,
                iou=args.iou, history_size=args.history_size, max_misses=args.history_max_misses,
            )

        wire = None
        crossing_file = None
        crossing_count = 0
        if args.tripwire:
            config = TripwireConfig.from_coordinates(
                args.tripwire, mode=args.tripwire_mode,
                positive_is_entry=args.entry_side == "positive", deadband=args.wire_deadband,
                min_movement=args.wire_min_movement, confirmation=args.wire_confirmation,
                cooldown=args.wire_cooldown, max_gap=args.wire_max_gap,
            )
            if args.video is None:
                identity = uuid4()
            else:
                fingerprint = args.video.stat()
                identity = uuid5(NAMESPACE_URL, (
                    f"{args.video.resolve()}:{fingerprint.st_size}:{fingerprint.st_mtime_ns}:"
                    f"{args.live_camera}:{args.start_time}:{config}"
                ))
            wire = Tripwire(config, args.recording_id or identity)
            if args.crossings_jsonl:
                args.crossings_jsonl.parent.mkdir(parents=True, exist_ok=True)
                crossing_file = stack.enter_context(args.crossings_jsonl.open("w", encoding="utf-8"))

        recognition = None
        if args.anpr:
            from vision.anpr import AnprConfig, RecognitionPipeline
            from vision.recognition import INDIAN_FORMATS

            formats = args.plate_format
            if formats is None and args.plate_formats_env:
                formats = json.loads(args.plate_formats_env)
                if not isinstance(formats, list) or not all(isinstance(item, str) for item in formats):
                    raise ValueError("PLATE_FORMATS must be a JSON array of regex strings.")
            recognition = RecognitionPipeline(AnprConfig(
                args.ocr_python, args.anpr_outbox, args.anpr_evidence,
                detector=args.ocr_detector, ocr=args.ocr_model, pre=args.ocr_pre, post=args.ocr_post,
                max_candidates=args.ocr_max_candidates, queue_size=args.ocr_queue_size,
                minimum=args.ocr_min_confidence, agreement=args.ocr_agreement,
                timeout=args.ocr_timeout, retries=args.ocr_retries,
                formats=tuple(formats) if formats else INDIAN_FORMATS,
            ))
            stack.callback(recognition.close)

        live = LivePreview(args.live_dir, args.live_camera) if args.live_camera else None
        window = None
        if args.show:
            window = stack.enter_context(PlaybackWindow("Vehicle Tracking", args.speed))
        writer = None
        interval = args.inference_interval if tracker is not None else 1
        if args.output:
            writer = make_writer(args.output, source.metadata, interval)
            stack.callback(writer.release)
        telemetry = None
        if args.tracks_jsonl:
            args.tracks_jsonl.parent.mkdir(parents=True, exist_ok=True)
            telemetry = stack.enter_context(args.tracks_jsonl.open("w", encoding="utf-8"))

        logger.info("video_opened")
        first_frame = last_frame = None
        decoded = displayed = written = 0
        next_inference = 0
        previous_epoch = None
        stop_reason = "end_of_file"
        started = perf_counter()
        cpu_started = process_time()
        for frame in source:
            if previous_epoch is not None and previous_epoch != frame.stream_epoch:
                if tracker is not None:
                    tracker.reset()
                if wire is not None:
                    wire.reset()
                if recognition is not None:
                    recognition.reset()
                next_inference = frame.number
            previous_epoch = frame.stream_epoch
            if first_frame is None:
                first_frame = frame_summary(frame)
            last_frame = frame_summary(frame)
            decoded += 1

            if tracker is None or frame.number >= next_inference:
                publish = live is not None and live.ready()
                active_tracks = 0
                if tracker is not None:
                    next_inference = frame.number + interval
                    vehicles = tracker.process(frame)
                    active_tracks = sum(vehicle.track_id is not None for vehicle in vehicles)
                    if wire is not None:
                        crossings = wire.process(frame, vehicles)
                        if recognition is not None:
                            recognition.process(frame, vehicles, crossings)
                        for crossing in crossings:
                            crossing_count += 1
                            if crossing_file is not None:
                                event = asdict(crossing)
                                event["event_uuid"] = str(crossing.event_uuid)
                                event["timestamp"] = crossing.timestamp.isoformat() if crossing.timestamp else None
                                crossing_file.write(json.dumps(event) + "\n")
                                crossing_file.flush()
                            logger.info("tripwire_crossing %s %s", crossing.event_uuid, crossing.direction,
                                        extra={"frame_number": crossing.frame_number})
                    if telemetry is not None:
                        row = {**last_frame, "vehicles": [asdict(vehicle) for vehicle in vehicles]}
                        telemetry.write(json.dumps(row) + "\n")
                    if writer is not None or window is not None or publish:
                        frame = replace(frame, image=frame.image.copy())
                        draw_tracks(frame.image, vehicles)
                        if wire is not None:
                            cv2.line(frame.image, tuple(map(round, wire.config.start)),
                                     tuple(map(round, wire.config.end)), (255, 180, 0), 3)
                            cv2.putText(frame.image, f"Crossings: {crossing_count}", (16, 48),
                                        cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 180, 0), 2)
                if publish:
                    live.publish(frame, active_tracks, crossing_count)
                if writer is not None:
                    if frame.image.shape[:2] != (source.metadata.height, source.metadata.width):
                        raise VideoSourceError("Frame dimensions changed during MP4 export.")
                    writer.write(frame.image)
                    written += 1
                if window is not None:
                    if not window.show(frame):
                        stop_reason = "window_closed"
                        break
                    displayed += 1

            if args.max_frames is not None and decoded >= args.max_frames:
                stop_reason = "frame_limit"
                break

        if decoded == 0:
            raise VideoSourceError("The recording contained no decodable frames.")
        wall_seconds = perf_counter() - started
        cpu_seconds = process_time() - cpu_started
        report = {
            "video": str(args.video) if args.video else None,
            "source": "rtsp" if args.rtsp_source else "file",
            "metadata": asdict(source.metadata),
            "first_frame": first_frame,
            "last_frame": last_frame,
            "decoded_frames": decoded,
            "dropped_frames": source.dropped_frames,
            "displayed_frames": displayed,
            "written_frames": written,
            "stop_reason": stop_reason,
            "wall_seconds": round(wall_seconds, 3),
            "processing_fps": round(decoded / wall_seconds, 2),
            "cpu_percent_one_core": round(100 * cpu_seconds / wall_seconds, 1),
            "peak_vision_rss_mib": peak_rss_mib(),
        }
        if tracker is not None:
            report["tracking"] = {"inference_interval": interval, **tracker.diagnostics()}
        if wire is not None:
            report["tripwire"] = {"crossings": crossing_count, "recording_id": str(wire.recording_id)}
        if recognition is not None:
            recognition.close()
            report["recognition"] = recognition.stats.copy()
            report["wall_with_recognition_drain_seconds"] = round(perf_counter() - started, 3)
        return report


def interrupted(_signal, _frame):
    raise KeyboardInterrupt


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    configure_logging(args.log_level)
    try:
        # Keep stdout machine-readable even when a third-party model prints diagnostics.
        with redirect_stdout(sys.stderr):
            report = json.dumps(run(args), indent=2)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(report + "\n", encoding="utf-8")
        print(report)
    except KeyboardInterrupt:
        logger.info("video_interrupted")
        return 130
    except (ImportError, OSError, RuntimeError, ValueError, cv2.error) as exc:
        logger.error("%s", exc)
        return 1
    except Exception:
        logger.exception("video_processing_failed")
        return 1
    return 0


if __name__ == "__main__":
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, interrupted)
    raise SystemExit(main())
