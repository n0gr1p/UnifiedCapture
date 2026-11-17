"""Read-only, offline discovery of repeatable in-zone teleport candidates.

A 'large_position_jump' is not automatically a teleport. This module pairs the
last position before the discontinuity with the recorder's immediately following
'new_anchor', clusters directed A->B jumps by XYZ proximity, and counts time-
separated traversal occasions so a six-box party cannot create six independent
confirmations. Findings are evidence only, never safe-to-execute nav edges.

Usage on the Linux host (no service restart or API access required):
    python3 server/teleport_discovery.py --db server-data/capture.sqlite3 --zone 298
"""

import argparse
from collections import Counter
from contextlib import closing
import json
import math
from pathlib import Path
import sqlite3

RECORDER_WARP_DISTANCE = 35.0
DEFAULT_ENTRY_RADIUS = 8.0
DEFAULT_EXIT_RADIUS = 8.0
DEFAULT_MAX_PREVIOUS_GAP = 8.0
DEFAULT_MAX_ANCHOR_GAP = 5.0
DEFAULT_COHORT_WINDOW = 120.0
DEFAULT_INTERACTION_LOOKBACK = 20.0


# Consistent with navigation.py: mode=ro and query_only; never writes to the
# user's historical capture database, including indexes or temporary tables.
def read_connection(db_path):
    uri = Path(db_path).resolve().as_uri() + '?mode=ro'
    db = sqlite3.connect(uri, uri=True, timeout=8)
    db.execute('PRAGMA query_only=ON')
    db.row_factory = sqlite3.Row
    return db


def _positive(value, label):
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value <= 0:
        raise ValueError(f'{label} must be positive and finite')
    return float(value)


def _xyz(row):
    if row is None:
        return None
    vals = [row['x'], row['y'], row['z']]
    if any(type(v) not in (float, int) or not math.isfinite(v) for v in vals):
        return None
    return tuple(float(v) for v in vals)


def _distance(a, b):
    return math.dist(a, b)


def _adjacent_position(db, session, sequence, direction):
    # positions(session, sequence) and events(session, sequence) are primary keys.
    comparator, order = ('<', 'DESC') if direction == 'before' else ('>', 'ASC')
    return db.execute(f'''
        SELECT p.sequence, p.zone, p.x, p.y, p.z, p.reason,
               e.observed_at
        FROM positions p JOIN events e
             ON e.session=p.session AND e.sequence=p.sequence
        WHERE p.session=? AND p.sequence {comparator} ?
        ORDER BY p.sequence {order} LIMIT 1
    ''', (session, sequence)).fetchone()


def _recent_activation(db, session, sequence, observed_at, lookback):
    """A nearby menu or item request is a hint, not proof of causality."""
    rows = db.execute('''
        SELECT observed_at, raw_json FROM events
        WHERE session=? AND sequence < ? AND observed_at >= ?
          AND event_type='packet_observation'
        ORDER BY sequence DESC LIMIT 100
    ''', (session, sequence, observed_at-lookback)).fetchall()
    for row in rows:
        obj = json.loads(row['raw_json'])
        if obj.get('packet_kind') == 'item_use_request':
            return 'item_nearby'
        if obj.get('interaction_stage') in ('menu_choice', 'menu_open', 'interaction_request'):
            return 'interaction_nearby'
    return 'unattributed'


