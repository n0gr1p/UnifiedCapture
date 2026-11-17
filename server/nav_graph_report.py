"""Read-only topology report for an UNVALIDATED derived walking graph.

No live-client calls, approvals, route execution, or source HDD access.
All proposed directed routes are graph observations, NOT safe walking paths.
"""
import argparse
import collections
import heapq
import json
import math
import sqlite3
from pathlib import Path

import derived_navigation_db

MAX_NODES = 200_000
MAX_EDGES = 500_000
ROUTE_COMPONENT_LIMIT = 12
ROUTE_ROOTS_PER_COMPONENT = 10


def rounded_xyz(xyz):
    return [round(float(v), 2) for v in xyz]


def components_weak(nodes, edges):
    parent = {node: node for node in nodes}

    def find(node):
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for start, end, _cost in edges:
        p, q = find(start), find(end)
        if p != q:
            parent[q] = p

    groups = collections.defaultdict(list)
    for node in nodes:
        groups[find(node)].append(node)
    return sorted(groups.values(), key=lambda group: (-len(group), min(group)))


def components_strong(nodes, edges):
    """Iterative Kosaraju: no recursion depth risk on long walking chains."""
    outgoing = {node: [] for node in nodes}
    incoming = {node: [] for node in nodes}
    for a, b, _cost in edges:
        outgoing[a].append(b)
        incoming[b].append(a)

    visited, finish = set(), []
    for node in nodes:
        if node in visited:
            continue
        visited.add(node)
        stack = [(node, iter(outgoing[node]))]
        while stack:
            current, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                finish.append(current)
                stack.pop()
            elif nxt not in visited:
                visited.add(nxt)
                stack.append((nxt, iter(outgoing[nxt])))

    visited.clear()
    sizes = []
    for node in reversed(finish):
        if node in visited:
            continue
        visited.add(node)
        stack = [node]
        count = 0
        while stack:
            current = stack.pop()
            count += 1
            for nxt in incoming[current]:
                if nxt not in visited:
                    visited.add(nxt)
                    stack.append(nxt)
        sizes.append(count)
    return sorted(sizes, reverse=True)


def furthest_shortest_route(start, outgoing, nodes, component_size):
    """Find a legitimate directed route through recorded edges (not invented shortcuts)."""
    distance = {start: 0.0}
    previous = {}
    heap = [(0.0, start)]
    while heap:
        cost, current = heapq.heappop(heap)
        if cost > distance[current]:
            continue
        for nxt, length in outgoing[current]:
            new_cost = cost + length
            if new_cost < distance.get(nxt, math.inf) - 1e-9:
                distance[nxt] = new_cost
                previous[nxt] = current
                heapq.heappush(heap, (new_cost, nxt))
    target = max(distance, key=lambda node: (distance[node], -node))
    hops = 0
    cursor = target
    while cursor != start:
        cursor = previous[cursor]
        hops += 1
    if not hops:
        return None
    return {
        "from": rounded_xyz(nodes[start]),
        "to": rounded_xyz(nodes[target]),
        "distance_yalms": round(distance[target], 1),
        "directed_edges": hops,
        "weak_component_nodes": component_size,
    }


