from vision.evaluate import evaluate


def test_complete_and_partial_ground_truth():
    box = dict(zip(("x1", "y1", "x2", "y2"), (0, 0, 100, 100)))
    label = {"frame_number": 1, "object_id": "car", "bbox": [0, 0, 100, 100]}
    truth = {
        "source": "synthetic", "crossings_complete": True,
        "vehicles": [{**label, "vehicle_type": "car"}],
        "plates": [{**label, "plate_number": "AP21AB1234"}],
        "crossings": [{**label, "direction": "IN", "plate_number": "AP21AB1234"}],
    }
    tracking = [{"number": 1, "vehicles": [{"bbox": box, "class_name": "car", "track_id": 2}]}]
    event = {"direction": "IN", "frame_number": 1, "track_id": 2,
             "plate_number": "AP21AB1235", "verification_status": "PENDING",
             "observations": [{"frame_number": 1, "bbox": box, "association": "MATCHED"}]}
    results = evaluate(truth, tracking, [event, {**event, "frame_number": 500}])
    assert results["vehicle_detection_recall"]["rate"] == 1
    assert results["crossing_precision"]["rate"] == 0.5
    assert results["plate_association_accuracy_on_detected_plates"]["rate"] == 1
    assert results["exact_plate_accuracy"]["rate"] == 0
    assert results["character_accuracy"]["rate"] == 0.9
    truth["crossings_complete"] = False
    assert evaluate(truth, tracking, [event])["crossing_precision"] is None
