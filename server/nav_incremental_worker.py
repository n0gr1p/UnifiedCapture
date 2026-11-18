"""NAV-10: bounded, checkpointed raw-capture -> candidate walk graph worker.

Reads the append-only HDD events table *only* via read-only SQLite URI.
Writes candidate geometry to the independently identified NVMe nav DB, and
stores a durable rowid cursor plus last per-session position in a separate
worker-state SQLite. Replaying an interrupted batch is safe because the
existing walk importer uses deterministic edge and evidence IDs.

This is candidate mining, NOT route approval or movement authorization.
No raw archive mutation, Docker control, sudo escalation or scheduling.
"""
import argparse
import collections
import fcntl
import json
import math
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

import derived_navigation_db
import walk_candidate_miner as miner

MAX_EVENTS = 2000
MAX_BATCHES = 25
ALLOWED_PREVIOUS = frozenset(("anchor", "new_anchor", "moving", "heartbeat"))
STATE_SQL = """
CREATE TABLE IF NOT EXISTS mining_cursor (
    slot INTEGER PRIMARY KEY CHECK (slot=1),
    raw_identity TEXT NOT NULL,
    nav_identity TEXT NOT NULL,
    last_event_rowid INTEGER NOT NULL CHECK (last_event_rowid >= 0)
);
CREATE TABLE IF NOT EXISTS mining_last_position (
    session TEXT PRIMARY KEY,
    zone INTEGER NOT NULL,
    sequence INTEGER NOT NULL,
    observed_at REAL NOT NULL,
    x REAL NOT NULL,
    y REAL NOT NULL,
    z REAL NOT NULL,
    reason TEXT NOT NULL
);
"""


def identity(path):
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise ValueError("Database missing: " + str(p))
    stat = p.stat()
    return p, json.dumps([str(p), stat.st_dev, stat.st_ino], separators=(",", ":"))


def validate_paths(raw_db, nav_db, state_db):
    raw, raw_id = identity(raw_db)
    nav, nav_id = identity(nav_db)
    state = Path(state_db).expanduser().resolve()
    if state == raw or state == nav or raw == nav:
        raise ValueError("Source, derived and worker-state paths must differ")
    # Prevent accidentally treating unrelated raw SQLite as a capture source.
    with sqlite3.connect(raw.as_uri() + "?mode=ro", uri=True, timeout=2) as db:
        db.execute("PRAGMA query_only=ON")
        tables = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('events','positions')"
        )}
        if tables != {"events", "positions"}:
            raise ValueError("Raw SQLite missing UnifiedCapture event/position tables")
    info = derived_navigation_db.inspect(nav)
    if info["release_status"] != "not_built":
        raise ValueError("Cannot mine into a staged/published navigation release")
    return raw, raw_id, nav, nav_id, state


def load_state(path, raw_id, nav_id):
    if not path.exists():
        return 0, {}
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2) as db:
        db.execute("PRAGMA query_only=ON")
        cursor = db.execute(
            "SELECT raw_identity,nav_identity,last_event_rowid "
            "FROM mining_cursor WHERE slot=1"
        ).fetchone()
        if cursor is None:
            raise ValueError("Worker state has no committed cursor")
        if cursor[0] != raw_id or cursor[1] != nav_id:
            raise ValueError("Worker source/derived identity changed; refuse silent skip")
        last = {}
        for row in db.execute(
            "SELECT session,zone,sequence,observed_at,x,y,z,reason "
            "FROM mining_last_position"
        ):
            session, zone, sequence, at, x, y, z, reason = row
            last[session] = (zone, sequence, at, x, y, z, reason)
        return cursor[2], last


