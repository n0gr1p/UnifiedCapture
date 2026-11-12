"""Offline navigation evidence tests: query correctness and fail-closed inputs."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
import navigation
import storage


def observation(session, sequence, event, zone, **extra):
    return dict(session=session, sequence=sequence, event=event, zone=zone,
                character="Terrasjr", time=1794520000.0 + sequence, **extra)


class NavigationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "capture.sqlite3"
        storage.initialize(self.db)
        rows = [
            observation("terrasjr_a", 1, "session_start", 50, schema=2),
            observation("terrasjr_a", 2, "position", 50, x=10., y=20., z=1., reason="anchor"),
            observation("terrasjr_a", 3, "packet_observation", 50, interaction_id=1,
                        interaction_stage="menu_choice", packet_id=91, option_index=3),
            observation("terrasjr_a", 4, "zone_change", 51, old_zone=50, new_zone=51),
            observation("terrasjr_a", 5, "position", 51, x=100., y=200., z=2., reason="anchor"),
            observation("terrasjr_a", 6, "discontinuity", 51, reason="large_position_jump"),
            observation("terrasjr_a", 7, "packet_observation", 51, packet_kind="item_use_request",
                        item_id=28533, item_name="Warp Ring", item_bag=0, item_slot=1),
            observation("terrasjr_a", 8, "zone_change", 52, old_zone=51, new_zone=52),
            observation("terrasjr_a", 9, "position", 52, x=1., y=2., z=3., reason="anchor"),
            observation("terrasjr_a", 10, "session_stop", 52, reason="rotation_duration"),
            observation("terrasjr_b", 1, "session_start", 50, schema=2),
            observation("terrasjr_b", 2, "position", 50, x=11., y=22., z=1.),
            observation("terrasjr_b", 3, "zone_change", 51, old_zone=50, new_zone=51),
            observation("terrasjr_b", 4, "position", 51, x=99., y=198., z=2.),
        ]
        for session in ("terrasjr_a", "terrasjr_b"):
            part = [row for row in rows if row["session"] == session]
            storage.import_batch(self.db, part)

    def test_dataset_and_zone_coverage(self):
        stats = navigation.dataset(self.db)
        self.assertEqual(stats["sessions"], 2)
        self.assertEqual(stats["positions"], 5)
        self.assertEqual(stats["zone_change_events"], 3)
        self.assertEqual(stats["zones_with_positions"], 3)
        zones = navigation.zones(self.db)["zones"]
        self.assertEqual({z["zone"]: (z["samples"], z["sessions"]) for z in zones},
                         {50: (2, 2), 51: (2, 2), 52: (1, 1)})

    def test_transitions_are_observations_not_walkable_edges(self):
        edges = navigation.transitions(self.db)["transitions"]
        self.assertEqual([(e["from_zone"], e["to_zone"], e["observations"])
                          for e in edges], [(50, 51, 2), (51, 52, 1)])
        self.assertEqual([e["to_zone"] for e in
                          navigation.transitions(self.db, from_zone=51)["transitions"]], [52])
        self.assertIn("no walkable edge", navigation.transitions(self.db)["interpretation"])

    def test_session_filter_and_paginated_route_evidence(self):
        listed = navigation.sessions(self.db, zone=52)["sessions"]
        self.assertEqual([s["session"] for s in listed], ["terrasjr_a"])
        first = navigation.route_events(self.db, "terrasjr_a", limit=3)
        self.assertEqual([e["sequence"] for e in first["events"]], [2, 3, 4])
        self.assertTrue(first["has_more"])
        self.assertEqual(first["events"][1]["interaction_stage"], "menu_choice")
        next_page = navigation.route_events(self.db, "terrasjr_a",
                                            after_sequence=first["next_after_sequence"],
                                            include_interactions=False)
        self.assertEqual([e["sequence"] for e in next_page["events"]], [5, 6, 8, 9])
        self.assertNotIn("raw_json", str(first))
        self.assertEqual(navigation.route_events(self.db, "terrasjr_a",
                                                  after_sequence=10)["events"], [])

    def test_invalid_inputs_rejected(self):
        invalid = [
            lambda: navigation.zones(self.db, limit=5000),
            lambda: navigation.zones(self.db, offset=-1),
            lambda: navigation.sessions(self.db, zone="52"),
            lambda: navigation.transitions(self.db, from_zone=-2),
            lambda: navigation.route_events(self.db, "../capture"),
            lambda: navigation.route_events(self.db, "terrasjr_a", after_sequence=-1),
            lambda: navigation.route_events(self.db, "terrasjr_a", limit=10000),
        ]
        for case in invalid:
            with self.subTest(case=case), self.assertRaises(navigation.NavigationQueryError):
                case()
        with self.assertRaisesRegex(navigation.NavigationQueryError, "unknown session"):
            navigation.route_events(self.db, "nonexistent")

    def test_missing_database_not_created(self):
        missing = Path(self.tmp.name) / "does-not-exist.sqlite3"
        with self.assertRaises(Exception):
            navigation.dataset(missing)
        self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()
