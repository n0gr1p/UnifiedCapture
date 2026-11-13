# Offline teleport discovery (experimental)

This module is **read-only** and is not a navigation executor. It discovers
**probable directed in-zone transports**, never automatically approved walking
edges. It does not modify the recording addon, database, game client or MCP
server. It uses the existing SQLite event and position indexes.

## Run against the ingested archive

From the Linux repository directory (where compose mounts
./server-data to /data):

```sh
sudo python3 server/teleport_discovery.py \
  --db server-data/capture.sqlite3 --zone 298 --min-occasions 5
```

Omit `--zone` to scan all zones; use `--min-occasions 1` temporarily to
inspect low-evidence clusters (still never executable). The JSON response
includes the number of scanned/rejected discontinuities, entry and exit XYZ,
distinct characters/sessions, independent occasion counts, activation hints,
and a few exact session/sequence identifiers for manual evidence review.

This runs on the **host**, without rebuilding or restarting the Docker service.
If the host database path is not readable by the current account, running the
script with sudo avoids changing database ownership or permissions. The SQLite
connection itself uses `mode=ro` and `PRAGMA query_only=ON`.

## How it works

1. Read recorded `discontinuity` events via the existing
   `idx_events_type_time` index. Retain only
   `reason=large_position_jump` (recorder threshold: greater than 35 units).
2. Resolve the immediately preceding recorded position and the next
   `new_anchor` from the same session. Reject missing anchors, zone mismatches,
   invalid XYZ, mismatched new-anchor locations, and suspicious capture gaps
   (previous sample must be within eight seconds; new anchor within five).
3. Cluster **directed** A-to-B jump endpoints in the same zone, using separate
   three-dimensional entry and exit distance tolerances (default eight units).
   An A-to-B observation says nothing about B-to-A.
4. Deduplicate six-box co-travel: events within the same two-minute
   window count as **one** traversal occasion, even across six distinct
   characters and recording sessions. A repeat after that window can count
   as a new occasion.
5. Promote clusters observed on five or more independent occasions to
   `probable_transport`; keep everything `verified=false`.
6. Record proximity of recent menu/item interactions as **hints**, not proof
   of activation method. Unattributed jumps may be automatic triggers,
   server-side repositioning, lag artifacts, or other unknown mechanisms.

## Interpretation and limitations

- Repeated A-to-B jumps are evidence, **not proof** of a teleport. Even a
  high-count cluster requires in-game validation before autonomous traversal.
- Position samples are *not* evenly distributed over geography; repeated idle
  heartbeats must not be mistaken for distinct routes.
- A large in-zone jump may be automatic (Inner Ra'Kaznar), interactive
  (Odyssey conflux), or waypoint-based (Adoulin); nearby menu requests are
  correlational evidence only.
- A jump smaller than the recorder's 35-unit threshold will not appear in
  this detector. Neither will cross-zone teleports (which need separate
  zone-transition analysis).
- The clustering implementation is intentionally conservative and preliminary.
  It can split a broad trigger region or merge very close links. The two-minute
  cohort window is a heuristic, not a guaranteed identity of a gameplay trip.
- A valid teleport may have no interaction metadata; missing metadata must
  not be treated as proof that it is automatic.
- **Never generate walking lines between entry and exit of a discontinuity.**

Tests:

```sh
python3 -m unittest discover -s tests -p 'test_*.py' -v
```
