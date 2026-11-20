"""NAV-10: privileged read-only stream -> unprivileged candidate worker."""
import json
import pathlib
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

SERVER = pathlib.Path(__file__).resolve().parents[1] / "server"
sys.path.insert(0, str(SERVER))
import derived_navigation_db as derived
import nav_incremental_worker as worker
import nav_read_window as reader


class SplitPrivilegeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = pathlib.Path(self.temp.name)
        self.raw = base / "raw.sqlite3"
        self.nav = base / "nav.sqlite3"
        self.state = base / "cursor.sqlite3"
        with sqlite3.connect(self.raw) as db:
            db.executescript("""
                CREATE TABLE events(
                    session TEXT NOT NULL, sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL, zone INTEGER,
                    observed_at REAL, PRIMARY KEY(session,sequence));
                CREATE TABLE positions(
                    session TEXT NOT NULL, sequence INTEGER NOT NULL,
                    zone INTEGER, x REAL, y REAL, z REAL, reason TEXT,
                    PRIMARY KEY(session,sequence));
            """)
        derived.initialize(self.nav)

    def tearDown(self):
        self.temp.cleanup()

    def insert(self, session, seq, zone, at, x, reason="moving", event="position"):
        with sqlite3.connect(self.raw) as db:
            db.execute("INSERT INTO events VALUES (?,?,?,?,?)",
                       (session, seq, event, zone, at))
            if event == "position":
                db.execute("INSERT INTO positions VALUES (?,?,?,?,?,?,?)",
                           (session, seq, zone, x, 0, 0, reason))

    def stream(self, cursor, limit=200):
        return json.dumps(reader.emit_window(self.raw, cursor, limit),
                          separators=(",", ":")).encode("utf-8")

    def consume(self, data, dry_run=False):
        return worker.consume_stream(self.raw, self.nav, self.state,
                                     data, dry_run=dry_run)

    def count(self):
        with sqlite3.connect(self.nav) as db:
            return tuple(db.execute("SELECT COUNT(*) FROM " + table)
                         .fetchone()[0] for table in ("walk_edge", "evidence_ref"))

    def test_stream_dry_run_and_first_import(self):
        self.insert("A", 1, 247, 1, 0, "anchor")
        self.insert("A", 2, 247, 1.2, 1)
        self.insert("A", 3, 247, 1.4, 2)
        self.assertEqual(worker.source_cursor(self.raw, self.nav, self.state), 0)
        data = self.stream(0, limit=2)
        self.assertEqual(self.consume(data, dry_run=True)["candidate_walk_edges"], 1)
        self.assertEqual(self.count(), (0, 0))
        self.assertFalse(self.state.exists())
        self.assertEqual(self.consume(data)["new_evidence"], 1)
        self.assertEqual(worker.source_cursor(self.raw, self.nav, self.state), 2)
        self.assertEqual(self.consume(self.stream(2))["new_evidence"], 1)
        self.assertEqual(self.count(), (2, 2))
        self.assertEqual(self.consume(self.stream(3))["processed_events"], 0)
        with self.assertRaisesRegex(ValueError, "STALE_SOURCE_CURSOR"):
            self.consume(data)
        self.assertEqual(self.count(), (2, 2))

    def test_unprivileged_consumer_never_opens_raw_sqlite(self):
        self.insert("A", 1, 247, 1, 0, "anchor")
        self.insert("A", 2, 247, 1.2, 1)
        data = self.stream(0)
        original = sqlite3.connect
        def guarded(database, *args, **kwargs):
            if str(self.raw) in str(database):
                raise AssertionError("Consumer attempted raw SQLite open")
            return original(database, *args, **kwargs)
        with patch.object(sqlite3, "connect", side_effect=guarded):
            self.assertEqual(self.consume(data)["new_evidence"], 1)
            self.assertEqual(worker.source_cursor(self.raw, self.nav, self.state), 2)

    def test_malformed_and_out_of_order_stream_rejected_before_writing(self):
        self.insert("A", 1, 247, 1, 0, "anchor")
        self.insert("A", 2, 247, 1.2, 1)
        source = reader.emit_window(self.raw, 0, 10)
        source["rows"].reverse()
        with self.assertRaisesRegex(ValueError, "Non-increasing source rowid"):
            self.consume(json.dumps(source).encode())
        self.assertEqual(self.count(), (0, 0))
        self.assertFalse(self.state.exists())
        source = reader.emit_window(self.raw, 0, 10)
        source["last_rowid"] += 1
        with self.assertRaisesRegex(ValueError, "Source rowid cursor mismatch"):
            self.consume(json.dumps(source).encode())
        self.assertEqual(self.count(), (0, 0))
        with self.assertRaisesRegex(ValueError, "Invalid read-only reader JSON"):
            self.consume(b"not-json")

    def test_cross_zone_stream_candidate_only(self):
        self.insert("A", 1, 247, 1, 0, "anchor")
        self.insert("A", 2, 247, 1.2, 1)
        self.insert("A", 3, 248, 1.3, 10, "new_anchor", event="zone_change")
        self.insert("A", 4, 248, 1.4, 10, "new_anchor")
        self.insert("A", 5, 248, 1.6, 11)
        result = self.consume(self.stream(0))
        self.assertEqual(result["candidate_walk_edges"], 2)
        self.assertEqual(result["boundaries_seen"], 1)
        with sqlite3.connect(self.nav) as db:
            self.assertEqual(db.execute("SELECT DISTINCT status FROM walk_edge").fetchall(),
                             [("ready",)])
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM walk_edge w "
                "JOIN node a ON a.node_id=w.from_node_id "
                "JOIN node b ON b.node_id=w.to_node_id WHERE a.zone_id<>b.zone_id"
            ).fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
