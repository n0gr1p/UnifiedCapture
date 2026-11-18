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

### Work started after this handoff (code draft, **not deployed**)

- [UnifiedCapture draft PR #18 — NAV-10 incremental candidate worker](https://github.com/n0gr1p/UnifiedCapture/pull/18), branch `feature/nav-10-incremental-worker-20261118` (created 2026-11-18 from PR #16), adds `server/nav_incremental_worker.py` and deterministic worker tests. It scans bounded **append-only `events.rowid` windows** on the raw HDD read-only, keeps last per-session anchors and durable checkpoint state in a separate NVMe SQLite, and imports candidate-only directed edges to the existing derived graph. Source watermark commits **after** idempotent imports; `--dry-run` is read-only. [Operator runbook](https://github.com/n0gr1p/UnifiedCapture/blob/feature/nav-10-incremental-worker-20261118/docs/nav-10-incremental-worker.md).
- **Current limitations:** this is a **manual, opt-in worker only**, not a deployed scheduled service. A late/out-of-order archival chunk for an already processed session causes `OUT_OF_ORDER_SESSION_SEQUENCE` and stops before checkpoint advancement; targeted per-session repair remains unbuilt. Real HDD performance, access permissions, cursor progression and a safe recurring schedule require operator test. **Do not claim the bulk corpus has been backfilled** merely because CI passes.
- **Next immediate checkpoint:** verify PR #18 CI, then user runs **one bounded dry-run command** from the runbook on Ubuntu. No ingestion restart, privileged background service or raw data modification.

### Latest live test — NAV-10 (2026-11-18)

A **200-event read-only host dry run succeeded** against the real HDD capture SQLite (via `sudo`): 149 positions, 106 candidate edges, 2 observed boundaries, 2 candidate documents, no writes. Exact output and safe next checkpoint are recorded in [NAV-10 issue #14](https://github.com/n0gr1p/UnifiedCapture/issues/14). Next check is Unix raw-read/NVMe-write permissions. **Do not use sudo for a mutating import** until privilege separation/file ownership is verified; no live import or schedule has yet run.

### NAV-10 permissions follow-up — 2026-11-18

A live host-user read-only SQLite open failed because the ingest directory is owned by UID 10001 and temporary WAL/SHM sidecars may vanish. **Draft PR #18 now adds a split reader/writer implementation**: read bounded raw events *inside the existing ingest container as UID 10001*, then pipe JSON to an unprivileged host consumer that updates Peter-owned NVMe graph/checkpoint. Synthetic split-worker tests passed; no live split-run yet. The **immediate one-step checkpoint** is a read-only container Python test documented in [NAV-10 issue #14](https://github.com/n0gr1p/UnifiedCapture/issues/14). Do not give Peter write permission to raw ingestion, run the graph writer as root, or assume the worker has been scheduled.

### NAV-10 first persistent live candidate import — 2026-11-18

**SUCCESS:** split UID 10001 read-only container reader -> unprivileged `peter` importer processed first **200** raw event rowids (149 positions, 106 detected walking candidate pairs, 2 boundaries), inserted **106 new nodes / 105 directed edges / 105 evidence records** into NVMe, and reported checkpoint advancement **0 -> 200**. No Docker restart or raw HDD mutation was requested. See [NAV-10 issue #14](https://github.com/n0gr1p/UnifiedCapture/issues/14) for exact JSON. **Next checkpoint:** read back persisted cursor and verify `peter` ownership of state and graph before processing rowids 201+. Production scheduling, all-history processing and late-session correction remain open.

### NAV-10 consecutive live incremental batch PASS — 2026-11-18

**Second UID-split live import succeeded:** processed event rowids **201–400** (`after_rowid=200`, `last_rowid=400`), detected 48 candidate walk pairs, inserted 49 additional nodes, 48 directed edges, and 48 evidence records as Peter-owned NVMe writes. Combined first two imports added **155 nodes / 153 edges / 153 evidence**. Read-only container ingestion remained separate; no restart requested. [NAV-10 issue #14](https://github.com/n0gr1p/UnifiedCapture/issues/14) has exact JSON output. Next verify persisted cursor / graph read-only, then benchmark safe historical throughput and address late-arriving sessions. Do not claim complete 43 GB backfill or production scheduling.

### NAV-10 readback verified — 2026-11-18

Independent read-only verification of the 2-batch split-worker pilot returned **checkpoint 400**, **1,286 nodes**, **1,402 directed edges**, **1,493 evidence refs**, and **zero executable edges**. These are total existing graph counts; **155 nodes / 153 edges / 153 evidence** were newly inserted by NAV-10 across two separate live batches. Next checkpoint: run one timed 2,000-event **dry run** to choose a safe historical-backfill budget. See [NAV-10 issue #14](https://github.com/n0gr1p/UnifiedCapture/issues/14). No scheduled multi-million-event backfill or late-arrival repair exists yet.

### NAV-10 timed read-only throughput sample — 2026-11-18

A UID-10001 container-reader -> unprivileged host-consumer **2,000-event dry run succeeded** from checkpoint 400: 463 positions, 401 candidate pairs, 3 boundaries, 5 documents, 0 invalid positions, proposed end rowid 2400; **real time 0.421 seconds** (~4,750 events/sec for that short read-only sample). **No writes; committed checkpoint remains 400**. Next: time one 2,000-event **actual NVMe candidate import** (same bounded split workflow, only remove `--dry-run` from consumer); then introduce manual I/O-budgeted backfill loop. [NAV-10 issue #14](https://github.com/n0gr1p/UnifiedCapture/issues/14) contains exact output. No schedule and no claim of full backfill.

### NAV-10 2,000-event live write benchmark — 2026-11-18

Real split-pipeline import from rowids **401–2400** passed: 463 positions, 401 candidate pairs, **370 new nodes / 391 edges / 400 evidence refs** (total NAV-10 additions across three runs: 525/544/553). Wall-clock `6.943s` included a `sudo` password prompt and is not a clean import throughput measurement. Reported committed cursor now 2400; independent readback pending. Next implement a bounded, pause-configurable manual backfill runner with no background service. See [issue #14](https://github.com/n0gr1p/UnifiedCapture/issues/14).

### NAV-10 budgeted backfill runner drafted — 2026-11-18

[Draft PR #18](https://github.com/n0gr1p/UnifiedCapture/pull/18) now has `scripts/nav_backfill_bounded.py` and test fixtures for bounded, multi-window, user-invoked historical backfill: read-only UID-10001 container extraction, unprivileged NVMe candidate import, strict max batches/events/pause, per-batch persisted cursor verification and fail-closed errors. **Default is one read-only dry-run window; writes require `--apply`**. Live three-batch-by-hand import proof succeeded through rowid 2400, but the **new orchestration script has not yet been run on uuserver**. Next: fetch updated branch, dry-run new runner, then supervised finite apply run. No scheduled service or late-chunk repair. See [NAV-10 issue #14](https://github.com/n0gr1p/UnifiedCapture/issues/14).

### NAV-10 live bounded runner dry-run PASS — 2026-11-18

At head `460b9e8`, the user successfully ran `scripts/nav_backfill_bounded.py --max-events 2000 --max-batches 3` without `--apply`: exactly **one** read-only 2,000-event window, 469 positions, 390 candidate pairs, 3 boundaries, 0 invalid samples, proposed rowid 2400→4400, **0 writes**, elapsed **0.587s**. It deliberately did not execute all 3 batches. Next: supervised finite `--apply --max-events 2000 --max-batches 3 --pause-seconds 5` and inspect outputs plus ingestion health. [NAV-10 issue #14](https://github.com/n0gr1p/UnifiedCapture/issues/14) contains the exact result. No automated ongoing service yet.

### NAV-10 first live 3-batch bounded backfill PASS — 2026-11-18

Operator ran `scripts/nav_backfill_bounded.py --apply --max-events 2000 --max-batches 3 --pause-seconds 5` at branch head `460b9e8`; **all three 2k-event batches succeeded**, with per-batch checkpoint readbacks **2400→4400→6400→8400**, 0 invalid positions and **942 new nodes / 1,079 directed candidate edges / 1,179 evidence references** in the finite run, taking 11.809 seconds including two 5-second pauses. Across all NAV-10 persistent live imports to date: **1,467 new nodes / 1,623 new edges / 1,732 new evidence**. Next independently verify cursor 8400 and capture service health, then benchmark ingest impact before any recurring schedule. Full historical backfill and late-session repair **remain open**. [NAV-10 issue #14](https://github.com/n0gr1p/UnifiedCapture/issues/14) has exact per-window results.

### NAV-10 independent checkpoint and ingest-health verified — 2026-11-18

Operator independently confirmed persisted **cursor 8400** and `/health` returned `{"status":"ok","schema":1}` from `192.168.96.45:8787` after the finite three-batch backfill. Basic service availability is verified; impact on ingest latency, queueing, and HDD I/O **not yet measured**. Next take read-only process usage baseline and evaluate a finite run under observation; full historical backfill, late-chunk correction, and scheduled execution remain open. See [NAV-10 #14](https://github.com/n0gr1p/UnifiedCapture/issues/14).

### NAV-10 initial live ingest resource snapshot — 2026-11-18

After successful 3-batch NAV-10 run (cursor **8400**; `/health` status ok), single `docker stats --no-stream` read: ingest CPU **0.01%**, memory **3.871GiB/46.91GiB**, cumulative block I/O **8.83GB read / 234GB written**, PID count 1. **This is not a live I/O rate or proof of zero backfill impact.** Next test finite 5 x 2000-event applied windows with pauses and check service health, checkpoint and stats. See [NAV-10 #14](https://github.com/n0gr1p/UnifiedCapture/issues/14).

### NAV-10 supervised 10k bounded run and ingest health — 2026-11-18

The operator's 5 x 2,000-event supervised **apply** run succeeded: per-batch cursor **8400→10400→12400→14400→16400→18400**, zero invalid positions, **1,039 new nodes / 1,421 directed candidate edges / 1,885 evidence records**; **22.94 seconds** including four 5-second pauses. Independent persisted cursor read returned **18400**. Total new inserts across all NAV-10 imports: **2,506 nodes / 3,044 edges / 3,617 evidence**. Post-run ingestion `/health` status was ok, latency **0.000805s**; Docker snapshot CPU 1.38%, memory 3.871GiB, cumulative read/write block I/O 8.84GB/234GB. This is **not a simultaneous-load benchmark**, only post-run availability. Next measure max raw event rowid via one read-only container query to estimate backlog, then design safe noninteractive scheduling. Full-history backfill and late-chunk repair remain open. [NAV-10 issue #14](https://github.com/n0gr1p/UnifiedCapture/issues/14).

### NAV-10 raw event backlog measured — 2026-11-18

Live UID-10001 read-only query returned **MAX(events.rowid)=31,153,494**; independently verified miner cursor **18,400**. Approximate unprocessed rowid span **31,135,094** (~15,568 x 2k-event windows; gaps/deletes and ongoing ingestion may change actual count). Current supervised throughput is not evidence of sustained performance across this backlog. Next inspect host Docker authorization (no permissions changes) before designing **noninteractive, resource-budgeted scheduling**. No persistent timer installed; avoid adding peter to root-equivalent Docker group or blanket passwordless sudo. Late out-of-order history repair remains open. [NAV-10 issue #14](https://github.com/n0gr1p/UnifiedCapture/issues/14).

### NAV-10 existing Docker socket authorization — 2026-11-18

The operator's `id -nG` includes **docker** and Docker socket is `root:docker srw-rw----`. Existing `peter` identity should be able to invoke Docker without `sudo` (not yet directly tested); Docker group is **root-equivalent**, so no new privileges should be added. Next verify `docker compose exec -T ingest ...` unprivileged, remove the runner's unnecessary sudo, add CI tests, then create an opt-in user-owned timer that applies bounded batches. No timer is installed yet. See [NAV-10 #14](https://github.com/n0gr1p/UnifiedCapture/issues/14).

### NAV-10 sudo-free reader implementation + CI — 2026-11-18

PR #18 code/test commit `e6285be` updated `scripts/nav_backfill_bounded.py` to call **`docker compose exec -T ingest` without sudo**, using existing `peter` docker-group/root-equivalent socket authorization; tests adjusted, CI green ([run 44366042237](https://github.com/n0gr1p/UnifiedCapture/actions/runs/44366042237)). Docs head `42b3a42`. Next refresh NavDev and run **one dry-run window** from persisted cursor 18,400 with no sudo prompt, then prepare an **opt-in** finite-rate user systemd timer. No timer or continuous backfill installed yet. [NAV-10 issue #14](https://github.com/n0gr1p/UnifiedCapture/issues/14).

### NAV-10 first sudo-free field PASS; opt-in user timer drafted — 2026-11-18

Operator updated NavDev to `42b3a42` and verified one **sudo-free read-only batch** from checkpoint 18,400 toward rowid 20,400: 2,000 events, 117 positions, 52 candidate pairs, 0 invalid positions, **0 writes**, 0.409s and no sudo prompt. PR #18 now includes user-systemd `.service`/`.timer` files plus parser tests and [deployment guide](https://github.com/n0gr1p/UnifiedCapture/blob/feature/nav-10-incremental-worker-20261118/docs/nav-10-user-timer.md), latest head `3b06eba`, CI green. Timer **not installed/enabled**; conservative 5x2k windows per invocation, 5s pauses, first trigger 5m, subsequent 15m after completion, nice/idle I/O on host writer, no missed-run catch-up. Next read-only check `loginctl show-user peter -p Linger && systemctl --user is-system-running` before any unit installation. No live latency guarantee, 31m backlog and late-chunk repair still open. [NAV-10 issue #14](https://github.com/n0gr1p/UnifiedCapture/issues/14).

### NAV-10 systemd user-manager / lingering check — 2026-11-18

Operator command returned `Linger=no` and `systemctl --user is-system-running` → `running`. User manager works during active session; recurring user timer cannot be relied on after logout/reboot unless lingering is explicitly enabled. No linger, timer or service changes made. Next fetch PR #18, install the two systemd **unit definitions only**, `daemon-reload`, then verify both units remain disabled. [NAV-10 issue #14](https://github.com/n0gr1p/UnifiedCapture/issues/14).

### NAV-10 user systemd unit installation disabled — 2026-11-18

Operator updated NavDev to `3b06eba`, installed the NAV-10 user service and timer definitions under `~/.config/systemd/user`, ran `systemctl --user daemon-reload` and confirmed `service=static`, **`timer=disabled`**. The timer's `PRESET enabled` is only the default preset, **not** its current enabled state. Neither unit was started; no recurring backfill and no change to `Linger=no`. Next: verify parsed unit definitions and inactive/disabled states read-only, then request explicit opt-in for activation and any linger change. [NAV-10 #14](https://github.com/n0gr1p/UnifiedCapture/issues/14).

### NAV-10 installed-unit verification PASS — 2026-11-18

User ran `systemd-analyze --user verify` with no errors; `systemctl --user show` confirms **timer loaded/inactive/disabled**, **service loaded/inactive/static**. Definitions installed but neither active; `Linger=no` unchanged. Next optional operator opt-in: `systemctl --user enable --now unifiedcapture-nav-backfill.timer`, inspect scheduled next run, monitor actual journal and ingest health. First timer trigger ~5m; repeat 15m after the service completes, capped 5x2000 per invocation. Do not call timer operational until enabled and live run verified. [NAV-10 issue #14](https://github.com/n0gr1p/UnifiedCapture/issues/14).

### NAV-10 first opt-in timer enabled — 2026-11-18

User explicitly enabled `unifiedcapture-nav-backfill.timer`; systemd created the `timers.target.wants` symlink and listed the **first scheduled run at Wed 2026-11-18 11:20:29 MST** (4m59s from activation). **Timer enabled, but no scheduled import verified yet.** Service still capped at 5x2k rows/run with five-second pauses; subsequent start 15m after completion. Last independent cursor: **18,400**. `Linger=no` unchanged, so no guaranteed headless continuation after logout/reboot. Next inspect service journal after first firing, persisted cursor, and ingestion `/health`. If import fails, stop timer; don't claim historic coverage or repair. [NAV-10 issue #14](https://github.com/n0gr1p/UnifiedCapture/issues/14).

### NAV-10 first timer-triggered unattended import PASS — 2026-11-18

At **11:20:31 MST**, `unifiedcapture-nav-backfill.timer` triggered the first actual user-level backfill service. Systemd journal shows successful finite completion **11:20:53**, five 2k-event batches, 10k total, **0 invalid positions**, reported cursor **18,400→28,400**, **450 new nodes / 660 directed candidate edges / 883 evidence refs**, elapsed 22.586s including four 5s pauses, CPU time 1.287s. Cumulative NAV-10 live inserts now **2,956 nodes / 3,704 edges / 4,500 evidence**. The service success is confirmed; independently re-read cursor, ingestion health and next timer fire before declaring continuous operation. Timer remains enabled, but **`Linger=no`**—no reliable logout/reboot continuation until separately approved. Source historical backlog still ~31m rowids and late-session repair remains open. [NAV-10 issue #14](https://github.com/n0gr1p/UnifiedCapture/issues/14).

### NAV-10 first scheduled run independently verified — 2026-11-18

Operator independently read the saved worker cursor **28,400**, verified ingestion `/health` returned `{"status":"ok","schema":1}`, and observed the next systemd user timer firing scheduled for **Wed 2026-11-18 11:35:53 MST** after first successful service run at 11:20:31–11:20:53. Timed backfill is now **enabled, one actual run successful, and next run scheduled**, not yet reboot-persistent because latest linger state is `Linger=no`. Next decision: operator may separately run `sudo loginctl enable-linger peter` and read back `Linger=yes` to permit background continuation after logout and reboot. No claim of full historical mining or out-of-order repair. [NAV-10 issue #14](https://github.com/n0gr1p/UnifiedCapture/issues/14).

### NAV-10 systemd lingering enabled — 2026-11-18

User explicitly ran `sudo loginctl enable-linger peter`; subsequent `loginctl show-user peter -p Linger` returned **`Linger=yes`**. Already-enabled `unifiedcapture-nav-backfill.timer` can now remain active after logout and start automatically on boot (not reboot-tested). Earlier first scheduled batch succeeded, independently verified NVMe cursor **28,400**, ingestion health ok, and next timer scheduled at **11:35:53 MST**. Next confirm timer remains enabled/active and observe another run; full 31m-rowid historical backfill and out-of-order session repair remain open. [NAV-10 issue #14](https://github.com/n0gr1p/UnifiedCapture/issues/14).

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