def extract_jumps(db, zone=None, max_previous_gap=DEFAULT_MAX_PREVIOUS_GAP,
                  max_anchor_gap=DEFAULT_MAX_ANCHOR_GAP,
                  interaction_lookback=DEFAULT_INTERACTION_LOOKBACK):
    """Yield validated in-zone jump observations and a mutable rejection counter.

    This is a generator; call scan_jumps for the materialized observations and
    rejection statistics. No full scan of the 22M events table is necessary:
    idx_events_type_time limits the candidate stream to discontinuity events.
    """
    if zone is not None and (type(zone) is not int or not 0 <= zone <= 65535):
        raise ValueError('zone must be an integer from 0 to 65535')
    _positive(max_previous_gap, 'max_previous_gap')
    _positive(max_anchor_gap, 'max_anchor_gap')
    _positive(interaction_lookback, 'interaction_lookback')
    filter_zone = ' AND zone=?' if zone is not None else ''
    params = [zone] if zone is not None else []
    # Separate cursor: nested point lookups must not reset our streaming result.
    cursor = db.execute(f'''
        SELECT session, sequence, zone, observed_at, raw_json
        FROM events INDEXED BY idx_events_type_time
        WHERE event_type='discontinuity'{filter_zone}
        ORDER BY observed_at, session, sequence
    ''', params)
    for event in cursor:
        data = json.loads(event['raw_json'])
        if data.get('reason') != 'large_position_jump':
            yield None, 'other_discontinuity'
            continue
        if event['zone'] is None:
            yield None, 'missing_zone'
            continue
        previous = _adjacent_position(db, event['session'], event['sequence'], 'before')
        after = _adjacent_position(db, event['session'], event['sequence'], 'after')
        if previous is None or after is None:
            yield None, 'missing_anchor'
            continue
        if (previous['zone'] != event['zone'] or after['zone'] != event['zone']
                or after['reason'] != 'new_anchor'):
            yield None, 'zone_or_anchor_mismatch'
            continue
        dt_before = event['observed_at'] - previous['observed_at']
        dt_after = after['observed_at'] - event['observed_at']
        if not 0 <= dt_before <= max_previous_gap or not 0 <= dt_after <= max_anchor_gap:
            yield None, 'recording_gap'
            continue
        start, end, marker = _xyz(previous), _xyz(after), _xyz(data)
        if start is None or end is None or marker is None:
            yield None, 'invalid_xyz'
            continue
        distance = _distance(start, end)
        if distance <= RECORDER_WARP_DISTANCE or _distance(end, marker) > 2.0:
            yield None, 'jump_mismatch'
            continue
        activation = _recent_activation(
            db, event['session'], event['sequence'], event['observed_at'], interaction_lookback)
        yield {
            'zone': event['zone'], 'session': event['session'],
            'character': event['session'].split('_', 1)[0].lower(),
            'sequence': event['sequence'], 'at': event['observed_at'],
            'entry': start, 'exit': end, 'jump_distance': round(distance, 2),
            'activation_hint': activation,
        }, None


def scan_jumps(db, **kwargs):
    candidates, rejected = [], Counter()
    for jump, reject_reason in extract_jumps(db, **kwargs):
        if jump is None:
            rejected[reject_reason] += 1
        else:
            candidates.append(jump)
    return candidates, dict(sorted(rejected.items()))


def _average_xyz(points):
    n = len(points)
    return tuple(sum(point[i] for point in points) / n for i in range(3))


def cluster_jumps(jumps, entry_radius=DEFAULT_ENTRY_RADIUS,
                  exit_radius=DEFAULT_EXIT_RADIUS,
                  cohort_window=DEFAULT_COHORT_WINDOW):
    """Cluster directed jumps by zone and two distinct XYZ endpoints.

    A traversal occasion is separated from the first event of the previous
    occasion by at least cohort_window seconds. Multiple clients following the
    same route together count as one occasion regardless of session count.
    """
    _positive(entry_radius, 'entry_radius')
    _positive(exit_radius, 'exit_radius')
    _positive(cohort_window, 'cohort_window')
    clusters_by_zone = {}
    for jump in sorted(jumps, key=lambda x: (x['at'], x['session'], x['sequence'])):
        clusters = clusters_by_zone.setdefault(jump['zone'], [])
        nearest, distance_sum = None, float('inf')
        for c in clusters:
            entry_delta = _distance(c['entry_centroid'], jump['entry'])
            exit_delta = _distance(c['exit_centroid'], jump['exit'])
            if entry_delta <= entry_radius and exit_delta <= exit_radius:
                delta = entry_delta + exit_delta
                if delta < distance_sum:
                    nearest, distance_sum = c, delta
        if nearest is None:
            nearest = {
                'zone': jump['zone'], 'entries': [], 'exits': [],
                'entry_centroid': jump['entry'], 'exit_centroid': jump['exit'],
                'sessions': set(), 'characters': set(), 'occasions': [],
                'activation_hints': Counter(), 'sample_sequences': []
            }
            clusters.append(nearest)
        nearest['entries'].append(jump['entry'])
        nearest['exits'].append(jump['exit'])
        n = len(nearest['entries'])
        # Running centroids avoid recalculating entire point arrays per observation.
        if n > 1:
            nearest['entry_centroid'] = tuple(
                (nearest['entry_centroid'][i] * (n-1) + jump['entry'][i])/n for i in range(3))
            nearest['exit_centroid'] = tuple(
                (nearest['exit_centroid'][i] * (n-1) + jump['exit'][i])/n for i in range(3))
        nearest['sessions'].add(jump['session'])
        nearest['characters'].add(jump['character'])
        nearest['activation_hints'][jump['activation_hint']] += 1
        nearest['sample_sequences'].append((jump['session'], jump['sequence']))
        if not nearest['occasions'] or jump['at'] - nearest['occasions'][-1] >= cohort_window:
            nearest['occasions'].append(jump['at'])
    return [c for clusters in clusters_by_zone.values() for c in clusters]


