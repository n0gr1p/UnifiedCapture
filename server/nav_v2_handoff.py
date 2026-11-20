"""Guarded, copy-only v1 graph + NAV-10 checkpoint handoff to v2.

Never edits old NVMe graph, old worker state, HDD, systemd timer or API.
Acquires the same exclusive worker lock as NAV-10 to snapshot graph and
cursor consistently. The operator must pause the timer for live cutover.
"""
import argparse
from contextlib import closing
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile

import nav_edge_migrate
import nav_incremental_worker as worker


def handoff_copy(raw_db, graph_v1, state_v1, graph_v2, state_v2):
    old_graph = Path(graph_v1).expanduser().resolve()
    old_state = Path(state_v1).expanduser().resolve()
    new_graph = Path(graph_v2).expanduser().resolve()
    new_state = Path(state_v2).expanduser().resolve()
    for p in (new_graph, new_state):
        if p.exists():
            raise FileExistsError("Refusing existing v2 handoff destination")
    if len({str(old_graph), str(old_state), str(new_graph),
            str(new_state)}) != 4:
        raise ValueError("Handoff paths must be distinct")
    if not old_state.is_file():
        raise ValueError("Existing NAV-10 worker state is required")
    new_state.parent.mkdir(parents=True, exist_ok=True)
    lock = Path(str(old_state) + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a") as lockfile:
        try:
            fcntl.flock(lockfile.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("NAV-10 worker active: refuse checkpoint handoff") from exc
        try:
            raw, raw_id = worker.identity(raw_db)
            source, source_id = worker.identity(old_graph)
            original_cursor, original_last = worker.load_state(
                old_state, raw_id, source_id)
            outcome = nav_edge_migrate.migrate_copy(source, new_graph)
            _, dest_id = worker.identity(new_graph)
            temp = None
            try:
                fd, name = tempfile.mkstemp(
                    dir=new_state.parent, prefix=".nav-worker-v2-",
                    suffix=".sqlite3")
                os.close(fd)
                temp = Path(name)
                os.chmod(temp, stat.S_IMODE(old_state.stat().st_mode))
                with closing(sqlite3.connect(
                    old_state.as_uri() + "?mode=ro", uri=True, timeout=10
                )) as read_db, closing(sqlite3.connect(
                    temp, timeout=10)) as write_db:
                    read_db.execute("PRAGMA query_only=ON")
                    read_db.backup(write_db)
                    write_db.execute("BEGIN IMMEDIATE")
                    count = write_db.execute("""UPDATE mining_cursor
                        SET nav_identity=? WHERE slot=1 AND
                        raw_identity=? AND nav_identity=? AND
                        last_event_rowid=?""",
                        (dest_id, raw_id, source_id, original_cursor)
                    ).rowcount
                    if count != 1:
                        raise ValueError("Worker state changed during handoff")
                    write_db.commit()
                # The original input ledger is never rewritten; only the copy
                # binds the same cursor and session anchors to the v2 inode.
                cursor, last = worker.load_state(
                    temp, raw_id, dest_id)
                if cursor != original_cursor or last != original_last:
                    raise ValueError("State cursor/anchors changed during handoff")
                os.link(temp, new_state)
            except Exception:
                # Both destinations were absent before this operation.
                new_graph.unlink(missing_ok=True)
                raise
            finally:
                if temp is not None:
                    temp.unlink(missing_ok=True)
            return {
                **outcome,
                "state_destination": str(new_state),
                "committed_raw_event_rowid": original_cursor,
                "preserved_session_anchors": len(original_last),
                "old_graph_and_checkpoint_unchanged": True,
                "switched_live_services": False,
            }
        finally:
            fcntl.flock(lockfile.fileno(), fcntl.LOCK_UN)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--raw-db", required=True)
    p.add_argument("--graph-v1", required=True)
    p.add_argument("--state-v1", required=True)
    p.add_argument("--graph-v2", required=True)
    p.add_argument("--state-v2", required=True)
    a = p.parse_args()
    print(json.dumps(handoff_copy(
        a.raw_db, a.graph_v1, a.state_v1, a.graph_v2, a.state_v2), indent=2))


if __name__ == "__main__":
    main()
