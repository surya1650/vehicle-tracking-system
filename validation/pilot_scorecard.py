"""Replay the pinned public clips and report a repeatable pilot baseline."""

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

from validation.fetch_assets import ASSETS, TRAFFIC_ASSETS, fetch
from vision.evaluate import score_files
from vision.recognition import INDIAN_FORMATS

CLIPS = (
    ("intel-car-detection", TRAFFIC_ASSETS[0], "0,216,767,216", "1"),
    ("intel-mixed-traffic", TRAFFIC_ASSETS[1], "0,260,767,260", "2"),
    ("public-portrait-complete", ASSETS[0], "0,1050,1079,1050", "3"),
)
TRACKER = Path("config/bytetrack.yaml")
OCR_DETECTOR = "yolo-v9-t-384-license-plate-end2end"
OCR_MODEL = "cct-xs-v2-global-model"


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def run_clip(output: Path, video: Path, labels: Path, weights: Path, ocr_python: Path,
             tripwire: str, interval: str, formats: tuple[str, ...]) -> dict:
    tracks, outbox, report = output / "tracks.jsonl", output / "outbox", output / "replay.json"
    command = [
        sys.executable, "-m", "vision.run_video", str(video),
        "--track", "--weights", str(weights), "--device", "cpu", "--image-size", "640",
        "--confidence", "0.1", "--iou", "0.7", "--no-agnostic-nms",
        "--inference-interval", interval, "--tracker-config", str(TRACKER),
        "--tripwire", tripwire, "--tripwire-mode", "AUTO", "--entry-side", "positive",
        "--wire-deadband", "4", "--wire-min-movement", "12", "--wire-confirmation", "2",
        "--wire-cooldown", "1", "--wire-max-gap", "1",
        "--anpr", "--ocr-python", str(ocr_python), "--ocr-detector", OCR_DETECTOR, "--ocr-model", OCR_MODEL,
        "--ocr-pre", "5", "--ocr-post", "5", "--ocr-max-candidates", "6", "--ocr-min-confidence", "0.65",
        "--ocr-agreement", "2", "--ocr-queue-size", "8", "--ocr-timeout", "30", "--ocr-retries", "1",
        "--tracks-jsonl", str(tracks), "--anpr-outbox", str(outbox),
        "--anpr-evidence", str(output / "evidence"), "--report", str(report),
    ]
    for pattern in formats:
        command += ["--plate-format", pattern]
    output.mkdir()
    with (output / "worker.log").open("wb") as log:
        subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT)
    return {
        "command": command, "labels_sha256": sha256(labels), "source_sha256": sha256(video),
        "scores": score_files(labels, tracks, outbox),
        "replay": json.loads(report.read_text(encoding="utf-8")),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New directory for this run")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    default_python = ".venv-ocr/Scripts/python.exe" if os.name == "nt" else ".venv-ocr/bin/python"
    parser.add_argument("--ocr-python", type=Path, default=Path(default_python))
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose an unused output directory so runs stay independent.")

    for name, url, digest in ASSETS + TRAFFIC_ASSETS:
        fetch(args.data_dir / name, url, digest)
    weights = args.data_dir / ASSETS[1][0]
    args.output.mkdir(parents=True)
    clips = {}
    for name, asset, tripwire, interval in CLIPS:
        formats = ("[0-9]{4}[A-Z]{2}[0-9]",) if asset == ASSETS[0] else INDIAN_FORMATS
        clips[name] = run_clip(args.output / name, args.data_dir / asset[0], Path("validation") / f"{name}.json",
                               weights, args.ocr_python, tripwire, interval, formats)
        match = clips[name]["scores"]["crossing_recall"]
        print(f"{name}: {match['matched']} of {match['total']} crossings", flush=True)
    scorecard = {
        "purpose": "public baseline",
        "platform": platform.platform(),
        "python": platform.python_version(),
        "tracker_sha256": sha256(TRACKER),
        "weights_sha256": sha256(weights),
        "clips": clips,
    }
    report = args.output / "scorecard.json"
    report.write_text(json.dumps(scorecard, indent=2) + "\n", encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
