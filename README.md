# UnifiedCapture

**Passive FFXI navigation and entity-observation telemetry** for Windower 4 / Lua 5.1. No movement commands, combat integration, packet injection, remote networking, or gameplay modification.

## Installation

Copy the repository files to `Windower/addons/UnifiedCapture/`, then run `lua load UnifiedCapture`. The 0.2 update includes a new required file: **`entity_tracker.lua`**.

Recording begins when the addon loads in a valid character session. If not ready, use `//ucap on` after zoning. `//ucap off` stops; `//ucap status` reports state. Run independently on each character you want to observe.

## Storage

`Windower/addons/UnifiedCapture/data/captures/<character>_<session>.jsonl`

Append-only JSONL records; each line has timestamp, session, sequence, optional observer state, event type, and event-specific fields. `session_start.schema=2` marks the 0.2 format; prior schema 1 files remain valid historical captures. Entity observation samples are batched (64 rows or 2 seconds), not forcibly flushed after every NPC update. Movement recording remains at a 300 ms sampling limit, 0.35-unit distance minimum and four-second idle heartbeat, with large-jump and zone discontinuity markers.

### Entity observation events

`entity_observation` has `observation_kind`: `first_seen`, `position_update`, `identity_update`, `still_visible`, or `last_seen`. It includes:
- `entity_id`, `entity_index`, `entity_epoch` (local visibility-window counter)
- `entity_type` (raw spawn packet type when available), `windower_spawn_type` (mob-cache type when available), `entity_kind` and `entity_kind_source` (notably Windower spawn type 16 = monster; packet type 3 = monster, packet type 0 = ambiguous NPC/casket)
- `entity_x/y/z` and `position_source` (`packet` or `last_known`)
- `entity_name` and `entity_name_source` (`packet_update` means provisional; `windower_mob` means a matching name was verified from Windower's mob cache). Once a verified Windower name is available, a later shortened packet name cannot overwrite it. The recorder continues throttled cache lookups while a packet-sourced name remains provisional. The 0x05B spawn packet itself **does not contain a name**
- `observer_x/y/z`, `zone`, `character`, timestamp, packet ID and injection status

Incoming `0x05B` is a **spawn/appearance**, never a menu response; `0x00E` is an NPC update with a field mask; `0x065` can refine the position of an already tracked entity. Updates lacking the position bit cannot replace stored coordinates with zeroes. Repeated identical appearance/update packets are suppressed while identity changes, movement and periodic five-second confirmation are retained; never count repeated packets as additional mobs. Classifications are retried when the entity cache becomes available.

An incoming `0x00E` mask bit 2 provides HP/status context, held in memory only. A `last_seen` event may contain `last_hp_percent`, `last_hp_age`, `last_entity_status`, and `death_signal` (recent zero HP or an explicit dead status). These are supporting clues, not proof of the cause; the `disappearance_cause` remains `unknown`. No per-HP-change combat events are emitted. A `last_seen` on the incoming `0x00E` visibility bit **cannot by itself establish why the entity vanished**: death, despawn and out-of-range are all possible. When zoning, the tracker emits `entity_visibility_reset` with a count and clears identity state rather than creating false per-mob disappearance events. IDs are scoped to zone and recording session; the per-appearance epoch distinguishes a repeated visibility window.

### NPC menu lifecycle

The recorder also captures the metadata of incoming `0x032`/`0x033`/`0x034` menu opens, incoming `0x05C` supplementary menu parameters (without raw menu data), outgoing `0x05B` choices, outgoing `0x05C` warp requests, and incoming `0x052` releases. Outgoing `0x01A` category 0 interactions seed an interaction even when no menu-open packet arrives. A release is retained for a brief two-second continuation window because observed Home Point flows can resume with a further menu choice. Correlated events have `interaction_id` (within a capture session), `interaction_stage`, `interaction_origin`, and matching NPC/menu identifiers when available. This is **packet correlation**, not proof of successful interaction. Unconfirmed menu contexts expire after two minutes; an explicitly opened menu remains correlatable for up to ten minutes of user dwell. A release still has only a two-second continuation window. All contexts reset when zoning; orphan choices remain recorded.

### Known limits and safety

- Windower packet definitions were checked for 0x05B/0x00E/0x065 and NPC menu packets; live verification is still required for the specific Windower version.
- Missing entity names are explicitly omitted instead of guessed. Inbound packet parsing can precede Windower mob-cache updates.
- `0x065` can include player repositioning, so it never creates a previously unknown monster on its own.
- `0x00E` updates without a known spawn type remain `unknown`; never treat all NPC updates as monsters.
- Observer positions are captured when packet handling begins. This is a measurement of reported coordinates, not evidence of visibility radius by itself.
- Data includes no chat, packet bytes, arbitrary menu parameters, or private payload strings.
- Entity type and coordinates support future coverage-aware mob density maps, but the heatmap, path planner and NAS/SQLite importer are intentionally separate, later projects.

## Repeat-test scenario

Run the same Mhaura → Western Adoulin → Foret de Hennetiel → Western Adoulin trip used in the first capture. Approach and retreat from a few mob groups if convenient. Stop with `//ucap off`. Compare the new JSONL's schema, `entity_observation` identities/coordinates, `last_seen` events, and NPC menu `interaction_id` sequences to the old recording. The old file is a v0.1 baseline, not an equivalent entity-data sample.

## Tests

```sh
luac5.1 -p UnifiedCapture.lua capture.lua entity_tracker.lua tests/selftest.lua tests/selftest_entity.lua
lua5.1 tests/selftest.lua
lua5.1 tests/selftest_entity.lua
```

## v0.2.2 live acceptance details

After the repeat run, inspect monster classification rates on 0x00E-first sightings, the number of distinct IDs versus periodic `still_visible` records, last-seen death-signal evidence near the Broadleaf Palm, and whether Home Point/Waypoint interaction requests, choices and releases share interaction IDs. Short-range disappearances without death context remain ambiguous and must not be used as automatic visibility-radius ground truth.

### Canonical-name regression (v0.2.2)

The Marjami Ravine capture showed names alternating between abbreviated packet values and complete cache values (e.g. `CliffclingerTo` versus `Cliffclinger Toad`, `DirtcakedJagil` versus `Dirtcaked Jagil`). Verified Windower names now take precedence permanently for that entity visibility window, while packet-only names remain tagged provisional until the cache can resolve them. Earlier provisional sightings can be joined to later canonical observations by zone, entity ID/index and visibility epoch during offline import; no name is guessed from a truncated string.

## v0.2.2 live acceptance findings

The 2026-10-23 Niightsiide replay captured 2,159 valid, continuously sequenced JSONL events over ~9m05s, including 1,474 entity observations and 613 position records. No verified Windower name ever reverted to an abbreviated packet name within an entity visibility window. Packet-only provisional names remain explicitly tagged. 53 distinct monster IDs were resolved through Windower's mob type.

**In-zone Waypoint warps produce transient incompatible coordinates:** in the replay the new-area entities arrived while the observer's pre-warp position was still reported (~350-390 coordinate units from those entities); moments later, old-area entities disappeared while the observer was at the destination. These raw events are preserved intentionally. Visibility-radius analysis must exclude observations near in-zone warp requests and subsequent large-position discontinuities; it must not interpret those transient distances as visibility limits. Entity identities/coordinates remain useful once the position has settled.

The Task Delegator menu remained open for approximately 129 seconds in the replay, outlasting the former 120-second correlation TTL. A longer timeout for explicitly opened menus is covered by an offline regression test; no additional game capture is needed for that boundary case.

## Server-side permanent collection (in progress)

The optional `feature/server-ingestion` branch adds a **separate** Python 3
standard-library server and uploader. The Windower Lua recorder remains local,
passive, and unchanged. The Docker service archives original JSONL upload batches,
imports validated events into SQLite, and deduplicates by `session + sequence`;
a Windows-side uploader follows every configured capture directory using a durable
byte-offset checkpoint and network retries.

See [deployment and backfill instructions](docs/ingestion.md), including
private-LAN binding, an explicit secret token, backup requirements and a
server-side integration test suite. No server deployment occurs automatically
when upgrading the Windower addon.

## Item-use requests and travel attribution (v0.2.4)

Outgoing `0x037` item-use packets are retained as `packet_observation` events
with `packet_kind=item_use_request`, `item_bag` and `item_slot`. When Windower's
inventory snapshot resolves that slot, the recorder also emits `item_id` and
its English `item_name` (e.g., Warp Ring or Instant Warp). If the snapshot or
resource name is unavailable, it still records the original request without
inventing a name. This is passive observation; no inventory dumps or raw packet
payloads are stored. Spell action requests remain available through `0x01A`.

A request does **not** prove item activation or successful teleportation.
Offline analysis should correlate requests with subsequent zone changes and
preserve unknown or ambiguous causes. In-zone teleports continue to produce
explicit position discontinuities rather than walking edges.

## 24/7 capture rotation and verified local retention (v0.2.3)

UnifiedCapture v0.2.3 automatically rotates its **file recording sessions**
after **24 hours or 64 MiB**, whichever comes first, even while the FFXI
character remains logged in. Each closed segment ends with `session_stop`
(reason `rotation_duration` or `rotation_size`), and the new file begins
with `session_start` and a fresh position anchor. A failure to open a new
file leaves the existing recording active. Zone changes do not rotate files.

The standalone Windows uploader can optionally delete completed capture files
after **seven days**, but only when the Linux server certifies an exact
SHA-256 match reconstructed from every archived upload chunk, uninterrupted
byte offsets, complete SQLite event sequences, and a final `session_stop`.
This verification is **not** inferred from an upload offset or batch HTTP 200
response alone. There is no deletion of current sessions, unverified files,
or incomplete/crashed recordings. The default uploader behavior remains
**no deletion** until `--retention-days 7` is deliberately enabled.

Older uploader checkpoints are replayed once after the upgrade to construct
verifiable chunk receipts; the server deduplicates their already-imported
events. See [deployment and safety details](docs/ingestion.md).