def store_state(path, raw_id, nav_id, cursor, last):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path, timeout=10) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=10000")
        db.executescript(STATE_SQL)
        # Recheck inside the writer transaction. The file lock serializes
        # cooperating workers; this check also prevents accidental resets.
        saved = db.execute(
            "SELECT raw_identity,nav_identity,last_event_rowid "
            "FROM mining_cursor WHERE slot=1"
        ).fetchone()
        if saved and (saved[0] != raw_id or saved[1] != nav_id
                      or saved[2] > cursor):
            raise ValueError("Cursor regression or source identity mismatch")
        db.execute(
            "INSERT INTO mining_cursor VALUES (1,?,?,?) "
            "ON CONFLICT(slot) DO UPDATE SET "
            "raw_identity=excluded.raw_identity,"
            "nav_identity=excluded.nav_identity,"
            "last_event_rowid=excluded.last_event_rowid",
            (raw_id, nav_id, cursor),
        )
        # Fully replace the modest per-session anchor ledger. The durable
        # cursor and anchors commit together; no half-window resume state.
        db.execute("DELETE FROM mining_last_position")
        db.executemany(
            "INSERT INTO mining_last_position VALUES (?,?,?,?,?,?,?,?)",
            ((session, *values) for session, values in sorted(last.items())),
        )


