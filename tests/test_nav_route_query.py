"""NAV-06: HTTP contract, auth, bounded routing and zero-write tests."""
import http.client
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
import derived_navigation_db
import nav_route_query


TOKEN = "test-token-0123456789-abcdef"


class RabaoRouteQueryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "navigation.sqlite3"
        derived_navigation_db.initialize(self.db_path)
        with sqlite3.connect(self.db_path) as db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("INSERT INTO zone(zone_id) VALUES (247)")
            db.executemany(
                "INSERT INTO node(node_id,zone_id,x,y,z) VALUES (?,247,?,?,?)",
                [(1, 0, 0, 0), (2, 1, 0, 0), (3, 2, 0, 0), (4, 2, 1, 0)],
            )
            db.executemany(
                """INSERT INTO walk_edge(edge_id,zone_id,from_node_id,to_node_id,
                                         cost,confidence,status)
                   VALUES (?,247,?,?,1,0.25,'candidate')""",
                [(101, 1, 2), (102, 2, 3), (103, 3, 4)],
            )
            db.executemany(
                """INSERT INTO evidence_ref(walk_edge_id,source_session,
                                             source_sequence,algorithm_version)
                   VALUES (?, ?, ?, 'walk-candidate-v1')""",
                [(101, "a", 1), (102, "a", 2), (103, "b", 3)],
            )
            db.commit()
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            nav_route_query.make_handler(self.db_path, TOKEN),
        )
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()

    def request(self, body=None, token=TOKEN, *, path="/v1/nav/preview",
                headers=None, method="POST"):
        if headers is None:
            headers = {}
        headers = {"Content-Type": "application/json", **headers}
        if token is not None:
            headers["Authorization"] = "Bearer " + token
        if body is None:
            body = {"zone": 247, "from_xyz": [0, 0, 0],
                    "to_xyz": [2, 1, 0]}
        if not isinstance(body, (bytes, str)):
            body = json.dumps(body)
        conn = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=3,
        )
        try:
            conn.request(method, path, body=body, headers=headers)
            resp = conn.getresponse()
            return resp.status, json.loads(resp.read()), dict(resp.getheaders())
        finally:
            conn.close()

    def test_returns_directed_waypoints_without_modifying_graph(self):
        before = self.db_path.read_bytes()
        code, data, headers = self.request()
        self.assertEqual(code, HTTPStatus.OK)
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["status"], "ROUTE_PREVIEW")
        self.assertFalse(data["approved_for_movement"])
        self.assertFalse(data["preview"]["approved_for_movement"])
        self.assertEqual(data["preview"]["release_status"], "not_built")
        self.assertEqual(data["preview"]["waypoints"],
                         [[0, 0, 0], [1, 0, 0], [2, 0, 0], [2, 1, 0]])
        self.assertEqual(data["preview"]["legs"][0]["edge_status"], "candidate")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(self.db_path.read_bytes(), before)

    def test_reverse_path_is_not_invented(self):
        code, data, _ = self.request({
            "zone": 247, "from_xyz": [2, 1, 0], "to_xyz": [0, 0, 0],
        })
        self.assertEqual(code, HTTPStatus.OK)
        self.assertEqual(data["status"], "NO_ROUTE")
        self.assertEqual(data["reason"], "NO_DIRECTED_PATH")
        self.assertFalse(data["approved_for_movement"])

    def test_unrecorded_goal_is_no_route(self):
        code, data, _ = self.request({
            "zone": 247, "from_xyz": [0, 0, 0], "to_xyz": [20, 0, 0],
        })
        self.assertEqual(code, HTTPStatus.OK)
        self.assertEqual(data["reason"], "NO_NEARBY_NODE")

    def test_rejects_missing_and_wrong_credentials(self):
        for token in (None, "not-a-real-secret"):
            code, data, headers = self.request(token=token)
            self.assertEqual(code, HTTPStatus.UNAUTHORIZED)
            self.assertEqual(data["status"], "UNAUTHORIZED")
            self.assertIn("WWW-Authenticate", headers)
        with self.assertRaisesRegex(ValueError, "24"):
            nav_route_query.make_handler(self.db_path, "short")

    def test_rejects_non_rabao_and_bad_coordinates(self):
        bad = [
            ({"zone": 248, "from_xyz": [0, 0, 0], "to_xyz": [2, 1, 0]},
             "RABAO_ZONE_AND_XYZ_REQUIRED"),
            ({"zone": True, "from_xyz": [0, 0, 0], "to_xyz": [2, 1, 0]},
             "RABAO_ZONE_AND_XYZ_REQUIRED"),
            ({"zone": 247, "from_xyz": "0,0,0", "to_xyz": [2, 1, 0]},
             "INVALID_XYZ"),
            ({"zone": 247, "from_xyz": [0, 0, 0], "to_xyz": [True, 1, 0]},
             "INVALID_XYZ"),
            ({"zone": 247, "from_xyz": [0, 0, 0], "to_xyz": [2, 1, 0],
              "unsafe": True}, "RABAO_ZONE_AND_XYZ_REQUIRED"),
        ]
        for request, reason in bad:
            with self.subTest(reason=reason, request=request):
                code, data, _ = self.request(request)
                self.assertEqual(code, HTTPStatus.BAD_REQUEST)
                self.assertEqual(data["reason"], reason)

    def test_bounded_body_invalid_json_and_no_get(self):
        code, data, _ = self.request(b"x" * 1025)
        self.assertEqual(code, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        self.assertEqual(data["reason"], "REQUEST_TOO_LARGE")
        code, data, _ = self.request(b"{bad json")
        self.assertEqual(code, HTTPStatus.BAD_REQUEST)
        self.assertEqual(data["reason"], "INVALID_JSON")
        code, data, _ = self.request(method="GET")
        self.assertEqual(code, HTTPStatus.METHOD_NOT_ALLOWED)
        self.assertEqual(data["status"], "METHOD_NOT_ALLOWED")

    def test_lan_bind_must_be_specific_private_ip(self):
        self.assertEqual(nav_route_query.bind_address("127.0.0.1"), "127.0.0.1")
        self.assertEqual(nav_route_query.bind_address("192.168.96.45"), "192.168.96.45")
        for address in ("0.0.0.0", "::", "::1", "8.8.8.8",
                        "0.0.0.1", "169.254.1.1", "uuserver"):
            with self.subTest(address=address):
                with self.assertRaisesRegex(ValueError, "private or loopback"):
                    nav_route_query.bind_address(address)

    def test_fails_closed_on_missing_graph(self):
        self.db_path.unlink()
        code, data, _ = self.request()
        self.assertEqual(code, HTTPStatus.SERVICE_UNAVAILABLE)
        self.assertEqual(data["status"], "GRAPH_UNAVAILABLE")
        self.assertNotIn(str(self.db_path), json.dumps(data))


if __name__ == "__main__":
    unittest.main()
