# NAV-06 pilot: bounded Rabao candidate route query (NOT movement)

**Scope:** Draft, opt-in read-only diagnostic endpoint for zone 247. Based on the
NAV-10 branch, without changing the running backfill, ingest, or UnifiedCombat.
This is a first route-query seam, **not** the full NAV-05 published/validated
release service or the NAV-09 executable navigation interface.

## Contract (v1 pilot)

- `POST /v1/nav/preview` on **127.0.0.1:18788** by default; opt-in **192.168.96.45:18788** for trusted private LAN. Wildcard/public binds are refused, never LAN by default.
- Require `Authorization: Bearer <token>` from `UC_NAV_QUERY_TOKEN` (24+ chars).
  Do not commit tokens. There is no anonymous or raw SQL endpoint.
- Request body is strictly `{"zone":247,"from_xyz":[x,y,z],"to_xyz":[x,y,z]}`.
  Invalid/missing/extra fields reject with HTTP 400; body maximum 1 KiB.
- Output: `schema_version=1`, `status=ROUTE_PREVIEW|NO_ROUTE|...`,
  and on success `preview.waypoints`, `preview.legs`, and evidence.
  `NO_ROUTE` returns an explicit reason, with HTTP 200 for a valid query.
- Only the existing directed, bounded SQLite planner is used:
  max 1-yalm start/goal snap, max 100-yalm cost, max 3x detour,
  max 101 response waypoints. No reverse links, shortcuts or cross-zone routes.
- Every preview is explicitly `approved_for_movement=false`.
  Candidate paths are **not approved executable routes**. Do not feed these
  responses into live UC movement before independent validation and scheduler
  handoff. Graph release remains `not_built` until NAV-05.
- The graph is opened read-only; source HDD database is **never touched**.
  No service starts on import, no systemd files or ingress config change.
  Endpoint errors return `INVALID_REQUEST`, `GRAPH_UNAVAILABLE`,
  or `NO_ROUTE`, not SQLite internals.

## Checks

CI runs `python3 -m unittest discover -s tests -p 'test_*.py' -v`.
Focused local fixture suite:

```sh
python3 -m unittest discover -s tests -p 'test_nav_route_query.py' -v
```

To serve manually **only after CI and an explicit operator checkpoint**, use
a separate checkout/worktree so the running NAV-10 timer keeps its current
scripts and graph unchanged. Example from that isolated checkout:

```sh
export UC_NAV_QUERY_TOKEN="$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')"
python3 server/nav_route_query.py --host 192.168.96.45 --nav-db /home/peter/UnifiedCapture-NavDev/nav-data/navigation.sqlite3
```

In another shell on **Windows or Ubuntu**, issue one read-only
Rabao query using the proven field-test endpoints:

```sh
curl --fail-with-body -sS http://192.168.96.45:18788/v1/nav/preview \
  -H "Authorization: Bearer $UC_NAV_QUERY_TOKEN" \
  -H "Content-Type: application/json" \
  --data '{"zone":247,"from_xyz":[-20.5,111.0,8.0],"to_xyz":[1.5,114.5,8.0]}'
```

The generated token must be available in the querying shell as well.
**Security boundary:** This pilot is plain HTTP and Bearer tokens could be
intercepted by a compromised device on the LAN. Bind only to the specific
private server IP, firewall TCP/18788 to the Windows UC machine(s), and never
forward the port from your router to the Internet. For stronger protection
use an HTTPS reverse proxy and an authenticated client. The existing capture
ingest service (port 8787) is unchanged; navigation remains a separate process.
The old CSV pilot stays unchanged. Validated release serving and movement
arbitration remain separate gates.

## Managed deployment: same Docker Compose project (preferred)

The NAV API is now a **separate container under the existing `compose.yaml`**,
not a separate systemd service. The ingest service stanza is unchanged.
Only the optional `nav-query` image is built/started when explicitly targeted:

```sh
docker compose up -d --build --no-deps nav-query
```

The container uses `network_mode: host` on Linux so the existing
`nav_route_query.py` RFC1918-only bind restriction still applies;
it never binds `0.0.0.0`. The interface defaults to the already configured
`UCAP_BIND`; override with `UCAP_NAV_BIND=192.168.96.45` if necessary.
It uses `restart: unless-stopped` just like ingest. Navigation has a
**separate Docker image, command, and unprivileged process**: updating or
restarting it never restarts the ingest container.

The container mounts only the existing **NVMe derived navigation directory**
read-only at `/nav-data`. Default path from the Compose checkout is
`../UnifiedCapture-NavDev/nav-data` (override `UCAP_NAV_DATA_DIR`).
It does **not** mount the HDD-backed `server-data` volume.
The existing token file from the earlier one-off systemd pilot,
`~/.config/unifiedcapture-nav/query.env`, is reused via
`UCAP_NAV_ENV_FILE` (default `../.config/unifiedcapture-nav/query.env`).
Do not duplicate this token in Git or in the ingest `.env`.
The default container user `1000:1000` matches the expected graph owner;
override `UCAP_NAV_RUN_AS=uid:gid` if that owner differs.

**Safe migration from the temporary standalone unit:**

1. Confirm that the live `~/UnifiedCapture` checkout can incorporate the
   new NAV Compose stanza and Dockerfile without replacing any local work.
   The PR branch also depends on NAV-10; do **not** blindly switch or reset
   the running ingestion checkout to that branch.
2. Stop and disable the redundant unit **before** launching the container,
   because both would claim `192.168.96.45:18788`:
   `systemctl --user disable --now uc-nav-query.service`.
   Keep its secret file, graph and worktrees unchanged.
3. Start **only** `nav-query` with the command above from the normal
   UnifiedCapture Compose project. Confirm `docker compose ps nav-query`
   and a probe returning HTTP 401 unauthenticated, then an authenticated
   `ROUTE_PREVIEW`. If startup fails, the old unit can be restarted
   immediately without impacting ingest.

Because the host has a live deployment on a different branch, the operator
checkout/migration is a separate checkpoint from this GitHub draft.
**Do not run** `docker compose down`, `up --force-recreate` across the whole
stack, or a new `systemctl --user enable` here. This is still a diagnostic,
unapproved candidate graph; none of the Compose changes permit movement.
