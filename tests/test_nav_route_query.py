"""NAV-06: HTTP contract, auth, bounded routing and zero-write tests."""
import http.client
import os
import socket
import subprocess
import json
import sqlite3
import sys
import tempfile
import time
import unittest
from http import HTTPStatus
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
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        listener.close()
        self.port = port
        self.capture_db = Path(self.tmp.name) / "capture.sqlite3"
        self.process = subprocess.Popen(
            [sys.executable, str(ROOT / "server" / "app.py"),
             "--host", "127.0.0.1", "--port", str(port),
             "--db", str(self.capture_db),
             "--archive", str(Path(self.tmp.name) / "archive"),
             "--nav-db", str(self.db_path)],
            env=dict(os.environ, UCAP_API_TOKEN=TOKEN),
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        self.addCleanup(self.stop_process)
        for _ in range(70):
            if self.process.poll() is not None:
                self.fail("Shared ingest API exited during startup")
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=0.3)
                conn.request("GET", "/health")
                resp = conn.getresponse()
                resp.read()
                conn.close()
                if resp.status == 200:
                    break
            except OSError:
                time.sleep(0.1)
        else:
            self.fail("Shared ingest API did not start")

    def stop_process(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process.stderr.close()

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
            "127.0.0.1", self.port, timeout=3,
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
        self.assertEqual(data["preview"]["legs"][0]["edge_id_str"], "101")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(self.db_path.read_bytes(), before)

    def test_read_only_v2_ready_proven_and_failed_route_exclusion(self):
        # Upgrade only this disposable fixture; the actual deployed graph
        # remains v1 until a coordinated, checkpoint-safe operator cutover.
        with sqlite3.connect(self.db_path) as db:
            db.execute("PRAGMA foreign_keys=OFF")
            db.execute("BEGIN IMMEDIATE")
            db.execute("""CREATE TABLE walk_edge_v2 (
                edge_id INTEGER PRIMARY KEY, zone_id INTEGER NOT NULL
                REFERENCES zone(zone_id),
                from_node_id INTEGER NOT NULL, to_node_id INTEGER NOT NULL,
                cost REAL NOT NULL CHECK (cost >= 0),
                confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
                status TEXT NOT NULL DEFAULT 'ready'
                    CHECK (status IN ('ready','proven','failed')),
                evidence_count INTEGER NOT NULL DEFAULT 0,
                CHECK (from_node_id <> to_node_id),
                FOREIGN KEY (zone_id,from_node_id) REFERENCES node(zone_id,node_id),
                FOREIGN KEY (zone_id,to_node_id) REFERENCES node(zone_id,node_id),
                UNIQUE(zone_id,from_node_id,to_node_id))""")
            db.execute("""INSERT INTO walk_edge_v2
                (edge_id,zone_id,from_node_id,to_node_id,cost,confidence,
                 status,evidence_count)
                SELECT edge_id,zone_id,from_node_id,to_node_id,cost,confidence,
                       'ready',evidence_count FROM walk_edge""")
            db.execute("DROP TABLE walk_edge")
            db.execute("ALTER TABLE walk_edge_v2 RENAME TO walk_edge")
            db.execute("CREATE INDEX idx_walk_edge_from ON walk_edge(zone_id,from_node_id,status)")
            db.execute("UPDATE walk_edge SET status='proven' WHERE edge_id=102")
            db.execute("PRAGMA user_version=2")
            db.commit()
        code, result, _ = self.request()
        self.assertEqual(code, 200)
        self.assertEqual(result["status"], "ROUTE_PREVIEW")
        self.assertEqual([leg["edge_status"] for leg in result["preview"]["legs"]],
                         ["ready", "proven", "ready"])
        self.assertFalse(result["approved_for_movement"])
        with sqlite3.connect(self.db_path) as db:
            db.execute("UPDATE walk_edge SET status='failed' WHERE edge_id=102")
        code, result, _ = self.request()
        self.assertEqual(code, 200)
        self.assertEqual(result["status"], "NO_ROUTE")
        self.assertEqual(result["reason"], "NO_DIRECTED_PATH")
        self.assertFalse(result["approved_for_movement"])

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
            self.assertEqual(data["error"], "unauthorized")
        # Same token authenticates telemetry and the graph (no extra token).
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        try:
            conn.request("GET", "/v1/stats", headers={"Authorization": "Bearer " + TOKEN})
            result = conn.getresponse()
            self.assertEqual(result.status, 200)
            self.assertEqual(json.loads(result.read())["sessions"], 0)
        finally:
            conn.close()

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
        self.assertEqual(code, HTTPStatus.NOT_FOUND)
        self.assertEqual(data["error"], "not found")

    def test_graph_is_distinct_from_raw_ingest_database(self):
        before = self.capture_db.read_bytes()
        code, payload, _ = self.request()
        self.assertEqual(code, 200)
        self.assertEqual(payload["status"], "ROUTE_PREVIEW")
        self.assertEqual(self.capture_db.read_bytes(), before)

    def test_auth_precedes_graph_and_rejects_chunked_requests(self):
        self.db_path.unlink()
        code, payload, _ = self.request(token=None)
        self.assertEqual(code, 401)
        self.assertEqual(payload["error"], "unauthorized")
        code, payload, _ = self.request(headers={"Transfer-Encoding": "chunked"})
        self.assertEqual(code, 400)
        self.assertEqual(payload["reason"], "CHUNKED_UNSUPPORTED")

    def test_fails_closed_on_missing_graph(self):
        self.db_path.unlink()
        code, data, _ = self.request()
        self.assertEqual(code, HTTPStatus.SERVICE_UNAVAILABLE)
        self.assertEqual(data["status"], "GRAPH_UNAVAILABLE")
        self.assertNotIn(str(self.db_path), json.dumps(data))


if __name__ == "__main__":
    unittest.main()
