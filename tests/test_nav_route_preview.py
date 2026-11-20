"""NAV-03 route-preview tests: directionality, snap, detour and provenance."""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
import derived_navigation_db as nav
import nav_route_preview as preview


class RoutePreviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dst = Path(self.tmp.name) / "nav.sqlite3"
        nav.initialize(self.dst)
        with sqlite3.connect(self.dst) as db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("INSERT INTO zone(zone_id) VALUES (247)")
            db.executemany("""
                INSERT INTO node(node_id,zone_id,x,y,z) VALUES (?,247,?,?,?)""",
                [(1, 0, 0, 0), (2, 1, 0, 0), (3, 2, 0, 0),
                 (4, 2, 1, 0), (5, 10, 0, 0)])
            db.executemany("""
                INSERT INTO walk_edge(edge_id,zone_id,from_node_id,to_node_id,
                                      cost,confidence,status)
                VALUES (?,247,?,?,?,0.25,'ready')""",
                [(101,1,2,1),(102,2,3,1),(103,3,4,1),
                 (104,4,3,1),(105,1,4,5)])
            db.executemany("""
                INSERT INTO evidence_ref(walk_edge_id,source_session,
                                         source_sequence,algorithm_version)
                VALUES (?,?,?,'walk-candidate-v1')""",
                [(101,'session_a',2), (101,'session_b',20),
                 (102,'session_a',3), (103,'session_b',25),
                 (104,'session_b',26), (105,'session_c',100)])
            db.commit()

    def test_shortest_directed_path_and_evidence_are_readonly(self):
        before = self.dst.read_bytes()
        result = preview.preview(self.dst, 247, "0,0,0", "2,1,0")
        self.assertEqual(result["preview_schema_version"], 1)
        self.assertEqual(result["distance_yalms"], 3)
        self.assertEqual(result["waypoints"], [[0,0,0],[1,0,0],[2,0,0],[2,1,0]])
        self.assertEqual(result["waypoint_count"], 4)
        self.assertEqual(result["edges_seen_in_multiple_recording_sessions"], 1)
        self.assertEqual(result["legs"][0]["observations"], 2)
        self.assertEqual(result["legs"][0]["edge_status"], "ready")
        self.assertEqual(result["legs"][0]["edge_id_str"],
                         str(result["legs"][0]["edge_id"]))
        self.assertEqual(result["legs"][0]["distinct_recording_sessions"], 2)
        self.assertEqual(result["edges_without_evidence"], 0)
        self.assertEqual(result["release_status"], "not_built")
        self.assertFalse(result["approved_for_movement"])
        self.assertEqual(self.dst.read_bytes(), before)

    def test_proven_wins_exact_distance_tie_without_longer_detour(self):
        # Two directed 3-yalm paths, only the upper one is PROVEN.
        # It is preferred without sacrificing the shortest real distance.
        with sqlite3.connect(self.dst) as db:
            db.execute("PRAGMA foreign_keys=ON")
            db.executemany("""INSERT INTO node(node_id,zone_id,x,y,z)
                VALUES (?,247,?,?,?)""", [(6,0,1,0),(7,1,1,0)])
            db.executemany("""INSERT INTO walk_edge(
                edge_id,zone_id,from_node_id,to_node_id,cost,confidence,status,
                evidence_count) VALUES (?,247,?,?,1,0.25,'proven',1)""",
                [(106,1,6),(107,6,7),(108,7,4)])
            db.executemany("""INSERT INTO evidence_ref(
                walk_edge_id,source_session,source_sequence,algorithm_version)
                VALUES (?,'uc-recording',?,'walk-candidate-v1')""",
                [(106,30),(107,31),(108,32)])
        p = preview.preview(self.dst,247,(0,0,0),(2,1,0))
        self.assertEqual(p["distance_yalms"], 3)
        self.assertEqual(p["waypoints"],
                         [[0,0,0],[0,1,0],[1,1,0],[2,1,0]])
        self.assertEqual([x["edge_status"] for x in p["legs"]],
                         ["proven"] * 3)
        self.assertFalse(p["approved_for_movement"])

    def test_never_invents_reverse_or_cross_zone_path(self):
        with self.assertRaisesRegex(ValueError, "No directed"):
            preview.preview(self.dst, 247, "2,1,0", "0,0,0")
        with self.assertRaisesRegex(ValueError, "No recorded navigation node"):
            preview.preview(self.dst, 247, "0,0,0", "50,0,0")
        with self.assertRaisesRegex(ValueError, "No recorded navigation node"):
            preview.preview(self.dst, 248, "0,0,0", "1,0,0")

    def test_snapping_and_identity_bounds(self):
        r = preview.preview(self.dst, 247, (0.2,0,0), (2,0.15,0))
        self.assertEqual(r["source_snapped"], [0,0,0])
        self.assertEqual(r["destination_snapped"], [2,0,0])
        self.assertAlmostEqual(r["source_snap_yalms"], 0.2)
        with self.assertRaisesRegex(ValueError, "same node"):
            preview.preview(self.dst, 247, (0,0,0), (0.1,0,0))
        with self.assertRaisesRegex(ValueError, "Invalid zone"):
            preview.preview(self.dst, -1, (0,0,0), (1,0,0))
        with self.assertRaisesRegex(ValueError, "finite"):
            preview.preview(self.dst, 247, "NaN,0,0", "1,0,0")
        with self.assertRaisesRegex(ValueError, "Invalid route bounds"):
            preview.preview(self.dst, 247, (0,0,0), (1,0,0), snap_radius=50)

    def test_detour_guard_rejects_long_route(self):
        with sqlite3.connect(self.dst) as db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("DELETE FROM evidence_ref WHERE walk_edge_id=102")
            db.execute("DELETE FROM walk_edge WHERE edge_id=102")
            db.execute("UPDATE walk_edge SET cost=100 WHERE edge_id=105")
            db.execute("INSERT INTO node(node_id,zone_id,x,y,z) VALUES (6,247,0,10,0)")
            db.execute("INSERT INTO node(node_id,zone_id,x,y,z) VALUES (7,247,2,10,0)")
            db.executemany("""
                INSERT INTO walk_edge(edge_id,zone_id,from_node_id,to_node_id,
                                      cost,confidence) VALUES (?,247,?,?,?,0.25)""",
                [(201,1,6,10),(202,6,7,2),(203,7,3,10)])
            db.commit()
        with self.assertRaisesRegex(ValueError, "detour"):
            preview.preview(self.dst, 247, (0,0,0), (2,0,0))
        valid = preview.preview(self.dst, 247, (0,0,0), (2,0,0),
                                max_stretch=20)
        self.assertGreater(valid["distance_yalms"], 20)
        self.assertFalse(valid["approved_for_movement"])

    def test_no_movements_from_unrecognized_database(self):
        raw = Path(self.tmp.name) / "capture.sqlite3"
        with sqlite3.connect(raw) as db:
            db.execute("CREATE TABLE events(session TEXT)")
        with self.assertRaisesRegex(ValueError, "Refusing unrelated"):
            preview.preview(raw, 247, "0,0,0", "1,0,0")


if __name__ == "__main__":
    unittest.main()
