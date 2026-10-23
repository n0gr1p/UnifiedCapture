"""Lossless JSONL archival and idempotent SQLite ingestion (stdlib only)."""
import hashlib
import json
import math
import os
import sqlite3
import tempfile
import threading
from pathlib import Path

MAX_BODY_BYTES = 1024 * 1024
MAX_RECORDS = 2048
SCHEMA_VERSION = 1
INGEST_LOCK = threading.RLock()


class IngestError(ValueError):
    pass


def connection(db_path):
    db = sqlite3.connect(db_path, timeout=30)
    db.execute("PRAGMA busy_timeout = 30000")
    db.execute("PRAGMA journal_mode = WAL")
    db.execute("PRAGMA synchronous = FULL")
    db.execute("PRAGMA foreign_keys = ON")
    return db


def initialize(db_path):
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with connection(path) as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS imported_batches (
            sha256 TEXT PRIMARY KEY,
            record_count INTEGER NOT NULL,
            imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS sessions (
            session TEXT PRIMARY KEY,
            character TEXT,
            started_at REAL,
            recorder_version TEXT,
            schema_version INTEGER
        );
        CREATE TABLE IF NOT EXISTS events (
            session TEXT NOT NULL,
            sequence INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            observed_at REAL NOT NULL,
            character TEXT,
            zone INTEGER,
            raw_json TEXT NOT NULL,
            source_file TEXT,
            PRIMARY KEY (session, sequence),
            FOREIGN KEY (session) REFERENCES sessions(session)
        );
        CREATE INDEX IF NOT EXISTS idx_events_zone_time
            ON events(zone, observed_at);
        CREATE INDEX IF NOT EXISTS idx_events_type_time
            ON events(event_type, observed_at);
        CREATE TABLE IF NOT EXISTS positions (
            session TEXT NOT NULL, sequence INTEGER NOT NULL,
            zone INTEGER, x REAL, y REAL, z REAL, reason TEXT,
            PRIMARY KEY(session, sequence),
            FOREIGN KEY(session, sequence) REFERENCES events(session, sequence)
        );
        CREATE TABLE IF NOT EXISTS entity_observations (
            session TEXT NOT NULL, sequence INTEGER NOT NULL,
            zone INTEGER, entity_id INTEGER, entity_index INTEGER,
            entity_epoch INTEGER, entity_kind TEXT, entity_name TEXT,
            entity_name_source TEXT, observation_kind TEXT,
            entity_x REAL, entity_y REAL, entity_z REAL,
            observer_x REAL, observer_y REAL, observer_z REAL,
            death_signal TEXT, disappearance_cause TEXT,
            PRIMARY KEY(session, sequence),
            FOREIGN KEY(session, sequence) REFERENCES events(session, sequence)
        );
        CREATE INDEX IF NOT EXISTS idx_entity_location
            ON entity_observations(zone, entity_kind, entity_x, entity_y);
        CREATE INDEX IF NOT EXISTS idx_entity_identity
            ON entity_observations(zone, entity_id, entity_index);
        CREATE TABLE IF NOT EXISTS interactions (
            session TEXT NOT NULL, sequence INTEGER NOT NULL,
            zone INTEGER, interaction_id INTEGER,
            interaction_stage TEXT, target_id INTEGER, menu_id INTEGER,
            packet_id INTEGER, option_index INTEGER,
            PRIMARY KEY(session, sequence),
            FOREIGN KEY(session, sequence) REFERENCES events(session, sequence)
        );
        CREATE INDEX IF NOT EXISTS idx_interaction
            ON interactions(session, interaction_id);
        """)
    return path


def parse_batch(raw):
    if not raw or len(raw) > MAX_BODY_BYTES or not raw.endswith(b"\n"):
        raise IngestError("Expected 1-1048576 bytes of complete newline-terminated JSONL")
    lines = raw.splitlines(keepends=True)
    if not 1 <= len(lines) <= MAX_RECORDS:
        raise IngestError("Invalid event count")
    records = []
    for line in lines:
        if not line.endswith(b"\n") or len(line) > 262144:
            raise IngestError("Incomplete or oversized event")
        try:
            obj = json.loads(line.decode("utf-8"))
        except (UnicodeError, ValueError) as exc:
            raise IngestError("Invalid UTF-8 JSONL event") from exc
        if not isinstance(obj, dict):
            raise IngestError("Event must be a JSON object")
        session, seq, kind, at = (
            obj.get("session"), obj.get("sequence"),
            obj.get("event"), obj.get("time"))
        if (not isinstance(session, str) or not 1 <= len(session) <= 128
                or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in session)
                or type(seq) is not int or not 1 <= seq <= 2147483647
                or not isinstance(kind, str) or not 1 <= len(kind) <= 80
                or type(at) not in (float, int) or not math.isfinite(at)):
            raise IngestError("Invalid session, sequence, event or time")
        # Reject duplicate sequence keys within one uploaded chunk.
        records.append(obj)
    keys = [(obj["session"], obj["sequence"]) for obj in records]
    if len(keys) != len(set(keys)):
        raise IngestError("Repeated session/sequence in one batch")
    return records


def number(obj, key):
    value = obj.get(key)
    if type(value) not in (int, float) or not math.isfinite(value):
        return None
    return value


def text(obj, key, max_length=256):
    v = obj.get(key)
    return v[:max_length] if isinstance(v, str) else None


def archive_bytes(archive_dir, raw):
    """Persist exact source bytes before acknowledging SQLite import."""
    root = Path(archive_dir) / "batches"
    root.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(raw).hexdigest()
    dest = root / (digest + ".jsonl")
    if dest.exists():
        if dest.read_bytes() != raw:
            raise IngestError("Archive digest collision")
        return digest
    fd, name = tempfile.mkstemp(prefix=".upload-", dir=root)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, dest)
        if hasattr(os, "O_DIRECTORY"):
            dirfd = os.open(str(root), os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(dirfd)
            finally:
                os.close(dirfd)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    return digest


def import_batch(db_path, records, source_file=None, digest=None):
    """Single SQLite transaction. Replaying the same batch is idempotent."""
    inserted = duplicates = 0
    with connection(db_path) as db:
        db.execute("BEGIN IMMEDIATE")
        for obj in records:
            session, seq = obj["session"], obj["sequence"]
            payload = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            old = db.execute(
                "SELECT raw_json FROM events WHERE session=? AND sequence=?",
                (session, seq)).fetchone()
            if old:
                if old[0] != payload:
                    raise IngestError("Conflicting payload for existing session/sequence")
                duplicates += 1
                continue
            db.execute(
                "INSERT OR IGNORE INTO sessions(session, character) VALUES (?,?)",
                (session, text(obj, "character")))
            if obj["event"] == "session_start":
                db.execute("""UPDATE sessions SET
                    character=COALESCE(?, character),
                    started_at=COALESCE(?, started_at),
                    recorder_version=COALESCE(?, recorder_version),
                    schema_version=COALESCE(?, schema_version)
                    WHERE session=?""",
                    (text(obj, "character"), number(obj, "time"),
                     text(obj, "recorder_version"), number(obj, "schema"), session))
            db.execute("""INSERT INTO events(
                session, sequence, event_type, observed_at, character, zone,
                raw_json, source_file) VALUES (?,?,?,?,?,?,?,?)""",
                (session, seq, obj["event"], obj["time"],
                 text(obj, "character"), number(obj, "zone"),
                 payload, source_file))
            if obj["event"] == "position":
                db.execute("INSERT INTO positions VALUES (?,?,?,?,?,?,?)",
                    (session, seq, number(obj, "zone"), number(obj, "x"),
                     number(obj, "y"), number(obj, "z"), text(obj, "reason")))
            elif obj["event"] == "entity_observation":
                db.execute("""INSERT INTO entity_observations VALUES
                    (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                    session, seq, number(obj, "zone"), number(obj, "entity_id"),
                    number(obj, "entity_index"), number(obj, "entity_epoch"),
                    text(obj, "entity_kind"), text(obj, "entity_name"),
                    text(obj, "entity_name_source"), text(obj, "observation_kind"),
                    number(obj, "entity_x"), number(obj, "entity_y"),
                    number(obj, "entity_z"), number(obj, "observer_x"),
                    number(obj, "observer_y"), number(obj, "observer_z"),
                    text(obj, "death_signal"), text(obj, "disappearance_cause")))
            elif obj["event"] == "packet_observation" and obj.get("interaction_id") is not None:
                db.execute("INSERT INTO interactions VALUES (?,?,?,?,?,?,?,?,?)",
                    (session, seq, number(obj, "zone"),
                     number(obj, "interaction_id"), text(obj, "interaction_stage"),
                     number(obj, "interaction_target_id"), number(obj, "interaction_menu_id"),
                     number(obj, "packet_id"), number(obj, "option_index")))
            inserted += 1
        if digest:
            db.execute(
                "INSERT OR IGNORE INTO imported_batches(sha256, record_count) VALUES (?,?)",
                (digest, len(records)))
    return {"inserted": inserted, "duplicates": duplicates}


