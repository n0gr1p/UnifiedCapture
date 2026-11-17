#!/usr/bin/env bash
# NAV-03 candidate-only pilot. HDD read is privileged; NVMe write is NOT.
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
raw_db="${UCAP_RAW_DB:-/home/peter/bulk-storage/UnifiedCapture/server-data/capture.sqlite3}"
nav_db="${UCAP_NAV_DB:-$repo/nav-data/navigation.sqlite3}"
manifest="$repo/docs/pilot_zone247_sessions.json"

# The raw HDD directory is root-protected: ordinary users cannot stat it.
# Its existence/readability is verified by the privileged read-only extractor.
if [[ ! -f "$nav_db" || ! -f "$manifest" ]]; then
  echo "Missing navigation DB or pilot manifest; no mining performed." >&2
  exit 1
fi
# -m canonicalizes paths even when the raw directory is not searchable.
if [[ "$(readlink -m "$raw_db")" == "$(readlink -m "$nav_db")" ]]; then
  echo "Refusing to use the raw database as a derived navigation target." >&2
  exit 1
fi
device="$(findmnt -no SOURCE --target "$nav_db")"
if [[ "$device" != /dev/nvme* ]]; then
  echo "Derived navigation DB is not on the verified NVMe mount ($device); refusing." >&2
  exit 1
fi

python3 "$repo/server/derived_navigation_db.py" inspect --db "$nav_db" >&2
echo "Bounded candidate-only pilot: zone 247, 5 sessions, at most 2 x 1200 positions each." >&2
echo "Raw SQLite is read-only; writes go only to $nav_db as the current user." >&2

sudo python3 "$repo/server/walk_candidate_miner.py" extract-batch \
  --raw-db "$raw_db" --zone 247 --manifest "$manifest" \
  --max-positions 1200 --max-windows 2 \
| python3 "$repo/server/walk_candidate_miner.py" import-batch \
  --nav-db "$nav_db"
