"""Fetch and verify the two public files used by the integration tests."""

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
    args = parser.parse_args()
    for name, url, expected in ASSETS:
        path = args.data_dir / name
        try:
            fetch(path, url, expected)
        except (OSError, ValueError) as error:
            parser.exit(1, f"{error}\n")
        print(f"Verified {path}")


if __name__ == "__main__":
    main()
