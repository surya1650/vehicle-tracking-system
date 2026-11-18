"""Remove expired evidence images while preserving the movement and audit ledgers."""

import argparse
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, select

from backend.database import make_sessions
from backend.models import Snapshot


def main() -> None:
    sessions = make_sessions()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=int(os.environ.get("ANPR_IMAGE_RETENTION_DAYS", "30")))
    parser.add_argument("--apply", action="store_true", help="Remove expired evidence")
    args = parser.parse_args()
    if args.days < 1:
        parser.error("Retention must be at least one day.")
    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
    root = Path(os.environ.get("ANPR_EVIDENCE", "data/snapshots")).resolve()
    with sessions() as db:
        items = list(db.scalars(select(Snapshot.id).where(Snapshot.created_at < cutoff)))
    if not args.apply:
        print(f"{len(items)} expired evidence records. Use --apply to remove them.")
        return
    if not root.is_dir():
        parser.error("The evidence directory must be mounted read/write for retention.")
    removed = 0
    for snapshot_id in items:
        with sessions.begin() as db:
            image = db.get(Snapshot, snapshot_id, with_for_update=True)
            if image is None or image.created_at >= cutoff:
                continue
            path = (root / image.path).resolve()
            if path.parent != root:
                raise ValueError("Invalid evidence path. No external files were removed.")
            references = db.scalar(select(func.count()).select_from(Snapshot).where(Snapshot.path == image.path))
            if references == 1:
                path.unlink(missing_ok=True)
            db.delete(image)
            removed += 1
    print(f"Removed {removed} expired evidence records.")


if __name__ == "__main__":
    main()
