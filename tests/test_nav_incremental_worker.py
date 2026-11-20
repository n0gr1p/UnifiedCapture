"""NAV-10 checkpoint worker tests: replay, boundaries, crash and identity safety."""
import importlib.util
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
import walk_candidate_miner as miner


class NavWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = pathlib.Path(self.tmp.name)
        self.raw = base / "raw.sqlite3"
        self.nav = base / "nav.sqlite3"
        self.state = base / "worker_state.sqlite3"
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
        self.tmp.cleanup()

    def record(self, session, seq, event, zone, at, xyz=None, reason="moving"):
        with sqlite3.connect(self.raw) as db:
            db.execute(
                "INSERT INTO events VALUES (?,?,?,?,?)",
                (session, seq, event, zone, at))
            if event == "position":
                assert xyz is not None
                db.execute(
                    "INSERT INTO positions VALUES (?,?,?,?,?,?,?)",
                    (session, seq, zone, *xyz, reason))

    def run_once(self, max_events=100, dry_run=False):
        return worker.run_once(
            self.raw, self.nav, self.state, max_events, dry_run)

    def counts(self):
        with sqlite3.connect(self.nav) as db:
            return (
                db.execute("SELECT COUNT(*) FROM node").fetchone()[0],
                db.execute("SELECT COUNT(*) FROM walk_edge").fetchone()[0],
                db.execute("SELECT COUNT(*) FROM evidence_ref").fetchone()[0],
                db.execute("SELECT COUNT(DISTINCT zone_id) FROM node").fetchone()[0],
            )

    def test_consecutive_batches_preserve_previous_anchor_and_idempotence(self):
        self.record("sess-A", 1, "position", 247, 1.0, (0, 0, 0), "anchor")
        self.record("sess-A", 2, "position", 247, 1.2, (1, 0, 0))
        self.record("sess-A", 3, "position", 247, 1.4, (2, 0, 0))
        a = self.run_once(max_events=2)
        self.assertEqual((a["processed_events"], a["candidate_walk_edges"]), (2, 1))
        self.assertEqual(self.counts(), (2, 1, 1, 1))
        b = self.run_once(max_events=2)
        self.assertEqual((b["processed_events"], b["candidate_walk_edges"]), (1, 1))
        self.assertEqual(self.counts(), (3, 2, 2, 1))
        c = self.run_once(max_events=2)
        self.assertEqual(c["processed_events"], 0)
        self.assertEqual(self.counts(), (3, 2, 2, 1))
        # The canonical per-session window extractor independently agrees.
        historical = miner.extract(self.raw, 247, "sess-A", max_positions=5)
        self.assertEqual(len(historical["edges"]), 2)

    def test_dry_run_does_not_write_graph_or_checkpoint(self):
        self.record("sess-A", 1, "position", 247, 1.0, (0, 0, 0), "anchor")
        self.record("sess-A", 2, "position", 247, 1.2, (1, 0, 0))
        a = self.run_once(max_events=10, dry_run=True)
        self.assertEqual(a["candidate_walk_edges"], 1)
        self.assertFalse(self.state.exists())
        self.assertFalse(pathlib.Path(str(self.state) + ".lock").exists())
        self.assertEqual(self.counts(), (0, 0, 0, 0))
        b = self.run_once(max_events=10)
        self.assertEqual(b["new_edges"], 1)
        self.assertEqual(self.counts(), (2, 1, 1, 1))

    def test_zone_transition_and_discontinuity_do_not_create_walks(self):
        self.record("A", 1, "position", 247, 1.0, (0, 0, 0), "anchor")
        self.record("A", 2, "position", 247, 1.2, (1, 0, 0))
        self.record("A", 3, "zone_change", 248, 1.25)
        self.record("A", 4, "position", 248, 1.3, (10, 10, 0), "new_anchor")
        self.record("A", 5, "position", 248, 1.5, (11, 10, 0))
        self.record("A", 6, "discontinuity", 248, 1.6)
        self.record("A", 7, "position", 248, 1.7, (12, 10, 0), "moving")
        self.record("A", 8, "position", 248, 1.9, (13, 10, 0), "moving")
        a = self.run_once(max_events=8)
        self.assertEqual(a["candidate_walk_edges"], 3)
        # Two edges in zone 248 would be wrong: discontinuity breaks one.
        self.assertEqual(self.counts(), (6, 3, 3, 2))
        with sqlite3.connect(self.nav) as db:
            self.assertEqual(
                db.execute("SELECT DISTINCT status FROM walk_edge").fetchall(),
                [("ready",)])

    def test_failure_before_checkpoint_replays_idempotently(self):
        self.record("A", 1, "position", 247, 1.0, (0, 0, 0), "anchor")
        self.record("A", 2, "position", 247, 1.2, (1, 0, 0))
        real_import = miner.import_candidates
        with patch.object(miner, "import_candidates", side_effect=RuntimeError("test failure")):
            with self.assertRaisesRegex(RuntimeError, "test failure"):
                self.run_once()
        self.assertFalse(self.state.exists())
        self.assertEqual(self.counts(), (0, 0, 0, 0))
        a = self.run_once()
        self.assertEqual(a["new_evidence"], 1)
        self.assertEqual(self.counts(), (2, 1, 1, 1))
        # Simulate loss of only the checkpoint (after import, before commit):
        # candidate reimport uses deterministic source IDs; no extra rows.
        self.state.unlink()
        b = self.run_once()
        self.assertEqual(b["new_evidence"], 0)
        self.assertEqual(self.counts(), (2, 1, 1, 1))

    def test_new_recording_after_empty_scan_is_picked_up(self):
        self.record("A", 1, "position", 247, 1.0, (0, 0, 0), "anchor")
        self.run_once()
        self.assertEqual(self.run_once()["processed_events"], 0)
        self.record("B", 1, "position", 249, 2.0, (0, 0, 0), "new_anchor")
        self.record("B", 2, "position", 249, 2.2, (1, 0, 0))
        c = self.run_once()
        self.assertEqual(c["candidate_walk_edges"], 1)
        self.assertEqual(self.counts(), (2, 1, 1, 1))

    def test_original_state_rejects_replaced_capture_database(self):
        self.record("A", 1, "position", 247, 1.0, (0, 0, 0), "anchor")
        self.run_once()
        replacement = self.raw.with_suffix(".new")
        with sqlite3.connect(replacement) as db:
            db.executescript("""
                CREATE TABLE events(session TEXT,sequence INTEGER,event_type TEXT,zone INTEGER,observed_at REAL);
                CREATE TABLE positions(session TEXT,sequence INTEGER,x REAL,y REAL,z REAL,reason TEXT);
            """)
        replacement.replace(self.raw)
        with self.assertRaisesRegex(ValueError, "identity changed"):
            self.run_once()

    def test_out_of_order_late_archival_chunk_fails_without_advancing_cursor(self):
        self.record("A", 1, "position", 247, 1.0, (0, 0, 0), "anchor")
        self.record("A", 3, "position", 247, 1.2, (1, 0, 0))
        first = self.run_once()
        self.assertEqual(first["new_evidence"], 1)
        before = worker.load_state(self.state,
            worker.identity(self.raw)[1], worker.identity(self.nav)[1])[0]
        self.record("A", 2, "position", 247, 1.1, (0.5, 0, 0))
        with self.assertRaisesRegex(ValueError, "OUT_OF_ORDER_SESSION_SEQUENCE"):
            self.run_once()
        after = worker.load_state(self.state,
            worker.identity(self.raw)[1], worker.identity(self.nav)[1])[0]
        self.assertEqual(after, before)
        self.assertEqual(self.counts(), (2, 1, 1, 1))

    def test_no_cross_session_or_invalid_speed_and_direction(self):
        self.record("A", 1, "position", 247, 1.0, (0, 0, 0), "anchor")
        self.record("B", 1, "position", 247, 1.0, (100, 0, 0), "anchor")
        self.record("A", 2, "position", 247, 1.2, (1, 0, 0))
        self.record("B", 2, "position", 247, 1.2, (0, 0, 0))
        self.record("A", 3, "position", 247, 1.4, (2, 0, 0))
        a = self.run_once()
        self.assertEqual(a["candidate_walk_edges"], 2)
        self.assertEqual(self.counts(), (3, 2, 2, 1))
        with sqlite3.connect(self.nav) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM walk_edge "
                                        "WHERE status='executable'").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
