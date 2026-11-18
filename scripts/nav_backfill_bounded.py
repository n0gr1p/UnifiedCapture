#!/usr/bin/env python3
"""NAV-10 manual, budgeted HDD->NVMe candidate-graph backfill runner.

Run from the existing UnifiedCapture Compose directory as the normal host
user (NOT sudo). The source reader executes inside the already-running
ingestion container (UID 10001), emitting one bounded JSON window. Only the
unprivileged host consumer writes NVMe graph/checkpoint data.

No cron, service changes, Docker restart, raw writes or edge approvals.
"""
import argparse
import fcntl
import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

MAX_BATCHES = 10
MAX_EVENTS = 2000
MAX_WINDOW_BYTES = 4 * 1024 * 1024
READER_TIMEOUT = 60
WRITER_TIMEOUT = 180


def invoke(command, *, input_data=None, timeout=60):
    result = subprocess.run(
        command, input=input_data, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, timeout=timeout, check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace")[-2400:]
        raise RuntimeError(f"Command exited {result.returncode}: {command[0]}: {detail}")
    return result.stdout


def numeric_cursor(raw, nav, state, worker, call=invoke):
    out = call([
        sys.executable, str(worker), "--raw-db", str(raw),
        "--nav-db", str(nav), "--state-db", str(state), "--cursor-only",
    ], timeout=20)
    try:
        value = int(out.strip())
    except ValueError as exc:
        raise ValueError("Worker returned an invalid committed cursor") from exc
    if value < 0:
        raise ValueError("Worker cursor cannot be negative")
    return value


def parse_summary(data, expected_cursor, max_events, dry_run):
    try:
        doc = json.loads(data)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("Consumer returned invalid JSON") from exc
    if not isinstance(doc, dict):
        raise ValueError("Consumer summary is not an object")
    for name in ("after_rowid", "last_rowid", "processed_events",
                 "new_nodes", "new_edges", "new_evidence"):
        if type(doc.get(name)) is not int or doc[name] < 0:
            raise ValueError("Invalid numeric summary: " + name)
    if (doc["after_rowid"] != expected_cursor
            or doc["last_rowid"] < expected_cursor
            or doc["processed_events"] > max_events
            or doc.get("dry_run") is not dry_run):
        raise ValueError("Consumer summary violates the requested window")
    if (doc["processed_events"] == 0) != (doc["last_rowid"] == expected_cursor):
        raise ValueError("Unchanged cursor with nonempty or empty window")
    if dry_run and any(doc[x] for x in ("new_nodes", "new_edges", "new_evidence")):
        raise ValueError("Dry run unexpectedly reported writes")
    return doc


@contextmanager
def run_lock(state, apply):
    # No lock file creation in dry-run mode.
    if not apply:
        yield
        return
    path = Path(str(state) + ".backfill.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another bounded backfill runner is active") from exc
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def execute(args, *, call=invoke, sleeper=time.sleep, clock=time.monotonic):
    if os.geteuid() == 0:
        raise ValueError("Do not execute the graph writer as root")
    if not (Path.cwd() / "compose.yaml").is_file():
        raise ValueError("Run from the UnifiedCapture Compose project directory")
    repo = Path(__file__).resolve().parents[1]
    reader = repo / "server" / "nav_read_window.py"
    worker = repo / "server" / "nav_incremental_worker.py"
    if not reader.is_file() or not worker.is_file():
        raise ValueError("Navigation reader or consumer source is missing")

    raw = Path(args.raw_db).expanduser().resolve()
    nav = Path(args.nav_db).expanduser().resolve()
    state = Path(args.state_db).expanduser().resolve()
    if raw == nav or raw == state or nav == state:
        raise ValueError("Raw, derived and state databases must use different paths")
    started = clock()
    totals = {"batches": 0, "events": 0, "new_nodes": 0,
              "new_edges": 0, "new_evidence": 0}

    with run_lock(state, args.apply):
        for i in range(args.max_batches):
            cursor = numeric_cursor(raw, nav, state, worker, call)
            source = call([
                "sudo", "docker", "compose", "exec", "-T",
                "ingest", "python", "-", "--db", "/data/capture.sqlite3",
                "--after-rowid", str(cursor),
                "--max-events", str(args.max_events),
            ], input_data=reader.read_bytes(), timeout=READER_TIMEOUT)
            if len(source) > MAX_WINDOW_BYTES:
                raise ValueError("Reader exceeded 4 MiB stream budget")
            command = [
                sys.executable, str(worker),
                "--raw-db", str(raw), "--nav-db", str(nav),
                "--state-db", str(state), "--consume-stdin",
            ]
            if not args.apply:
                command.append("--dry-run")
            report = call(command, input_data=source, timeout=WRITER_TIMEOUT)
            summary = parse_summary(report, cursor, args.max_events, not args.apply)
            if args.apply:
                persisted = numeric_cursor(raw, nav, state, worker, call)
                if persisted != summary["last_rowid"]:
                    raise RuntimeError(
                        f"Checkpoint mismatch: {persisted} != {summary['last_rowid']}"
                    )
            totals["batches"] += 1
            totals["events"] += summary["processed_events"]
            for name in ("new_nodes", "new_edges", "new_evidence"):
                totals[name] += summary[name]
            print(json.dumps({"batch": i + 1, **summary}, separators=(",", ":")),
                  flush=True)
            if summary["processed_events"] < args.max_events or not args.apply:
                break
            if i + 1 < args.max_batches:
                sleeper(args.pause_seconds)
    totals["elapsed_seconds"] = round(clock() - started, 3)
    totals["dry_run"] = not args.apply
    print(json.dumps({"summary": totals}, separators=(",", ":")), flush=True)
    return totals


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-db", default="/home/peter/bulk-storage/UnifiedCapture/server-data/capture.sqlite3")
    parser.add_argument("--nav-db", default="/home/peter/UnifiedCapture-NavDev/nav-data/navigation.sqlite3")
    parser.add_argument("--state-db", default="/home/peter/UnifiedCapture-NavDev/nav-data/nav_worker_state.sqlite3")
    parser.add_argument("--max-events", type=int, default=2000)
    parser.add_argument("--max-batches", type=int, default=3)
    parser.add_argument("--pause-seconds", type=float, default=5)
    parser.add_argument("--apply", action="store_true",
                        help="Opt in to actual candidate and checkpoint writes")
    args = parser.parse_args(argv)
    if not 1 <= args.max_events <= MAX_EVENTS:
        parser.error("--max-events must be in 1..2000")
    if not 1 <= args.max_batches <= MAX_BATCHES:
        parser.error("--max-batches must be in 1..10")
    if not 1 <= args.pause_seconds <= 60:
        parser.error("--pause-seconds must be between 1 and 60")
    execute(args)


if __name__ == "__main__":
    main()
