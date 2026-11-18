"""Emit one bounded, read-only UnifiedCapture event window as JSON.

Designed to run with the existing ingest container's UID 10001 via
"docker compose exec -T ingest python - ... < nav_read_window.py".
It has no dependencies on the host checkout and performs no writes.
Run with a concrete after-rowid supplied by the unprivileged cursor command.
"""
import argparse
import json
import sqlite3
from pathlib import Path

MAX_EVENTS = 2000
SCHEMA_VERSION = 1


def emit_window(db_path, after_rowid, max_events):
    if type(after_rowid) is not int or after_rowid < 0:
        raise ValueError("after_rowid must be nonnegative")
    if type(max_events) is not int or not 1 <= max_events <= MAX_EVENTS:
        raise ValueError("max_events must be between 1 and 2000")
    path = Path(db_path).resolve()
    if not path.is_file():
        raise ValueError("Capture SQLite is missing")
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)
    try:
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA busy_timeout=2000")
        tables = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('events','positions')"
        )}
        if tables != {"events", "positions"}:
            raise ValueError("Not a UnifiedCapture event database")
        # Bounded primary table-rowid scan, no GROUP BY or historical full scan.
        rows = db.execute("""
            SELECT e.rowid,e.session,e.sequence,e.event_type,e.zone,
                   e.observed_at,p.x,p.y,p.z,p.reason
            FROM events AS e LEFT JOIN positions AS p
                ON p.session=e.session AND p.sequence=e.sequence
            WHERE e.rowid > ? ORDER BY e.rowid LIMIT ?
        """, (after_rowid, max_events)).fetchall()
    finally:
        db.close()
    return {
        "source_schema": "UnifiedCapture/events",
        "stream_version": SCHEMA_VERSION,
        "after_rowid": after_rowid,
        "last_rowid": rows[-1][0] if rows else after_rowid,
        "rows": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--after-rowid", type=int, required=True)
    parser.add_argument("--max-events", type=int, default=200)
    args = parser.parse_args()
    print(json.dumps(emit_window(args.db, args.after_rowid,
                                 args.max_events), allow_nan=False,
                     separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
