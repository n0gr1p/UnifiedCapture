# ADR-0001: Separate raw telemetry from published navigation intelligence

- **Status:** Accepted architectural direction; implementation not yet delivered
- **Decision date:** 2026-11-13
- **Owner:** UnifiedCapture (evidence + mining + publication); UnifiedCombat (runtime consumer)
- **Tracker:** [Navigation intelligence implementation tracker](navigation-implementation-tracker.md)
- **Related:** [UnifiedCombat consumer contract](https://github.com/n0gr1p/unifiedcombat/blob/master/docs/navigation_intelligence_consumer.md) · [RemoteXI boundary](https://github.com/n0gr1p/RemoteXI/blob/master/docs/UNIFIEDCAPTURE_NAVIGATION_BOUNDARY.md)

## Decision

**Keep mass telemetry and all authoritative observational evidence on the large spinning disk. Mine it asynchronously into a much smaller, separately versioned SQLite navigation database on the NVMe. UnifiedCombat must plan and execute against published, validated navigation intelligence, not query the raw telemetry corpus during gameplay.**

```text
Six Windower clients (passive observations)
    -> UnifiedCapture recorder + durable uploader
    -> 20 TB HDD: raw SQLite, append-only archive, evidence, rebuild inputs
    -> offline/low-priority mining: walk candidates, discontinuities,
       directed transports, geometry, uncertainty and provenance
    -> validation + atomic publication
    -> NVMe: compact, versioned navigation SQLite (derived/rebuildable)
    -> bounded, read-only navigation query contract
    -> UnifiedCombat planner -> existing movement coordinator/dispatcher
```

RemoteXI/MCP remains an optional **diagnostic and inspection surface**. It is not a required transport for live movement, does not approve paths, and does not control the game as part of this pipeline. A future read-only runtime API/adapter must be explicitly specified, tested and secured; the exact network protocol is not yet decided.

## Rationale and rejected alternatives

The raw corpus grows with **time and number of observations** (millions or billions). The actionable corpus should instead grow mainly with **zone topology, useful alternatives, and decision metadata**. Keeping the entire historical SQLite database on NVMe would reimpose NVMe capacity as a ceiling on permanent capture. Running heavy queries against the raw HDD database in the movement loop would make gameplay depend on unpredictable random-I/O latency. Both alternatives are rejected.

The HDD is the long-term source of truth; the NVMe DB is a disposable, rebuildable **materialized navigation product**, not a second unbounded telemetry mirror. RAM and bounded caches may accelerate reads, but must not create a hidden requirement to fit the historical corpus on NVMe.

## Storage and operational boundaries

- **HDD / authoritative:** raw JSONL batch archives, source event/position SQLite, recording receipts, unmodified evidence, mining checkpoints and reproducibility metadata. The production deployment currently mounts `/home/peter/bulk-storage/UnifiedCapture/server-data` at `/data`; this is an operator-specific location, **not** a committed default or secret. Never replace or delete a raw archive solely because a derived edge exists.
- **NVMe / derived:** only compact graph, transport, landmark, cost, confidence, provenance pointers, build metadata and indexes needed for low-latency decisions. Put an explicit size budget and growth report in the publisher.
- **Ingestion priority:** preserve bounded latency and reliable receipts during mining. Mining is separately scheduled, resumable, and I/O-budgeted; no full historical re-scan on every runtime lookup. Historical scanning or backups must not hold ingestion offline.
- **Crash/data recovery:** raw archive and raw DB are the rebuild inputs; versioned navigation releases may be reconstructed. Document exact restore and WAL/snapshot rules before running a live-database miner at scale. Do not treat `immutable=1` as a safe shortcut against a changing WAL database.
- **Current migration follow-up (not resolved by this ADR):** HDD-backed `/v1/ingest` has returned HTTP 200, while `/v1/verify-file` experienced client disconnects / `BrokenPipeError` after expensive archival verification. Windows uploader defaults to a 15-second timeout. Measure verification latency and retry/backlog behavior before declaring operations stable; do not assume paired 200/500 log entries mean two independent verification failures.

## Evidence-to-action trust boundary

All coordinates are **zone-local XYZ with explicit zone IDs**. A position sample is not a walkable surface; an entity sighting is not a collision map; a menu request is not proof of successful activation.

- Never interpolate a walking edge across `discontinuity`, zone changes, missing anchors, unbounded sample gaps, or capture-session rotations.
- Preserve **directionality**. An observed A -> B transport does not imply B -> A; a zone pair does not imply walking.
- De-duplicate co-travel. Six simultaneously moving party members do not constitute six independent trials.
- Classify edges and transitions distinctly: `observed_walk_candidate`, `probable_transport`, `validated_walk`, `validated_transport`, `executable` (final enum names belong in the schema contract). Unsupported edges remain non-executable.
- Retain evidence references (recording session, event sequence/ranges, algorithm version, occasion count, distinct trials, time and confidence rationale) sufficient for auditing and rebuilding. Do not replicate every raw event into NVMe.
- Publish uncertain geography and unknown activation requirements honestly. No inferred NPC interaction or teleport is executable until its activation steps are validated.
- A successful path must be safety-checked against UC's current mode, combat/casting ownership, zone state and movement guards. The published DB grants no authority to issue `run`, `turn`, zoning, item-use or menu actions.

Existing experimental evidence: the offline Odyssey zone 298 detector reproduced five **probable, unverified** directed transports out of 387 in-zone discontinuities (15 clusters, threshold five independent occasions), with fixed arrival coordinates. These are useful fixtures, **not** navigable paths. The detector currently lives on UnifiedCapture's `feature/navigation-readonly-queries` branch ([draft PR #4](https://github.com/n0gr1p/UnifiedCapture/pull/4)).

## Derived navigation contract (design target, not implemented schema)

The schema/serialization slice must settle a minimal, indexed model:

| Entity | Minimum semantics |
| --- | --- |
| `release` | Immutable release ID, schema version, algorithm/build version, source dataset watermark, build timestamp, validation status, content checksum |
| `zone` | FFXI zone ID, coordinate-space assumptions, survey status; unknown regions remain unknown |
| `node` | Zone-local XYZ, stable identifier, geometric tolerance; not necessarily a recorded point |
| `walk_edge` | Directed endpoints, cost/length, traversability status, uncertainty, provenance summary |
| `transport` | Typed/directed entry and exit, possibly different zones; activation mode and validation status, no implied reverse |
| `landmark` | Optional NPC/waypoint/interaction references with confidence and source |
| `evidence_ref` | Compact references back to raw sessions/sequences and derivation rule; bounded diagnostic samples |

Indexes, exact table names, geometry representation and schema migrations are **open decisions**, to be resolved with fixtures in the first implementation slice. Runtime read paths must be bounded and must not expose arbitrary SQL or raw packet payloads.

## Mining and publication workflow

1. Read a **consistent** raw source (controlled snapshot or a proven incremental read protocol); use durable source watermarks and resume checkpoints. The mining worker is independently stoppable.
2. Extract candidate movement geometry and transport links while preserving exclusions, provenance and uncertainty; run deterministic fixture tests including six-box de-duplication, session rotation, large jumps and false-positive examples.
3. Validate graph integrity: zone and endpoint referential integrity, impossible geometry checks, directed edge semantics, no cross-gap walking, algorithm/schema compatibility, explainable candidate status.
4. Build a **new** staged derived SQLite database, run integrity and size checks, then atomically publish a complete immutable release. Failed/partial builds never replace the previous healthy release.
5. The runtime service advertises release ID/schema and serves **read-only, bounded, versioned** graph/route queries; clients pin or atomically switch releases and fail closed if mismatched or unavailable.
6. UnifiedCombat first uses **shadow-mode planning** (no movement effects), then an opt-in guarded execution phase reusing existing UC movement arbitration/dispatcher, with explicit stop/replan/fallback and field validation.

Incremental mining, release pruning and graph quality metrics come after a reliable deterministic full rebuild. A full rebuild must remain possible from authoritative historical evidence.

## Ownership boundaries

| Repository | Owns | Does not own |
| --- | --- | --- |
| [UnifiedCapture](https://github.com/n0gr1p/UnifiedCapture) | Capture, ingest, archival integrity, offline miners, candidate evidence, derived SQLite builder/publisher, read-only navigation service | Live movement |
| [unifiedcombat](https://github.com/n0gr1p/unifiedcombat) | Path-planning client, safety gating, movement execution via existing coordinator, in-game feedback | Scanning the raw historical DB or silently reclassifying evidence |
| [RemoteXI](https://github.com/n0gr1p/RemoteXI) | Optional bounded read-only observability/MCP tools | Navigation authority or required runtime path |

## Explicitly deferred

Exact tessellation/grid vs graph representation, navigability confidence thresholds, transport activation classification, path-cost heuristics, the runtime API wire format, cross-machine caching, incremental update granularity, and actual NVMe size limits require evidence-based slices. Do not present these as approved designs.

## Acceptance of the architecture

- The historical corpus can grow beyond NVMe capacity without forcing a raw-database migration.
- Slow or failed mining leaves ingestion and UC movement unaffected.
- Every published path is reproducible/auditable and has a clearly stated executable status.
- Runtime behavior is fast, read-only at the graph boundary, feature-gated, and reverts safely.
- Each implementation slice has acceptance tests and an issue link in the [tracker](navigation-implementation-tracker.md).
