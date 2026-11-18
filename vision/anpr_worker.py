"""FastALPR subprocess. This interpreter uses headless OpenCV."""

import argparse
import json
import statistics
import sys
from contextlib import redirect_stdout
from pathlib import Path

import cv2
import onnxruntime
from fast_alpr import ALPR
from fast_plate_ocr.inference import hub as ocr_hub
from open_image_models.detection.core import hub as detector_hub


def recognize(engine: ALPR, path: str) -> list[dict]:
    image = cv2.imread(path, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Unreadable candidate image")
    observations = []
    for plate in engine.predict(image):
        box = plate.detection.bounding_box
        read = plate.ocr
        confidence = read.confidence if read is not None else None
        if isinstance(confidence, list):
            confidence = statistics.mean(confidence[:len(read.text)]) if read.text else None
        observations.append({
            "bbox": [int(box.x1), int(box.y1), int(box.x2), int(box.y2)],
            "plate_confidence": float(plate.detection.confidence),
            "raw_text": read.text if read is not None else None,
            "ocr_confidence": confidence,
        })
    return observations


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--detector", default="yolo-v9-t-384-license-plate-end2end")
    parser.add_argument("--ocr", default="cct-xs-v2-global-model")
    parser.add_argument("--cache", type=Path, default=Path(".cache/ocr"))
    args = parser.parse_args()
    detector_hub.MODEL_CACHE_DIR = args.cache / "detector"
    ocr_hub.MODEL_CACHE_DIR = args.cache / "reader"
    options = onnxruntime.SessionOptions()
    options.intra_op_num_threads = 1
    with redirect_stdout(sys.stderr):
        engine = ALPR(
            detector_model=args.detector, ocr_model=args.ocr, ocr_device="cpu",
            detector_providers=["CPUExecutionProvider"], ocr_providers=["CPUExecutionProvider"],
            detector_sess_options=options, ocr_sess_options=options,
        )
    for line in sys.stdin:
        try:
            with redirect_stdout(sys.stderr):
                response = {"plates": recognize(engine, json.loads(line))}
        except (ValueError, RuntimeError, cv2.error):
            response = {"error": "Could not process the candidate image"}
        print(json.dumps(response), flush=True)


if __name__ == "__main__":
    main()
