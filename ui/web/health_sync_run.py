"""Result 2 bridge runner (Mac side).

Ingest the iCloud inbox into the canonical store, then write the iOS outbox
snapshot. One-shot by default; ``--watch`` loops on an interval. Mirrors how the
dashboard is built (a script under ui/web), so no package CLI changes needed.

    python ui/web/health_sync_run.py            # one pass against the iCloud bridge
    python ui/web/health_sync_run.py --watch    # keep syncing
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "ui" / "web"))

from openhealth.connectors import health_sync_bridge as bridge  # noqa: E402
import build_outbox  # noqa: E402


def _icloud_base() -> Path:
    return Path("~/Library/Mobile Documents/iCloud~org~openhealth~app/Documents").expanduser()


def run_once(db: Path, inbox: Path, outbox: Path, state: Path) -> dict:
    result = bridge.ingest_inbox(db, inbox, state)
    result["outbox"] = str(build_outbox.write_ios_outbox(db, outbox))
    return result


def main() -> None:
    base = _icloud_base()
    parser = argparse.ArgumentParser(description="Ingest iCloud inbox + write iOS outbox.")
    parser.add_argument("--db", default=str(ROOT / "data" / "index" / "health_os.sqlite3"))
    parser.add_argument("--inbox", default=str(base / "inbox"))
    parser.add_argument("--outbox", default=str(base / "outbox"))
    parser.add_argument("--state", default=None)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=float, default=15.0)
    args = parser.parse_args()

    db = Path(args.db)
    inbox = Path(args.inbox)
    outbox = Path(args.outbox)
    state = Path(args.state) if args.state else db.parent / "health_sync_bridge_state.json"
    db.parent.mkdir(parents=True, exist_ok=True)

    if args.watch:
        print(f"watch: {inbox} -> {db}; outbox {outbox} every {args.interval}s")
        while True:
            try:
                print(run_once(db, inbox, outbox, state))
            except Exception as exc:  # noqa: BLE001
                print(f"[warn] {exc}")
            time.sleep(args.interval)
    else:
        print(run_once(db, inbox, outbox, state))


if __name__ == "__main__":
    main()
