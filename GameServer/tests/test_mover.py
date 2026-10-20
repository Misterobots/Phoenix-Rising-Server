"""Behemoth mover: root motion playback, walking, ground clamp, walkability and change detection (offline)."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))  # GameServer/, for mpgs
import array
import json
import math
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, '..')))

from mpgs.behemoth.mover import FPS, MOVE_EPS, ROOTMOTION_DIR, Mover, RootCurve  # noqa: E402
from mpgs.world.terrain import NODATA, Terrain, default_base  # noqa: E402

REAL_CURVE = os.path.join(ROOTMOTION_DIR, 'b_sq_rok_00_atk_fronttackle_fwd_00.rootmotion.json')


def flat_terrain(z=1000.0, n=64):
    """Flat ground at z (cm) on a grid of 100 cm quads, origin 0."""
    header = {'width': n, 'height': n, 'quad_origin': [0, 0], 'landscape_location': [0.0, 0.0, 0.0],
              'quad_world': [100.0, 100.0], 'z_world': {'base': 0.0, 'scale': 128.0}}
    raw = int(round(z)) + 32768
    return Terrain(header, array.array('H', [raw] * (n * n)))


def straight_curve(distance=100.0, frames=31, yaw_total=0.0):
    """Root moves +X by `distance` cm over `frames` (1 s at 30 Hz when frames=31) and turns yaw_total degrees."""
    tkeys = [[f, distance * f / (frames - 1), 0.0, 0.0] for f in range(frames)]
    rkeys = []
    for f in range(frames):
        a = math.radians(yaw_total * f / (frames - 1)) / 2
        rkeys.append([f, 0.0, 0.0, math.sin(a), math.cos(a)])
    return RootCurve(frames, tkeys, rkeys)


class RootCurveTests(unittest.TestCase):
    def test_duration_and_sample(self):
        c = straight_curve(100.0, 31)
        self.assertAlmostEqual(c.duration, 30 / FPS)
        dx, dy, dz, dyaw = c.sample(0.5)
        self.assertAlmostEqual(dx, 50.0, places=3)
        self.assertAlmostEqual(dy, 0.0, places=3)
        self.assertAlmostEqual(dyaw, 0.0, places=3)

    def test_yaw_delta(self):
        c = straight_curve(0.0, 31, yaw_total=90.0)
        self.assertAlmostEqual(c.sample(c.duration)[3], 90.0, places=2)

    def test_load_error_marker(self):
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False, encoding='utf-8') as f:
            json.dump({'error': 'no root track'}, f)
        try:
            with self.assertRaises(ValueError):
                RootCurve.load(f.name)
        finally:
            os.unlink(f.name)


class MoverTests(unittest.TestCase):
    def test_idle_sends_nothing(self):
        m = Mover(flat_terrain(), (100.0, 100.0), 0.0)
        m.tick(0.0)
        m.tick(1.0)
        self.assertFalse(m.moving)
        self.assertFalse(m.pop_changed())
        self.assertFalse(m.pop_changed())

    def test_root_motion_moves_forward_by_exported_distance(self):
        m = Mover(flat_terrain(), (100.0, 100.0), 0.0, half_height=250.0)
        c = straight_curve(100.0, 31)
        m.tick(0.0)
        self.assertTrue(m.play(c, 0.0))
        for k in range(1, 60):
            m.tick(k / 30.0)
        self.assertFalse(m.moving)
        self.assertAlmostEqual(m.x, 200.0, delta=1.0)
        self.assertAlmostEqual(m.y, 100.0, delta=1.0)
        self.assertAlmostEqual(m.z, 1000.0 + 250.0, delta=1.0)

    def test_root_motion_uses_start_yaw(self):
        m = Mover(flat_terrain(), (100.0, 100.0), 90.0)   # facing +Y
        m.tick(0.0)
        m.play(straight_curve(100.0, 31), 0.0)
        for k in range(1, 40):
            m.tick(k / 30.0)
        self.assertAlmostEqual(m.x, 100.0, delta=1.0)
        self.assertAlmostEqual(m.y, 200.0, delta=1.0)

    def test_yaw_follows_root_turn(self):
        m = Mover(flat_terrain(), (100.0, 100.0), 0.0)
        m.tick(0.0)
        m.play(straight_curve(0.0, 31, yaw_total=60.0), 0.0)
        m.tick(2.0)
        self.assertAlmostEqual(m.yaw, 60.0, delta=1.0)

    def test_never_below_ground(self):
        m = Mover(flat_terrain(), (100.0, 100.0), 0.0)
        m.tick(0.0)
        m.play(straight_curve(300.0, 31), 0.0)
        for k in range(1, 40):
            m.tick(k / 30.0)
            self.assertGreaterEqual(m.z, 1000.0 + 250.0 - 1e-6)

    def test_walk_speed(self):
        m = Mover(flat_terrain(), (100.0, 100.0), 0.0)
        m.walk_to([(100.0 + 4000.0, 100.0)])
        m.tick(0.0)
        m.tick(1.0)
        self.assertAlmostEqual(m.x, 100.0 + 400.0, delta=1.0)

    def test_change_detection(self):
        m = Mover(flat_terrain(), (100.0, 100.0), 0.0)
        m.pop_changed()
        m.walk_to([(200.0, 100.0)])
        m.tick(0.0)
        m.tick(0.1)
        self.assertTrue(m.pop_changed())
        self.assertFalse(m.pop_changed())
        self.assertGreater(MOVE_EPS, 0)

    def _sends_during_attack(self, interval, tick_hz=250):
        m = Mover(flat_terrain(), (1000.0, 1000.0), 0.0)
        m.play(straight_curve(distance=600.0, frames=116), 0.0)        # a 3.83 s lunge, like FrontTackle
        sent, last = 0, None
        n = int(5.0 * tick_hz)                                       # run past the end of the montage
        for i in range(n + 1):
            now = i / tick_hz
            m.tick(now)
            if m.pop_changed(now, interval):
                sent, last = sent + 1, list(m.loc)
        return sent, last, m

    def test_send_rate_is_capped_during_root_motion(self):
        sent, _, _ = self._sends_during_attack(1.0 / 30)
        self.assertLessEqual(sent, 30 * 3.833 + 2)                   # at most 30 per second of motion
        self.assertGreater(sent, 30 * 3.833 * 0.8)                   # and still a steady stream
        uncapped, _, _ = self._sends_during_attack(0.0)
        self.assertGreater(uncapped, 3 * sent)                       # what the live run did (one per tick)

    def test_capped_stream_ends_on_the_final_position(self):
        _, last, m = self._sends_during_attack(1.0 / 30)
        self.assertEqual(last, list(m.loc))
        self.assertAlmostEqual(m.x - 1000.0, 600.0, delta=1.0)

    def test_stops_on_unwalkable_ground(self):
        # steep ramp rising along x: the montage must stop rather than climb it
        n = 64
        raw = [32768 + 150 * (i % n) for j in range(n) for i in range(n)]
        header = {'width': n, 'height': n, 'quad_origin': [0, 0], 'landscape_location': [0.0, 0.0, 0.0],
                  'quad_world': [100.0, 100.0], 'z_world': {'base': 0.0, 'scale': 128.0}}
        t = Terrain(header, array.array('H', raw))
        m = Mover(t, (1000.0, 1000.0), 0.0, half_height=0.0)
        m.tick(0.0)
        m.play(straight_curve(2000.0, 31), 0.0)
        for k in range(1, 40):
            m.tick(k / 30.0)
        self.assertEqual(m.stopped_reason, 'unwalkable')
        self.assertFalse(m.moving)


@unittest.skipUnless(os.path.exists(REAL_CURVE) and os.path.exists(default_base() + '.bin'),
                     'Front Tackle curve or cora_poodle grid not exported')
class FrontTackleOnIsland(unittest.TestCase):
    def test_front_tackle_moves_forward_on_the_ground(self):
        curve = RootCurve.load(REAL_CURVE)
        terrain = Terrain.load(default_base())
        start = (24832.8, 8957.6)
        ground = terrain.ground_z(*start)
        m = Mover(terrain, start, 0.0, half_height=0.0)
        m.tick(0.0)
        self.assertTrue(m.play(curve, 0.0))
        t = 0.0
        while m.moving and t < curve.duration + 1.0:
            t += 1 / 30.0
            m.tick(t)
        dx, dy, _, _ = curve.sample(curve.duration)
        travelled = math.hypot(m.x - start[0], m.y - start[1])
        self.assertAlmostEqual(travelled, math.hypot(dx, dy), delta=20.0)
        self.assertGreater(travelled, 500.0)
        # the ground changes along the lunge: the Rock must sit on the ground under its final position
        self.assertAlmostEqual(m.z, terrain.ground_z(m.x, m.y), delta=1.0)
        self.assertIsNotNone(ground)


if __name__ == '__main__':
    unittest.main()
