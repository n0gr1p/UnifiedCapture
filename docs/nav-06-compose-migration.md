# NAV-06: unify candidate route preview with existing ingestion API

This replaces the experimental separate port-18788 listener and Compose
`nav-query` sidecar. The existing authenticated API on **8787** now serves
`POST /v1/nav/preview`, reusing the **existing `UCAP_API_TOKEN`**.
No extra listener, systemd service, Docker image, firewall rule, or secret.

## API and data safety

- Authentication is exactly the existing ingestion Bearer-token check.
  Request payload: `{"zone":247,"from_xyz":[x,y,z],"to_xyz":[x,y,z]}`.
- A strict 1 KiB request limit, fixed Rabao zone 247, directed graph only,
  1-yalm node snap, 100-yalm total distance, 3x detour ceiling, and 101
  output waypoint ceiling. Unsupported queries fail closed.
- One preview query runs at a time so bursts don't overwhelm ingestion;
  concurrent requests receive `429 NAV_QUERY_BUSY`.
- The derived **NVMe** navigation directory is mounted into the existing
  ingest container at `/nav-data:ro`; the route handler opens
  `/nav-data/navigation.sqlite3` with SQLite `mode=ro` and
  `PRAGMA query_only=ON`. The graph schema's application ID is validated.
- The existing HDD telemetry mount from the **untracked**
  `compose.override.yaml` is preserved. All other ingest endpoints
  (`/v1/ingest`, `/v1/verify-file`, existing telemetry GETs) remain
  intact, and the running NAV-10 backfill timer is unrelated.
- Every candidate route response says `approved_for_movement=false`,
  with `release_status=not_built`. No executable navigation or scheduler
  movement integration exists in this slice.
- API on 8787 is **HTTP, not TLS**: firewall the existing port to trusted
  Windows clients only, and never router-forward to the Internet.

## Controlled deploy on Ubuntu (one operator checkpoint at a time)

1. Pull branch `feature/nav-06-compose-live-integration-20261119`
   into the already checked-out `~/UnifiedCapture`. Check the diff and
   verify the untracked `compose.override.yaml` remains in place.
2. Run `docker compose config` and confirm there is **only** `ingest`
   with two mounts: HDD `/data` and NVMe `/nav-data:ro`. Confirm
   `/nav-data/navigation.sqlite3` is readable by the existing ingest
   container UID/GID **10001:10001** before restarting anything.
3. Build/recreate **only** `ingest`:
   `docker compose up -d --build --no-deps ingest`. This causes a brief
   ingest API restart; it does not remove, migrate, or replace the raw
   HDD archive and database, and it does not stop NAV-10.
4. Verify `/health` and `/v1/stats` on 8787, then POST an authenticated
   Rabao preview with **the same `UCAP_API_TOKEN`**. Expected:
   HTTP 200, `ROUTE_PREVIEW`, ~15 waypoints, `approved_for_movement=false`.
   Verify the existing Windows uploader resumes after the restart.
5. Only **after** that succeeds, retire the orphaned
   `unifiedcapture-nav-query-1` container on port 18788. The temporary
   `uc-nav-query.service` was already stopped and disabled; keep its
   credential file temporarily until Windows UC has been configured with
   the *existing ingestion token* instead of the old navigation token.
   Do not reuse the old token or expose it in chat.

No `docker compose down`, no volume deletion, no broad service recreate,
no changes to NAV-10 miner, and no automatic walking. If the updated image
fails, restore the original `feature/navigation-readonly-queries` branch
and rebuild only `ingest`; the HDD dataset and graph are unchanged.

## NAV-04 READY / PROVEN / FAILED read compatibility (future cutover)

A small, **undeployed** change in this branch allows the existing 8787
route-query reader to inspect both UCNV v1 and v2 derived graph SQLite.
It continues to parse `candidate` walking edges only for v1.
For v2 it accepts READY/PROVEN walking edges and **excludes FAILED**
walking edges from directed route calculation. Any unrecognized status
fails closed. The external JSON response retains schema version 1
and `approved_for_movement=false`; no new write endpoint, token, port,
scheduler control, or permission has been introduced.

The running API still uses **v1**. The separately staged
[READY/PROVEN/FAILED lifecycle PR #21](https://github.com/n0gr1p/UnifiedCapture/pull/21)
and its NAV-10 checkpoint handoff must be verified before any v2 switch.
Do **not** pull and deploy a new v2 graph directly against a v1 worker.
