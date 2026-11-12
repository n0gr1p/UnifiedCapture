# Offline navigation evidence API (read-only MVP)

This API exposes **already-ingested** telemetry from the UnifiedCapture
SQLite database to a trusted local MCP bridge for aggregation and route
reconstruction. It does **not** operate game clients, record additional
telemetry, change the permanent archive, or make navigation decisions.

The existing LAN ingestion service (port 8787) provides five authenticated
**GET** routes. All use the existing `Authorization: Bearer <UCAP_API_TOKEN>`
header. The server's SQLite query connection is opened in `mode=ro` and
`PRAGMA query_only=ON`; clients cannot submit SQL, filesystem paths, or
custom query fragments.

| Endpoint | Purpose | Bounded parameters |
| --- | --- | --- |
| `/v1/nav/dataset` | Counts of sessions, events, positions, observed zone changes, distinct zones, newest event | None |
| `/v1/nav/zones` | Per-zone recorded positional sample count, distinct session count, min/max time | `limit=1..200`, `offset=0..100000` |
| `/v1/nav/transitions` | Aggregated observed `old_zone → new_zone` events, session/character counts and last timestamp | `limit`, `offset`, optional `from_zone` |
| `/v1/nav/sessions` | Historical capture session identifiers; optionally select a zone | `limit`, `offset`, optional `zone` |
| `/v1/nav/route-events` | Historical sequenced positions, discontinuities, zone changes, and optional travel/menu metadata | `session` required; `after_sequence`, `limit=1..200`, `include_interactions=true/false` |

Example (Windows PowerShell, with the **already configured** token in the
current process; never print or commit it):

```powershell
$base = 'http://192.168.96.45:8787'   # replace if server IP changed
$headers = @{Authorization = "Bearer $env:UCAP_API_TOKEN"}
Invoke-RestMethod "$base/v1/nav/dataset" -Headers $headers
Invoke-RestMethod "$base/v1/nav/transitions?limit=25" -Headers $headers
```

### Evidence and interpretation boundaries

- These numbers describe **records successfully ingested**, not every game
  movement ever attempted. Compare with uploader checkpoint and server stats
  before drawing completeness conclusions.
- `zone_change` records provide reported old/new zones. They are **candidate
  travel links, not guaranteed walking edges**. NPC menus, Home Points,
  Waypoints, teleport items and spells can all produce zone transitions.
- Position samples have variable spacing and are observations, not a collision
  or navigation mesh. Elevation and `discontinuity` markers must be preserved.
- Never join separate `session` IDs into a walking edge just because file
  rotation occurred after 24 hours. Never interpolate through large jumps.
- `route-events` returns only a known safe subset of original event fields,
  omitting raw packet data. The original JSONL archives remain authoritative.
- `next_after_sequence` is the cursor for subsequent requests; `has_more`
  indicates further matching rows. Empty results do not imply the session or
  zone is fully surveyed.
- This is an **analysis interface**, not yet a route graph or automated
  movement executor. The graph can be assembled offline from accepted evidence
  with confidence/provenance and later integrated with UC's movement dispatcher.

### Tests and rollout

```sh
python3 -m unittest discover -s tests -p 'test_*.py' -v
docker compose up -d --build
```

Deploy the **server** side first. Only after confirming the protected endpoints
on the trusted LAN should the matching RemoteXI bridge MCP client be enabled.
The recorder, uploader, UC engine and running game sessions do not need updates.

Do not expose this plaintext authenticated service to the internet. Any
RemoteXI host using these endpoints must be on a trusted LAN (or reach the
service over an appropriately protected loopback/private connection).
