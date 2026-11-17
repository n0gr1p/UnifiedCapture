#!/usr/bin/env bash
# Convert the existing zone-247 NVMe graph into a static one-shot UC test route.
# Read-only graph operations; only writes a small export CSV in nav-data.
set -euo pipefail
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
nav_db="${UCAP_NAV_DB:-$repo/nav-data/navigation.sqlite3}"
preview="$repo/nav-data/zone247_walk_preview.json"
route="$repo/nav-data/nav_mvp_zone247.csv"
if [[ ! -f "$nav_db" ]]; then
  echo "Derived navigation database missing: $nav_db" >&2
  exit 1
fi
python3 "$repo/server/nav_route_preview.py" \
  --nav-db "$nav_db" --zone 247 \
  --from-xyz=-20.5,111.0,8.0 \
  --to-xyz=1.5,114.5,8.0 \
  --max-distance 80 --max-stretch 2.5 > "$preview.tmp"
mv "$preview.tmp" "$preview"
python3 "$repo/scripts/export_nav_mvp_route.py" \
  --preview "$preview" --out "$route"
echo "This route remains unverified in-game: supervise the manual //uc nav start test." >&2
