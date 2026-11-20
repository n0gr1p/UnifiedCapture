# NAV-04 walking-edge lifecycle: READY, PROVEN, FAILED

## Decision

A directed walking edge that passes the **existing** NAV-03/NAV-10
position-continuity, speed, elevation, zoning and discontinuity filters is
**READY** as soon as it is imported to the NVMe navigation graph. There is
**no manual route-by-route approval requirement**. The full provenance
(source session + event sequence) is still retained and the original HDD
data remains authoritative.

An individual directed walking edge has one of three states:

| State | Source | Route behavior |
| --- | --- | --- |
| `ready` | Passed telemetry mining filters | May be planned/traversed when separately armed UC navigation ownership is installed |
| `proven` | UC explicitly confirmed successful traversal of that directed edge | May be planned; proof is a runtime outcome, not a count of six correlated recordings |
| `failed` | Two separate UC navigation runs confirmed blocked passage of the same directed edge since its most recent success | Excluded from route planning until a new confirmed traversal demonstrates recovery |

Both READY and PROVEN are route-eligible; FAILED is not.
An interruption from combat, casting, zoning, a manual stop, disconnect,
timeout, or competing movement owner is **not evidence the edge is blocked**.
The ledger persists such interruptions for analysis without changing the
state. A confirmed successful traversal can recover FAILED -> PROVEN.

The model still permits internal `confidence` and `evidence_count` fields.
Mining does **not** manufacture confidence by treating six party members
walking together as independent trials; fresh provenance updates do not
reset PROVEN or FAILED.

## Versioned implementation

- Graph schema v2 changes only `walk_edge.status` to the
  `ready/proven/failed` enum and adds the `edge_outcome` audit ledger.
  **Transport statuses are unchanged.** Detecting a teleport still does not
  authorize automatic NPC/menu/item/spell activation.
- The existing NAV-10 miner writes newly qualified edges as READY with the
  original 0.5-yalm quantization, provenance and stable IDs, and leaves
  existing PROVEN/FAILED rows untouched via `INSERT OR IGNORE`.
- `nav_edge_lifecycle.record_outcome()` is a **local library function only**.
  It rejects unknown edges, unconfirmed traversals/blocks, invalid
  failure reasons, conflicting IDs and duplicate blocks in one run. Its
  event ledger is append-only, and old events replay without new effects.
  This is NOT a remotely accessible write API.
- `nav_route_preview` continues to plan on recorded directed edges, but
  now considers only READY/PROVEN walking edges. It never invents
  reverse/diagonal links or crosses zones. It excludes FAILED edges and
  continues to return `approved_for_movement=false`; it does **not**
  dispatch UC movement or imply a published executable graph.

## Migration and deployment guardrail

**No operator or live service should switch to this branch yet.**

The existing live graph and incremental worker use v1, as do the
deployed port-8787 route API and Windower shadow client. Changing the
schema in place would be unsafe and break those readers/writers.

`nav_edge_migrate.py --source OLD --destination NEW` implements an
explicit, non-overwriting, transactional SQLite **snapshot copy**
from a v1 graph containing only `candidate` walking edges.
It maps each such edge to READY and preserves node IDs, direction,
`evidence_ref`, `transport`, graph counts and source data. A failure
leaves the original untouched and does not overwrite the destination.
It does not publish, deploy or replace a live database.

**Critical NAV-10 checkpoint dependency:** The incremental miner binds
its cursor/last-position ledger to the graph file's absolute path, device
and inode. A newly copied v2 graph has a different identity. Before
production cutover we must implement/test an explicit, coordinated
checkpoint handoff with the NAV-10 worker paused and a consistent graph
snapshot; never reset the cursor or silently skip HDD data.

Cutover must also update the existing UnifiedCapture API route modules
and the Windower NAV-08 parser to accept `ready/proven` edge statuses.
Keep port 8787, the same `UCAP_API_TOKEN`, and the NVMe mount read-only.
No second API port or token.

**No automatic movement is enabled by this PR.** The UC NAV-09 movement
owner must first be implemented with explicit opt-in, scheduler arbitration,
casting/combat/manual-priority preemption, per-leg completion evidence,
stuck detection, and an immediate stop. Only then may the executor
consume READY/PROVEN walking edges under guarded conditions.

## Tests

`python3 -m unittest discover -s tests -p 'test_*.py' -v`

The v2 suite verifies accepted filtering -> READY; route inclusion;
confirmed UC traversal -> PROVEN; two independent confirmed block runs ->
FAILED; duplicate/unconfirmed/transient attempts do not punish a route;
old successful event replay cannot resurrect FAILED; a real later success
recovers FAILED; FAILED is absent from route results; miner replay cannot
overwrite failed state; unrelated raw data and original v1 graph are
unchanged by copy-only migration; foreign keys/provenance survive. Existing
ingestion and worker tests remain active.
