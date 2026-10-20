"""RockDirector with MPGS_MOVER=1: root motion on attacks, ground spawn, movement only on change (offline)."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))  # GameServer/, for mpgs
import array
from types import SimpleNamespace
import json
import math
import os
import tempfile
import unittest

from mpgs.behemoth import bp_schema
from mpgs.behemoth.brain import ROCK
from mpgs.behemoth.mover import ROOTMOTION_DIR, RootCurve, compose, montage_curve
from mpgs.behemoth.session import RockDirector
from mpgs.world.terrain import Terrain, default_base

from test_behemoth_session import FakeSession

READY = os.path.exists(ROCK) and os.path.exists(bp_schema.ROCK_SCHEMA)
HAS_CURVES = os.path.exists(os.path.join(ROOTMOTION_DIR, 'b_sq_rok_00_atk_fronttackle_fwd_00.rootmotion.json'))


def straight(distance, frames=31):
    tkeys = [[f, distance * f / (frames - 1), 0.0, 0.0] for f in range(frames)]
    rkeys = [[f, 0.0, 0.0, 0.0, 1.0] for f in range(frames)]
    return RootCurve(frames, tkeys, rkeys)


def write_flat_terrain(z=1000.0, n=64):
    """Flat synthetic landscape in a temp folder; returns the base path for MPGS_TERRAIN_BASE."""
    d = tempfile.mkdtemp()
    base = os.path.join(d, 'flat_height')
    header = {'width': n, 'height': n, 'quad_origin': [0, 0], 'landscape_location': [0.0, 0.0, 0.0],
              'quad_world': [100.0, 100.0], 'z_world': {'base': 0.0, 'scale': 128.0}}
    with open(base + '.json', 'w', encoding='utf-8') as f:
        json.dump(header, f)
    with open(base + '.bin', 'wb') as f:
        f.write(array.array('H', [int(round(z)) + 32768] * (n * n)).tobytes())
    return base


class ComposeTests(unittest.TestCase):
    def test_two_segments_add_up(self):
        c = compose([straight(100.0), straight(50.0)])
        dx, dy, _, _ = c.sample(c.duration)
        self.assertAlmostEqual(dx, 150.0, delta=1.0)
        self.assertAlmostEqual(dy, 0.0, delta=1.0)

    def test_second_segment_follows_the_turn(self):
        # curves are relative to their start: the yaw ramps from identity to 45 degrees over the segment
        turn = RootCurve(30, [[f, 0.0, 0.0, 0.0] for f in range(31)],
                         [[f, 0.0, 0.0, math.sin(math.radians(45 * f / 30) / 2), math.cos(math.radians(45 * f / 30) / 2)]
                          for f in range(31)])
        c = compose([turn, straight(100.0)])
        dx, dy, _, dyaw = c.sample(c.duration)
        self.assertAlmostEqual(dyaw, 45.0, delta=1.0)
        self.assertAlmostEqual(dx, 100.0 * math.cos(math.radians(45)), delta=2.0)
        self.assertAlmostEqual(dy, 100.0 * math.sin(math.radians(45)), delta=2.0)


@unittest.skipUnless(HAS_CURVES, 'root motion not exported (tools.AssetDump rootmotion)')
class MontageLookupTests(unittest.TestCase):
    def test_single_sequence_montage(self):
        c = montage_curve('b_sq_rok_00_atk_fronttackle_fwd_00_mot')
        self.assertIsNotNone(c)
        self.assertGreater(c.duration, 3.0)

    def test_multi_sequence_montage_is_composed(self):
        c = montage_curve('b_sq_rok_00_atk_whirlingfortress_left_00_mot')
        self.assertIsNotNone(c)
        self.assertGreater(c.duration, 9.0)

    def test_unknown_montage_is_none(self):
        self.assertIsNone(montage_curve('no_such_montage_mot'))


@unittest.skipUnless(READY, 'Rock data not generated')
class MoverDirectorTests(unittest.TestCase):
    def setUp(self):
        self.base = write_flat_terrain(1000.0)

    def director(self, session, **env):
        base = {'MPGS_ROCK_DELAY': '5', 'MPGS_ROCK_DISTANCE': '1500', 'MPGS_MOVER': '1', 'MPGS_TERRAIN_BASE': self.base,
                'MPGS_ROCK_HALF_HEIGHT': '250', 'MPGS_ROCK_MOVE_EVERY': '0'}
        base.update(env)
        return RockDirector(session, env=base)

    def spawned(self):
        s = FakeSession(loc=(0.0, 0.0, 100.0))
        d = self.director(s)
        d.step(0.0)
        d.step(5.0)
        return s, d

    def test_spawns_on_the_ground_with_half_height(self):
        s, d = self.spawned()
        self.assertIsNotNone(d.mover)
        self.assertAlmostEqual(d.loc[2], 1000.0 + 250.0, delta=1.0)

    def test_mover_off_by_default(self):
        s = FakeSession(loc=(0.0, 0.0, 100.0))
        d = RockDirector(s, env={'MPGS_ROCK_DELAY': '5', 'MPGS_ROCK_DISTANCE': '1500'})
        d.step(0.0)
        d.step(5.0)
        self.assertIsNone(d.mover)

    def test_attack_plays_root_motion(self):
        s, d = self.spawned()
        d.step(5.25)
        if d.actions_started == 0:
            self.skipTest('no attack chosen for this geometry')
        # a quarter second into the montage the Rock is still moving under root motion (or it stopped on bad ground)
        self.assertTrue(d.mover.montage is not None or d.mover.stopped_reason is not None)

    def test_no_movement_packets_while_idle(self):
        # stage 'move' keeps attacks off, so nothing but facing could move the Rock
        s = FakeSession(loc=(0.0, 0.0, 100.0))
        d = self.director(s, MPGS_ROCK_STAGE='move')
        d.step(0.0)
        d.step(5.0)
        d.step(5.0)            # spawn already queued its move; no attack, no turn
        n0 = d.sent['move']
        for k in range(1, 20):
            d.step(5.0 + k * 0.5)
        self.assertEqual(d.sent['move'], n0)

    def test_montage_without_curve_stays_in_place(self):
        s, d = self.spawned()
        before = list(d.loc)
        d.mover.montage = None
        orig = d.brain.actions
        for a in orig.values():
            a['montage'] = 'no_such_montage_mot'
        d.step(5.25)
        for k in range(1, 10):
            d.step(5.25 + k * 0.1)
        self.assertLess(math.hypot(d.loc[0] - before[0], d.loc[1] - before[1]), 1.0)


if __name__ == '__main__':
    unittest.main()


@unittest.skipUnless(READY, 'Rock data not generated')
class PlayerHitTests(unittest.TestCase):
    """Damage window while the player is in the attack's hit area -> PLAYER-HIT (computed, not sent)."""

    def setUp(self):
        self.base = write_flat_terrain(1000.0)

    def director(self, s, **env):
        base = {'MPGS_ROCK_DELAY': '5', 'MPGS_ROCK_DISTANCE': '1500', 'MPGS_MOVER': '1', 'MPGS_TERRAIN_BASE': self.base,
                'MPGS_ROCK_HALF_HEIGHT': '250', 'MPGS_ROCK_MOVE_EVERY': '0', 'MPGS_ROCK_STAGE': 'move'}
        base.update(env)
        return RockDirector(s, env=base)

    def inside_point(self, d):
        """A ground point inside FrontTackle's hit area, found by searching around the Rock."""
        b = d.brain
        for r in range(0, 2000, 50):
            for k in range(0, 36):
                a = math.radians(k * 10)
                p = (b.pos[0] + r * math.cos(a), b.pos[1] + r * math.sin(a))
                if b.in_area('FrontTackle', p):
                    return p
        self.skipTest('no point inside the FrontTackle hit area at this spawn')

    def test_player_inside_the_area_takes_damage(self):
        s = FakeSession(loc=(0.0, 0.0, 100.0))
        d = self.director(s)
        d.step(0.0)
        d.step(5.0)
        p = self.inside_point(d)
        s.pawn_state['loc'] = [p[0], p[1], 1687.0]
        d.brain.run = SimpleNamespace(name='FrontTackle')
        d._on_event('damage_window_open', {'keys': ['FrontTackle']}, 1.0)
        self.assertEqual(d.player_hits, 1)
        d._on_event('damage_window_close', {}, 1.5)
        self.assertFalse(d._window_open)

    def test_player_outside_the_area_takes_nothing(self):
        s = FakeSession(loc=(0.0, 0.0, 100.0))
        d = self.director(s)
        d.step(0.0)
        d.step(5.0)
        s.pawn_state['loc'] = [d.brain.pos[0] + 20000.0, d.brain.pos[1] + 20000.0, 1687.0]
        d.brain.run = SimpleNamespace(name='FrontTackle')
        d._on_event('damage_window_open', {'keys': ['FrontTackle']}, 1.0)
        self.assertEqual(d.player_hits, 0)

    def test_damage_uses_the_behemoth_to_player_curve(self):
        from mpgs.behemoth import damage
        model = damage.load_model()
        elems = {'TerraDamage': 175.0}
        dmg = damage.behemoth_to_player(model, elems, 100.0, 100.0)
        self.assertIsNotNone(dmg)
        self.assertGreater(dmg, 0.0)
        self.assertIsNone(damage.behemoth_to_player(model, {}, 100.0, 100.0))
        self.assertAlmostEqual(damage.behemoth_to_player(model, elems, 100.0, 100.0, scale=2.0), 2 * dmg)


