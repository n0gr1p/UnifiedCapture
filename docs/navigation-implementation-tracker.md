# Navigation intelligence implementation tracker

**Status:** Planned; architectural decision accepted 2026-11-13. **This document is the durable cross-repository index** for small, independently testable work slices. Update the checkbox/status and linked PR when a slice changes. Do not infer completion merely from an issue being created.

- [Canonical ADR-0001: raw telemetry -> derived navigation intelligence](adr/0001-raw-telemetry-to-navigation-intelligence.md)
- [GitHub epic #5](https://github.com/n0gr1p/UnifiedCapture/issues/5)
- [UnifiedCombat consumer boundary](https://github.com/n0gr1p/unifiedcombat/blob/master/docs/navigation_intelligence_consumer.md)
- [RemoteXI observability boundary](https://github.com/n0gr1p/RemoteXI/blob/master/docs/UNIFIEDCAPTURE_NAVIGATION_BOUNDARY.md)

## Non-negotiable constraints

1. Preserve all authoritative JSONL/raw SQLite on 20 TB HDD. The NVMe holds **only compact derived release databases** (and bounded temporary artifacts), never the whole historical corpus.
2. Ingestion remains independent of miners. Mining is offline/read-only against a consistent source, resumable and resource-budgeted; no live UC query depends on scanning raw telemetry.
3. No interpolation across discontinuities/zone transitions/recording rotations. Zone-local XYZ, directed links, actual provenance, confidence and unknown state must survive mining.
4. Only **explicitly validated executable** graph elements may inform automatic movement. A probable teleport is not an executable action. Approval requires real activation evidence.
5. Publication is all-or-nothing with a verified last-known-good release; UC feature is default-off and its existing movement coordinator remains sole movement-effect owner.
6. Each slice should fit one narrow PR with deterministic tests and explicit acceptance. Do not combine unrelated code migrations, deploy without a checkpoint, or silently advance a blocked dependency.

## Current foundation — existing, not promised work

- Raw ingestion, archival and historic read-only navigation query API exist on UnifiedCapture's unmerged [draft PR #4](https://github.com/n0gr1p/UnifiedCapture/pull/4). Experimental Odyssey [teleport discovery](https://github.com/n0gr1p/UnifiedCapture/blob/feature/navigation-readonly-queries/docs/teleport_discovery.md) is a **read-only candidate detector**.
- RemoteXI's five read-only historical evidence MCP tools exist on [draft PR #28](https://github.com/n0gr1p/RemoteXI/pull/28), not a released navigation API.
- UC has an established [movement coordinator/dispatcher](https://github.com/n0gr1p/unifiedcombat/blob/master/docs/movement_coordinator_implementation_checklist.md), which any future executor must reuse.
- Operator verified a copy of approximately 53.49 GB / 2,018,370 files to the HDD, rsync metadata comparison with zero transfers, and HDD SQLite `PRAGMA quick_check = ok`. Docker active bind mount now points to HDD. Normal `/v1/ingest` returned HTTP 200, but expensive `/v1/verify-file` encountered timeout/disconnects; sustained uploader/verification performance remains open. Historic observation counts are **not** proof of route coverage.

## Small slices (each issue is the acceptance checklist)

| Milestone | Slice / issue | Output and hard test | Depends on | Status |
| --- | --- | --- | --- | --- |
| 0 · Preserve ingest | [NAV-00](https://github.com/n0gr1p/UnifiedCapture/issues/6) | HDD ingestion/retention measured and safe under slow verification + retries | None | Open |
| 1 · Define product | [NAV-01](https://github.com/n0gr1p/UnifiedCapture/issues/7) | v1 derived SQLite release schema + fixture, provenance and fail-closed status | ADR | Open |
| 2 · Mine evidence | [NAV-02](https://github.com/n0gr1p/UnifiedCapture/issues/8) | Deterministic **non-executable** Odyssey directed transport candidates; deduped trials | NAV-01 | Open |
| 2 · Mine evidence | [NAV-03](https://github.com/n0gr1p/UnifiedCapture/issues/9) | Conservative walk candidate edges, gap/zone/elevation tests | NAV-01 | Open |
| 3 · Validate | [NAV-04](https://github.com/n0gr1p/UnifiedCapture/issues/10) | Review, approve, revoke and explain candidates; verified activation required | NAV-02 and NAV-03 | Open |
| 4 · Publish | [NAV-05](https://github.com/n0gr1p/UnifiedCapture/issues/11) | Stage, validate and atomically publish compact NVMe SQLite; rollback test | NAV-01, miner fixture; NAV-04 before any executable approvals | Open |
| 5 · Serve | [NAV-06](https://github.com/n0gr1p/UnifiedCapture/issues/12) | Secure bounded, versioned, read-only **published** graph API | NAV-05 | Open |
| 6 · Plan | [NAV-07](https://github.com/n0gr1p/UnifiedCapture/issues/13) | Side-effect-free single-zone validated walking route / no-route planner | NAV-05/06 | Open |
| 7 · Observe in UC | [NAV-08](https://github.com/n0gr1p/unifiedcombat/issues/103) | UC navigation adapter with shadow-mode diagnostics and **zero movement effects** | NAV-06/07 | Open |
| 8 · Guarded use | [NAV-09](https://github.com/n0gr1p/unifiedcombat/issues/104) | Explicit opt-in single-zone walking via UC dispatcher, stop/replan/fallback tests | NAV-04, NAV-08 | Open |
| Scale after MVP | [NAV-10](https://github.com/n0gr1p/UnifiedCapture/issues/14) | Incremental/checkpointed/low-I/O mining; ingestion load-test safety | NAV-02/03/05 | Open |
| Optional inspection | [NAV-11](https://github.com/n0gr1p/RemoteXI/issues/29) | Read-only published release/provenance MCP inspection; no UC dependency | NAV-05/06 | Open |

Issue URL is authoritative for per-slice acceptance tests; this table is the order-of-work and status index.

## Recommended efficient implementation sequence

**First:** NAV-00 (operational safety) and NAV-01 (schema/fixtures) can proceed independently. Do not launch a sustained miner against live HDD ingestion before NAV-00's load/timeout baseline.

**First tangible navigation-intelligence artifact:** NAV-01 -> NAV-02 -> a reduced NAV-05 *candidate-only* release. Prove that the five Odyssey jumps are reproducible, have evidence and remain **non-executable**. This isolates correctness from runtime safety.

**First usable automatic walking route:** NAV-03 -> NAV-04 -> NAV-05 -> NAV-06 -> NAV-07 -> NAV-08 (shadow) -> NAV-09 (field-validated opt-in). UC need not wait for transport activation or the optional MCP slice.

**Later:** NAV-10 increases scale and freshness after full rebuild works; NAV-11 enables easier inspection when useful. Multi-zone or automated transport activation is **not scoped** to this first walking MVP and needs its own explicit safety design and issues.

## Definition of done for every slice

- [ ] Scope limited to one issue; change clearly separates raw evidence, derived build, published query, and live execution.
- [ ] Unit/integration fixtures reproduce expected results and negative cases; tests actually run and results linked.
- [ ] Provenance, uncertainty, confidence and directional semantics preserved as applicable.
- [ ] Existing capture/uploader/UC behavior regression-tested; no accidental default-on movement.
- [ ] Any new SQLite query benchmarked against the appropriate workload and does not silently cause full HDD scans on runtime paths.
- [ ] PR links, evidence and issue closure updated here; no completed claim without actual verification.
- [ ] Operator deployment data and auth tokens stay out of repository; release/rollback and restart instructions are documented where relevant.

## Resume guide for a future conversation or contributor

1. Read [ADR-0001](adr/0001-raw-telemetry-to-navigation-intelligence.md) and this tracker; use issue links rather than re-deriving the decision.
2. Inspect open [epic #5](https://github.com/n0gr1p/UnifiedCapture/issues/5), existing [UnifiedCapture PR #4](https://github.com/n0gr1p/UnifiedCapture/pull/4), [RemoteXI PR #28](https://github.com/n0gr1p/RemoteXI/pull/28), and current branch heads before editing. Do not assume draft PRs have merged.
3. Select **one** earliest unblocked issue. Confirm current code/tests and live service state; implement a narrow, independently verifiable change.
4. Report actual observed tests and update the relevant issue + tracker. Never overwrite an operator's live SQLite/JSONL or infer a transport action from correlation alone.