def check_conflicts(db_path, records):
    """Reject changed-payload retries before they enter the immutable archive."""
    with connection(db_path) as db:
        for obj in records:
            old = db.execute(
                "SELECT raw_json FROM events WHERE session=? AND sequence=?",
                (obj["session"], obj["sequence"])).fetchone()
            if old and old[0] != json.dumps(
                obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")):
                raise IngestError("Conflicting payload for existing session/sequence")


def ingest(db_path, archive_dir, raw, source_file=None):
    records = parse_batch(raw)
    # Filenames are only advisory metadata; never user-controlled paths.
    if source_file is not None and (
        not isinstance(source_file, str) or len(source_file) > 160 or
        any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-" for c in source_file)):
        raise IngestError("Invalid source file label")
    # A single-server lock also keeps preflight and archive/SQL commit ordered.
    # This prevents conflicting retries from poisoning the recovery archive.
    with INGEST_LOCK:
        check_conflicts(db_path, records)
        digest = archive_bytes(archive_dir, raw)
        result = import_batch(db_path, records, source_file, digest)
    return dict(result, count=len(records), sha256=digest)


def recover(db_path, archive_dir):
    """Reconcile only batches lacking a committed SQLite batch ledger row."""
    with connection(db_path) as db:
        imported = {row[0] for row in db.execute("SELECT sha256 FROM imported_batches")}
    total = 0
    for path in sorted((Path(archive_dir) / "batches").glob("*.jsonl")):
        if path.stem in imported:
            continue
        raw = path.read_bytes()
        if path.stem != hashlib.sha256(raw).hexdigest():
            raise IngestError("Archive checksum mismatch: " + path.name)
        # Never silently discard corrupt evidence or conflicting session IDs.
        total += import_batch(db_path, parse_batch(raw), digest=path.stem)["inserted"]
    return total


def stats(db_path):
    with connection(db_path) as db:
        counts = {}
        for table in ("sessions", "events", "positions",
                      "entity_observations", "interactions"):
            counts[table] = db.execute("SELECT count(*) FROM " + table).fetchone()[0]
        counts["distinct_monsters"] = db.execute(
            """SELECT count(*) FROM (
                SELECT DISTINCT zone, entity_id FROM entity_observations
                WHERE entity_kind='monster' AND entity_id IS NOT NULL)""").fetchone()[0]
        return counts
