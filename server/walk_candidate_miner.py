"""Conservative, one-session walking candidate mining from historical telemetry.

Deliberately split into two privilege domains:
  extract: read-only historical HDD SQLite -> bounded JSON on stdout
  import:  JSON on stdin -> user-owned derived NVMe SQLite

This is NOT executable navigation: all edges have status='candidate'.
"""
import argparse
import bisect
import collections
import hashlib
import json
import math
import sqlite3
import sys
from pathlib import Path

import derived_navigation_db

ALGORITHM = "walk-candidate-v1"
FORMAT_VERSION = 1
MAX_POSITIONS = 2000
MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
MAX_GAP_SECONDS = 1.5
MAX_DISTANCE = 6.0
MAX_SPEED = 12.0
MAX_STEP_Z = 1.5
GRID = 0.5


def source_db(path):
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise ValueError("Source historical database does not exist: " + str(path))
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)
    db.execute("PRAGMA query_only=ON")
    db.execute("PRAGMA busy_timeout=2000")
    return db


def valid_xyz(row):
    return all(type(row[k]) in (float, int) and math.isfinite(row[k])
               and abs(row[k]) < 1_000_000 for k in ("x", "y", "z"))


def extract(raw_db_path, zone, session, max_positions=800, after_sequence=0):
    if not 0 <= zone <= 65535 or not 1 <= len(session) <= 128:
        raise ValueError("Invalid zone or recording session")
    if not 2 <= max_positions <= MAX_POSITIONS:
        raise ValueError("max_positions must be between 2 and 2000")
    if type(after_sequence) is not int or not 0 <= after_sequence <= 2147483647:
        raise ValueError("Invalid source sequence cursor")

    # One bounded session/range only. Never take a long-lived full-corpus
    # snapshot against the ingest writer on the HDD.
    db = source_db(raw_db_path)
    try:
        rows = db.execute("""
            SELECT p.sequence, e.observed_at, p.x, p.y, p.z, p.reason
            FROM positions AS p
            JOIN events AS e ON e.session=p.session AND e.sequence=p.sequence
            WHERE p.session=? AND p.zone=? AND p.sequence>?
            ORDER BY p.sequence LIMIT ?""",
            (session, zone, after_sequence, max_positions)).fetchall()
        markers = []
        if rows:
            markers = [r[0] for r in db.execute("""
                SELECT sequence FROM events
                WHERE session=? AND sequence>? AND sequence<=?
                  AND (event_type IN ('discontinuity', 'zone_change')
                       OR (event_type='position' AND
                           (zone IS NULL OR zone<>?)))
                ORDER BY sequence""",
                (session, rows[0][0], rows[-1][0], zone))]
    finally:
        db.close()

    reasons = collections.Counter()
    edges = []
    for a, b in zip(rows, rows[1:]):
        seq_a, at_a, ax, ay, az, reason_a = a
        seq_b, at_b, bx, by, bz, reason_b = b
        start = {"sequence": seq_a, "x": ax, "y": ay, "z": az}
        end = {"sequence": seq_b, "x": bx, "y": by, "z": bz}
        if not valid_xyz(start) or not valid_xyz(end):
            reasons["invalid_coordinates"] += 1
            continue
        if reason_a not in ("anchor", "new_anchor", "moving", "heartbeat") or reason_b != "moving":
            reasons["not_continuous_movement"] += 1
            continue
        if (bisect.bisect_right(markers, seq_a) < len(markers)
                and markers[bisect.bisect_right(markers, seq_a)] <= seq_b):
            reasons["explicit_boundary"] += 1
            continue
        if (type(at_a) not in (float, int)
                or type(at_b) not in (float, int)
                or not math.isfinite(at_a) or not math.isfinite(at_b)):
            reasons["invalid_time"] += 1
            continue
        dt = at_b - at_a
        if not 0 < dt <= MAX_GAP_SECONDS:
            reasons["time_gap"] += 1
            continue
        dist = math.dist((ax, ay, az), (bx, by, bz))
        if not 0.35 <= dist <= MAX_DISTANCE or dist / dt > MAX_SPEED:
            reasons["distance_or_speed"] += 1
            continue
        if abs(bz - az) > MAX_STEP_Z:
            reasons["vertical_gap"] += 1
            continue
        # Return only bounded real evidence, never fabricated intermediate steps.
        edges.append({"from": start, "to": end})
    return {
        "format": FORMAT_VERSION,
        "algorithm": ALGORITHM,
        "zone": zone,
        "session": session,
        "positions_scanned": len(rows),
        "source_last_sequence": rows[-1][0] if rows else None,
        "source_after_sequence": after_sequence,
        "rejected": dict(sorted(reasons.items())),
        "edges": edges,
    }


