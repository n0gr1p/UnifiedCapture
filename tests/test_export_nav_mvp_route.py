"""Standalone route CSV MVP export contract tests."""
import copy
import importlib.util
import sys
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "export_nav_mvp_route.py"
spec = importlib.util.spec_from_file_location("export_nav_mvp_route", SCRIPT)
exporter = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = exporter
spec.loader.exec_module(exporter)


def route():
    return {
        "preview_schema_version": 1,
        "zone": 247,
        "approved_for_movement": False,
        "release_status": "not_built",
        "waypoints": [[0,0,0], [1,0,0], [2,0,0]],
        "waypoint_count": 3,
        "distance_yalms": 2.0,
        "detour_ratio": 1.0,
        "edges_without_evidence": 0,
        "legs": [
            {"from_index":0,"to_index":1,"edge_status":"candidate",
             "observations":1,"length_yalms":1.0},
            {"from_index":1,"to_index":2,"edge_status":"candidate",
             "observations":2,"length_yalms":1.0},
        ]
    }


class ExportRouteTests(unittest.TestCase):
    def test_csv_export(self):
        self.assertEqual(
            exporter.export(route()),
            "# UC_NAV_MVP_V1 zone=247\n0.00,0.00,0.00\n"
            "1.00,0.00,0.00\n2.00,0.00,0.00\n"
        )

    def test_rejects_unobserved_jump(self):
        r = route()
        r["waypoints"][1] = [200,0,0]
        with self.assertRaises(ValueError):
            exporter.export(r)
        r = route()
        r["legs"][1]["observations"] = 0
        with self.assertRaises(ValueError):
            exporter.export(r)

    def test_rejects_unsupported_contract(self):
        for field, value in (
            ("preview_schema_version", 2),
            ("zone", 248),
            ("approved_for_movement", True),
            ("release_status", "published"),
            ("edges_without_evidence", 1),
            ("distance_yalms", 500),
        ):
            with self.subTest(field=field):
                r = route()
                r[field] = value
                with self.assertRaises(ValueError):
                    exporter.export(r)

    def test_rejects_reverse_leg(self):
        r = route()
        r["legs"][1]["to_index"] = 0
        with self.assertRaises(ValueError):
            exporter.export(r)


if __name__ == "__main__":
    unittest.main()
