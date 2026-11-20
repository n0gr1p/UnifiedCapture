"""Copy-only v1 navigation graph and NAV-10 checkpoint consistency tests."""
import fcntl
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
import derived_navigation_db as nav
import nav_v2_handoff
import nav_incremental_worker as worker


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        folder = Path(self.temp.name)
        self.raw = folder / "raw.sqlite3"
        self.old = folder / "nav-v1.sqlite3"
        self.old_state = folder / "worker-v1.sqlite3"
        self.new = folder / "nav-v2.sqlite3"
        self.new_state = folder / "worker-v2.sqlite3"
        with sqlite3.connect(self.raw) as db:
            db.execute("CREATE TABLE events(session TEXT)")
            db.execute("CREATE TABLE positions(session TEXT)")
        with sqlite3.connect(self.old) as db:
            schema = nav.SCHEMA_SQL
            start = schema.index("CREATE TABLE edge_outcome (")
            end = schema.index("-- A transport may", start)
            schema = schema[:start] + schema[end:]
            schema = schema.replace(
                "status TEXT NOT NULL DEFAULT 'ready'\n"
                "        CHECK (status IN ('ready', 'proven', 'failed'))",
                "status TEXT NOT NULL DEFAULT 'candidate'\n"
                "        CHECK (status IN ('candidate', 'validated', 'executable'))",1)
            db.executescript(schema)
            db.execute("PRAGMA application_id=" + str(nav.APPLICATION_ID))
            db.execute("PRAGMA user_version=1")
            db.execute("INSERT INTO zone(zone_id) VALUES (247)")
            db.executemany("INSERT INTO node(node_id,zone_id,x,y,z) "
                           "VALUES (?,247,?,?,?)", [(1,0,0,0), (2,1,0,0)])
            db.execute("""INSERT INTO walk_edge(
                edge_id,zone_id,from_node_id,to_node_id,cost,confidence,status,
                evidence_count) VALUES (100,247,1,2,1,0.25,'candidate',1)""")
            db.execute("""INSERT INTO evidence_ref(
                walk_edge_id,source_session,source_sequence,algorithm_version)
                VALUES (100,'session-01',2,'walk-candidate-v1')""")
        self.raw_id = worker.identity(self.raw)[1]
        self.old_id = worker.identity(self.old)[1]
        self.anchor = {"session-01": (247,2,1.5,1.0,0.0,0.0,"moving")}
        worker.store_state(self.old_state, self.raw_id, self.old_id, 37,
                           self.anchor)

    def test_preserves_cursor_and_anchors_across_new_graph_inode(self):
        before_graph = self.old.read_bytes()
        before_state = self.old_state.read_bytes()
        r = nav_v2_handoff.handoff_copy(
            self.raw,self.old,self.old_state,self.new,self.new_state)
        self.assertEqual(r["schema_version"], 2)
        self.assertEqual(r["ready_edges"], 1)
        self.assertEqual(r["committed_raw_event_rowid"], 37)
        self.assertEqual(r["preserved_session_anchors"], 1)
        self.assertFalse(r["switched_live_services"])
        self.assertEqual(self.old.read_bytes(), before_graph)
        self.assertEqual(self.old_state.read_bytes(), before_state)
        self.assertEqual(worker.load_state(
            self.new_state, self.raw_id,worker.identity(self.new)[1]),
            (37,self.anchor))
        with sqlite3.connect(self.new) as db:
            self.assertEqual(db.execute(
                "SELECT status FROM walk_edge WHERE edge_id=100"
            ).fetchone()[0],"ready")
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(),[])
        with self.assertRaises(FileExistsError):
            nav_v2_handoff.handoff_copy(
                self.raw,self.old,self.old_state,self.new,self.new_state)

    def test_rejects_active_worker_lock_without_copying(self):
        lock_path = Path(str(self.old_state)+".lock")
        with lock_path.open("a") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                with self.assertRaisesRegex(ValueError, "worker active"):
                    nav_v2_handoff.handoff_copy(
                        self.raw,self.old,self.old_state,self.new,self.new_state)
                self.assertFalse(self.new.exists())
                self.assertFalse(self.new_state.exists())
            finally:
                fcntl.flock(f.fileno(),fcntl.LOCK_UN)

    def test_mismatched_worker_ledger_is_rejected(self):
        with sqlite3.connect(self.old_state) as db:
            db.execute("UPDATE mining_cursor SET nav_identity='wrong'")
        with self.assertRaisesRegex(ValueError,"identity changed"):
            nav_v2_handoff.handoff_copy(
                self.raw,self.old,self.old_state,self.new,self.new_state)
        self.assertFalse(self.new.exists())
        self.assertFalse(self.new_state.exists())


if __name__ == "__main__":
    unittest.main()
