"""NAV-03/NAV-07 bridge: read-only, non-executable directed route preview.

Consumes ONLY the derived navigation SQLite; no raw HDD, UC movement,
route approvals, or generated reverse/diagonal edges.
"""
import argparse
import collections
import heapq
import json
import math
import sqlite3
from pathlib import Path

import derived_navigation_db

MAX_NODES = 200000
MAX_EDGES = 500000
MAX_WAYPOINTS = 201


def coordinates(value):
    if isinstance(value, str):
        pieces = value.split(",")
        if len(pieces) != 3:
            raise ValueError("Coordinates must be comma-separated X,Y,Z")
        try:
            value = tuple(float(p.strip()) for p in pieces)
        except ValueError as exc:
            raise ValueError("Coordinates must be finite X,Y,Z numbers") from exc
    if (not isinstance(value, (tuple, list)) or len(value) != 3
            or not all(type(x) in (float, int) and math.isfinite(x)
                       and abs(x) <= 1000000 for x in value)):
        raise ValueError("Coordinates must be finite X,Y,Z numbers")
    return tuple(float(x) for x in value)


def nearest_node(nodes, query, radius):
    choice = min(
        ((math.dist(point, query), node) for node, point in nodes.items()),
        default=None,
    )
    if choice is None or choice[0] > radius:
        raise ValueError(
            f"No recorded navigation node within {radius:g} yalms of "
            f"{[round(n, 2) for n in query]}"
        )
    return choice[1], choice[0]


def shortest_directed(start, goal, outgoing, max_distance):
    """Dijkstra on recorded forward edges; no invented connections."""
    costs = {start: 0.0}
    previous = {}
    heap = [(0.0, start)]
    while heap:
        distance, node = heapq.heappop(heap)
        if distance > costs[node] + 1e-9:
            continue
        if distance > max_distance:
            break
        if node == goal:
            break
        for other, edge_id, length in outgoing.get(node, ()):
            candidate = distance + length
            if candidate > max_distance:
                continue
            if candidate < costs.get(other, math.inf) - 1e-9:
                costs[other] = candidate
                previous[other] = (node, edge_id, length)
                heapq.heappush(heap, (candidate, other))
    if goal not in costs:
        raise ValueError("No directed candidate path within route-distance limit")
    route = []
    current = goal
    while current != start:
        if current not in previous:
            raise ValueError("Directed graph path reconstruction failed")
        parent, edge_id, cost = previous[current]
        route.append((parent, current, edge_id, cost))
        if len(route) >= MAX_WAYPOINTS:
            raise ValueError("Candidate route exceeds waypoint limit")
        current = parent
    route.reverse()
    return route, costs[goal]


