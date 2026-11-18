"""Pure tests for the finite, opt-in NAV-10 manual backfill orchestrator."""
import argparse
import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import nav_backfill_bounded as runner


class FakeCalls:
    def __init__(self, max_events=4, batches=2, dry=False):
        self.cursor = 2400
        self.max_events = max_events
        self.batches = batches
        self.dry = dry
        self.calls = []
        self.reads = 0
        self.consumes = 0

    def __call__(self, cmd, *, input_data=None, timeout=None):
        self.calls.append((cmd, input_data, timeout))
        if "--cursor-only" in cmd:
            return str(self.cursor).encode()
        if cmd[:4] == ["docker", "compose", "exec", "-T"]:
            self.reads += 1
            assert input_data is not None and b"emit_window" in input_data
            assert cmd[4:7] == ["ingest", "python", "-"]
            assert cmd[-1] == str(self.max_events)
            assert cmd[cmd.index("--after-rowid") + 1] == str(self.cursor)
            return b'{"some":"bounded stream"}'
        if "--consume-stdin" in cmd:
            self.consumes += 1
            assert input_data == b'{"some":"bounded stream"}'
            assert ("--dry-run" in cmd) is self.dry
            after = self.cursor
            n = self.max_events if self.consumes < self.batches else 2
            last = after + n
            if not self.dry:
                self.cursor = last
            return json.dumps({
                "processed_events": n, "after_rowid": after,
                "last_rowid": last, "dry_run": self.dry,
                "new_nodes": 0 if self.dry else n - 1,
                "new_edges": 0 if self.dry else n - 1,
                "new_evidence": 0 if self.dry else n - 1,
            }).encode()
        raise AssertionError("Unexpected command " + repr(cmd))


class BackfillTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = pathlib.Path(self.tmp.name)
        self.args = argparse.Namespace(
            raw_db=str(self.base / "capture.sqlite3"),
            nav_db=str(self.base / "graph.sqlite3"),
            state_db=str(self.base / "state.sqlite3"),
            max_events=4,
            max_batches=4,
            pause_seconds=3,
            apply=False,
        )
        self.cwd = patch.object(pathlib.Path, "cwd", return_value=ROOT)
        self.uid = patch.object(runner.os, "geteuid", return_value=1000)
        self.cwd.start()
        self.uid.start()

    def tearDown(self):
        self.cwd.stop()
        self.uid.stop()
        self.tmp.cleanup()

    def test_default_is_only_one_dry_run_without_lock_files(self):
        fake = FakeCalls(max_events=4, dry=True)
        sleeps = []
        result = runner.execute(self.args, call=fake, sleeper=sleeps.append,
                                clock=lambda: 10.0)
        self.assertEqual(result["batches"], 1)
        self.assertEqual(result["events"], 4)
        self.assertEqual(result["dry_run"], True)
        self.assertEqual(fake.reads, 1)
        self.assertEqual(fake.consumes, 1)
        self.assertEqual(sleeps, [])
        self.assertFalse((self.base / "state.sqlite3.backfill.lock").exists())

    def test_apply_finite_resumable_batches_with_pauses(self):
        self.args.apply = True
        fake = FakeCalls(max_events=4, batches=3, dry=False)
        sleeps = []
        result = runner.execute(self.args, call=fake, sleeper=sleeps.append,
                                clock=lambda: 10.0)
        self.assertEqual(result["batches"], 3)
        self.assertEqual(result["events"], 10)
        self.assertEqual(result["new_nodes"], 7)
        self.assertEqual(fake.reads, 3)
        self.assertEqual(fake.consumes, 3)
        self.assertEqual(fake.cursor, 2410)
        self.assertEqual(sleeps, [3, 3])
        self.assertTrue((self.base / "state.sqlite3.backfill.lock").exists())

    def test_reader_failure_prevents_consumer_and_retries(self):
        self.args.apply = True
        fake = FakeCalls()
        def fail_reader(command, **kwargs):
            if command[0] == "docker":
                raise RuntimeError("read failed")
            return fake(command, **kwargs)
        with self.assertRaisesRegex(RuntimeError, "read failed"):
            runner.execute(self.args, call=fail_reader, sleeper=lambda _: None)
        self.assertEqual(fake.reads, 0)
        self.assertEqual(fake.consumes, 0)

    def test_bad_consumer_output_fails_before_next_batch(self):
        self.args.apply = True
        fake = FakeCalls()
        def corrupt(command, **kwargs):
            if "--consume-stdin" in command:
                return json.dumps({
                    "processed_events": 4,
                    "after_rowid": 0,  # wrong: committed source cursor 2400
                    "last_rowid": 2404, "dry_run": False,
                    "new_nodes": 0, "new_edges": 0, "new_evidence": 0,
                }).encode()
            return fake(command, **kwargs)
        with self.assertRaisesRegex(ValueError, "summary violates"):
            runner.execute(self.args, call=corrupt, sleeper=lambda _: None)
        self.assertEqual(fake.reads, 1)

    def test_oversized_reader_is_rejected_before_consumer(self):
        fake = FakeCalls()
        def oversized(command, **kwargs):
            if command[0] == "docker":
                return b" " * (runner.MAX_WINDOW_BYTES + 1)
            return fake(command, **kwargs)
        with self.assertRaisesRegex(ValueError, "4 MiB"):
            runner.execute(self.args, call=oversized, sleeper=lambda _: None)
        self.assertEqual(fake.consumes, 0)

    def test_refuses_root_and_wrong_compose_directory(self):
        with patch.object(runner.os, "geteuid", return_value=0):
            with self.assertRaisesRegex(ValueError, "root"):
                runner.execute(self.args)
        with patch.object(pathlib.Path, "cwd", return_value=self.base):
            with self.assertRaisesRegex(ValueError, "Compose"):
                runner.execute(self.args)

    def test_summary_validation_rejects_misleading_dry_run(self):
        bad = {
            "processed_events": 1, "after_rowid": 400,
            "last_rowid": 401, "dry_run": True,
            "new_nodes": 1, "new_edges": 0, "new_evidence": 0,
        }
        with self.assertRaisesRegex(ValueError, "unexpectedly reported writes"):
            runner.parse_summary(json.dumps(bad), 400, 10, True)
        bad["new_nodes"] = 0
        bad["last_rowid"] = 400
        with self.assertRaisesRegex(ValueError, "Unchanged cursor"):
            runner.parse_summary(json.dumps(bad), 400, 10, True)


if __name__ == "__main__":
    unittest.main()
