"""NAV-11: archived UC event -> split-privilege stream -> v2 graph states."""
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SERVER = Path(__file__).resolve().parents[1] / "server"
sys.path.insert(0, str(SERVER))
import derived_navigation_db as nav
import nav_incremental_worker as worker
import nav_read_window as reader

EDGE_ID = 4611686018427387903
SESSION = "terrasjr_20261120T140000Z_12345_1"


class OutcomeStreamTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.raw = root / "raw.sqlite3"
        self.graph = root / "graph.sqlite3"
        self.state = root / "cursor.sqlite3"
        with sqlite3.connect(self.raw) as db:
            db.executescript("""
                CREATE TABLE events (
                    session TEXT NOT NULL,sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL,zone INTEGER,
                    observed_at REAL,character TEXT,raw_json TEXT NOT NULL,
                    PRIMARY KEY(session,sequence));
                CREATE TABLE positions (
                    session TEXT,sequence INTEGER,zone INTEGER,
                    x REAL,y REAL,z REAL,reason TEXT,
                    PRIMARY KEY(session,sequence));
            """)
        nav.initialize(self.graph)
        with sqlite3.connect(self.graph) as db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("INSERT INTO zone(zone_id) VALUES (247)")
            db.executemany(
                "INSERT INTO node(node_id,zone_id,x,y,z) VALUES (?,247,?,?,?)",
                [(1,0,0,0),(2,1,0,0)])
            db.execute("""INSERT INTO walk_edge(
                edge_id,zone_id,from_node_id,to_node_id,cost,confidence,
                status,evidence_count) VALUES (?,247,1,2,1,.25,'ready',1)""",
                (EDGE_ID,))
            db.execute("""INSERT INTO evidence_ref(
                walk_edge_id,source_session,source_sequence,
                algorithm_version) VALUES (?,'old-recording',10,'walk-candidate-v1')""",
                (EDGE_ID,))

    def insert(self, seq, kind, *, xyz=None, reason=None, run="tj-run-001",
               eid=None, confirmed=True, zone=247, actor="Terrasjr"):
        obj = {"event": kind,"session": SESSION,"sequence": seq,
               "character": actor,"zone": zone,"time": 1000 + seq * .25}
        if xyz is not None:
            obj.update(zip(("x","y","z"), xyz))
        if kind == "position":
            obj["reason"] = reason or "moving"
        if kind == "nav_edge_outcome":
            kind_value = reason or "traversed"
            obj.update({
                "nav_event_id": eid or ("tj-event-%03d" % seq),
                "nav_run_id": run,
                "nav_edge_id_str": str(EDGE_ID),
                "nav_kind": kind_value,
                "nav_reason": ("completed_edge" if kind_value == "traversed"
                               else "stalled" if kind_value == "blocked"
                               else "combat"),
                "nav_confirmed": confirmed,
                "nav_from_index": 0,"nav_to_index": 1,
            })
        with sqlite3.connect(self.raw) as db:
            db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?)",
                (SESSION,seq,kind,zone,obj["time"],actor,
                 json.dumps(obj,sort_keys=True)))
            if kind == "position":
                db.execute("INSERT INTO positions VALUES (?,?,?,?,?,?,?)",
                    (SESSION,seq,zone,*xyz,obj["reason"]))

    def stream(self, cursor=0, limit=20):
        return reader.emit_window(self.raw,cursor,limit)

    def consume(self, source):
        return worker.consume_stream(
            self.raw,self.graph,self.state,
            json.dumps(source,separators=(",",":")).encode("utf8")
        )

    def state_of_edge(self):
        with sqlite3.connect(self.graph) as db:
            return db.execute(
                "SELECT status FROM walk_edge WHERE edge_id=?",
                (EDGE_ID,)).fetchone()[0]

    def outcome_count(self):
        with sqlite3.connect(self.graph) as db:
            return db.execute("SELECT count(*) FROM edge_outcome").fetchone()[0]

    def test_readonly_archived_event_becomes_proven_with_exact_identity(self):
        self.insert(1,"position",xyz=(0,0,0),reason="anchor")
        self.insert(2,"position",xyz=(.4,0,0))
        self.insert(3,"nav_edge_outcome",xyz=(.9,0,0))
        source=self.stream()
        self.assertEqual(source["stream_version"],2)
        self.assertEqual(len(source["rows"][0]),10)
        self.assertEqual(source["outcomes"][0]["nav_edge_id_str"],str(EDGE_ID))
        original = self.raw.read_bytes()
        report=self.consume(source)
        self.assertEqual(report["outcomes_seen"],1)
        self.assertEqual(report["outcomes_recorded"],1)
        self.assertEqual(self.state_of_edge(),"proven")
        self.assertEqual(self.outcome_count(),1)
        self.assertEqual(worker.source_cursor(
            self.raw,self.graph,self.state),3)
        self.assertEqual(self.raw.read_bytes(),original)
        self.assertEqual(len(source["rows"]),3)

    def test_blockage_requires_two_distinct_runs_and_interrupt_neutral(self):
        self.insert(1,"nav_edge_outcome",xyz=(.35,0,0),
                    reason="interrupted",confirmed=False)
        self.insert(2,"nav_edge_outcome",xyz=(.35,0,0),
                    reason="blocked",run="tj-run-002")
        self.insert(3,"nav_edge_outcome",xyz=(.35,0,0),
                    reason="blocked",run="tj-run-003")
        source=self.stream()
        self.consume(source)
        self.assertEqual(self.state_of_edge(),"failed")
        self.assertEqual(self.outcome_count(),3)
        self.assertEqual(worker.source_cursor(
            self.raw,self.graph,self.state),3)

    def test_crash_before_cursor_is_idempotent(self):
        self.insert(1,"nav_edge_outcome",xyz=(1,0,0))
        src=self.stream()
        with patch.object(worker,"store_state",side_effect=RuntimeError(
                "simulated crash")):
            with self.assertRaisesRegex(RuntimeError,"simulated crash"):
                self.consume(src)
        self.assertEqual(self.state_of_edge(),"proven")
        self.assertEqual(self.outcome_count(),1)
        self.assertFalse(self.state.exists())
        repeated=self.consume(src)
        self.assertEqual(repeated["outcomes_recorded"],0)
        self.assertEqual(self.outcome_count(),1)
        self.assertEqual(worker.source_cursor(
            self.raw,self.graph,self.state),1)

    def test_missing_or_forged_outcome_never_advances_cursor(self):
        self.insert(1,"nav_edge_outcome",xyz=(1,0,0))
        good=self.stream()
        bad=json.loads(json.dumps(good))
        bad.pop("outcomes")
        with self.assertRaisesRegex(ValueError,"missing from"):
            self.consume(bad)
        bad=json.loads(json.dumps(good))
        bad["outcomes"][0]["character"]="Redrogue"
        with self.assertRaisesRegex(ValueError,"source identity"):
            self.consume(bad)
        bad=json.loads(json.dumps(good))
        bad["outcomes"][0]["nav_edge_id_str"]="9007199254740992.0"
        with self.assertRaisesRegex(ValueError,"directed edge"):
            self.consume(bad)
        bad=json.loads(json.dumps(good))
        bad["outcomes"][0]["x"]=10
        with self.assertRaisesRegex(ValueError,"near directed endpoint"):
            self.consume(bad)
        bad=json.loads(json.dumps(good))
        bad["stream_version"]=1
        bad.pop("outcomes")
        with self.assertRaisesRegex(ValueError,"missing from"):
            self.consume(bad)
        self.assertFalse(self.state.exists())
        self.assertEqual(self.state_of_edge(),"ready")
        self.assertEqual(self.outcome_count(),0)

    def test_direct_legacy_reader_refuses_to_skip_outcome(self):
        self.insert(1,"nav_edge_outcome",xyz=(1,0,0))
        with self.assertRaisesRegex(ValueError,"REQUIRES_V2_SPLIT_READER"):
            worker.run_once(self.raw,self.graph,self.state)
        self.assertFalse(self.state.exists())

    def test_unprivileged_worker_never_opens_raw_sqlite(self):
        self.insert(1,"nav_edge_outcome",xyz=(1,0,0))
        payload=self.stream()
        original=sqlite3.connect
        def guard(path,*args,**kwargs):
            if str(self.raw) in str(path):
                raise AssertionError("Reader privilege boundary violated")
            return original(path,*args,**kwargs)
        with patch.object(sqlite3,"connect",side_effect=guard):
            self.assertEqual(self.consume(payload)["outcomes_recorded"],1)
        self.assertEqual(self.state_of_edge(),"proven")


if __name__ == "__main__":
    unittest.main()
