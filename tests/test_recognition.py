import re
from dataclasses import replace

from vision.recognition import INDIAN_FORMATS, aggregate, associate
from vision.tracks import BoundingBox

FORMATS = tuple(map(re.compile, INDIAN_FORMATS))


def observation(text, frame, neighbors=()):
    result, = associate(
        detections=[{"bbox": (40, 50, 70, 60), "raw_text": text,
                     "plate_confidence": 0.95, "ocr_confidence": 0.90}],
        frame_number=frame, vehicle=BoundingBox(0, 0, 100, 100), neighbors=neighbors,
        offset=(0, 0), quality=10, formats=FORMATS,
    )
    return result


def test_normalization_and_bh_consensus():
    reads = [observation("22 BH 4590 a", 3), observation("22BH4590A", 4)]
    best, reason = aggregate(reads, 0.8, 2)
    assert reason == "accepted" and best.normalized_text == "22BH4590A"
    assert reads[0].raw_text == "22 BH 4590 a"


def test_same_frame_does_not_count_twice():
    item = observation("AP21AB1234", 3)
    assert aggregate([item, item], 0.8, 2)[1] == "insufficient_agreement"


def test_conflicting_readings_are_preserved_for_review():
    valid = [observation("AP21AB1234", 1), observation("AP21AB1234", 2)]
    alternative = observation("AP21AB1235", 3)
    assert aggregate(valid + [alternative], 0.8, 2)[1] == "conflicting_readings"


def test_spatial_ambiguity_is_never_accepted():
    item = observation("AP21AB1234", 3, (BoundingBox(40, 50, 100, 150),))
    assert item.association == "AMBIGUOUS"
    assert aggregate([item], 0.8, 1)[1] == "ambiguous_association"


def test_unrecognized_format_and_low_confidence_go_to_review():
    item = observation("3066MP4", 3)
    assert aggregate([item], 0.8, 1)[1] == "unrecognized_format"
    assert aggregate([replace(item, ocr_confidence=0.2)], 0.8, 1)[1] == "no_confident_plate"


def test_interrupted_crossing_is_recovered_from_disk(tmp_path):
    import sys
    from pathlib import Path
    from vision.anpr import AnprConfig, RecognitionPipeline

    outbox = tmp_path / "outbox"
    pending = outbox / ".pending"
    pending.mkdir(parents=True)
    original = '{"recognition_status":"RECOGNITION_FAILED","reason":"worker_interrupted"}'
    (pending / "id.json").write_text(original, encoding="utf-8")
    worker = RecognitionPipeline(AnprConfig(Path(sys.executable), outbox, tmp_path / "evidence"))
    worker.close()
    assert (outbox / "id.json").read_text(encoding="utf-8") == original
    assert not list(pending.iterdir())


def test_fastalpr_cpu_on_real_opencv_crop(tmp_path):
    import os
    from itertools import islice
    from pathlib import Path

    import cv2
    import pytest

    from vision.anpr import FastAlprProcess
    from vision.sources.file_source import FileVideoSource

    video = os.environ.get("ANPR_TEST_VIDEO")
    python = os.environ.get("ANPR_TEST_OCR_PYTHON")
    if not video or not python:
        pytest.skip("Set ANPR_TEST_VIDEO and ANPR_TEST_OCR_PYTHON for real FastALPR.")
    with FileVideoSource(Path(video)) as source:
        frame = next(islice(iter(source), 60, None))
    path = tmp_path / "candidate.png"
    assert cv2.imwrite(str(path), frame.image[790:1240, 120:620])
    process = FastAlprProcess(Path(python), "yolo-v9-t-384-license-plate-end2end",
                              "cct-xs-v2-global-model", 30, 0)
    try:
        observations = process.predict(path)
        assert any(item["raw_text"] == "3066MP4" and item["ocr_confidence"] > .8
                   for item in observations)
    finally:
        process.close()
