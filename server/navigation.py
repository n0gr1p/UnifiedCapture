"""Bounded, read-only navigation evidence queries over UnifiedCapture SQLite.

These queries describe observed telemetry, not proven walkable paths or usable
teleport links. No arbitrary SQL, file paths, or mutation controls are exposed.
"""
import json
import re
from contextlib import closing
import sqlite3
from pathlib import Path

MAX_PAGE = 200
MAX_OFFSET = 100_000
SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
TRAVEL_FIELDS = (
    "reason", "x", "y", "z", "old_zone", "new_zone",
    "interaction_id", "interaction_stage", "interaction_target_id",
    "interaction_menu_id", "interaction_correlation", "packet_kind",
    "packet_id", "option_index", "item_id", "item_name", "item_bag",
    "item_slot", "direction"
)


class NavigationQueryError(ValueError):
    pass


def bounded_int(value, label, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise NavigationQueryError(f"{label} must be an integer in [{minimum}, {maximum}]")
    return value


def _read_connection(db_path):
    # SQLite URI mode=ro prevents any mutation, even if future code makes a mistake.
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    db = sqlite3.connect(uri, uri=True, timeout=8)
    db.execute("PRAGMA query_only=ON")
    db.row_factory = sqlite3.Row
    return db


def _page(limit, offset):
    return (bounded_int(limit, "limit", 1, MAX_PAGE),
            bounded_int(offset, "offset", 0, MAX_OFFSET))


def dataset(db_path):
    with closing(_read_connection(db_path)) as db:
        events = db.execute("SELECT COUNT(*) AS total, MAX(observed_at) AS newest FROM events").fetchone()
        sessions = db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        positions = db.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
        transitions = db.execute(
            "SELECT COUNT(*) FROM events WHERE event_type='zone_change'").fetchone()[0]
        zones = db.execute("SELECT COUNT(DISTINCT zone) FROM positions WHERE zone IS NOT NULL").fetchone()[0]
        return {
            "sessions": sessions, "events": events["total"],
            "positions": positions, "zone_change_events": transitions,
            "zones_with_positions": zones, "newest_event_at": events["newest"],
            "source": "ingested_sqlite", "read_only": True
        }


# The position-only partial index is maintained by storage.initialize().
# It avoids millions of random primary-key joins into the large events table.
# zone + session order also makes the distinct-session aggregation efficient.
ZONE_COVERAGE_SQL = """
    SELECT zone, COUNT(*) AS samples,
           COUNT(DISTINCT session) AS sessions,
           MIN(observed_at) AS first_seen_at,
           MAX(observed_at) AS last_seen_at
    FROM events INDEXED BY idx_events_position_zone_session_time
    WHERE event_type='position' AND zone IS NOT NULL
    GROUP BY zone
    ORDER BY samples DESC, zone ASC LIMIT ? OFFSET ?
"""


def zones(db_path, limit=50, offset=0):
    limit, offset = _page(limit, offset)
    with closing(_read_connection(db_path)) as db:
        rows = db.execute(ZONE_COVERAGE_SQL, (limit, offset)).fetchall()
    return {
        "zones": [dict(row) for row in rows], "limit": limit, "offset": offset,
        "interpretation": "Observed position sample counts; not coverage completeness or navigability."
    }


def transitions(db_path, limit=50, offset=0, from_zone=None):
    limit, offset = _page(limit, offset)
    if from_zone is not None:
        bounded_int(from_zone, "from_zone", 0, 65535)
    where = "AND CAST(json_extract(raw_json,'$.old_zone') AS INTEGER)=?" if from_zone is not None else ""
    values = ([from_zone] if from_zone is not None else []) + [limit, offset]
    with closing(_read_connection(db_path)) as db:
        rows = db.execute(f"""
            SELECT CAST(json_extract(raw_json,'$.old_zone') AS INTEGER) AS from_zone,
                   CAST(json_extract(raw_json,'$.new_zone') AS INTEGER) AS to_zone,
                   COUNT(*) AS observations,
                   COUNT(DISTINCT session) AS sessions,
                   COUNT(DISTINCT character) AS characters,
                   MAX(observed_at) AS last_seen_at
            FROM events
            WHERE event_type='zone_change'
              AND json_type(raw_json,'$.old_zone') IN ('integer','real')
              AND json_type(raw_json,'$.new_zone') IN ('integer','real')
              {where}
            GROUP BY from_zone, to_zone
            ORDER BY observations DESC, from_zone, to_zone LIMIT ? OFFSET ?
        """, values).fetchall()
    return {
        "transitions": [dict(row) for row in rows], "limit": limit, "offset": offset,
        "from_zone": from_zone,
        "interpretation": "Observed zoning pairs. A warp, item, NPC, or walk may have caused a transition; no walkable edge is implied."
    }


def sessions(db_path, zone=None, limit=50, offset=0):
    limit, offset = _page(limit, offset)
    if zone is not None:
        bounded_int(zone, "zone", 0, 65535)
    with closing(_read_connection(db_path)) as db:
        if zone is None:
            rows = db.execute("""
                SELECT s.session, s.character, s.started_at,
                       s.recorder_version, s.schema_version
                FROM sessions s ORDER BY s.started_at DESC, s.session
                LIMIT ? OFFSET ?
            """, (limit, offset)).fetchall()
        else:
            rows = db.execute("""
                SELECT s.session, s.character, s.started_at,
                       s.recorder_version, s.schema_version
                FROM sessions s WHERE EXISTS (
                    SELECT 1 FROM positions p
                    WHERE p.session=s.session AND p.zone=? LIMIT 1)
                ORDER BY s.started_at DESC, s.session LIMIT ? OFFSET ?
            """, (zone, limit, offset)).fetchall()
    return {"sessions": [dict(row) for row in rows], "zone": zone,
            "limit": limit, "offset": offset}


def route_events(db_path, session, after_sequence=0, limit=100, include_interactions=True):
    if not isinstance(session, str) or not SESSION_ID.fullmatch(session):
        raise NavigationQueryError("session must be a recorded session identifier")
    bounded_int(after_sequence, "after_sequence", 0, 2147483647)
    bounded_int(limit, "limit", 1, MAX_PAGE)
    if not isinstance(include_interactions, bool):
        raise NavigationQueryError("include_interactions must be boolean")
    predicates = "event_type IN ('position','discontinuity','zone_change')"
    if include_interactions:
        predicates += """ OR (event_type='packet_observation' AND (
            json_type(raw_json,'$.interaction_stage') IS NOT NULL
            OR json_extract(raw_json,'$.packet_kind')='item_use_request'))"""
    with closing(_read_connection(db_path)) as db:
        known = db.execute("SELECT 1 FROM sessions WHERE session=?", (session,)).fetchone()
        if not known:
            raise NavigationQueryError("unknown session")
        rows = db.execute(f"""
            SELECT sequence, observed_at, zone, event_type, raw_json
            FROM events WHERE session=? AND sequence>? AND ({predicates})
            ORDER BY sequence LIMIT ?
        """, (session, after_sequence, limit + 1)).fetchall()
    has_more = len(rows) > limit
    records = []
    for row in rows[:limit]:
        original = json.loads(row["raw_json"])
        record = {"sequence": row["sequence"], "at": row["observed_at"],
                  "zone": row["zone"], "event": row["event_type"]}
        record.update({key: original[key] for key in TRAVEL_FIELDS if key in original})
        records.append(record)
    return {
        "session": session, "after_sequence": after_sequence, "limit": limit,
        "events": records, "next_after_sequence": records[-1]["sequence"] if records else None,
        "has_more": has_more,
        "interpretation": "Observations only; no automatic classification of walking, teleportation, or successful NPC travel."
    }
