# UnifiedCapture — permanent collection (Phase 2)

The recorder remains completely independent of the network. **Nothing in this
deployment changes the Windower addon or UnifiedCombat.**

## Design / durability

```
Windower client(s) -> JSONL files -> one Windows Python uploader
  -> authenticated HTTP POST /v1/ingest -> immutable exact-byte batch archive
  -> SQLite WAL database -> future navigation/coverage analyses
```

- The Lua recorder v0.2.3 rolls each file at 24 hours or 64 MiB
  (whichever occurs first) without logout. Each segment has its own session ID,
  terminal `session_stop`, initial `session_start`, and a fresh observer
  anchor. A replacement-file open failure keeps the prior recording active.
- Optional uploader retention cleans up only old, fully verified, closed files.
  Retention is disabled by default. The recommended opt-in is
  `--retention-days 7`.
- One uploader can watch one or more `data/captures` folders. Six characters
  using a shared Windower installation normally share the same folder.
- Files are read by complete newline-terminated records only, so a game
  session actively appending a partial line cannot produce corrupt imports.
- Each acknowledged chunk advances a durable **local byte-offset checkpoint**.
  Failed or interrupted requests retry; no progress is recorded until the
  server confirms all records in the chunk.
- Ingestion is idempotent by `(session, sequence)`. Retries are not additional
  observations. Conflicting data for an existing key must be rejected.
- The raw archive contains **exact original JSONL bytes** for each HTTP batch
  in immutable SHA-256-named files. SQLite stores original event objects and
  query-ready position, entity, interaction, and session projections.
- Replay only archive batches not present in the SQLite committed-batch
  ledger at server startup to repair the gap between archival fsync and SQLite
  commit. Existing committed batches are skipped rather than fully reread. Raw evidence is never modified to fit
  an interpretation. Backup the entire `server-data` directory, not just
  `capture.sqlite3`. A live backup must use SQLite's backup API or a
  consistent filesystem snapshot.
- Data schema is additive; the early v0.1 and v0.2 recordings may be backfilled
  without pretending unavailable entity names or types existed.

## Linux deployment

From the UnifiedCapture repo root on the Linux Docker host:

```sh
cp .env.example .env
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Copy the generated random token into `UCAP_API_TOKEN` in `.env`. Keep that
file private. Before the first start, prepare a persistent host directory
writable by the container's fixed unprivileged UID/GID (10001):

```sh
sudo install -d -m 0700 -o 10001 -g 10001 ./server-data
```

If that directory already exists with data, inspect its ownership before
changing anything. Set `UCAP_BIND` to the Linux server's **LAN IPv4 address** to
allow the gaming PC to connect. The default `127.0.0.1` is intentionally
localhost-only for safe first boot.

```sh
docker compose up -d --build
docker compose ps
curl http://127.0.0.1:8787/health
```

For `UCAP_BIND=<LAN-IP>`, run the health request against that LAN address.
Use a firewall to allow port 8787 **only from the gaming PC**.
The service requires a token for every ingest/statistics request, but plain
HTTP does not encrypt that token. **Do not expose port 8787 to the public
Internet.** For untrusted networks use a TLS reverse proxy or private VPN.

`server-data/` is a host directory bind mount, not an ephemeral volume.
Its ownership is deliberately separate from your interactive account.
Inside, `capture.sqlite3` (and SQLite WAL files) and
`archive/batches/<sha256>.jsonl` hold the durable dataset.

### Verify server

```sh
docker compose logs --tail=60 ingest
curl -H "Authorization: Bearer ${UCAP_API_TOKEN}" http://127.0.0.1:8787/v1/stats
```

The latter uses a shell variable; `.env` is not automatically exported to
your interactive shell. Either export it securely or substitute the token
only in the local shell (avoid pasting it into screenshots/logs).

## Windows uploader

Install Python 3.11 or newer on the Windows gaming PC. The uploader uses
only the Python standard library.

In PowerShell, from the UnifiedCapture checkout:

```powershell
$env:UCAP_API_TOKEN = '<SAME_TOKEN_AS_SERVER>'
py -3 .\client\uploader.py --server http://<LINUX-LAN-IP>:8787 --capture-dir "C:\Program Files (x86)\Windower\addons\UnifiedCapture\data\captures" --state "$env:LOCALAPPDATA\UnifiedCapture\uploader-state.json" --once
```

Use `--once` to **backfill all existing complete captures** and exit.
Remove `--once` for continuous upload every three seconds.

Add `--capture-dir` again for each different Windower installation path.
**Run one uploader process**, not one per character, so its checkpoint is
not written concurrently. Set `UCAP_API_TOKEN` through an appropriately
protected user environment/secret management approach for unattended
execution, not a committed script or versioned config.

The upload state file is not required for correctness (the server deduplicates),
but keeping it saves re-reading old data. If the Windows computer or server
goes offline, captures remain local and upload resumes from its last checkpoint.

## Query-ready tables

- `events`: losslessly retains every original JSON object as canonical JSON;
  `session + sequence` is the exact-once logical key
- `sessions`: character, session timestamp, recorder version/schema
- `positions`: coordinates and reason for player route reconstruction
- `entity_observations`: time-ordered identity, type, names and sources,
  position/observer samples, last-seen death context
- `interactions`: correlated NPC menu and transportation steps

`GET /v1/stats` reports these table counts. Graph construction is a separate
later pipeline: **never bridge teleport discontinuities as walking edges**.
Waypoint and Home Point menu interactions are observations, not proof of a
universally available transportation link. Use successful observed transitions,
runtime availability checks and replay confirmation before creating executable
travel routes.

## Smoke tests

```sh
python3 -m unittest discover -s tests -p 'test_ingestion.py' -v
```

Tests cover request authentication, validated import, exact raw archive,
duplicate uploads, crash recovery, an active partial JSONL line, checkpoint
retries, and normalized table contents.

## 24/7 rotation and safe Windows cleanup

**Recording segmentation is unrelated to player logout.** The v0.2.3 Lua
recorder rotates every 24 hours or 64 MiB, creating a new segment and
`session_start`, while leaving the character online. An old segment contains
a `session_stop` event explicitly marking closure. A failed attempt to open
the next file does not interrupt the old recording. On successful rotation
the local in-memory sighting/menu windows reset, as expected for a new capture
session; zone and actor context are preserved in the emitted records.

**Server whole-file attestation:** the updated uploader sends
`X-UCAP-Offset` for each JSONL batch. The server commits this byte range,
batch SHA-256 and event-sequence range in the same SQLite transaction as the
events. `POST /v1/verify-file` checks contiguous bytes starting at zero,
contiguous event sequences starting at one, every permanent archived batch's
SHA-256, a matching complete-file SHA-256 and length, corresponding SQLite
rows, and an actual final `session_stop`. Only when all checks pass can the
uploader delete the original Windows file. No partially uploaded or still-open
capture can pass verification.

**Safe rollout order:** update and rebuild the **Linux server first**;
then update the **Windows uploader and Windower Lua addon**. The previous
uploader does not supply the new file-range receipts. The new uploader
detects checkpoints written by that earlier version and safely replays their
already-acknowledged bytes once with offsets; existing events are
deduplicated. The migration may produce a short burst of "already stored"
records without changing the observation counts.

Cleanup remains opt-in; to enable seven-day retention, append the following
argument to the Python uploader command in your scheduled PowerShell launcher
(after the server and Windows updates, and once a verification test passes):

```powershell
--retention-days 7
```

Keep the existing scheduled task; do not create a second uploader. Stop
and restart that task after updating its launcher. This removes only files
older than seven days that the server has explicitly verified. Without that
flag no file is removed. A terminal `session_stop` is required: abnormal
crash leftovers and files with missing evidence are retained for inspection
rather than guessed closed. Requests refused by Linux, changed file sizes or
timestamps, checksum mismatches and network interruptions **fail closed**.

If a verified file is deleted, its local checkpoint entry is also retired.
The Linux SHA-addressed batch archive and SQLite rows remain permanent.
Server disk retention/backup is a separate policy and **must not** be tied
to deletion of the only local copies.

To inspect the service after upgrading:
```sh
sudo docker compose ps
sudo docker compose logs --tail=30 ingest
```

To manually check server ingestion and the new API integrity:
```powershell
Invoke-RestMethod -Uri "http://192.168.96.45:8787/v1/stats" -Headers @{ Authorization = "Bearer $env:UCAP_API_TOKEN" }
```

Note: the IP above is the user's existing LAN server binding; update it if
the server's LAN IP changes. The service is intended only for a trusted LAN.
