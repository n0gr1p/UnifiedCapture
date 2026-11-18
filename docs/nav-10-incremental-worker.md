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
- Execution requires the process to have **raw read** and **derived write** OS permissions. Current raw capture directory has required elevated access for one-off pilot extraction. Do not run this mutating worker under `sudo` without deciding file ownership/permission strategy; this PR intentionally does not install privileged services.
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