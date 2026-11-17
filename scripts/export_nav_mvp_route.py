#!/usr/bin/env python3
"""Export one reviewed nav-preview JSON to the deliberately simple UC MVP CSV.

Local file conversion only. No HDD queries, SQLite writes, network, or
movement side effects. NOT a formal route-validation / publication system.
"""
import argparse
import json
import math
from pathlib import Path

HEADER = "# UC_NAV_MVP_V1 zone=247"
MAX_POINTS = 101


def export(preview):
    if not isinstance(preview, dict):
        raise ValueError("Expected a route preview object")
    if (preview.get("preview_schema_version") != 1
            or preview.get("zone") != 247
            or preview.get("approved_for_movement") is not False
            or preview.get("release_status") != "not_built"):
        raise ValueError("Unsupported candidate-preview contract")
    points = preview.get("waypoints")
    legs = preview.get("legs")
    if (not isinstance(points, list) or not isinstance(legs, list)
            or not 2 <= len(points) <= MAX_POINTS
            or len(legs) != len(points) - 1
            or preview.get("waypoint_count") != len(points)
            or preview.get("edges_without_evidence") != 0):
        raise ValueError("Invalid waypoint or evidence structure")
    dist = preview.get("distance_yalms")
    ratio = preview.get("detour_ratio")
    if (type(dist) not in (float, int) or not 0 < dist <= 100
            or type(ratio) not in (float, int) or not 1 <= ratio <= 2.5):
        raise ValueError("Route distance or detour too large")

    coords = []
    for p in points:
        if (not isinstance(p, list) or len(p) != 3 or
                any(type(v) not in (int, float) or not math.isfinite(v)
                    or abs(v) > 100000 for v in p)):
            raise ValueError("Invalid coordinate")
        coords.append(tuple(float(v) for v in p))
    observed = 0.0
    for i, leg in enumerate(legs):
        step = math.dist(coords[i], coords[i+1])
        if (type(leg) is not dict or leg.get("from_index") != i
                or leg.get("to_index") != i + 1
                or leg.get("edge_status") != "candidate"
                or type(leg.get("observations")) is not int
                or leg["observations"] < 1
                or type(leg.get("length_yalms")) not in (float, int)
                or not 0.1 <= step <= 6.0
                or abs(coords[i+1][2] - coords[i][2]) > 1.5
                or abs(step - leg["length_yalms"]) > 0.05):
            raise ValueError("Route contains a non-continuous or unobserved leg")
        observed += step
    if abs(observed - dist) > 0.15:
        raise ValueError("Inconsistent graph distance")
    return HEADER + "\n" + "".join(
        f"{x:.2f},{y:.2f},{z:.2f}\n" for x, y, z in coords
    )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--preview", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args()
    preview = json.loads(Path(args.preview).read_text(encoding="utf8"))
    content = export(preview)
    destination = Path(args.out)
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_name(destination.name + ".tmp")
    tmp.write_text(content, encoding="ascii")
    tmp.replace(destination)
    print(f"Exported {len(content.splitlines()) - 1} route waypoints to {destination}")


if __name__ == "__main__":
    main()