def stable_id(kind, *values):
    payload = json.dumps([kind, *values], separators=(",", ":"), ensure_ascii=True)
    value = hashlib.sha256(payload.encode("ascii")).digest()
    return int.from_bytes(value[:8], "big") & ((1 << 62) - 1)


def quantize(point):
    return tuple(round(float(point[k]) / GRID) * GRID for k in ("x", "y", "z"))


def import_candidates(nav_path, doc):
    if (not isinstance(doc, dict) or doc.get("format") != FORMAT_VERSION
            or doc.get("algorithm") != ALGORITHM
            or type(doc.get("zone")) is not int
            or not 0 <= doc["zone"] <= 65535
            or not isinstance(doc.get("session"), str)
            or not 1 <= len(doc["session"]) <= 128
            or not isinstance(doc.get("edges"), list)
            or len(doc["edges"]) > MAX_POSITIONS):
        raise ValueError("Unsupported or malformed walking candidate document")

    # Verify derived DB identity *before* opening it for writes.
    info = derived_navigation_db.inspect(nav_path)
    if info["release_status"] != "not_built":
        raise ValueError("Refusing to mutate a derived database with a release")
    if not doc["edges"]:
        return {"zone": doc["zone"], "session": doc["session"], "candidate_edges": 0,
                "new_nodes": 0, "new_edges": 0, "new_evidence": 0,
                "release_status": "not_built"}

    zone, session = doc["zone"], doc["session"]
    db = sqlite3.connect(nav_path, timeout=10)
    try:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT COUNT(*) FROM nav_release").fetchone()[0]:
            raise ValueError("Refusing to modify a database with any staged release")
        db.execute("INSERT OR IGNORE INTO zone(zone_id, survey_status) VALUES (?, 'observed')",
                   (zone,))
        created = collections.Counter()
        seen_edges = 0
        for edge in doc["edges"]:
            if not isinstance(edge, dict):
                raise ValueError("Malformed edge")
            a, b = edge.get("from"), edge.get("to")
            if not isinstance(a, dict) or not isinstance(b, dict):
                raise ValueError("Malformed edge endpoints")
            for point in (a, b):
                if (not valid_xyz(point)
                        or type(point.get("sequence")) is not int
                        or point["sequence"] <= 0):
                    raise ValueError("Invalid source endpoint")
            if b["sequence"] <= a["sequence"]:
                raise ValueError("Non-increasing source sequence")
            aq, bq = quantize(a), quantize(b)
            if aq == bq:
                continue
            node_ids = []
            for xyz in (aq, bq):
                node_id = stable_id("node-v1", zone, *xyz)
                count = db.execute("""
                    INSERT OR IGNORE INTO node(node_id,zone_id,x,y,z)
                    VALUES (?,?,?,?,?)""",
                    (node_id, zone, *xyz)).rowcount
                if count:
                    created["nodes"] += 1
                current = db.execute(
                    "SELECT zone_id,x,y,z FROM node WHERE node_id=?", (node_id,)
                ).fetchone()
                if current != (zone, *xyz):
                    raise ValueError("Unexpected node hash collision")
                node_ids.append(node_id)
            from_id, to_id = node_ids
            edge_id = stable_id("edge-v1", zone, from_id, to_id)
            cost = math.dist(aq, bq)
            count = db.execute("""
                INSERT OR IGNORE INTO walk_edge(
                    edge_id,zone_id,from_node_id,to_node_id,cost,confidence,status)
                VALUES (?,?,?,?,?,0.25,'candidate')""",
                (edge_id, zone, from_id, to_id, cost)).rowcount
            if count:
                created["edges"] += 1
            current = db.execute("""
                SELECT zone_id,from_node_id,to_node_id
                FROM walk_edge WHERE edge_id=?""", (edge_id,)).fetchone()
            if current != (zone, from_id, to_id):
                raise ValueError("Unexpected edge hash collision")
            evidence_id = stable_id("evidence-walk-v1", edge_id, session, b["sequence"])
            count = db.execute("""
                INSERT OR IGNORE INTO evidence_ref(
                    evidence_id, walk_edge_id, source_session, source_sequence,
                    algorithm_version, occasion_key)
                VALUES (?,?,?,?,?,?)""",
                (evidence_id, edge_id, session, b["sequence"],
                 ALGORITHM, session)).rowcount
            if count:
                created["evidence"] += 1
                # An observed sample count, *not* independent validation trials.
                db.execute("UPDATE walk_edge SET evidence_count=evidence_count+1 "
                           "WHERE edge_id=?", (edge_id,))
            seen_edges += 1
        db.commit()
        return {"zone": zone, "session": session, "candidate_edges": seen_edges,
                "new_nodes": created["nodes"], "new_edges": created["edges"],
                "new_evidence": created["evidence"],
                "release_status": "not_built"}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()




