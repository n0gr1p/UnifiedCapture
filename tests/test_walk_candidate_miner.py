"""NAV-03: deterministic one-session candidate walking graph extraction."""
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
import derived_navigation_db as nav
import walk_candidate_miner as miner


class WalkingCandidateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raw = Path(self.tmp.name) / "raw" / "capture.sqlite3"
        self.dst = Path(self.tmp.name) / "nvme" / "navigation.sqlite3"
        self.raw.parent.mkdir()
        self.session = "recorded_character_session_1"
        with sqlite3.connect(self.raw) as db:
            db.executescript("""
                CREATE TABLE events(
                    session TEXT, sequence INTEGER, event_type TEXT,
                    observed_at REAL, zone INTEGER,
                    PRIMARY KEY(session, sequence));
                CREATE TABLE positions(
                    session TEXT, sequence INTEGER, zone INTEGER,
                    x REAL, y REAL, z REAL, reason TEXT,
                    PRIMARY KEY(session, sequence));
            """)
            self.add(db, 1, 0.0, 247, 0, 0, 0, "anchor")
            self.add(db, 2, 0.4, 247, 1, 0, 0, "moving")
            self.add(db, 3, 0.8, 247, 2, 0, 0, "moving")
            self.add(db, 4, 0.9, 247, event="discontinuity")
            self.add(db, 5, 1.2, 247, 100, 0, 0, "new_anchor")
            self.add(db, 6, 1.6, 247, 101, 0, 0, "moving")
            self.add(db, 7, 3.8, 247, 102, 0, 0, "moving")  # time gap
            self.add(db, 8, 4.0, 247, 110, 0, 0, "moving")  # big step
            self.add(db, 9, 4.3, 247, 111, 0, 3, "moving")  # steep elevation
            self.add(db, 10, 4.6, 247, 111.2, 0, 3, "moving")  # tiny
            self.add(db, 11, 5.0, 247, 112, 0, 3, "moving")
            self.add(db, 12, 5.1, 247, event="zone_change")
            self.add(db, 13, 5.4, 247, 113, 0, 3, "moving")
            self.add(db, 14, 5.5, 248, 114, 0, 3, "new_anchor")
            self.add(db, 15, 5.8, 247, 115, 0, 3, "new_anchor")
            self.add(db, 16, 6.2, 247, 116, 0, 3, "moving")
        nav.initialize(self.dst)

    def add(self, db, seq, at, zone, x=None, y=None, z=None, reason=None,
            event="position"):
        db.execute("INSERT INTO events VALUES (?,?,?,?,?)",
                   (self.session, seq, event, at, zone))
        if event == "position":
            db.execute("INSERT INTO positions VALUES (?,?,?,?,?,?,?)",
                       (self.session, seq, zone, x, y, z, reason))

    def test_extract_excludes_teleports_zoning_gaps_speed_and_height(self):
        doc = miner.extract(self.raw, 247, self.session)
        self.assertEqual(doc["format"], 1)
        self.assertEqual(doc["algorithm"], miner.ALGORITHM)
        self.assertEqual({(e["from"]["sequence"], e["to"]["sequence"])
                          for e in doc["edges"]},
                         {(1, 2), (2, 3), (5, 6), (10, 11), (15, 16)})
        self.assertGreater(doc["rejected"]["explicit_boundary"], 0)
        self.assertGreater(doc["rejected"]["time_gap"], 0)
        self.assertGreater(doc["rejected"]["distance_or_speed"], 0)
        self.assertGreater(doc["rejected"]["vertical_gap"], 0)
        self.assertNotIn((13, 15), {(x["from"]["sequence"], x["to"]["sequence"])
                                    for x in doc["edges"]})

    def test_import_is_rebuildable_idempotent_and_not_executable(self):
        doc = miner.extract(self.raw, 247, self.session)
        result = miner.import_candidates(self.dst, doc)
        self.assertEqual(result["candidate_edges"], 5)
        self.assertEqual(result["release_status"], "not_built")
        self.assertEqual(result["new_evidence"], 5)
        with sqlite3.connect(self.dst) as db:
            self.assertEqual(db.execute(
                "SELECT count(*) FROM walk_edge WHERE status='candidate'"
            ).fetchone()[0], 5)
            self.assertEqual(db.execute(
                "SELECT count(*) FROM walk_edge WHERE status='executable'"
            ).fetchone()[0], 0)
            self.assertEqual(db.execute(
                "SELECT count(*) FROM evidence_ref"
            ).fetchone()[0], 5)
            self.assertEqual(db.execute("SELECT count(*) FROM nav_release"
                                        ).fetchone()[0], 0)
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
        second = miner.import_candidates(self.dst, doc)
        self.assertEqual(second["new_nodes"], 0)
        self.assertEqual(second["new_edges"], 0)
        self.assertEqual(second["new_evidence"], 0)
        self.assertEqual(nav.inspect(self.dst)["release_status"], "not_built")

    def test_extract_is_nonmutating_and_bounded(self):
        before = self.raw.read_bytes()
        doc = miner.extract(self.raw, 247, self.session, max_positions=4)
        self.assertEqual(doc["positions_scanned"], 4)
        self.assertEqual(before, self.raw.read_bytes())
        with self.assertRaises(ValueError):
            miner.extract(self.raw, 247, self.session, max_positions=3000)
        empty = miner.extract(self.raw, 247, "no_such_session")
        self.assertEqual(empty["positions_scanned"], 0)
        self.assertEqual(empty["edges"], [])

    def test_refuse_non_navigation_database_and_released_database(self):
        doc = miner.extract(self.raw, 247, self.session)
        with self.assertRaisesRegex(ValueError, "Refusing unrelated"):
            miner.import_candidates(self.raw, doc)
        with sqlite3.connect(self.dst) as db:
            db.execute("""INSERT INTO nav_release(
                slot,release_id,algorithm_version,source_watermark,status,
                build_timestamp) VALUES
                (1,'staged-test','walk-v1','source-1','staging','2026-11-17')""")
        with self.assertRaisesRegex(ValueError, "release"):
            miner.import_candidates(self.dst, doc)

    def test_invalid_document_rolls_back_atomically(self):
        doc = miner.extract(self.raw, 247, self.session)
        bad = json.loads(json.dumps(doc))
        bad["edges"][2]["to"]["x"] = "not-a-number"
        with self.assertRaises(ValueError):
            miner.import_candidates(self.dst, bad)
        with sqlite3.connect(self.dst) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM zone").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM node").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM walk_edge").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
