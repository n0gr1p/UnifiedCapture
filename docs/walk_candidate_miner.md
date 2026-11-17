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
