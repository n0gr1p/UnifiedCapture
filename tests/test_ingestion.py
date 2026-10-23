"""End-to-end tests for uploader, raw archive, SQLite and authenticated API."""
import hashlib
import importlib.util
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT / "client"))
import storage
import uploader


def event(session, sequence, kind, **fields):
    return dict(session=session, sequence=sequence, event=kind,
                time=1792788000.0 + sequence, character="Niightsiide",
                zone=266, **fields)


def ndjson(*rows):
    return b"".join(json.dumps(r, separators=(",", ":")).encode("utf-8") + b"\n" for r in rows)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "data.sqlite3"
        self.archive = Path(self.tmp.name) / "archive"
        storage.initialize(self.db)
        self.rows = [
            event("niightsiide_20261023T220846Z_139221", 1, "session_start",
                  schema=2, recorder_version="0.2.2"),
            event("niightsiide_20261023T220846Z_139221", 2, "position",
                  reason="moving", x=1.2, y=2.3, z=3.4),
            event("niightsiide_20261023T220846Z_139221", 3, "entity_observation",
                  observation_kind="first_seen", entity_id=17850757,
                  entity_index=4, entity_epoch=1, entity_kind="monster",
                  entity_name="Cliffclinger Toad", entity_name_source="windower_mob",
                  entity_x=3.0, entity_y=4.0, entity_z=5.0,
                  observer_x=1.0, observer_y=2.0, observer_z=3.0),
            event("niightsiide_20261023T220846Z_139221", 4, "packet_observation",
                  interaction_id=1, interaction_stage="menu_choice",
                  interaction_target_id=999, interaction_menu_id=88,
                  packet_id=91, option_index=7),
        ]
        self.raw = ndjson(*self.rows)

    def test_archive_normalized_tables_and_replay(self):
        r = storage.ingest(self.db, self.archive, self.raw, "capture.jsonl")
        self.assertEqual((r["inserted"], r["duplicates"], r["count"]), (4, 0, 4))
        self.assertEqual((self.archive / "batches" / (r["sha256"] + ".jsonl")).read_bytes(), self.raw)
        r2 = storage.ingest(self.db, self.archive, self.raw, "capture.jsonl")
        self.assertEqual((r2["inserted"], r2["duplicates"]), (0, 4))
        counts = storage.stats(self.db)
        self.assertEqual([counts[k] for k in ("sessions", "events", "positions",
                          "entity_observations", "interactions", "distinct_monsters")],
                         [1, 4, 1, 1, 1, 1])
        with sqlite3.connect(self.db) as db:
            name = db.execute("SELECT entity_name FROM entity_observations").fetchone()[0]
            self.assertEqual(name, "Cliffclinger Toad")
            schema = db.execute("SELECT recorder_version FROM sessions").fetchone()[0]
            self.assertEqual(schema, "0.2.2")
        self.assertEqual(storage.recover(self.db, self.archive), 0)

    def test_resume_partial_batches_and_restore(self):
        first = ndjson(*self.rows[:2])
        second = ndjson(*self.rows[2:])
        storage.ingest(self.db, self.archive, first)
        # Archive evidence exists even if the SQLite import never completed.
        storage.archive_bytes(self.archive, second)
        self.assertEqual(storage.recover(self.db, self.archive), 2)
        self.assertEqual(storage.stats(self.db)["events"], 4)

    def test_validation_and_conflicts(self):
        bad = [
            self.raw[:-1], self.raw + b"\x00",
            ndjson(dict(self.rows[0], session="../escape")),
            ndjson(self.rows[0], self.rows[0]),
            ndjson(dict(self.rows[0], sequence=True)),
            ndjson(dict(self.rows[0], time=float("nan"))),
        ]
        for raw in bad:
            with self.subTest(raw=raw[:64]), self.assertRaises(storage.IngestError):
                storage.ingest(self.db, self.archive, raw)
        self.assertEqual(storage.stats(self.db)["events"], 0)
        storage.ingest(self.db, self.archive, self.raw)
        altered = ndjson(dict(self.rows[0], character="Different"))
        with self.assertRaisesRegex(storage.IngestError, "Conflicting"):
            storage.ingest(self.db, self.archive, altered)
        # Conflicting retries must not poison permanent recovery evidence.
        self.assertEqual(len(list((self.archive / "batches").glob("*.jsonl"))), 1)
        self.assertEqual(storage.recover(self.db, self.archive), 0)
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM imported_batches").fetchone()[0], 1)


