"""Offline, copy-only NAV-01 v1 -> NAV-04 v2 walking-state migration.

NEVER migrates the active navigation SQLite in place. Operators must
coordinate the NAV-10 worker and existing v1 ingest query before cutover.
Existing provenance, directions and transport records are preserved.
"""
import argparse
import os
from pathlib import Path
import sqlite3
import stat
import tempfile

import derived_navigation_db as nav


def _statement(prefix):
    start = nav.SCHEMA_SQL.index(prefix)
    end = nav.SCHEMA_SQL.index(";\n", start)
    return nav.SCHEMA_SQL[start:end + 1]


def migrate_copy(source, destination):
    src = Path(source).expanduser().resolve()
    dst = Path(destination).expanduser().resolve()
    if src == dst or not src.is_file():
        raise ValueError("Source must exist and destination must differ")
    if dst.exists():
        raise FileExistsError("Refusing to overwrite navigation database")
    dst.parent.mkdir(parents=True, exist_ok=True)
    src_db = sqlite3.connect(src.as_uri() + "?mode=ro", uri=True, timeout=10)
    temp = None
    try:
        src_db.execute("PRAGMA query_only=ON")
        application_id = src_db.execute("PRAGMA application_id").fetchone()[0]
        version = src_db.execute("PRAGMA user_version").fetchone()[0]
        if application_id != nav.APPLICATION_ID or version != 1:
            raise ValueError("Only a NAV UCNV schema-v1 source can be migrated")
        if src_db.execute("SELECT COUNT(*) FROM nav_release").fetchone()[0]:
            raise ValueError("Refusing to migrate a staged/published release")
        old_states = src_db.execute(
            "SELECT DISTINCT status FROM walk_edge"
        ).fetchall()
        if any(state != "candidate" for (state,) in old_states):
            raise ValueError("Refusing to reinterpret noncandidate legacy states")
        source_counts = tuple(src_db.execute(
            "SELECT COUNT(*) FROM " + t).fetchone()[0]
            for t in ("node", "walk_edge", "evidence_ref", "transport")
        )

        fd, tmp_name = tempfile.mkstemp(prefix=".nav-v2-", suffix=".sqlite3",
                                         dir=dst.parent)
        os.close(fd)
        temp = Path(tmp_name)
        os.chmod(temp, stat.S_IMODE(src.stat().st_mode))
        new_db = sqlite3.connect(temp, timeout=10)
        try:
            src_db.backup(new_db)
            new_db.execute("PRAGMA foreign_keys=OFF")
            new_db.execute("BEGIN IMMEDIATE")
            walk_sql = _statement("CREATE TABLE walk_edge (").replace(
                "CREATE TABLE walk_edge (", "CREATE TABLE walk_edge_v2 (", 1
            )
            new_db.execute(walk_sql)
            new_db.execute("""INSERT INTO walk_edge_v2
                (edge_id,zone_id,from_node_id,to_node_id,cost,
                 confidence,status,evidence_count)
                SELECT edge_id,zone_id,from_node_id,to_node_id,cost,
                       confidence,'ready',evidence_count FROM walk_edge""")
            new_db.execute("DROP TABLE walk_edge")
            new_db.execute("ALTER TABLE walk_edge_v2 RENAME TO walk_edge")
            new_db.execute(_statement("CREATE INDEX idx_walk_edge_from "))
            new_db.execute(_statement("CREATE TABLE edge_outcome ("))
            new_db.execute(_statement("CREATE INDEX idx_edge_outcome_edge"))
            new_db.execute("PRAGMA user_version=2")
            foreign_key_errors = new_db.execute(
                "PRAGMA foreign_key_check").fetchall()
            if foreign_key_errors:
                raise ValueError("Broken references after schema upgrade")
            dest_counts = tuple(new_db.execute(
                "SELECT COUNT(*) FROM " + t).fetchone()[0]
                for t in ("node", "walk_edge", "evidence_ref", "transport")
            )
            if dest_counts != source_counts:
                raise ValueError("Graph/provenance counts changed during upgrade")
            if new_db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("Migrated navigation SQLite failed quick check")
            new_db.commit()
        except Exception:
            new_db.rollback()
            raise
        finally:
            new_db.close()
        info = nav.inspect(temp)
        if info["schema_version"] != 2:
            raise ValueError("Navigation schema upgrade verification failed")
        # Link on the same filesystem: atomic, refuses an existing destination.
        os.link(temp, dst)
        return {"destination": str(dst), "schema_version": 2,
                "ready_edges": source_counts[1],
                "preserved_evidence": source_counts[2],
                "source_unchanged": True,
                "release_status": info["release_status"]}
    finally:
        src_db.close()
        if temp is not None:
            temp.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--destination", required=True)
    args = parser.parse_args()
    import json
    print(json.dumps(migrate_copy(args.source, args.destination), indent=2))


if __name__ == "__main__":
    main()
