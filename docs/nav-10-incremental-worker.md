# NAV-10: bounded incremental walk-candidate worker (draft)

Source: [NAV-10 issue](https://github.com/n0gr1p/UnifiedCapture/issues/14).
Parent: [navigation epic](https://github.com/n0gr1p/UnifiedCapture/issues/5).
Current draft: [PR #18](https://github.com/n0gr1p/UnifiedCapture/pull/18) on `feature/nav-10-incremental-worker-20261118`.
Handoff: [canonical tracker](https://github.com/n0gr1p/UnifiedCapture/blob/main/docs/navigation-implementation-tracker.md).

## Current implementation and limits

- Uses a **bounded ascending raw `events.rowid` cursor**, not a repeated full-table scan, to backfill history and then pick up new append-only captures across all zones.
- Reads raw HDD SQLite exclusively in read-only/query-only mode. New candidates are written only to the independent existing NVMe `navigation.sqlite3`; a separate `nav_worker_state.sqlite3` stores cursor and last eligible position for each recorded session.
- Maintains session-local anchors across invocations and honors explicit discontinuity/zone-change markers, zone changes, timing, speed, height and movement-reason checks. Every walk edge is **candidate-only**, with deterministic evidence IDs.
- Commits the source rowid and anchor state **after** all candidate imports. A crash between import and checkpoint replays the same batch; existing deterministic IDs prevent duplicate evidence.
- `--dry-run` is strictly read-only: no graph changes, checkpoint writes or lock file creation. No schedule, Docker integration, remote runtime/API, transport activation or movement effects are installed by this PR.
- The worker **fails closed with OUT_OF_ORDER_SESSION_SEQUENCE** if an older chunk for an already seen session arrives late. Detection is implemented; targeted session-indexed repair is **not implemented** yet. Do not claim that arbitrary historical imports are fully processed until this is resolved or empirical source append ordering is verified.
- **Privilege separation added after first field test:** the separate nav_read_window.py reader runs inside the existing ingest container as UID 10001, accessing its own /data/capture.sqlite3; it emits only bounded JSON to stdout. The host-side nav_incremental_worker.py --consume-stdin imports candidate edges as unprivileged peter, and --cursor-only obtains the committed checkpoint without opening raw SQLite. Do NOT give peter write access to the raw directory or run the importer with sudo. The original single-process --dry-run remains for diagnostics with raw permissions.
- Derived database must remain `not_built`; when a published release exists, candidate mining must use a separate staging database, not mutate that release in-place.
- The first empirical benchmark is pending. Do not enable a recurring timer until HDD load, uploader receipt latency and permissions are measured.

## First read-only host check (manual, limited)

On Ubuntu with this draft branch checked out in `~/UnifiedCapture-NavDev`:

```sh
sudo python3 ~/UnifiedCapture-NavDev/server/nav_incremental_worker.py \
  --raw-db /home/peter/bulk-storage/UnifiedCapture/server-data/capture.sqlite3 \
  --nav-db /home/peter/UnifiedCapture-NavDev/nav-data/navigation.sqlite3 \
  --state-db /home/peter/UnifiedCapture-NavDev/nav-data/nav_worker_state.sqlite3 \
  --max-events 200 --dry-run
```

Expected: one JSON summary of at most 200 events, current source rowid bounds, candidate edge counts, and `dry_run:true`. It must create **no checkpoint SQLite**, and should not alter raw or derived SQLite. If the query is slow, errors, or reports unexpected anomalies, stop and inspect rather than scheduling anything.

## Narrow next acceptance steps

1. **CI**: compile/tests; replay across a window boundary; newly ingested sessions; explicit zoning and discontinuity; post-import crash replay; raw/derived identity replacement; late out-of-order source behavior.
2. **Live dry run**: user runs the single bounded read-only command and pastes output. Verify workload against ongoing ingestion.
3. **One bounded write** after permissions strategy is agreed: append candidate edges/receipt to NVMe; confirm cursor advanced and second run has no duplicate evidence.
4. **Backfill/refresh automation**: select a reasonable max-events and number of batches per run after performance testing, then install a separate scheduled worker with a bounded I/O budget. Never restart the ingest container as part of mining setup.
5. **Missing historical edge repair**: handle late session chunks with indexed per-session reconciliation. Only then claim complete, continuously growing historical coverage.
6. **Separate milestones**: NAV-05/06/07 publish and serve routes; NAV-09 coordinate live UC movement; NAV-12/13 verified cross-zone; NAV-14 MCP/activity travel.

Nothing in this draft changes the previously successful Rabao HP #2 -> destination walking proof.
## Privilege separation checkpoint: first container read test

The live ingestion directory is mode 0700, UID 10001. Raw capture.sqlite3 and temporary WAL/SHM files are owned by UID 10001. Peter can stat the DB through a directory-traversal ACL but could not open read-only SQLite when the WAL-mode reader needed a shared-memory file. Sidecars disappear between commands; never ACL individual SHM paths or give peter write permission to the ingestion directory.

From the **running Compose project** directory (~/UnifiedCapture), test read-only container access:

~~~sh
sudo docker compose exec -T ingest python -c 'import os,sqlite3; c=sqlite3.connect("file:/data/capture.sqlite3?mode=ro",uri=True,timeout=2); c.execute("PRAGMA query_only=ON"); print("UID",os.getuid(),"events",c.execute("SELECT name FROM sqlite_master WHERE name=? ",("events",)).fetchone()); c.close()'
~~~

Expected UID 10001, events ('events',). No host SQLite write access required. **Do not run the graph importer until this succeeds.**

## Planned bounded split-worker acceptance (NOT FIELD TESTED YET)

After container check, update the NavDev checkout to PR #18 head. The first separated pipeline is still one bounded, manually initiated window:

~~~sh
raw=/home/peter/bulk-storage/UnifiedCapture/server-data/capture.sqlite3
nav=/home/peter/UnifiedCapture-NavDev/nav-data/navigation.sqlite3
state=/home/peter/UnifiedCapture-NavDev/nav-data/nav_worker_state.sqlite3
cursor=$(python3 ~/UnifiedCapture-NavDev/server/nav_incremental_worker.py --raw-db "$raw" --nav-db "$nav" --state-db "$state" --cursor-only)
set -o pipefail
sudo docker compose exec -T ingest python - --db /data/capture.sqlite3 --after-rowid "$cursor" --max-events 200 < ~/UnifiedCapture-NavDev/server/nav_read_window.py \
| python3 ~/UnifiedCapture-NavDev/server/nav_incremental_worker.py --raw-db "$raw" --nav-db "$nav" --state-db "$state" --consume-stdin --dry-run
~~~

The reader only SELECTs from SQLite in the running ingest container and emits a bounded JSON window; the consumer checks the schema, rowid monotonicity and committed cursor. The dry-run makes no checkpoint or graph writes. After further checks, removing --dry-run from ONLY the consumer enables peter-owned NVMe graph/checkpoint writes. NEVER run consumer under sudo.

This is a manual pipeline, not a deployed scheduler. Verify user-visible output and ingest performance first. Late/out-of-order archived chunks currently fail closed with OUT_OF_ORDER_SESSION_SEQUENCE; targeted reconciliation remains unbuilt.
