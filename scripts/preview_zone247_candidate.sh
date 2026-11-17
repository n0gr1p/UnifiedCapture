#!/usr/bin/env bash
# Produces a candidate-only waypoint preview; never moves a character.
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
nav_db="${UCAP_NAV_DB:-$repo/nav-data/navigation.sqlite3}"
out="${UCAP_ROUTE_REPORT:-$repo/nav-data/zone247_walk_preview.json}"

if [[ ! -f "$nav_db" ]]; then
  echo "Derived NVMe navigation DB missing: $nav_db" >&2
  exit 1
fi

# Do not replace a previous successful preview if the current route fails.
tmp="$(mktemp "$repo/nav-data/.zone247-preview.XXXXXXXX")"
trap 'rm -f "$tmp"' EXIT
python3 "$repo/server/nav_route_preview.py" \
  --nav-db "$nav_db" --zone 247 \
  --from-xyz=-20.5,111.0,8.0 \
  --to-xyz=1.5,114.5,8.0 \
  --max-distance 80 --max-stretch 2.5 > "$tmp"
mv "$tmp" "$out"

python3 - "$out" <<'PY'
import json
import sys
p = sys.argv[1]
with open(p, encoding="utf-8") as f:
    route = json.load(f)
print("Zone:", route["zone"])
print("Waypoints:", route["waypoint_count"])
print("Observed forward edges:", len(route["legs"]))
print("Distance:", route["distance_yalms"], "yalms")
print("Direct distance:", route["direct_yalms"], "yalms")
print("Detour ratio:", route["detour_ratio"])
print("Edges found in multiple sessions:",
      route["edges_seen_in_multiple_recording_sessions"])
print("Edges lacking source evidence:", route["edges_without_evidence"])
print("Release:", route["release_status"])
print("Executable:", route["approved_for_movement"])
print("Full audit:", p)
PY