class UploaderTests(StoreTests):
    def test_ignore_partial_line_and_resume(self):
        capture = Path(self.tmp.name) / "captures"
        capture.mkdir()
        file = capture / "niightsiide_20261023T220846Z_139221.jsonl"
        raw = ndjson(*self.rows[:2])
        partial = json.dumps(self.rows[2]).encode("utf-8")
        file.write_bytes(raw + partial)
        chunk, offset, lines = uploader.read_batch(file, 0)
        self.assertEqual((chunk, offset, lines), (raw, len(raw), 2))
        with file.open("ab") as fp:
            fp.write(b"\n")
        next_chunk, new_offset, next_lines = uploader.read_batch(file, offset)
        self.assertEqual(next_lines, 1)
        self.assertEqual(next_chunk, partial + b"\n")
        self.assertEqual(new_offset, file.stat().st_size)

    def test_failed_ack_never_advances_checkpoint(self):
        capture = Path(self.tmp.name) / "captures"
        capture.mkdir()
        file = capture / "capture.jsonl"
        file.write_bytes(self.raw)
        state_path = Path(self.tmp.name) / "checkpoint.json"
        state = uploader.load_state(state_path)
        original = uploader.upload
        try:
            uploader.upload = lambda *args, **kwargs: (_ for _ in ()).throw(OSError("offline"))
            with self.assertRaises(OSError):
                uploader.upload_file(file, state, state_path, "http://localhost:1", "test-token")
            self.assertFalse(state_path.exists())
            uploader.upload = lambda url, token, source, raw, records, timeout: storage.ingest(
                self.db, self.archive, raw, source)
            count = uploader.upload_file(file, state, state_path, "http://unused", "test-token")
            self.assertEqual(count, 4)
            self.assertEqual(uploader.load_state(state_path)["files"][str(file.resolve())]["offset"],
                             len(self.raw))
            self.assertEqual(uploader.upload_file(file, state, state_path,
                                                   "http://unused", "test-token"), 0)
        finally:
            uploader.upload = original


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "db.sqlite3"
        self.archive = Path(self.tmp.name) / "archive"
        self.token = "this-is-a-unique-long-test-token"
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        listener.close()
        self.base = "http://127.0.0.1:" + str(port)
        self.process = subprocess.Popen(
            [sys.executable, str(ROOT / "server" / "app.py"), "--host", "127.0.0.1",
             "--port", str(port), "--db", str(self.db), "--archive", str(self.archive)],
            env=dict(os.environ, UCAP_API_TOKEN=self.token),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self.addCleanup(self.terminate)
        for _ in range(70):
            if self.process.poll() is not None:
                self.fail("Server exited during startup")
            try:
                with urlopen(self.base + "/health", timeout=0.3) as response:
                    if response.status == 200:
                        return
            except OSError:
                time.sleep(0.1)
        self.fail("Server did not start")

    def terminate(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()

    def test_auth_http_and_retries(self):
        raw = ndjson(event("replay_123", 1, "session_start", schema=2),
                     event("replay_123", 2, "position", x=0, y=0, z=0))
        with self.assertRaises(HTTPError) as err:
            urlopen(Request(self.base + "/v1/ingest", data=raw,
                            headers={"Content-Type": "application/x-ndjson"}))
        self.assertEqual(err.exception.code, 401)
        a = uploader.upload(self.base, self.token, "replay.jsonl", raw, 2)
        b = uploader.upload(self.base, self.token, "replay.jsonl", raw, 2)
        self.assertEqual((a["inserted"], b["duplicates"]), (2, 2))
        req = Request(self.base + "/v1/stats",
                      headers={"Authorization": "Bearer " + self.token})
        with urlopen(req) as response:
            self.assertEqual(json.load(response)["events"], 2)


if __name__ == "__main__":
    unittest.main()
