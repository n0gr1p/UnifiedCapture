# Navigation intelligence implementation tracker

**Status:** 2026-11-18: first supervised walking proof succeeded; automated ingestion-to-navigation pipeline, live queries and multi-zone travel remain unbuilt. Architectural decision accepted 2026-11-13. **This document is the durable cross-repository index** for small, independently testable work slices. Update the checkbox/status and linked PR when a slice changes. Do not infer completion merely from an issue being created.

- [Canonical ADR-0001: raw telemetry -> derived navigation intelligence](adr/0001-raw-telemetry-to-navigation-intelligence.md)
- [GitHub epic #5](https://github.com/n0gr1p/UnifiedCapture/issues/5)
- [UnifiedCombat consumer boundary](https://github.com/n0gr1p/unifiedcombat/blob/master/docs/navigation_intelligence_consumer.md)
- [RemoteXI observability boundary](https://github.com/n0gr1p/RemoteXI/blob/master/docs/UNIFIEDCAPTURE_NAVIGATION_BOUNDARY.md)

## CURRENT HANDOFF — 2026-11-18 (read first in a new chat)

**User's actual goal:** Continuously transform bulk UnifiedCapture HDD telemetry into a usable, up-to-date **compact NVMe navigation graph**; make that graph accessible to UnifiedCombat; plan and execute zone-to-zone journeys selected in-game, via MCP, or by a saved activity. The user wants **implementation now**, not more architecture or a monolithic release process. Work in small, directly testable steps; the user prefers **one command/checkpoint at a time**. Use the existing GitHub connector/repos and preserve running clients, source archives, uncommitted files and engine safety.

### Demonstrated, not hypothetical

- **2026-11-18 live field test PASS:** In Rabao (FFXI zone 247), Terrasjr (PLD/WAR) stood near **Home Point #2**, at approximately (-20.97, 111.02, 8.15), within 0.49 yalms of the recorded route start (-20.5, 111.0, 8.0). A derived route of **28 waypoints, 27 directed edges, 41.37 yalms**, with **22.28 yalms** straight-line distance and **1.86x detour**, successfully drove real character movement. In-game chat displayed **`UC nav MVP: STARTED:28_WAYPOINTS`** followed by **`UC nav MVP: ARRIVED`**. The character visibly moved along a non-straight path and stopped without bouncing back. This proves **capture -> conservative parse/mine -> directed graph path preview -> CSV -> UC dispatcher -> actual movement** for ONE manually supervised route; it **does not** prove all graph edges, zone coverage or transports.
- **UC engine ownership finding:** Initial start with combat engine ON failed with **`MOVEMENT_OWNER:scheduler`**. Pilot branch was corrected so the manual one-shot run requires **`//uc stop`**; engine ON is deliberately rejected. Proper navigation/scheduler arbitration is **still unbuilt**, not something to bypass with global-lock tampering.
- **Pilot graph on Ubuntu:** `/home/peter/UnifiedCapture-NavDev/nav-data/navigation.sqlite3` (NVMe-derived), zone 247 previously reported ~1,131 nodes and 1,249 candidate directed edges; data/release remains `not_built` and unvalidated for general unattended use. Route exporter produced `/home/peter/UnifiedCapture-NavDev/nav-data/nav_mvp_zone247.csv`, a 28-point CSV transferred once by SCP to Windows.
- **Windows Windower Git tree:** `C:\Program Files (x86)\Windower\addons\UnifiedCombat`, current field-test branch **`feature/nav-mvp-nyzul-field-test-20261118`**, SHA **`9d26750d20cd5850ea2bc01ad1c7a19a5149c882`** at successful field test, [draft PR #106](https://github.com/n0gr1p/unifiedcombat/pull/106) based on **`feature/nyzul-lamp-diagnostics-20261020`**. `local_ownership_flags.lua` was locally MODIFIED and must not be overwritten. `data/nav_mvp_zone247.csv`, logs and build outputs are UNTRACKED. User stopped engine and reloaded UC; verify state again rather than assuming it persists.
- **Ubuntu Git tree:** `~/UnifiedCapture-NavDev` checked out **detached** at SHA **`453b848fee53c90e9dfb7f1896db9fd19d80a15c`**, branch source **`feature/nav-03-walk-candidates`**, [draft PR #16](https://github.com/n0gr1p/UnifiedCapture/pull/16). Bounded miner `server/walk_candidate_miner.py`, derived DB schema `server/derived_navigation_db.py`, directed Dijkstra preview `server/nav_route_preview.py`, one-off exporter `scripts/export_nav_mvp_pilot.sh`. This pilot is **NOT** an autonomous ingestion worker.
- **Raw HDD dataset:** Historical authoritative SQLite on Linux at `/home/peter/bulk-storage/UnifiedCapture/server-data/capture.sqlite3`, archive on HDD; running ingestion uses Docker and must not be stopped for experiments. Existing [raw read-only query PR #4](https://github.com/n0gr1p/UnifiedCapture/pull/4) is **draft**, independent of candidate graph PR #16. RemoteXI [draft PR #28](https://github.com/n0gr1p/RemoteXI/pull/28) exposes five read-only `ucap_*` historical telemetry tools via the existing MCP bridge; they are not dynamic graph queries or movement commands. Do not print credentials or infer deployment/merge state from a draft PR.
- Older UC [draft PR #105](https://github.com/n0gr1p/unifiedcombat/pull/105) is **master-based** and unsuitable as a replacement for the user's more advanced Nyzul Git tree; use **PR #106** as the field-test reference.

### Current gaps: do not report these as completed

- **No automated full-history backfill or continuous new-capture mining**, durable source watermarks/checkpoints, scheduling, I/O load verification or graph release promotion; current miner requires manually selected bounded sessions.
- **No published, stable read-only derived-graph route API**, no arbitrary origin/destination route access in live UC, no world landmark directory. A working offline directed preview is not an exposed service.
- **No working navigation-vs-combat scheduler handoff**; current pilot requires combat engine OFF.
- **No verified cross-zone transport catalog or executable zone-transition itinerary**, no NPC/Home Point menu activation. Historic zone-change observations alone cannot justify transport execution.
- **No MCP `uc_nav_plan/start/status/cancel` or saved-activity travel phase.** Existing `ucap_*` tools are read-only historical evidence.

### Implementation order and acceptance (user priority)

1. **NEXT — [NAV-10 incremental miner, issue #14](https://github.com/n0gr1p/UnifiedCapture/issues/14):** Implement a **separate, opt-in/resumable worker** with historical bounded backfill and incremental new-session/sequence checkpoints, reusing `walk_candidate_miner.py` and indexed raw queries. Read HDD SQLite **read-only**; write only derived NVMe SQLite and local worker checkpoint/metrics. Start with small fixture and a bounded real batch, verify idempotence/restarts and zero effect on ingestion, then expand safely. This is now the priority before more UC commands. No automatic `sudo` scheduled job before access/I/O verification.
2. **NAV-05/06/07:** Turn updated graph into a recoverable last-good snapshot and a **small authenticated, bounded read-only route/landmark service**, using directed shortest-path code already present. Provide reason-coded NO_ROUTE; expose release/schema/watermark. There is no need for a separate enterprise workflow, but never serve half-written or unknown edges as unattended executable routes.
3. **[NAV-09, UC issue #104](https://github.com/n0gr1p/unifiedcombat/issues/104):** Integrate UC dynamic waypoint response and a **navigation movement owner in the existing coordinator** so normal combat engine can be running without competing or bypassing cast/combat safety. Explicit user start/stop, stuck/zone/conflict abort. Do not replace the current Nyzul branch with old PR #105.
4. **[NAV-12 cross-zone planner](https://github.com/n0gr1p/UnifiedCapture/issues/17):** Multi-zone directed itinerary of walking segments plus verified transport links, with NO_ROUTE for unsupported or unverified activation.
5. **[NAV-13 UC transport executor](https://github.com/n0gr1p/unifiedcombat/issues/107):** Typed walk/zone-exit/zone-confirm legs, and only independently verified Home Point/NPC/spell/item activations with expected arrival and cancellations.
6. **[NAV-14 MCP and activities](https://github.com/n0gr1p/RemoteXI/issues/31):** Typed `uc_nav_plan` (read), `uc_nav_start` (explicit confirm/exact SIDs/plan revision), `uc_nav_status` (read), `uc_nav_cancel` (explicit); then activity PREPARE -> TRAVEL -> ARRIVED -> APPLY only after verified arrival.
7. **NAV-00/04 crosscutting:** Benchmark HDD ingestion/verify under mining; provenance and validation distinguish observed candidate from independently tested executable. Preserve direction, zone XYZ, session gaps, and individual occasion evidence. Six simultaneous clients are not six independent demonstrations.

### First task for a new ChatGPT conversation

**Start with implementing NAV-10's bounded, checkpointed graph backfill worker** in a fresh feature branch derived from the actual current UnifiedCapture nav-miner branch. Check branch heads, PR statuses, schema and real raw-DB indexes first. Deliver code + tests through GitHub; do not run bulk scanning, restart Docker, mutate HDD SQLite, merge PRs or edit live UC by assumption. After tests, give the user **exactly one** safe bounded Ubuntu test command; await output before further deployment. Update this tracker and issue #14 with actual tested results.

Suggested new-thread prompt:

> Continue the FFXI navigation project from n0gr1p/UnifiedCapture epic #5 and docs/navigation-implementation-tracker.md (CURRENT HANDOFF section). The 28-waypoint Rabao walking MVP succeeded in-game on 2026-11-18. I want the real automatic capture-to-navigation pipeline and then cross-zone MCP/activity travel. Start implementing NAV-10's bounded, resumable historical + incremental miner with GitHub tools. Preserve the current UC Nyzul branch, raw HDD telemetry and my local changes. Give me one test command at a time; update the tracker as you go.

---

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
| 1 · Define product | [NAV-01](https://github.com/n0gr1p/UnifiedCapture/issues/7) | v1 derived SQLite release schema + fixture, provenance and fail-closed status | ADR | **In progress** — [draft PR #15](https://github.com/n0gr1p/UnifiedCapture/pull/15), CI passed; host NVMe bootstrap pending |
| 2 · Mine evidence | [NAV-02](https://github.com/n0gr1p/UnifiedCapture/issues/8) | Deterministic **non-executable** Odyssey directed transport candidates; deduped trials | NAV-01 | Open |
| 2 · Mine evidence | [NAV-03](https://github.com/n0gr1p/UnifiedCapture/issues/9) | Conservative walk candidate edges, gap/zone/elevation tests | NAV-01 | **In progress** — [draft PR #16](https://github.com/n0gr1p/UnifiedCapture/pull/16), tests passed; host pilot pending |
| 3 · Validate | [NAV-04](https://github.com/n0gr1p/UnifiedCapture/issues/10) | Review, approve, revoke and explain candidates; verified activation required | NAV-02 and NAV-03 | Open |
| 4 · Publish | [NAV-05](https://github.com/n0gr1p/UnifiedCapture/issues/11) | Stage, validate and atomically publish compact NVMe SQLite; rollback test | NAV-01, miner fixture; NAV-04 before any executable approvals | Open |
| 5 · Serve | [NAV-06](https://github.com/n0gr1p/UnifiedCapture/issues/12) | Secure bounded, versioned, read-only **published** graph API | NAV-05 | Open |
| 6 · Plan | [NAV-07](https://github.com/n0gr1p/UnifiedCapture/issues/13) | Side-effect-free single-zone validated walking route / no-route planner | NAV-05/06 | Open |
| 7 · Observe in UC | [NAV-08](https://github.com/n0gr1p/unifiedcombat/issues/103) | UC navigation adapter with shadow-mode diagnostics and **zero movement effects** | NAV-06/07 | Open |
| 8 · Guarded use | [NAV-09](https://github.com/n0gr1p/unifiedcombat/issues/104) | Explicit opt-in single-zone walking via UC dispatcher, stop/replan/fallback tests | NAV-04, NAV-08 | Open |
| **Priority now: backfill + incremental mining** | [NAV-10](https://github.com/n0gr1p/UnifiedCapture/issues/14) | Incremental/checkpointed/low-I/O mining; ingestion load-test safety | NAV-02/03/05 | Open |
| Optional inspection | [NAV-11](https://github.com/n0gr1p/RemoteXI/issues/29) | Read-only published release/provenance MCP inspection; no UC dependency | NAV-05/06 | Open |
| Cross-zone route | [NAV-12](https://github.com/n0gr1p/UnifiedCapture/issues/17) | Plan walking + *verified directed* transport legs or NO_ROUTE | NAV-02/07 | Open |
| Cross-zone travel | [NAV-13](https://github.com/n0gr1p/unifiedcombat/issues/107) | Execute verified zoning legs with UC's coordinator, fail safely | NAV-09/12 | Open |
| MCP and activities | [NAV-14](https://github.com/n0gr1p/RemoteXI/issues/31) | User-authorized plan/start/status/cancel; activity travel phase | NAV-09/12/13 | Open |

Issue URL is authoritative for per-slice acceptance tests; this table is the order-of-work and status index.

## Recommended efficient implementation sequence

**First:** NAV-00 (operational safety) and NAV-01 (schema/fixtures) can proceed independently. Do not launch a sustained miner against live HDD ingestion before NAV-00's load/timeout baseline.

**First tangible navigation-intelligence artifact:** NAV-01 -> NAV-02 -> a reduced NAV-05 *candidate-only* release. Prove that the five Odyssey jumps are reproducible, have evidence and remain **non-executable**. This isolates correctness from runtime safety.

**First usable automatic walking route:** NAV-03 -> NAV-04 -> NAV-05 -> NAV-06 -> NAV-07 -> NAV-08 (shadow) -> NAV-09 (field-validated opt-in). UC need not wait for transport activation or the optional MCP slice.

**Updated 2026-11-18 priority:** NAV-10's bounded, resumable worker is now the immediate implementation task; the first supervised walking field proof already succeeded via PR #106. Old sequential prerequisites describe production hardening, not a reason to defer pipeline work. Multi-zone planning/execution and MCP are now explicitly tracked in NAV-12/13/14. NAV-11 remains optional read-only inspection.

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
