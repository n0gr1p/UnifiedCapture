"""NAV-11: replay archived same-client UC outcomes into the UCNV v2 ledger.

The NAV-10 reader runs inside ingest UID 10001 and extracts bounded,
sanitized outcome fields; the unprivileged consumer owns the NVMe graph.
This code never opens the privileged HDD database or exposes HTTP writes.
"""
import math
import sqlite3

import nav_edge_lifecycle as lifecycle
import derived_navigation_db as navigation

MAX_EDGE_ID = (1 << 62) - 1
MAX_OFFSET = 1.5
SUCCESS_RADIUS = 0.85


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value) and abs(value) < 100000


def _dist(a, b):
    return math.dist(a, b)


def _corridor(pos, a, b):
    vector = [b[i] - a[i] for i in range(3)]
    length2 = sum(v * v for v in vector)
    if length2 < .01:
        return math.inf
    t = sum((pos[i] - a[i]) * vector[i] for i in range(3)) / length2
    t = max(0, min(1, t))
    return _dist(pos, [a[i] + t * vector[i] for i in range(3)])


def validate(outcome, *, expected_row):
    """Fail closed on identity, event source and bounded physical evidence.

    expected_row is the ten-field immutable source-window row, not a
    mutable client-supplied row ID. The sender's claim of "confirmed" is
    not by itself enough to promote an edge.
    """
    if not isinstance(outcome, dict) or len(expected_row) != 10:
        raise ValueError("Invalid NAV outcome envelope")
    rowid, session, seq, kind, zone, at, *_ = expected_row
    if (kind != "nav_edge_outcome"
            or type(outcome.get("source_rowid")) is not int
            or outcome["source_rowid"] != rowid
            or outcome.get("source_session") != session
            or outcome.get("source_sequence") != seq
            or not isinstance(session, str)
            or not session.startswith("terrasjr_")
            or outcome.get("character") != "Terrasjr"
            or zone != 247 or outcome.get("zone") != zone):
        raise ValueError("NAV outcome source identity mismatch")
    edge_str = outcome.get("nav_edge_id_str")
    if (not isinstance(edge_str, str) or not edge_str
            or not edge_str.isascii() or not edge_str.isdecimal()
            or edge_str[0] == "0"
            or len(edge_str) > 19):
        raise ValueError("Invalid exact directed edge ID")
    edge_id = int(edge_str)
    if not 0 < edge_id <= MAX_EDGE_ID:
        raise ValueError("Directed edge exceeds UCNV range")
    event_id, run_id = outcome.get("nav_event_id"), outcome.get("nav_run_id")
    if (not isinstance(event_id, str) or not lifecycle.IDENTIFIER.fullmatch(event_id)
            or not isinstance(run_id, str) or not lifecycle.IDENTIFIER.fullmatch(run_id)):
        raise ValueError("Invalid UC outcome event/run identity")
    direction_a, direction_b = (outcome.get("nav_from_index"),
                                outcome.get("nav_to_index"))
    if (type(direction_a) is not int or type(direction_b) is not int
            or not 0 <= direction_a <= 99 or direction_b != direction_a + 1):
        raise ValueError("Invalid source directed leg indices")
    result = outcome.get("nav_kind")
    reason = outcome.get("nav_reason")
    confirmed = outcome.get("nav_confirmed")
    if (not isinstance(result, str)
            or result not in lifecycle.OUTCOMES
            or not isinstance(reason, str)
            or type(confirmed) is not bool
            or reason not in (
                lifecycle.SUCCESS_REASONS if result == "traversed"
                else lifecycle.BLOCK_REASONS if result == "blocked"
                else lifecycle.INTERRUPT_REASONS)
            or confirmed is not (result != "interrupted")):
        raise ValueError("Invalid or unconfirmed UC navigation result")
    pos = tuple(outcome.get(k) for k in ("x", "y", "z"))
    if not all(_finite(n) for n in pos):
        raise ValueError("NAV outcome has no valid contemporaneous XYZ")
    return edge_id, event_id, run_id, result, reason, confirmed, pos


def replay(nav_db, outcome, *, expected_row):
    """Validate actor/zone/identity and recorded geometry before mutating.

    Replays are idempotent through the NAV-04 event_id ledger. A position
    sample near the edge is supporting evidence, not cryptographic proof
    of who emitted an arbitrary local addon command.
    """
    navigation.inspect(nav_db)
    edge_id, eid, run_id, kind, reason, confirmed, pos = validate(
        outcome, expected_row=expected_row)
    with sqlite3.connect(nav_db) as db:
        db.execute("PRAGMA query_only=ON")
        row = db.execute("""
            SELECT e.zone_id,a.x,a.y,a.z,b.x,b.y,b.z
            FROM walk_edge e
            JOIN node a ON a.node_id=e.from_node_id
            JOIN node b ON b.node_id=e.to_node_id
            WHERE e.edge_id=?""", (edge_id,)).fetchone()
    if not row or row[0] != 247:
        raise ValueError("Navigation outcome references unknown or wrong-zone edge")
    start, end = row[1:4], row[4:7]
    if kind == "traversed":
        if _dist(pos, end) > SUCCESS_RADIUS:
            raise ValueError("Claimed successful traversal not near directed endpoint")
    elif kind == "blocked":
        if _corridor(pos, start, end) > MAX_OFFSET:
            raise ValueError("Claimed blockage not on the recorded directed edge")
        if _dist(pos, end) <= .25:
            raise ValueError("Cannot claim blockage while already at endpoint")
    return lifecycle.record_outcome(
        nav_db, edge_id=edge_id, event_id=eid, run_id=run_id,
        kind=kind, reason=reason, confirmed=confirmed)
