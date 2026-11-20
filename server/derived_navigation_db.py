"""Bootstrap the compact, rebuildable navigation SQLite (not raw telemetry).

This file is intentionally independent of storage.py and the ingestion server.
An initialized database has *no published navigation release*. READY walking
edges are navigation-eligible data, but this schema alone grants no UC
movement ownership or automatic execution.
"""
import argparse
import json
import sqlite3
from pathlib import Path

SCHEMA_VERSION = 2
APPLICATION_ID = 0x55434E56  # "UCNV": distinct from the raw capture database

SCHEMA_SQL = """
CREATE TABLE nav_release (
    slot INTEGER PRIMARY KEY CHECK (slot = 1),
    release_id TEXT NOT NULL UNIQUE,
    algorithm_version TEXT NOT NULL,
    source_watermark TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('staging', 'validated', 'published')),
    build_timestamp TEXT NOT NULL,
    content_checksum TEXT,
    CHECK (status <> 'published' OR content_checksum IS NOT NULL)
);

CREATE TABLE zone (
    zone_id INTEGER PRIMARY KEY CHECK (zone_id BETWEEN 0 AND 65535),
    survey_status TEXT NOT NULL DEFAULT 'unknown'
        CHECK (survey_status IN ('unknown', 'observed', 'validated'))
);

CREATE TABLE node (
    node_id INTEGER PRIMARY KEY,
    zone_id INTEGER NOT NULL REFERENCES zone(zone_id),
    x REAL NOT NULL,
    y REAL NOT NULL,
    z REAL NOT NULL,
    kind TEXT NOT NULL DEFAULT 'walk'
        CHECK (kind IN ('walk', 'transport_entry', 'transport_exit', 'landmark')),
    UNIQUE (zone_id, node_id)
);
CREATE INDEX idx_node_zone_xyz ON node(zone_id, x, y, z);

-- Walking is always directed and must remain entirely within one zone.
-- Composite foreign keys prevent an accidental cross-zone walking edge.
CREATE TABLE walk_edge (
    edge_id INTEGER PRIMARY KEY,
    zone_id INTEGER NOT NULL REFERENCES zone(zone_id),
    from_node_id INTEGER NOT NULL,
    to_node_id INTEGER NOT NULL,
    cost REAL NOT NULL CHECK (cost >= 0),
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    status TEXT NOT NULL DEFAULT 'ready'
        CHECK (status IN ('ready', 'proven', 'failed')),
    evidence_count INTEGER NOT NULL DEFAULT 0 CHECK (evidence_count >= 0),
    CHECK (from_node_id <> to_node_id),
    FOREIGN KEY (zone_id, from_node_id) REFERENCES node(zone_id, node_id),
    FOREIGN KEY (zone_id, to_node_id) REFERENCES node(zone_id, node_id),
    UNIQUE (zone_id, from_node_id, to_node_id)
);
CREATE INDEX idx_walk_edge_from ON walk_edge(zone_id, from_node_id, status);

-- Runtime walking outcomes are independent of historical HDD evidence.
-- Two distinct navigation runs with confirmed blocks are needed to fail an edge.
CREATE TABLE edge_outcome (
    outcome_id INTEGER PRIMARY KEY,
    event_id TEXT NOT NULL UNIQUE CHECK (length(event_id) BETWEEN 8 AND 128),
    edge_id INTEGER NOT NULL REFERENCES walk_edge(edge_id),
    run_id TEXT NOT NULL CHECK (length(run_id) BETWEEN 8 AND 128),
    kind TEXT NOT NULL CHECK (kind IN ('traversed', 'blocked', 'interrupted')),
    reason TEXT NOT NULL CHECK (length(reason) BETWEEN 1 AND 96),
    recorded_at TEXT NOT NULL CHECK (length(recorded_at) BETWEEN 10 AND 40),
    confirmed INTEGER NOT NULL CHECK (confirmed IN (0, 1)),
    CHECK (kind <> 'blocked' OR confirmed = 1)
);
CREATE INDEX idx_edge_outcome_edge
    ON edge_outcome(edge_id, outcome_id, run_id, kind);

-- A transport may connect different zones and NEVER implies reverse travel
-- or ordinary walking. Executable transports require an activation method;
-- a separate validator must still check real-world activation evidence.
CREATE TABLE transport (
    transport_id INTEGER PRIMARY KEY,
    from_node_id INTEGER NOT NULL REFERENCES node(node_id),
    to_node_id INTEGER NOT NULL REFERENCES node(node_id),
    kind TEXT NOT NULL DEFAULT 'unknown'
        CHECK (kind IN ('unknown', 'conflux', 'waypoint', 'npc', 'item', 'spell', 'automatic')),
    activation_method TEXT,
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    status TEXT NOT NULL DEFAULT 'candidate'
        CHECK (status IN ('candidate', 'validated', 'executable')),
    evidence_count INTEGER NOT NULL DEFAULT 0 CHECK (evidence_count >= 0),
    CHECK (from_node_id <> to_node_id),
    CHECK (status <> 'executable'
        OR (activation_method IS NOT NULL AND length(trim(activation_method)) > 0))
);
CREATE INDEX idx_transport_from ON transport(from_node_id, status);

-- This is a compact pointer back to the authoritative HDD records.
-- Never copy raw event JSON into the navigation database.
CREATE TABLE evidence_ref (
    evidence_id INTEGER PRIMARY KEY,
    walk_edge_id INTEGER REFERENCES walk_edge(edge_id),
    transport_id INTEGER REFERENCES transport(transport_id),
    source_session TEXT NOT NULL,
    source_sequence INTEGER NOT NULL CHECK (source_sequence > 0),
    algorithm_version TEXT NOT NULL,
    occasion_key TEXT,
    CHECK ((walk_edge_id IS NOT NULL) <> (transport_id IS NOT NULL))
);
CREATE INDEX idx_evidence_walk ON evidence_ref(walk_edge_id);
CREATE INDEX idx_evidence_transport ON evidence_ref(transport_id);
CREATE UNIQUE INDEX idx_evidence_source_walk
    ON evidence_ref(walk_edge_id, source_session, source_sequence)
    WHERE walk_edge_id IS NOT NULL;
CREATE UNIQUE INDEX idx_evidence_source_transport
    ON evidence_ref(transport_id, source_session, source_sequence)
    WHERE transport_id IS NOT NULL;

CREATE TABLE landmark (
    landmark_id INTEGER PRIMARY KEY,
    node_id INTEGER NOT NULL REFERENCES node(node_id),
    kind TEXT NOT NULL,
    label TEXT,
    source_confidence REAL CHECK
        (source_confidence IS NULL OR source_confidence BETWEEN 0 AND 1)
);
"""


