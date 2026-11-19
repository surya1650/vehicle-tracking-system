"""Fit a camera-specific YOLOv8n candidate on a small public calibration set."""

import argparse
import hashlib
import json
import os
from pathlib import Path

import cv2
import yaml

from vision.sources.file_source import FileVideoSource

LABELS = Path(__file__).with_name("overhead-training-labels.json")


def export_dataset(video: Path, output: Path, names: dict[int, str]) -> Path:
    data = json.loads(LABELS.read_text(encoding="utf-8"))
    with video.open("rb") as recording:
        if hashlib.file_digest(recording, "sha256").hexdigest() != data["sha256"]:
            raise ValueError("Expected the pinned Intel car-detection.mp4 recording.")
    frames = {
        int(frame): (split, box)
        for split in ("train", "val") for frame, box in data[split].items()
    }
    if len(frames) != len(data["train"]) + len(data["val"]):
        raise ValueError("The training and internal validation frame sets must be disjoint.")
    for split in ("train", "val"):
        (output / "images" / split).mkdir(parents=True)
        (output / "labels" / split).mkdir(parents=True)

    with FileVideoSource(video) as source:
        width, height = source.metadata.width, source.metadata.height
        for frame in source:
            if frame.number not in frames:
                continue
            split, box = frames.pop(frame.number)
            stem = f"frame-{frame.number:04}"
            destination = output / "images" / split / f"{stem}.png"
            if not cv2.imwrite(str(destination), frame.image):
                raise OSError(f"Could not export {destination}.")
            lines = ""
            if box is not None:
                x1, y1, x2, y2 = box
                if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
                    raise ValueError(f"Invalid training box at frame {frame.number}.")
                values = (
                    (x1 + x2) / (2 * width), (y1 + y2) / (2 * height),
                    (x2 - x1) / width, (y2 - y1) / height,
                )
                lines = "2 " + " ".join(f"{value:.6f}" for value in values) + "\n"
            (output / "labels" / split / f"{stem}.txt").write_text(lines, encoding="utf-8")
            if not frames:
                break
    if frames:
        raise ValueError(f"The recording is missing labelled frames {sorted(frames)}.")
    config = output / "dataset.yaml"
    config.write_text(yaml.safe_dump({
        "path": str(output.resolve()), "train": "images/train", "val": "images/val", "names": names,
    }), encoding="utf-8")
    return config


def train(video: Path, weights: Path, output: Path, epochs: int) -> None:
    cache = Path(".cache/ultralytics").resolve()
    cache.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("YOLO_CONFIG_DIR", str(cache))
    os.environ.setdefault("YOLO_AUTOINSTALL", "false")
    from ultralytics import YOLO

    if output.exists():
        raise FileExistsError(f"Choose a new output directory instead of {output}.")
    if not weights.is_file():
        raise FileNotFoundError(f"Missing pretrained detector at {weights}.")
    detector = YOLO(str(weights))
    if detector.task != "detect" or detector.names.get(2) != "car":
        raise ValueError("The calibration requires a detection model with the COCO car class ID.")
    config = export_dataset(video, output / "dataset", detector.names)
    detector.train(
        data=str(config), project=str(output.resolve()), name="fit", device="cpu",
        imgsz=256, epochs=epochs, batch=4, nbs=4, workers=0, freeze=8, seed=7,
        optimizer="AdamW", lr0=0.001, lrf=0.2, warmup_epochs=0,
        mosaic=0, mixup=0, copy_paste=0, degrees=5, translate=0.04, scale=0.20,
        patience=0, plots=False,
    )
    checkpoints = output / "fit" / "weights"
    if not all((checkpoints / name).is_file() for name in ("best.pt", "last.pt")):
        raise RuntimeError("Training returned without both expected checkpoints.")
    print(f"Training checkpoints at {checkpoints}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, default=Path("data/videos/intel-car-detection.mp4"))
    parser.add_argument("--weights", type=Path, default=Path("data/models/yolov8n.pt"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--epochs", type=int, default=12)
    args = parser.parse_args()
    if args.epochs < 1:
        parser.error("--epochs must be positive.")
    train(args.video, args.weights, args.output, args.epochs)


if __name__ == "__main__":
    main()
