# NAV-03: conservative walking candidates (one-session pilot)

[Decision](adr/0001-raw-telemetry-to-navigation-intelligence.md) · [NAV-03 issue](https://github.com/n0gr1p/UnifiedCapture/issues/9)

This first extraction uses **one recording session in one zone**, not a global
navmesh. It reads historical source records from the **HDD** without opening them
for writes, then imports a bounded, quantized graph of **candidate-only**
directed walking edges into the distinct **NVMe** SQLite created in NAV-01.

## Privilege boundary on Ubuntu

The raw HDD data is owned by the ingestion UID and generally requires `sudo`
to read; the derived NVMe database must stay owned by the normal `peter`
user. **Do not run the importer as root.** The pipe separates permissions.

From a worktree containing this branch (after tests pass), for example:

```bash
set -o pipefail
sudo python3 ~/UnifiedCapture-NavDev/server/walk_candidate_miner.py extract \
  --raw-db /home/peter/bulk-storage/UnifiedCapture/server-data/capture.sqlite3 \
  --zone 247 \
  --session etamame_20261023T235956Z_296968 \
  --max-positions 800 \
| python3 ~/UnifiedCapture-NavDev/server/walk_candidate_miner.py import \
  --nav-db ~/UnifiedCapture-NavDev/nav-data/navigation.sqlite3
```

The exporter writes a single bounded JSON document to stdout (max 2 MiB
accepted by the importer). The importer refuses unrecognized database/schema,
a staging/published release, an unsupported document, or malformed endpoints,
and writes atomically in one transaction. The source is opened with
`mode=ro` and `query_only=ON`, never written. The live ingest service,
its Docker Compose deployment and the historical archive remain untouched.

This is a **pilot** source recording, not a promised walking route: a session
can contain mostly idle heartbeats, and extraction may produce zero edges.
Any explicit zoning or large-jump boundary is excluded.

## Acceptance / limitations

- Only `position` records with one specific session and zone, ordered
  by capture sequence and linked to their event timestamps.
- A potential walking edge requires a `moving` destination sample,
  an immediately preceding valid position (including a `new_anchor` as an origin, but never traversing into it), elapsed
  time `0 < dt <= 1.5` seconds, three-dimensional step
  `0.35 <= distance <= 6` yalms, speed `<= 12` yalms/sec and absolute
  Z difference `<= 1.5` units. Reject intervening
  `discontinuity`, `zone_change`, or out-of-zone position events.
- Every inserted edge is `status=candidate`, `confidence=0.25`.
  Quantization uses 0.5-unit XYZ cells and is **not** evidence of wall
  clearance, walkability, route continuity or independent traversal trials.
  The `evidence_count` field counts source references, not six-box
  independent confirmations.
- Stable deterministic hashes deduplicate repeated imports of the same
  source evidence; no raw event JSON is copied into the NVMe. A source
  session+sequence points to the immutable evidence in the HDD.
- A finished import leaves `nav_release` empty and reports
  `release_status=not_built`. It **cannot** drive UC.
- The initial 800-position scan is a small, bounded proof of the pipeline,
  not a zone coverage claim. Use only historical/closed recordings first.
- Any significant HDD load or uploader backlog is grounds to stop expanding
  the scan until [NAV-00 ingestion verification](https://github.com/n0gr1p/UnifiedCapture/issues/6)
  is healthy. Bulk mining and I/O controls belong to NAV-10.

## Test command

```bash
python3 -m unittest discover -s tests -p 'test_walk_candidate_miner.py' -v
```

Next: verify a real candidate count and inspect the resulting nodes/edges
before implementing NAV-05 staged publication, NAV-06 queries and NAV-07
path planning. Unverified candidates are never executable.

## Automated bounded multi-session pilot (recommended)

Instead of manually inspecting and invoking one recording at a time, use
the audited [zone-247 pilot manifest](pilot_zone247_sessions.json) and
[one-command pilot runner](../scripts/mine_zone247_pilot.sh).
The manifest names five historical sessions whose zone-247 movement totals
were observed from the raw source. **Moving-sample count alone does not
establish route quality.**

Once the code is fetched into the isolated NavDev worktree, run:

```sh
bash ~/UnifiedCapture-NavDev/scripts/mine_zone247_pilot.sh
```

The script checks the derived database exists on the expected NVMe mount,
validates database identity, and then handles the extraction and import
pipeline without requiring a manual command for each session.
It processes **at most five sessions, two windows per session, 1200
zone-specific positions per window**, with a one-position overlap so a
valid edge is not dropped at a window boundary. It does not enumerate
the full raw SQLite corpus or hold a long-lived snapshot.

The source-side worker (elevated only for historical reads) produces
newline-delimited bounded JSON, one record per window. The importer
runs without sudo as the workstation/server user and reports aggregate
`new_nodes`, `new_edges`, `new_evidence`, and session/document counts.
Re-running the pilot is idempotent for the same source evidence.

A failed batch may leave **previous successful candidate-only documents**
committed, which is safe to resume by re-running. This is not atomic
publication of a navigable graph; NAV-05 will own that separate process.
All imported edges remain `status=candidate` and the database
remains `release_status=not_built`.

**Important:** This is a bounded field test, not a background daemon,
automated cross-zone mining, or proof of walkability. If the HDD's ingestion
or file verification becomes slow, stop the pilot and resolve NAV-00 first.
