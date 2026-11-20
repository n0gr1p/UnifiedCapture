"""NAV-01: bounded tests for the derived navigation SQLite bootstrap."""
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
import derived_navigation_db as nav


class DerivedNavigationSchemaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "nvme" / "navigation.sqlite3"

    def connect(self):
        db = sqlite3.connect(self.path)
        db.execute("PRAGMA foreign_keys=ON")
        self.addCleanup(db.close)
        return db

    def seed_nodes(self, db):
        db.executemany("INSERT INTO zone(zone_id) VALUES (?)", [(100,), (200,)])
        db.executemany(
            "INSERT INTO node(node_id,zone_id,x,y,z) VALUES (?,?,?,?,?)",
            [(1, 100, 0, 0, 0), (2, 100, 10, 0, 0),
             (3, 200, 0, 0, 30)],
        )
        db.commit()

    def test_init_and_reopen_without_publishing(self):
        first = nav.initialize(self.path)
        second = nav.initialize(self.path)
        self.assertEqual(first, second)
        self.assertEqual(first["schema_version"], 2)
        self.assertEqual(first["release_status"], "not_built")
        self.assertIsNone(first["release_id"])
        self.assertFalse(first["raw_telemetry_copied"])
        db = self.connect()
        self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(db.execute("SELECT count(*) FROM node").fetchone()[0], 0)

    def test_refuses_to_initialize_over_raw_telemetry_sqlite(self):
        self.path.parent.mkdir(parents=True)
        raw = sqlite3.connect(self.path)
        raw.execute("CREATE TABLE events(session TEXT)")
        raw.execute("INSERT INTO events VALUES ('irreplaceable')")
        raw.commit()
        raw.close()
        with self.assertRaisesRegex(ValueError, "Refusing unrelated"):
            nav.initialize(self.path)
        check = sqlite3.connect(self.path)
        try:
            self.assertEqual(check.execute("SELECT * FROM events").fetchall(),
                             [("irreplaceable",)])
            self.assertEqual(check.execute(
                "SELECT name FROM sqlite_master WHERE name='node'").fetchall(), [])
        finally:
            check.close()

    def test_walking_edges_are_directed_and_same_zone(self):
        nav.initialize(self.path)
        db = self.connect()
        self.seed_nodes(db)
        db.execute("""INSERT INTO walk_edge
            (edge_id,zone_id,from_node_id,to_node_id,cost,confidence)
            VALUES (1,100,1,2,10,0.8)""")
        self.assertEqual(db.execute(
            "SELECT status FROM walk_edge WHERE edge_id=1").fetchone()[0],
            "ready")
        with self.assertRaises(sqlite3.IntegrityError):
            db.execute("""INSERT INTO walk_edge
                (zone_id,from_node_id,to_node_id,cost,confidence)
                VALUES (100,2,3,10,0.8)""")
        # Insertion of 1->2 must not silently create the reverse 2->1.
        self.assertEqual(db.execute("SELECT COUNT(*) FROM walk_edge").fetchone()[0], 1)

    def test_transports_require_activation_and_evidence_refs_are_exclusive(self):
        nav.initialize(self.path)
        db = self.connect()
        self.seed_nodes(db)
        db.execute("""INSERT INTO transport
            (transport_id,from_node_id,to_node_id,confidence)
            VALUES (1,1,3,0.9)""")
        self.assertEqual(db.execute(
            "SELECT status FROM transport WHERE transport_id=1").fetchone()[0],
            "candidate")
        with self.assertRaises(sqlite3.IntegrityError):
            db.execute("UPDATE transport SET status='executable' WHERE transport_id=1")
        db.execute("""INSERT INTO evidence_ref
            (transport_id,source_session,source_sequence,algorithm_version)
            VALUES (1,'recorded-session',42,'discovery-v1')""")
        with self.assertRaises(sqlite3.IntegrityError):
            db.execute("""INSERT INTO evidence_ref
                (source_session,source_sequence,algorithm_version)
                VALUES ('no-owner',5,'v1')""")

    def test_cli_inspect_is_read_only_and_reports_no_release(self):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "server" / "derived_navigation_db.py"),
             "init", "--db", str(self.path)],
            check=True, capture_output=True, text=True,
        )
        self.assertIn('"release_status": "not_built"', proc.stdout)
        proc = subprocess.run(
            [sys.executable, str(ROOT / "server" / "derived_navigation_db.py"),
             "inspect", "--db", str(self.path)],
            check=True, capture_output=True, text=True,
        )
        self.assertIn('"schema_version": 1', proc.stdout)


if __name__ == "__main__":
    unittest.main()