def summarize_clusters(clusters, min_occasions=5, max_results=25):
    if type(min_occasions) is not int or min_occasions < 1:
        raise ValueError('min_occasions must be an integer >= 1')
    if type(max_results) is not int or not 1 <= max_results <= 500:
        raise ValueError('max_results must be an integer from 1 to 500')
    results = []
    for c in clusters:
        occasions = len(c['occasions'])
        if occasions < min_occasions:
            continue
        entry, exit = c['entry_centroid'], c['exit_centroid']
        results.append({
            'zone': c['zone'],
            'entry_xyz': [round(v, 2) for v in entry],
            'exit_xyz': [round(v, 2) for v in exit],
            'observations': len(c['entries']), 'independent_occasions': occasions,
            'recording_sessions': len(c['sessions']), 'characters': len(c['characters']),
            'entry_max_spread': round(max(_distance(p, entry) for p in c['entries']), 2),
            'exit_max_spread': round(max(_distance(p, exit) for p in c['exits']), 2),
            'activation_hints': dict(c['activation_hints']),
            'example_evidence': [
                {'session': s, 'sequence': seq} for s, seq in c['sample_sequences'][:3]
            ],
            'classification': 'probable_transport' if occasions >= 5 else 'candidate',
            'verified': False,
            'direction': 'entry_to_exit_only',
        })
    results.sort(key=lambda item: (-item['independent_occasions'], -item['observations'], item['zone'], item['entry_xyz']))
    return results[:max_results], len(results)


def discover(db_path, zone=None, min_occasions=5, max_results=25,
             entry_radius=DEFAULT_ENTRY_RADIUS, exit_radius=DEFAULT_EXIT_RADIUS,
             cohort_window=DEFAULT_COHORT_WINDOW, max_previous_gap=DEFAULT_MAX_PREVIOUS_GAP):
    with closing(read_connection(db_path)) as db:
        jumps, rejected = scan_jumps(db, zone=zone, max_previous_gap=max_previous_gap)
    groups = cluster_jumps(jumps, entry_radius=entry_radius,
                           exit_radius=exit_radius, cohort_window=cohort_window)
    results, total_qualifying = summarize_clusters(groups, min_occasions, max_results)
    return {
        'read_only': True, 'zone_filter': zone, 'discontinuities_evaluated': len(jumps) + sum(rejected.values()),
        'valid_in_zone_jumps': len(jumps), 'rejected': rejected,
        'clusters': len(groups), 'qualifying_clusters': total_qualifying,
        'min_independent_occasions': min_occasions,
        'cohort_window_seconds': cohort_window,
        'interpretation': 'Inferred directed in-zone transport candidates only; not verified or executable routes.',
        'candidates': results,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True, help='Path to existing UnifiedCapture SQLite database')
    parser.add_argument('--zone', type=int, help='Optional FFXI zone ID (e.g. 298 for Odyssey)')
    parser.add_argument('--min-occasions', type=int, default=5,
                        help='Minimum independent traversal occasions (default: 5)')
    parser.add_argument('--max-results', type=int, default=25)
    parser.add_argument('--entry-radius', type=float, default=DEFAULT_ENTRY_RADIUS)
    parser.add_argument('--exit-radius', type=float, default=DEFAULT_EXIT_RADIUS)
    parser.add_argument('--cohort-window', type=float, default=DEFAULT_COHORT_WINDOW)
    parser.add_argument('--max-previous-gap', type=float, default=DEFAULT_MAX_PREVIOUS_GAP)
    args = parser.parse_args(argv)
    result = discover(args.db, args.zone, args.min_occasions, args.max_results,
                      args.entry_radius, args.exit_radius,
                      args.cohort_window, args.max_previous_gap)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
