"""NAV-03: read-only derived SQLite graph reporting with directed reachability."""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
import derived_navigation_db as nav
import nav_graph_report as report


class GraphReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dbfile = Path(self.temp.name) / "navigation.sqlite3"
        nav.initialize(self.dbfile)

    def fixture(self):
        with sqlite3.connect(self.dbfile) as db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("INSERT INTO zone(zone_id, survey_status) VALUES (247,'observed')")
            db.executemany("INSERT INTO node(node_id,zone_id,x,y,z) VALUES (?,247,?,?,?)",
                           [(1,0,0,0),(2,1,0,0),(3,2,0,0),(4,3,0,0),(5,10,0,0)])
            db.executemany("""
                INSERT INTO walk_edge(edge_id,zone_id,from_node_id,to_node_id,
                                      cost,confidence,status,evidence_count)
                VALUES (?,247,?,?,1.0,0.25,'candidate',0)""",
                [(101,1,2),(102,2,3),(103,3,2),(104,3,4)])
            db.executemany("""
                INSERT INTO evidence_ref(
                  walk_edge_id,source_session,source_sequence,algorithm_version)
                VALUES (?,?,?,'walk-candidate-v1')""",
                [(101,"recording1",10),(101,"recording2",11),
                 (102,"recording1",12),(103,"recording1",13)])
            db.commit()

    def test_directed_paths_components_and_repeat_evidence(self):
        self.fixture()
        before = self.dbfile.read_bytes()
        result = report.analyze(self.dbfile, 247)
        self.assertEqual(result["nodes"], 5)
        self.assertEqual(result["directed_walk_edges"], 4)
        self.assertEqual(result["edge_statuses"], {"candidate": 4})
        self.assertEqual(result["evidence_references"], 4)
        self.assertEqual(result["evidence_sessions"], 2)
        self.assertEqual(result["edges_with_repeat_evidence"], 1)
        self.assertEqual(result["edges_seen_in_multiple_sessions"], 1)
        self.assertEqual(result["weak_components"], 2)
        self.assertEqual(result["largest_weak_components"][0]["nodes"], 4)
        self.assertEqual(result["largest_weak_components"][0]["edges"], 4)
        self.assertEqual(result["strong_components"], 4)  # {1},{2,3},{4},{5}
        self.assertEqual(result["largest_strong_component_nodes"], 2)
        self.assertEqual(result["start_nodes_no_incoming"], 1)
        self.assertEqual(result["end_nodes_no_outgoing"], 1)
        self.assertEqual(result["branching_nodes"], 1)
        route = result["top_directed_path_candidates"][0]
        self.assertEqual(route["from"], [0, 0, 0])
        self.assertEqual(route["to"], [3, 0, 0])
        self.assertEqual(route["distance_yalms"], 3.0)
        self.assertEqual(route["directed_edges"], 3)
        self.assertFalse(result["navigation_executable"])
        self.assertEqual(result["release_status"], "not_built")
        self.assertEqual(before, self.dbfile.read_bytes())

    def test_empty_zone_and_identity_guard(self):
        self.assertEqual(report.analyze(self.dbfile, 247)["nodes"], 0)
        self.assertEqual(report.analyze(self.dbfile, 247)["weak_components"], 0)
        self.assertEqual(report.analyze(self.dbfile, 247)["strong_components"], 0)
        self.assertEqual(report.analyze(self.dbfile, 247)["top_directed_path_candidates"], [])
        with self.assertRaises(ValueError):
            report.analyze(self.dbfile, -1)
        with sqlite3.connect(Path(self.temp.name)/"raw.db") as raw:
            raw.execute("CREATE TABLE events(session TEXT)")
        with self.assertRaisesRegex(ValueError, "Refusing unrelated"):
            report.analyze(Path(self.temp.name)/"raw.db", 247)

    def test_long_directed_chain_does_not_recurse(self):
        with sqlite3.connect(self.dbfile) as db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("INSERT INTO zone(zone_id) VALUES (247)")
            db.executemany(
                "INSERT INTO node(node_id,zone_id,x,y,z) VALUES (?,247,?,?,?)",
                [(i,float(i),0,0) for i in range(1,1202)]
            )
            db.executemany("""
                INSERT INTO walk_edge(zone_id,from_node_id,to_node_id,cost,confidence)
                VALUES (247,?,?,1,0.1)""",
                [(i,i+1) for i in range(1,1201)]
            )
            db.commit()
        result = report.analyze(self.dbfile, 247)
        self.assertEqual(result["strong_components"], 1201)
        self.assertEqual(result["weak_components"], 1)
        self.assertEqual(result["top_directed_path_candidates"][0]["directed_edges"], 1200)


if __name__ == "__main__":
    unittest.main()