def analyze(nav_db, zone=247):
    if type(zone) is not int or not 0 <= zone <= 65535:
        raise ValueError("Invalid zone")
    info = derived_navigation_db.inspect(nav_db)
    path = Path(nav_db).expanduser().resolve()
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10)
    try:
        db.execute("PRAGMA query_only=ON")
        counts = db.execute("""
            SELECT
              (SELECT COUNT(*) FROM node WHERE zone_id=?),
              (SELECT COUNT(*) FROM walk_edge WHERE zone_id=?)
        """, (zone, zone)).fetchone()
        if counts[0] > MAX_NODES or counts[1] > MAX_EDGES:
            raise ValueError("Graph exceeds bounded analysis budget")
        rows = db.execute(
            "SELECT node_id,x,y,z FROM node WHERE zone_id=?", (zone,)
        ).fetchall()
        nodes = {node: (x, y, z) for node, x, y, z in rows}
        records = db.execute("""
            SELECT from_node_id,to_node_id,cost,status,evidence_count
            FROM walk_edge WHERE zone_id=?""", (zone,)).fetchall()
        edges = [(a, b, cost) for a, b, cost, _status, _evidence in records]
        if any(a not in nodes or b not in nodes for a, b, _ in edges):
            raise ValueError("Dangling walking edge found")
        evidence = db.execute("""
            SELECT COUNT(*), COUNT(DISTINCT e.source_session)
            FROM evidence_ref AS e
            JOIN walk_edge AS w ON e.walk_edge_id=w.edge_id
            WHERE w.zone_id=?""", (zone,)).fetchone()
        repeated = db.execute("""
            SELECT
              SUM(CASE WHEN n>1 THEN 1 ELSE 0 END),
              SUM(CASE WHEN sessions>1 THEN 1 ELSE 0 END)
            FROM (
              SELECT e.walk_edge_id,
                     COUNT(*) AS n, COUNT(DISTINCT source_session) AS sessions
              FROM evidence_ref AS e
              JOIN walk_edge AS w ON e.walk_edge_id=w.edge_id
              WHERE w.zone_id=?
              GROUP BY e.walk_edge_id
            )""", (zone,)).fetchone()
        # Root candidates are historical paths only, not safe/tested automation.
        indeg = collections.Counter()
        outdeg = collections.Counter()
        outgoing = collections.defaultdict(list)
        for a, b, cost in edges:
            indeg[b] += 1
            outdeg[a] += 1
            outgoing[a].append((b, cost))
        for adj in outgoing.values():
            adj.sort()
        weak = components_weak(nodes, edges)
        strong_sizes = components_strong(nodes, edges)
        group_index = {}
        for i, group in enumerate(weak):
            for node in group:
                group_index[node] = i
        component_edge_counts = collections.Counter(group_index[a] for a, _b, _d in edges)
        component_lengths = collections.Counter()
        for a, _b, dist in edges:
            component_lengths[group_index[a]] += dist

        route_candidates = []
        truncated = False
        for group in weak[:ROUTE_COMPONENT_LIMIT]:
            starts = sorted(
                (node for node in group if indeg[node] == 0 and outdeg[node] > 0),
                key=lambda node: nodes[node],
            )
            if not starts and len(group) > 1:
                starts = [min(group, key=lambda node: nodes[node])]
            if len(starts) > ROUTE_ROOTS_PER_COMPONENT:
                truncated = True
            for start in starts[:ROUTE_ROOTS_PER_COMPONENT]:
                route = furthest_shortest_route(start, outgoing, nodes, len(group))
                if route:
                    route_candidates.append(route)
        if len(weak) > ROUTE_COMPONENT_LIMIT:
            truncated = True
        route_candidates.sort(
            key=lambda r: (-r["distance_yalms"], -r["directed_edges"],
                           r["from"], r["to"])
        )
        statuses = collections.Counter(status for *_ab, status, _count in records)
        return {
            "zone": zone,
            "release_status": info["release_status"],
            "nodes": len(nodes),
            "directed_walk_edges": len(edges),
            "edge_statuses": dict(sorted(statuses.items())),
            "evidence_references": evidence[0],
            "evidence_sessions": evidence[1],
            "edges_with_repeat_evidence": repeated[0] or 0,
            "edges_seen_in_multiple_sessions": repeated[1] or 0,
            "weak_components": len(weak),
            "largest_weak_components": [
                {
                    "nodes": len(group),
                    "edges": component_edge_counts[i],
                    "sum_edge_yalms": round(component_lengths[i], 1),
                }
                for i, group in enumerate(weak[:8])
            ],
            "strong_components": len(strong_sizes),
            "largest_strong_component_nodes": strong_sizes[0] if strong_sizes else 0,
            "start_nodes_no_incoming": sum(
                indeg[node] == 0 and outdeg[node] > 0 for node in nodes
            ),
            "end_nodes_no_outgoing": sum(
                outdeg[node] == 0 and indeg[node] > 0 for node in nodes
            ),
            "branching_nodes": sum(outdeg[node] > 1 for node in nodes),
            "top_directed_path_candidates": route_candidates[:5],
            "path_search_limited": truncated,
            "navigation_executable": info["release_status"] == "published"
            and statuses.get("executable", 0) > 0,
            "safety_note": (
                "These are historical candidate-edge graph paths, not "
                "validated or executable walking routes."
            ),
        }
    finally:
        db.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nav-db", required=True)
    parser.add_argument("--zone", type=int, default=247)
    args = parser.parse_args(argv)
    print(json.dumps(analyze(args.nav_db, args.zone), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
