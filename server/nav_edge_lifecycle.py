"""NAV-04: audited READY / PROVEN / FAILED directed walking edge lifecycle.

Only the future UC movement coordinator may submit a *confirmed* traversal
outcome. This module adds no HTTP write endpoint and performs no movement.
Plain telemetry import always creates READY edges; interrupted UC attempts
never count as failures. Failed edges never silently reset on fresh mining.
"""
import datetime
import re
import sqlite3

import derived_navigation_db as navigation

STATES = ("ready", "proven", "failed")
OUTCOMES = ("traversed", "blocked", "interrupted")
IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:-]{8,128}$")
BLOCK_REASONS = {"obstacle", "stalled", "diverged"}
INTERRUPT_REASONS = {
    "combat", "casting", "movement_owner", "manual_stop", "zoning",
    "timeout", "position_lost", "disconnect", "other",
}
SUCCESS_REASONS = {"completed_edge"}
REQUIRED_INDEPENDENT_BLOCK_RUNS = 2


def record_outcome(nav_db, *, edge_id, event_id, run_id, kind, reason,
                   confirmed):
    """Persist one UC-reported result idempotently, with fail-closed semantics.

    The integration must prove scheduler ownership and verified XYZ progress
    BEFORE using 'completed_edge' or 'blocked'. No network write interface is
    exposed here. Duplicate event IDs must match exactly, not rewrite history.
    """
    navigation.inspect(nav_db)
    if (type(edge_id) is not int or edge_id <= 0
            or not isinstance(event_id, str) or not IDENTIFIER.fullmatch(event_id)
            or not isinstance(run_id, str) or not IDENTIFIER.fullmatch(run_id)
            or kind not in OUTCOMES or type(confirmed) is not bool):
        raise ValueError("Invalid navigation outcome identity or kind")
    allowed = (SUCCESS_REASONS if kind == "traversed" else
               BLOCK_REASONS if kind == "blocked" else INTERRUPT_REASONS)
    if reason not in allowed or (kind != "interrupted" and confirmed is not True):
        raise ValueError("Unconfirmed or unsupported navigation outcome")

    db = sqlite3.connect(nav_db, timeout=10)
    try:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT status FROM walk_edge WHERE edge_id=?", (edge_id,)
        ).fetchone()
        if row is None:
            raise ValueError("Unknown directed walking edge")
        previous = db.execute("""SELECT edge_id, run_id, kind, reason, confirmed
            FROM edge_outcome WHERE event_id=?""", (event_id,)).fetchone()
        expected = (edge_id, run_id, kind, reason, int(confirmed))
        if previous is not None and previous != expected:
            raise ValueError("Conflicting duplicate navigation outcome event")
        if previous is None:
            # Multiple reports from the same UC navigation run do not count
            # as independent confirmations of one blocked edge.
            if kind == "blocked":
                already = db.execute("""SELECT 1 FROM edge_outcome
                    WHERE edge_id=? AND run_id=? AND kind='blocked'
                    LIMIT 1""", (edge_id, run_id)).fetchone()
                if already is not None:
                    raise ValueError("Duplicate blocked edge in one run")
            when = datetime.datetime.now(datetime.timezone.utc).isoformat()
            db.execute("""INSERT INTO edge_outcome(
                event_id,edge_id,run_id,kind,reason,recorded_at,confirmed)
                VALUES (?,?,?,?,?,?,?)""",
                (event_id, edge_id, run_id, kind, reason, when, int(confirmed)))
        # A successful, verified traversal overcomes an earlier blocked state.
        # A previous success separates runs of consecutive confirmed failures.
        latest_success = db.execute("""SELECT COALESCE(MAX(outcome_id),0)
            FROM edge_outcome WHERE edge_id=? AND kind='traversed'
            AND confirmed=1""", (edge_id,)).fetchone()[0]
        blocks = db.execute("""SELECT COUNT(DISTINCT run_id)
            FROM edge_outcome WHERE edge_id=? AND kind='blocked'
            AND confirmed=1 AND outcome_id>?""", (edge_id, latest_success)
        ).fetchone()[0]
        success = latest_success > 0 and (
            db.execute("""SELECT kind FROM edge_outcome
                WHERE edge_id=? ORDER BY outcome_id DESC LIMIT 1""",
                (edge_id,)).fetchone()[0] == "traversed"
        )
        # A transient interruption should preserve the prior state.
        current = row[0]
        if kind == "traversed":
            new_status = "proven"
        elif kind == "blocked" and blocks >= REQUIRED_INDEPENDENT_BLOCK_RUNS:
            new_status = "failed"
        elif kind == "interrupted":
            new_status = current
        else:
            new_status = current
        if new_status != current:
            db.execute("UPDATE walk_edge SET status=? WHERE edge_id=?",
                       (new_status, edge_id))
        db.commit()
        return {"edge_id": edge_id, "state": new_status,
                "confirmed_block_runs_since_success": blocks,
                "recorded": previous is None}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