def inspect(path):
    """Read-only verification of an existing derived navigation database."""
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise ValueError("No navigation SQLite database at " + str(path))
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        db.execute("PRAGMA query_only=ON")
        app_id = db.execute("PRAGMA application_id").fetchone()[0]
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if app_id != APPLICATION_ID or version != SCHEMA_VERSION:
            raise ValueError(
                "Refusing unrelated or unsupported SQLite database: "
                f"application_id={app_id}, schema_version={version}"
            )
        release = db.execute(
            "SELECT release_id, status FROM nav_release WHERE slot=1"
        ).fetchone()
        return {
            "path": str(path),
            "application_id": app_id,
            "schema_version": version,
            "release_id": release[0] if release else None,
            "release_status": release[1] if release else "not_built",
            "raw_telemetry_copied": False,
        }
    finally:
        db.close()


def initialize(path):
    """Create schema once. Existing non-navigation databases are never changed."""
    path = Path(path).expanduser().resolve()
    if path.exists():
        if not path.is_file() or path.stat().st_size:
            return inspect(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    try:
        db.execute("PRAGMA foreign_keys=ON")
        # Derived releases are built offline, not under the live raw WAL writer.
        db.execute("PRAGMA journal_mode=DELETE")
        db.executescript(
            "BEGIN IMMEDIATE;\n" + SCHEMA_SQL
            + f"\nPRAGMA application_id={APPLICATION_ID};\n"
            + f"PRAGMA user_version={SCHEMA_VERSION};\nCOMMIT;\n"
        )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return inspect(path)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Initialize or inspect an isolated, derived navigation SQLite."
    )
    parser.add_argument("command", choices=("init", "inspect"))
    parser.add_argument("--db", required=True, help="Explicit target path on NVMe")
    args = parser.parse_args(argv)
    result = initialize(args.db) if args.command == "init" else inspect(args.db)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
