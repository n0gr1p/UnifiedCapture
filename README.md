# UnifiedCapture

Standalone **passive** Windower 4 / Lua 5.1 navigation recorder for FFXI. No movement commands, combat integration, injected packets, or gameplay modification.

## Installation

Copy files to `Windower/addons/UnifiedCapture/`, then run `lua load UnifiedCapture`.

Capturing starts when loaded in a valid character session. If character state is not ready, issue `//ucap on` after zoning. `//ucap off` stops; `//ucap status` reports state. Run independently on each character.

## Output

`Windower/addons/UnifiedCapture/data/captures/<character>_<session>.jsonl`

Each line is a standalone JSON object. Events include a versioned `session_start`, positions, discontinuities, zone changes, provisional packet observations and session end. Timestamp is a real-time Unix clock with fractional seconds; zone, X/Y/Z, heading, name, and other fields are present when available.

Sampling: 300 ms ceiling, 0.35-unit minimum, four-second heartbeat. Large position jumps become discontinuities, not walkable graph edges. Events are buffered in groups of 64 or flushed every two seconds and on packet/zone boundaries.

Raw packets and chat content are never stored. Packet IDs and menu field names are **hypotheses to validate with live traces**, not claimed to be verified universal FFXI menu semantics. Movement source is not yet classified. NPC menu choices may depend on character progression; the recorder observes attempts, not confirmed results.

## Roadmap

1. Verify real movement, NPC dialogue, and zone transition captures.
2. Add reliable movement-origin signals and correlated NPC interaction outcomes.
3. Offline importer, spatial graph construction and directed route visualization.
4. Only then explore navigation execution with live feedback.
