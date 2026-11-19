"""Fetch and verify the public files used for integration and traffic replay."""

import argparse
import hashlib
import os
import tempfile
from pathlib import Path
from urllib.request import urlopen

RELEASES = "https://github.com/ultralytics/assets/releases/download"
ASSETS = (
    (
        "videos/anpr-demo-video.mp4",
        f"{RELEASES}/v0.0.0/anpr-demo-video.mp4",
        "82d3a2f8a8f02f86906aaed6d097346a66cc924b0ffb591a76f0e7a5bfb4f853",
    ),
    (
        "models/yolov8n.pt",
        f"{RELEASES}/v8.3.0/yolov8n.pt",
        "f59b3d833e2ff32e194b5bb8e08d211dc7c5bdf144b90d2c8412c47ccfc83b36",
    ),
)
INTEL = (
    "https://raw.githubusercontent.com/intel-iot-devkit/sample-videos/"
    "57978890822836f2b4743852f04f62fc511757e4"
)
TRAFFIC_ASSETS = (
    (
        "videos/intel-car-detection.mp4",
        f"{INTEL}/car-detection.mp4",
        "d31e0ebf194cc16e50eea784e7c3b0b1bb7976a4fb9b3fec26cb92a6704cca34",
    ),
    (
        "videos/intel-person-bicycle-car-detection.mp4",
        f"{INTEL}/person-bicycle-car-detection.mp4",
        "452b11b7e0efbd019f1d9570d0c790e90416ad4ad29eec6003872d08443140ef",
    ),
)


def fetch(path: Path, url: str, expected: str) -> None:
    if path.is_file():
        with path.open("rb") as existing:
            actual = hashlib.file_digest(existing, "sha256").hexdigest()
        if actual != expected:
            raise ValueError(f"{path} has a different SHA-256. Move it before downloading the fixture.")
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with urlopen(url, timeout=60) as response, tempfile.NamedTemporaryFile(
            dir=path.parent, delete=False,
        ) as target:
            temporary = Path(target.name)
            digest = hashlib.sha256()
            while chunk := response.read(1024 * 1024):
                target.write(chunk)
                digest.update(chunk)
        if digest.hexdigest() != expected:
            raise ValueError(f"SHA-256 mismatch for {path}.")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--traffic", action="store_true", help="Also fetch the CC BY 4.0 Intel videos")
    args = parser.parse_args()
    assets = ASSETS + TRAFFIC_ASSETS if args.traffic else ASSETS
    for name, url, expected in assets:
        path = args.data_dir / name
        try:
            fetch(path, url, expected)
        except (OSError, ValueError) as error:
            parser.exit(1, f"{error}\n")
        print(f"Verified {path}")


if __name__ == "__main__":
    main()
