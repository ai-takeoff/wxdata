"""CLI: python -m pipeline.run --data site/data --work state/work [--models rrfs,gfs,rap]"""
import argparse
import logging
import sys

from . import process


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="published data directory")
    ap.add_argument("--work", required=True, help="working state directory")
    ap.add_argument("--models", default="", help="comma list (default: all)")
    ap.add_argument("--now", default="", help="override current UTC time (testing), e.g. 2026-11-06T15:00")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        stream=sys.stdout)
    models = [m for m in a.models.split(",") if m] or None
    now = None
    if a.now:
        from datetime import datetime, timezone
        now = datetime.fromisoformat(a.now).replace(tzinfo=timezone.utc)
    changed = process.main(a.data, a.work, models, now)
    # tell CI whether anything new was produced
    print(f"CHANGED={'true' if changed else 'false'}")


if __name__ == "__main__":
    main()
