# NAV-11: replay observed UC walking outcomes from existing capture telemetry

**Status: draft; not deployed.** This is the outcome-learning portion of
READY / PROVEN / FAILED (NAV-04) and the guarded walker (UC NAV-09).
It does NOT install a new server, token, API route, Docker service, timer,
privilege, or movement control.

## Data path

1. The guarded NAV-09 walker in
   [UnifiedCombat NAV-11](https://github.com/n0gr1p/unifiedcombat/pulls)
   produces a directed-leg outcome: exact decimal 62-bit edge ID, event/run
   identity, leg indices, result and reason.
2. UC issues a **same-client Windower addon command**
   `lua c UnifiedCapture navout ...`. It does NOT send IPC to other
   logged-in characters and does NOT send HTTP requests.
3. The UnifiedCapture 0.2.5 recorder verifies local **Terrasjr, Rabao 247**,
   exact decimal ID, direction and result code, then appends a
   `nav_edge_outcome` event to its existing capture-session JSONL.
   It calls `flush()` immediately rather than waiting two seconds.
   Only after a successful flush does it reply locally to UC with
   `lua c UnifiedCombat nav-outcome-ack EVENT_ID`.
4. The existing uploader archives the JSONL **unchanged** using its normal
   checkpointed file offsets and existing authenticated
   `POST /v1/ingest` endpoint at port **8787**. No new HTTP writes.
5. NAV-10's existing **UID 10001 read-only HDD window** now emits stream
   version 2, preserving its original 10-field position rows and adding
   only a bounded, sanitized `outcomes` array when those events occur.
   The reader verifies event/session/sequence/zone/actor metadata against
   archived `raw_json`; never sends other raw event contents to the
   unprivileged graph writer.
6. The existing unprivileged NAV-10 worker accepts both legacy source
   stream v1 (when no nav events are present) and v2 with required
   outcome projections. It rejects missing/extra/duplicate or malformed
   event projections **before** writes. It imports walking evidence first,
   validates each outcome's graph edge/character/recorded XYZ, then
   appends to the NAV-04 idempotent outcome ledger.
7. Only after all imports and outcomes succeed does NAV-10 commit its
   raw-event rowid and last-position anchors. If the process crashes
   after graph updates but before cursor commit, the same original event
   ID replays without counting twice. Existing NVMe/HDD file-identity
   guards and per-worker locks remain.

## State rules

- A correctly mined directed edge starts **READY**.
- A UC-confirmed traversal near that edge's recorded directed endpoint
  moves it to **PROVEN**.
- **Two distinct UC navigation runs** reporting confirmed obstacles
  since the last successful traversal move it to **FAILED**.
- Combat, casting, manual movement, disconnect, timeout and other
  interruptions are stored as evidence but **do not** fail an edge.
- FAILED edges remain excluded from read-only path previews. A later
  confirmed success can restore PROVEN. New historical telemetry does not
  reset a failed edge.

The observer requires a canonical positive 62-bit decimal-string
`nav_edge_id_str`; Lua 5.1 cannot safely preserve such large numbers
as ordinary floating-point JSON numbers. The server checks UCNV node
geometry, correct zone and a contemporaneous captured XYZ position,
not only UC's Boolean confirmation.

## Operational scope

**Nothing is live yet.** The running 8787 API and NAV-10 worker/graph
are still on v1 until the explicit coordinated v2 migration. The
new stream reader, consumer and capture addon are staged together on
this development branch, based on NAV-04 [PR #21](https://github.com/n0gr1p/UnifiedCapture/pull/21).
Read-only v2 HTTP support is staged separately in
[NAV-06 API PR #20](https://github.com/n0gr1p/UnifiedCapture/pull/20).
UC guarded walking remains default-off on its own branch.

Do not deploy only one part of this change: if a new
`nav_edge_outcome` reaches the HDD while an old v1 reader/consumer
processes that row, the old consumer would skip it; it must not
advance the navigation checkpoint past a result that has not been
consumed. Start from a verified snapshot and stop the NAV-10 user
timer for the coordinated v2 graph/checkpoint handoff.

**Limits requiring live acceptance:** a Windower addon command is
not cryptographic provenance; it depends on UnifiedCapture being
loaded and recording on the SAME client. UC uses in-memory receipt
retries, but pending events can be lost if both addons exit before
a successful ACK. The server applies outcomes in source rowid order,
so a far-behind historical NAV-10 cursor delays newly recorded
outcomes until the worker catches up; do not claim instantaneous
learning. No standalone live cutover or field-traversal acceptance
has yet occurred.

## Test commands

```sh
lua5.1 tests/selftest_nav_outcome.lua
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

Tests cover exact edge ID, actor and zone checks; immediate
JSONL flush; malformed outcome rejection; all existing ingestion
and candidate mining; v2 source stream while preserving 10-field
walking rows; success/failure/interrupt states; crash-before-cursor
idempotence; rejection of missing, spoofed, wrong-zone or physically
inconsistent outcomes; no unprivileged HDD open. GitHub CI runs both.
