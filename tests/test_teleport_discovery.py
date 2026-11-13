"""Offline teleport discovery regression tests (synthetic, deterministic)."""
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
import teleport_discovery as td


class TeleportDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.db_path = Path(self.dir.name) / 'capture.sqlite3'
        self.db = sqlite3.connect(self.db_path)
        self.addCleanup(self.db.close)
        self.db.executescript('''
            CREATE TABLE events (
                session TEXT NOT NULL, sequence INTEGER NOT NULL,
                zone INTEGER, event_type TEXT NOT NULL,
                observed_at REAL NOT NULL, raw_json TEXT NOT NULL,
                PRIMARY KEY (session, sequence)
            );
            CREATE INDEX idx_events_type_time ON events(event_type, observed_at);
            CREATE TABLE positions (
                session TEXT NOT NULL, sequence INTEGER NOT NULL,
                zone INTEGER, x REAL, y REAL, z REAL, reason TEXT,
                PRIMARY KEY (session, sequence)
            );
        ''')

    def put(self, session, sequence, zone, event_type, t, xyz=None, **fields):
        data = dict(session=session, sequence=sequence, zone=zone,
                    event=event_type, time=t, **fields)
        if xyz is not None:
            data.update(zip(('x', 'y', 'z'), xyz))
        self.db.execute('INSERT INTO events VALUES (?,?,?,?,?,?)',
                        (session, sequence, zone, event_type, t, json.dumps(data)))
        if event_type == 'position':
            self.db.execute('INSERT INTO positions VALUES (?,?,?,?,?,?,?)',
                            (session, sequence, zone, *xyz, fields.get('reason')))

    def jump(self, session, base, t, zone=298, a=(10., 5., 0.), b=(100., 60., 0.),
             gap=0.4, activation=None, anchor_reason='new_anchor'):
        self.put(session, base, zone, 'position', t, xyz=a, reason='moving')
        if activation:
            self.put(session, base+1, zone, 'packet_observation', t+0.1,
                     packet_kind=('item_use_request' if activation == 'item' else 'menu_selection'),
                     interaction_stage=('menu_choice' if activation == 'menu' else None))
        j = base + (2 if activation else 1)
        self.put(session, j, zone, 'discontinuity', t+gap, xyz=b,
                 reason='large_position_jump', distance=td._distance(a, b))
        self.put(session, j+1, zone, 'position', t+gap, xyz=b, reason=anchor_reason)

    def test_five_time_separated_occurrences_promoted(self):
        for n in range(5):
            self.jump(f'terrasjr_{n}', 1, n*600., a=(10.+n*0.5, 5., 0.),
                      b=(100.+n*0.3, 60., 0.), activation='menu' if n == 0 else None)
        self.db.commit()
        report = td.discover(self.db_path, zone=298)
        self.assertEqual(report['qualifying_clusters'], 1)
        c = report['candidates'][0]
        self.assertEqual(c['independent_occasions'], 5)
        self.assertEqual(c['observations'], 5)
        self.assertEqual(c['activation_hints']['interaction_nearby'], 1)
        self.assertEqual(c['classification'], 'probable_transport')
        self.assertEqual(c['direction'], 'entry_to_exit_only')
        self.assertFalse(c['verified'])
        self.assertEqual(report['valid_in_zone_jumps'], 5)

    def test_six_box_same_traversal_counts_one_occasion(self):
        for i, char in enumerate(('terrasjr', 'nyoourke', 'redrogue',
                                  'etamame', 'niightsiide', 'ponponponponpon')):
            self.jump(f'{char}_party', 1, 100.+i*2)
        self.db.commit()
        report = td.discover(self.db_path, zone=298)
        self.assertEqual(report['qualifying_clusters'], 0)
        low = td.discover(self.db_path, zone=298, min_occasions=1)['candidates'][0]
        self.assertEqual(low['observations'], 6)
        self.assertEqual(low['characters'], 6)
        self.assertEqual(low['recording_sessions'], 6)
        self.assertEqual(low['independent_occasions'], 1)

    def test_directed_endpoints_and_zone_separation(self):
        self.jump('a_1', 1, 0.)
        self.jump('a_2', 1, 200., a=(100., 60., 0.), b=(10., 5., 0.))
        self.jump('a_3', 1, 400., zone=68)
        self.db.commit()
        report = td.discover(self.db_path, min_occasions=1)
        self.assertEqual(report['clusters'], 3)
        self.assertEqual(len(report['candidates']), 3)

    def test_drop_missing_anchors_zone_and_time_gaps(self):
        self.jump('valid_1', 1, 0.)
        self.jump('bad_gap', 1, 100., gap=45.)
        self.jump('bad_anchor', 1, 200., anchor_reason='heartbeat')
        self.put('bad_zone', 1, 68, 'position', 0., xyz=(0., 0., 0.), reason='moving')
        self.put('bad_zone', 2, 298, 'discontinuity', 0.3,
                 xyz=(100., 20., 0.), reason='large_position_jump')
        self.put('bad_zone', 3, 298, 'position', 0.3,
                 xyz=(100., 20., 0.), reason='new_anchor')
        self.db.commit()
        report = td.discover(self.db_path, min_occasions=1)
        self.assertEqual(report['valid_in_zone_jumps'], 1)
        self.assertEqual(report['rejected']['recording_gap'], 1)
        self.assertEqual(report['rejected']['zone_or_anchor_mismatch'], 2)

    def test_ignore_nonjump_discontinuity_and_bad_marker(self):
        self.jump('valid_1', 1, 0.)
        self.put('other_1', 1, 298, 'discontinuity', 0., reason='zone_or_character_change')
        self.put('mismatch_1', 1, 298, 'position', 50., xyz=(10., 5., 0.), reason='moving')
        self.put('mismatch_1', 2, 298, 'discontinuity', 50.3,
                 xyz=(999., 0., 0.), reason='large_position_jump')
        self.put('mismatch_1', 3, 298, 'position', 50.3,
                 xyz=(100., 60., 0.), reason='new_anchor')
        self.db.commit()
        report = td.discover(self.db_path, min_occasions=1)
        self.assertEqual(report['valid_in_zone_jumps'], 1)
        self.assertEqual(report['rejected']['other_discontinuity'], 1)
        self.assertEqual(report['rejected']['jump_mismatch'], 1)

    def test_cohort_repeated_visits_and_stable_clustering(self):
        # One party over 1 min, then another traversal 180s later; note
        # sessions alone cannot be used as independence evidence.
        for i in range(6):
            self.jump('terrasjr_continuous', i*5+1, i*10.)
        self.jump('terrasjr_continuous', 50, 180.)
        self.db.commit()
        report = td.discover(self.db_path, min_occasions=2)
        c = report['candidates'][0]
        self.assertEqual(c['independent_occasions'], 2)
        self.assertEqual(c['recording_sessions'], 1)
        self.assertEqual(c['observations'], 7)

    def test_no_mutation_and_query_index(self):
        self.jump('a_1', 1, 0.)
        self.db.commit()
        with td.read_connection(self.db_path) as db:
            with self.assertRaises(sqlite3.OperationalError):
                db.execute('CREATE TABLE forbidden(x)')
            plan = [x[3] for x in db.execute('''EXPLAIN QUERY PLAN
                SELECT session, sequence, zone, observed_at, raw_json
                FROM events INDEXED BY idx_events_type_time
                WHERE event_type='discontinuity'
                ORDER BY observed_at, session, sequence''')]
            self.assertTrue(any('idx_events_type_time' in p for p in plan))

    def test_invalid_limits_fail_closed(self):
        self.jump('a_1', 1, 0.)
        self.db.commit()
        with self.assertRaises(ValueError):
            td.discover(self.db_path, zone=-1)
        with self.assertRaises(ValueError):
            td.discover(self.db_path, cohort_window=0)
        with self.assertRaises(ValueError):
            td.discover(self.db_path, min_occasions=0)
        with self.assertRaises(ValueError):
            td.discover(self.db_path, entry_radius=float('nan'))


if __name__ == '__main__':
    unittest.main()