def manifest_sessions(path, expected_zone):
    """Explicit, audited session manifest; no expensive whole-corpus ranking."""
    path = Path(path).expanduser()
    if path.stat().st_size > 32768:
        raise ValueError("Session manifest exceeds 32 KiB")
    data = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(data, dict) or not isinstance(data.get("sessions"), list)
            or not 1 <= len(data["sessions"]) <= 10):
        raise ValueError("Expected JSON object with 1 to 10 sessions")
    if type(data.get("zone")) is not int or data["zone"] != expected_zone:
        raise ValueError("Manifest zone does not match requested zone")
    sessions = data["sessions"]
    if any(not isinstance(s, str) or not 1 <= len(s) <= 128 for s in sessions):
        raise ValueError("Invalid session identifier in manifest")
    if len(set(sessions)) != len(sessions):
        raise ValueError("Duplicate session identifiers")
    return sessions


def extract_batch(raw_db, zone, sessions, max_positions, max_windows, write):
    """Emit one bounded JSON line per session/window, never unbounded output."""
    if not 1 <= max_windows <= 10:
        raise ValueError("max_windows must be from 1 to 10")
    if not 1 <= len(sessions) <= 10:
        raise ValueError("At most 10 sessions per pilot")
    for session in sessions:
        cursor = 0
        for _ in range(max_windows):
            doc = extract(raw_db, zone, session, max_positions, cursor)
            if doc["positions_scanned"] == 0:
                break
            write(json.dumps(doc, separators=(",", ":"), allow_nan=False) + "\n")
            if doc["positions_scanned"] < max_positions:
                break
            last = doc["source_last_sequence"]
            next_cursor = last - 1  # overlap one position at window boundary
            if next_cursor <= cursor:
                break
            cursor = next_cursor


def import_batch(nav_path, source):
    """Consume NDJSON stream, one verified transaction per document."""
    summary = collections.Counter()
    sessions = set()
    documents = 0
    for raw in source:
        documents += 1
        if documents > 100:
            raise ValueError("Exceeded 100 bounded pilot documents")
        if len(raw) > MAX_DOCUMENT_BYTES:
            raise ValueError("Candidate document exceeds 2 MiB")
        doc = json.loads(raw)
        result = import_candidates(nav_path, doc)
        sessions.add((result["zone"], result["session"]))
        for key in ("candidate_edges", "new_nodes", "new_edges", "new_evidence"):
            summary[key] += result[key]
    if documents == 0:
        raise ValueError("No candidate documents received; nothing imported")
    return {"documents": documents, "sessions": len(sessions),
            "candidate_edges": summary["candidate_edges"],
            "new_nodes": summary["new_nodes"],
            "new_edges": summary["new_edges"],
            "new_evidence": summary["new_evidence"],
            "release_status": "not_built"}

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    ex = sub.add_parser("extract", help="Read-only historical HDD query; JSON to stdout")
    ex.add_argument("--raw-db", required=True)
    ex.add_argument("--zone", required=True, type=int)
    ex.add_argument("--session", required=True)
    ex.add_argument("--max-positions", type=int, default=800)
    ex.add_argument("--after-sequence", type=int, default=0)
    imp = sub.add_parser("import", help="Import bounded candidates to NVMe; no raw access")
    imp.add_argument("--nav-db", required=True)
    batch_ex = sub.add_parser("extract-batch", help="Bounded historic sessions -> NDJSON")
    batch_ex.add_argument("--raw-db", required=True)
    batch_ex.add_argument("--zone", required=True, type=int)
    batch_ex.add_argument("--manifest", required=True, help="JSON with explicit sessions list")
    batch_ex.add_argument("--max-positions", type=int, default=800)
    batch_ex.add_argument("--max-windows", type=int, default=2)
    batch_imp = sub.add_parser("import-batch", help="NDJSON -> candidate-only NVMe graph")
    batch_imp.add_argument("--nav-db", required=True)
    args = parser.parse_args(argv)
    if args.command == "extract":
        print(json.dumps(extract(args.raw_db, args.zone, args.session,
                                 args.max_positions, args.after_sequence),
                         separators=(",", ":"), allow_nan=False))
    elif args.command == "import":
        data = sys.stdin.buffer.read(MAX_DOCUMENT_BYTES + 1)
        if len(data) > MAX_DOCUMENT_BYTES:
            raise ValueError("Candidate document exceeds 2 MiB")
        print(json.dumps(import_candidates(args.nav_db, json.loads(data)),
                         indent=2, sort_keys=True))
    elif args.command == "extract-batch":
        extract_batch(args.raw_db, args.zone, manifest_sessions(args.manifest, args.zone),
                      args.max_positions, args.max_windows,
                      lambda line: print(line, end="", flush=True))
    else:
        def bounded_lines():
            while True:
                line = sys.stdin.buffer.readline(MAX_DOCUMENT_BYTES + 1)
                if not line:
                    break
                yield line
        print(json.dumps(import_batch(args.nav_db, bounded_lines()),
                         indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