@unittest.skipUnless(READY, 'Rock data not generated')
class StaggerTests(unittest.TestCase):
    """MPGS_PLAYER_STAGGER=1: a player hit queues ClientStartStaggerAction on the player's hit reaction component."""

    def setUp(self):
        self.base = write_flat_terrain(1000.0)

    def director(self, s, **env):
        base = {'MPGS_ROCK_DELAY': '5', 'MPGS_ROCK_DISTANCE': '1500', 'MPGS_MOVER': '1', 'MPGS_TERRAIN_BASE': self.base,
                'MPGS_ROCK_HALF_HEIGHT': '250', 'MPGS_ROCK_MOVE_EVERY': '0', 'MPGS_ROCK_STAGE': 'move'}
        base.update(env)
        return RockDirector(s, env=base)

    def hit_point(self, d):
        return PlayerHitTests.inside_point(self, d)

    def run_hit(self, **env):
        s = FakeSession(loc=(0.0, 0.0, 100.0))
        d = self.director(s, **env)
        d.step(0.0)
        d.step(5.0)
        p = self.hit_point(d)
        s.pawn_state['loc'] = [p[0], p[1], 1687.0]
        d.brain.run = SimpleNamespace(name='FrontTackle')
        n0 = len(s.entries)
        d._on_event('damage_window_open', {'keys': ['FrontTackle']}, 1.0)
        return s, d, n0

    def test_stagger_rpc_queued_on_hit(self):
        s, d, n0 = self.run_hit(MPGS_PLAYER_STAGGER='1')
        self.assertEqual(d.sent.get('stagger'), 1)
        self.assertGreater(len(s.entries), n0)
        names = [f[1] for e in s.entries for b in e.parsed.blocks for f in (b.fields or [])]
        self.assertIn('ClientStartStaggerAction', names)

    def test_stagger_off_by_default(self):
        s, d, n0 = self.run_hit()
        self.assertEqual(d.sent.get('stagger', 0), 0)
        self.assertEqual(d.player_hits, 1)

    def test_player_pawn_lookup(self):
        from mpgs.behemoth import spawn
        ch, guid = spawn.player_pawn(os.path.join(os.path.dirname(__file__), '..', 'world', 'ramsgate_template.json'),
                                     'hit_reaction_bpc')
        self.assertEqual((ch, guid), (4, 130))
