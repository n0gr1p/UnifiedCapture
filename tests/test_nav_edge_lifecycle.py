"""NAV-04 READY -> PROVEN / FAILED lifecycle and offline v1 migration."""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
import derived_navigation_db as nav
import nav_edge_lifecycle as lifecycle
import nav_edge_migrate as migration
import nav_route_preview as routes
import walk_candidate_miner as miner


class ReadyProvenFailedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "navigation-v2.sqlite3"
        nav.initialize(self.db_path)
        self.doc = {
            "format": miner.FORMAT_VERSION,
            "algorithm": miner.ALGORITHM, "zone": 247, "session": "historical-1",
            "edges": [
                {"from": {"sequence": 1, "x": 0, "y": 0, "z": 0},
                 "to": {"sequence": 2, "x": 1, "y": 0, "z": 0}},
                {"from": {"sequence": 2, "x": 1, "y": 0, "z": 0},
                 "to": {"sequence": 3, "x": 2, "y": 0, "z": 0}}
            ]
        }
        miner.import_candidates(self.db_path, self.doc)
        with sqlite3.connect(self.db_path) as db:
            self.edge_id = db.execute("""
                SELECT e.edge_id FROM walk_edge AS e
                JOIN node AS n ON n.node_id=e.from_node_id
                WHERE n.zone_id=247 AND n.x=0 AND n.y=0 AND n.z=0
            """).fetchone()[0]

    def state(self):
        with sqlite3.connect(self.db_path) as db:
            return db.execute("SELECT status FROM walk_edge WHERE edge_id=?",
                              (self.edge_id,)).fetchone()[0]

    def report(self, eid, rid, kind, reason, confirmed=True):
        return lifecycle.record_outcome(
            self.db_path, edge_id=self.edge_id, event_id=eid,
            run_id=rid, kind=kind, reason=reason, confirmed=confirmed
        )

    def test_filtered_ingestion_makes_ready_not_proven(self):
        self.assertEqual(self.state(), "ready")
        p = routes.preview(self.db_path, 247, (0,0,0), (2,0,0))
        self.assertEqual([x["edge_status"] for x in p["legs"]],
                         ["ready", "ready"])
        self.assertFalse(p["approved_for_movement"])
        self.assertEqual(nav.inspect(self.db_path)["release_status"], "not_built")

    def test_traversed_marks_proven_and_idempotent_event(self):
        r = self.report("event-succ-01", "route-0001",
                        "traversed", "completed_edge")
        self.assertEqual(r["state"], "proven")
        self.assertTrue(r["recorded"])
        again = self.report("event-succ-01", "route-0001",
                            "traversed", "completed_edge")
        self.assertFalse(again["recorded"])
        self.assertEqual(self.state(), "proven")
        with sqlite3.connect(self.db_path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM edge_outcome"
                                        ).fetchone()[0], 1)
        with self.assertRaisesRegex(ValueError, "Conflicting duplicate"):
            self.report("event-succ-01", "route-0002",
                        "traversed", "completed_edge")

    def test_two_distinct_confirmed_blocks_fail_but_interruptions_do_not(self):
        interrupted = self.report("event-int-01", "route-0001",
                                  "interrupted", "combat", confirmed=False)
        self.assertEqual(interrupted["state"], "ready")
        first = self.report("event-blk-01", "route-0002",
                            "blocked", "obstacle")
        self.assertEqual(first["confirmed_block_runs_since_success"], 1)
        self.assertEqual(self.state(), "ready")
        with self.assertRaisesRegex(ValueError, "Duplicate blocked"):
            self.report("event-blk-02", "route-0002", "blocked", "stalled")
        self.assertEqual(self.state(), "ready")
        second = self.report("event-blk-03", "route-0003",
                             "blocked", "diverged")
        self.assertEqual(second["confirmed_block_runs_since_success"], 2)
        self.assertEqual(self.state(), "failed")
        with self.assertRaisesRegex(ValueError, "No directed"):
            routes.preview(self.db_path, 247, (0,0,0), (2,0,0))
        # The historical miner may learn more evidence but must NEVER
        # silently turn a failed edge back into READY.
        miner.import_candidates(self.db_path, self.doc)
        self.assertEqual(self.state(), "failed")
        third = self.report("event-int-02", "route-0004",
                            "interrupted", "casting", confirmed=False)
        self.assertEqual(third["state"], "failed")

    def test_old_success_replay_does_not_override_newer_failed_state(self):
        self.report("event-succ-01", "route-0001",
                    "traversed", "completed_edge")
        self.assertEqual(self.state(), "proven")
        self.report("event-blk-01", "route-0002", "blocked", "stalled")
        self.report("event-blk-02", "route-0003", "blocked", "obstacle")
        self.assertEqual(self.state(), "failed")
        replay = self.report("event-succ-01", "route-0001",
                             "traversed", "completed_edge")
        self.assertFalse(replay["recorded"])
        self.assertEqual(replay["state"], "failed")
        self.assertEqual(self.state(), "failed")

    def test_confirmed_recovery_resets_fail_count_to_proven(self):
        self.report("event-blk-01", "route-0001", "blocked", "stalled")
        self.report("event-blk-02", "route-0002", "blocked", "obstacle")
        self.assertEqual(self.state(), "failed")
        recovered = self.report("event-succ-01", "route-0003",
                                "traversed", "completed_edge")
        self.assertEqual(recovered["state"], "proven")
        self.assertEqual(recovered["confirmed_block_runs_since_success"], 0)
        p = routes.preview(self.db_path, 247, (0,0,0), (2,0,0))
        self.assertEqual(p["legs"][0]["edge_status"], "proven")
        self.assertFalse(p["approved_for_movement"])

    def test_invalid_outcomes_cannot_poison_graph(self):
        for kwargs in (
            dict(eid="short", rid="route-0001", kind="blocked", reason="obstacle"),
            dict(eid="event-blk-11", rid="route-0001",
                 kind="blocked", reason="combat"),
            dict(eid="event-blk-12", rid="route-0001",
                 kind="blocked", reason="obstacle", confirmed=False),
            dict(eid="event-blk-13", rid="route-0001",
                 kind="traversed", reason="completed_edge", confirmed=False),
            dict(eid="event-blk-14", rid="route-0001",
                 kind="interrupted", reason="obstacle"),
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.report(kwargs["eid"], kwargs["rid"], kwargs["kind"],
                            kwargs["reason"], kwargs.get("confirmed", True))
        self.assertEqual(self.state(), "ready")
        with sqlite3.connect(self.db_path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM edge_outcome"
                                        ).fetchone()[0], 0)

    def test_migration_refuses_overwrite_and_preserves_provenance(self):
        v1 = Path(self.tmp.name) / "v1.sqlite3"
        with sqlite3.connect(v1) as db:
            schema = nav.SCHEMA_SQL
            start = schema.index("CREATE TABLE edge_outcome (")
            end = schema.index("-- A transport may", start)
            schema = schema[:start] + schema[end:]
            schema = schema.replace(
                "status TEXT NOT NULL DEFAULT 'ready'\n"
                "        CHECK (status IN ('ready', 'proven', 'failed'))",
                "status TEXT NOT NULL DEFAULT 'candidate'\n"
                "        CHECK (status IN ('candidate', 'validated', 'executable'))",
                1)
            db.executescript(schema)
            db.execute("PRAGMA application_id=" + str(nav.APPLICATION_ID))
            db.execute("PRAGMA user_version=1")
            db.execute("INSERT INTO zone(zone_id) VALUES (247)")
            db.executemany("INSERT INTO node(node_id,zone_id,x,y,z) "
                           "VALUES (?,247,?,?,?)",
                           [(1,0,0,0), (2,1,0,0)])
            db.execute("""INSERT INTO walk_edge(
                edge_id,zone_id,from_node_id,to_node_id,cost,confidence,status,
                evidence_count) VALUES (100,247,1,2,1,0.25,'candidate',1)""")
            db.execute("""INSERT INTO evidence_ref(
                walk_edge_id,source_session,source_sequence,algorithm_version)
                VALUES (100,'historical-1',2,'walk-candidate-v1')""")
        before = v1.read_bytes()
        target = Path(self.tmp.name) / "navigation-upgraded.sqlite3"
        report = migration.migrate_copy(v1, target)
        self.assertEqual(report["ready_edges"], 1)
        self.assertEqual(report["preserved_evidence"], 1)
        self.assertEqual(before, v1.read_bytes())
        with self.assertRaises(FileExistsError):
            migration.migrate_copy(v1, target)
        with sqlite3.connect(target) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT status FROM walk_edge"
                                        ).fetchone()[0], "ready")
            self.assertEqual(db.execute("SELECT source_session,source_sequence "
                                        "FROM evidence_ref").fetchone(),
                             ("historical-1",2))
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertFalse(routes.preview(target,247,(0,0,0),(1,0,0))
                         ["approved_for_movement"])


if __name__ == "__main__":
    unittest.main()