@contextmanager
def single_worker(state_path, dry_run):
    if dry_run:
        yield
        return
    lock_path = Path(str(state_path) + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def raw_events(raw_path, after_rowid, limit):
    db = sqlite3.connect(raw_path.as_uri() + "?mode=ro", uri=True, timeout=2)
    try:
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA busy_timeout=2000")
        highest = db.execute("SELECT MAX(rowid) FROM events").fetchone()[0] or 0
        if highest < after_rowid:
            raise ValueError("Raw event rowid moved backwards; source may have been replaced")
        # This scans only the next bounded, indexed ROWID range.
        rows = db.execute("""
            SELECT e.rowid,e.session,e.sequence,e.event_type,e.zone,e.observed_at,
                   p.x,p.y,p.z,p.reason
            FROM events AS e
            LEFT JOIN positions AS p
              ON p.session=e.session AND p.sequence=e.sequence
            WHERE e.rowid > ?
            ORDER BY e.rowid LIMIT ?
        """, (after_rowid, limit)).fetchall()
        return rows
    finally:
        db.close()


def candidate_edge(previous, current):
    zone, seq, at, x, y, z, reason = current
    if previous is None or previous[0] != zone:
        return False, "new_zone_or_session"
    pzone, pseq, pat, px, py, pz, preason = previous
    if preason not in ALLOWED_PREVIOUS or reason != "moving":
        return False, "not_continuous_movement"
    dt = at - pat
    if not 0 < dt <= miner.MAX_GAP_SECONDS:
        return False, "time_gap"
    distance = math.dist((x, y, z), (px, py, pz))
    if not 0.35 <= distance <= miner.MAX_DISTANCE or distance / dt > miner.MAX_SPEED:
        return False, "distance_or_speed"
    if abs(z - pz) > miner.MAX_STEP_Z:
        return False, "vertical_gap"
    if seq <= pseq:
        return False, "non_increasing_sequence"
    return True, "observed_walk_candidate"


def process_rows(rows, starting_last):
    previous_by_session = dict(starting_last)
    docs = {}
    counters = collections.Counter()
    final_rowid = None
    for row in rows:
        rowid, session, seq, kind, zone, at, x, y, z, reason = row
        final_rowid = rowid
        counters["events"] += 1
        if not isinstance(session, str) or not 1 <= len(session) <= 128:
            raise ValueError("Unexpected source session identity")
        if kind in ("zone_change", "discontinuity"):
            previous_by_session.pop(session, None)
            counters["boundary_markers"] += 1
            continue
        if kind != "position":
            continue
        counters["positions"] += 1
        if (not isinstance(zone, int) or not 0 <= zone <= 65535
                or type(seq) is not int or seq < 1
                or type(at) not in (int, float) or not math.isfinite(at)
                or not miner.valid_xyz({"x": x, "y": y, "z": z})
                or not isinstance(reason, str)):
            previous_by_session.pop(session, None)
            counters["invalid_positions"] += 1
            continue
        point = (zone, seq, at, x, y, z, reason)
        previous = previous_by_session.get(session)
        valid, outcome = candidate_edge(previous, point)
        counters[outcome] += 1
        if valid:
            key = (zone, session)
            doc = docs.setdefault(key, {
                "format": miner.FORMAT_VERSION,
                "algorithm": miner.ALGORITHM,
                "zone": zone,
                "session": session,
                "edges": [],
            })
            doc["edges"].append({
                "from": {"sequence": previous[1], "x": previous[3],
                         "y": previous[4], "z": previous[5]},
                "to": {"sequence": seq, "x": x, "y": y, "z": z},
            })
        previous_by_session[session] = point
    return final_rowid, docs, previous_by_session, counters


def run_once(raw_db, nav_db, state_db, max_events=1000, dry_run=False):
    if type(max_events) is not int or not 1 <= max_events <= MAX_EVENTS:
        raise ValueError("max_events must be between 1 and 2000")
    raw, raw_id, nav, nav_id, state_path = validate_paths(raw_db, nav_db, state_db)
    with single_worker(state_path, dry_run):
        previous_rowid, anchors = load_state(state_path, raw_id, nav_id)
        rows = raw_events(raw, previous_rowid, max_events)
        if not rows:
            return {"processed_events": 0, "after_rowid": previous_rowid,
                    "last_rowid": previous_rowid, "dry_run": dry_run,
                    "documents": 0, "new_nodes": 0, "new_edges": 0, "new_evidence": 0}
        latest, docs, next_anchors, counters = process_rows(rows, anchors)
        stats = collections.Counter()
        if not dry_run:
            # Import first, then persist the rowid and anchor state. If import
            # succeeds but a crash occurs before the state commit, replay is
            # idempotent due to the existing importer stable IDs.
            for (_zone, _session), doc in sorted(docs.items()):
                result = miner.import_candidates(nav, doc)
                stats["documents"] += 1
                for key in ("new_nodes", "new_edges", "new_evidence"):
                    stats[key] += result[key]
            store_state(state_path, raw_id, nav_id, latest, next_anchors)
        return {
            "processed_events": counters["events"],
            "positions_seen": counters["positions"],
            "candidate_walk_edges": counters["observed_walk_candidate"],
            "boundaries_seen": counters["boundary_markers"],
            "invalid_positions": counters["invalid_positions"],
            "documents": len(docs),
            "after_rowid": previous_rowid,
            "last_rowid": latest,
            "dry_run": dry_run,
            "new_nodes": stats["new_nodes"],
            "new_edges": stats["new_edges"],
            "new_evidence": stats["new_evidence"],
        }


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--raw-db", required=True, help="HDD capture SQLite (read only)")
    p.add_argument("--nav-db", required=True, help="NVMe derived candidate SQLite")
    p.add_argument("--state-db", required=True, help="NVMe worker checkpoint SQLite")
    p.add_argument("--max-events", type=int, default=1000)
    p.add_argument("--max-batches", type=int, default=1)
    p.add_argument("--pause-ms", type=int, default=100)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    if not 1 <= args.max_batches <= MAX_BATCHES:
        p.error("--max-batches must be from 1 to 25")
    if not 0 <= args.pause_ms <= 30000:
        p.error("--pause-ms must be from 0 to 30000")
    if args.dry_run and args.max_batches > 1:
        p.error("dry-run supports one batch only")
    summaries = []
    for i in range(args.max_batches):
        result = run_once(args.raw_db, args.nav_db, args.state_db,
                          args.max_events, args.dry_run)
        summaries.append(result)
        print(json.dumps(result, separators=(",", ":"), allow_nan=False),
              flush=True)
        if result["processed_events"] < args.max_events or args.dry_run:
            break
        if i + 1 < args.max_batches and args.pause_ms:
            time.sleep(args.pause_ms / 1000)


if __name__ == "__main__":
    main()