def preview(nav_db, zone, start, goal, snap_radius=1.0,
            max_distance=100.0, max_stretch=3.0):
    if type(zone) is not int or not 0 <= zone <= 65535:
        raise ValueError("Invalid zone")
    if (not 0 < snap_radius <= 5 or not 0 < max_distance <= 1000
            or not 1 <= max_stretch <= 20):
        raise ValueError("Invalid route bounds")
    start, goal = coordinates(start), coordinates(goal)
    info = derived_navigation_db.inspect(nav_db)
    p = Path(nav_db).expanduser().resolve()
    db = sqlite3.connect(p.as_uri() + "?mode=ro", uri=True, timeout=10)
    try:
        db.execute("PRAGMA query_only=ON")
        n, e = db.execute("""
            SELECT (SELECT COUNT(*) FROM node WHERE zone_id=?),
                   (SELECT COUNT(*) FROM walk_edge WHERE zone_id=?)
        """, (zone, zone)).fetchone()
        if n > MAX_NODES or e > MAX_EDGES:
            raise ValueError("Graph exceeds analysis budget")
        nodes = {
            nid: (x, y, z)
            for nid, x, y, z in db.execute(
                "SELECT node_id,x,y,z FROM node WHERE zone_id=?", (zone,)
            )
        }
        source, source_snap = nearest_node(nodes, start, snap_radius)
        target, target_snap = nearest_node(nodes, goal, snap_radius)
        if source == target:
            raise ValueError("Start and destination snap to the same node")
        outgoing = collections.defaultdict(list)
        edge_status = {}
        for eid, a, b, length, status in db.execute("""
            SELECT edge_id,from_node_id,to_node_id,cost,status
            FROM walk_edge WHERE zone_id=?""", (zone,)):
            if a not in nodes or b not in nodes:
                raise ValueError("Dangling walking edge")
            outgoing[a].append((b, eid, length))
            edge_status[eid] = status
        for adj in outgoing.values():
            adj.sort()
        route, distance = shortest_directed(
            source, target, outgoing, max_distance
        )
        direct = math.dist(nodes[source], nodes[target])
        stretch = distance / direct if direct > 1e-9 else math.inf
        if stretch > max_stretch:
            raise ValueError(
                f"Candidate detour is too long: {stretch:.2f}x straight line "
                f"(max {max_stretch:.2f}x)"
            )
        # Restrict provenance query to selected route legs (never a full scan).
        evidence = collections.defaultdict(list)
        ids = [leg[2] for leg in route]
        for i in range(0, len(ids), 450):
            batch = ids[i:i + 450]
            placeholders = ",".join("?" for _ in batch)
            for eid, session, sequence in db.execute(
                "SELECT walk_edge_id,source_session,source_sequence "
                "FROM evidence_ref WHERE walk_edge_id IN (" + placeholders + ")",
                batch,
            ):
                evidence[eid].append((session, sequence))
        legs = []
        for a, b, eid, cost in route:
            provenance = evidence[eid]
            sessions = len({session for session, _seq in provenance})
            legs.append({
                "from_index": len(legs),
                "to_index": len(legs) + 1,
                "edge_id": eid,
                "length_yalms": round(cost, 2),
                "edge_status": edge_status[eid],
                "observations": len(provenance),
                "distinct_recording_sessions": sessions,
            })
        waypoints = [source] + [b for _a, b, _eid, _length in route]
        return {
            "zone": zone,
            "source_requested": [round(n, 2) for n in start],
            "destination_requested": [round(n, 2) for n in goal],
            "source_snapped": [round(n, 2) for n in nodes[source]],
            "destination_snapped": [round(n, 2) for n in nodes[target]],
            "source_snap_yalms": round(source_snap, 2),
            "destination_snap_yalms": round(target_snap, 2),
            "distance_yalms": round(distance, 2),
            "direct_yalms": round(direct, 2),
            "detour_ratio": round(stretch, 2),
            "waypoint_count": len(waypoints),
            "waypoints": [
                [round(n, 2) for n in nodes[nid]] for nid in waypoints
            ],
            "legs": legs,
            "edges_seen_in_multiple_recording_sessions": sum(
                l["distinct_recording_sessions"] > 1 for l in legs
            ),
            "edges_without_evidence": sum(
                l["observations"] == 0 for l in legs
            ),
            "release_status": info["release_status"],
            "approved_for_movement": False,
            "safety_note": (
                "READ-ONLY candidate preview; quantized nodes and historical "
                "edge evidence do not establish physical walkability. "
                "Do not execute this path in UC without independent "
                "field validation and guarded opt-in."
            ),
        }
    finally:
        db.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nav-db", required=True)
    parser.add_argument("--zone", type=int, default=247)
    parser.add_argument("--from-xyz", required=True)
    parser.add_argument("--to-xyz", required=True)
    parser.add_argument("--snap-radius", type=float, default=1.0)
    parser.add_argument("--max-distance", type=float, default=100)
    parser.add_argument("--max-stretch", type=float, default=3)
    args = parser.parse_args(argv)
    print(json.dumps(preview(
        args.nav_db, args.zone, args.from_xyz, args.to_xyz,
        args.snap_radius, args.max_distance, args.max_stretch,
    ), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
