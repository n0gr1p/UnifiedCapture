# Derived navigation SQLite bootstrap (NAV-01)

Architecture: [ADR-0001](adr/0001-raw-telemetry-to-navigation-intelligence.md). Tracker: [NAV-01](https://github.com/n0gr1p/UnifiedCapture/issues/7).

This initial schema belongs to **the NVMe**, not the raw telemetry/archives
mounted at \`/data\` inside Docker. It is a separate, compact, rebuildable
navigation product. This first slice creates **empty versioned tables**, not a
populated or executable route graph.

## Initialize on the host's NVMe

From the UnifiedCapture repository on the Linux host (assuming the repository
is on NVMe; verify filesystem placement if it has moved):

\`\`\`sh
python3 server/derived_navigation_db.py init --db nav-data/navigation.sqlite3
python3 server/derived_navigation_db.py inspect --db nav-data/navigation.sqlite3
\`\`\`

The target file is ignored by Git via the existing \`*.sqlite3\` pattern.
The command creates its parent directory if needed. No \`sudo\`, Docker
restart, raw database connection, or telemetry migration is required.
**Do not use the raw \`server-data/capture.sqlite3\` path.** Existing
non-navigation SQLite files are refused without modification. Re-running
\`init\` on the same schema is harmless and does not overwrite rows.

## First schema

- \`PRAGMA application_id = 0x55434E56\` ("UCNV")
- \`PRAGMA user_version = 1\`
- \`nav_release\` (no release until a builder publishes; cannot claim routes are ready)
- \`zone\`, \`node\` (zone-local XYZ and height), \`walk_edge\` (directed, same-zone)
- \`transport\` (directed, may be cross-zone, never implied walking)
- \`evidence_ref\` (pointers back to source session/sequence, not copied raw events)
- \`landmark\` (optional position-tied metadata)

\`candidate\` is the default for inferred edges. A transport cannot become
\`executable\` without nonempty activation information; future dedicated
validation/publishing logic must also require field evidence. The schema
is intentionally conservative: no automatic transport activation and no UC
movement effects. Coordinates are observations, not guaranteed collision-safe
geometry or a complete navmesh.

Inspect reports \`"release_status": "not_built"\` until a miner and publisher
produce a checked release. The file must never be treated as navigable merely
because the tables exist.

## Validation

\`\`\`sh
python3 -m unittest discover -s tests -p 'test_derived_navigation_db.py' -v
\`\`\`

Tests exercise schema bootstrap, raw-DB overwrite protection, idempotence,
directed same-zone walk edges, non-executable transport defaults, and
provenance constraints.

## Next slices

- [NAV-03 walking extraction](https://github.com/n0gr1p/UnifiedCapture/issues/9)
- [NAV-02 directed transport candidate extraction](https://github.com/n0gr1p/UnifiedCapture/issues/8)
- [NAV-05 staged, validated atomic publication](https://github.com/n0gr1p/UnifiedCapture/issues/11)

No raw telemetry is scanned or written in NAV-01. Ingestion must continue
independently, and no use of this file in the UC runtime is permitted yet.
