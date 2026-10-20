"""Ground height, walkability and path search over the exported landscape grid (offline)."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))  # GameServer/, for mpgs
import array
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, '..')))

from mpgs.world.terrain import NODATA, Terrain, default_base  # noqa: E402

REAL = default_base() + '.bin'


def synthetic(w=4, h=4, raw=None, z_scale=1.0):
    """Synthetic landscape: quad step 100 cm, z = z_scale * (raw - 32768) / 128."""
    header = {'width': w, 'height': h, 'quad_origin': [0, 0], 'landscape_location': [0.0, 0.0, 0.0],
              'quad_world': [100.0, 100.0], 'z_world': {'base': 0.0, 'scale': z_scale}}
    samples = array.array('H', raw if raw is not None else [32768 + 128] * (w * h))
    return Terrain(header, samples)


class SyntheticTerrain(unittest.TestCase):
    def test_flat_plane_height(self):
        t = synthetic()
        self.assertAlmostEqual(t.ground_z(150.0, 150.0), 1.0)

    def test_bilinear_between_corners(self):
        raw = [32768, 32768 + 128 * 10, 32768, 32768 + 128 * 10,
               32768, 32768 + 128 * 10, 32768, 32768 + 128 * 10,
               32768, 32768 + 128 * 10, 32768, 32768 + 128 * 10,
               32768, 32768 + 128 * 10, 32768, 32768 + 128 * 10]
        t = synthetic(4, 4, raw)
        self.assertAlmostEqual(t.ground_z(50.0, 150.0), 5.0)  # halfway between x=0 (0) and x=1 (10) columns

    def test_nodata_and_outside(self):
        raw = [NODATA] * 16
        t = synthetic(4, 4, raw)
        self.assertIsNone(t.ground_z(150.0, 150.0))
        self.assertFalse(t.inside(150.0, 150.0))
        t2 = synthetic()
        self.assertIsNone(t2.ground_z(-5000.0, 0.0))

    def test_slope_of_plane_is_zero_and_ramp_is_positive(self):
        t = synthetic()
        self.assertAlmostEqual(t.slope_deg(200.0, 200.0), 0.0)
        ramp = [32768 + 128 * (i * 50) for j in range(4) for i in range(4)]
        t2 = synthetic(4, 4, ramp)
        self.assertGreater(t2.slope_deg(200.0, 200.0), 0.0)

    def test_steep_ramp_not_walkable(self):
        # 150 cm rise per 100 cm step (z_scale 128 makes one raw unit one cm): about 56 degrees, over the 45 limit
        ramp = [32768 + 150 * i for j in range(4) for i in range(4)]
        t = synthetic(4, 4, ramp, z_scale=128.0)
        self.assertFalse(t.walkable(200.0, 200.0))

    def test_path_on_flat_ground(self):
        t = synthetic(8, 8, [32768] * 64)
        p = t.path((100.0, 100.0), (500.0, 500.0))
        self.assertIsNotNone(p)
        self.assertLess(abs(p[0][0] - 100.0), 100.0)
        self.assertLess(abs(p[-1][0] - 500.0), 100.0)


@unittest.skipUnless(os.path.exists(REAL), 'cora_poodle height grid not exported (run tools/export_terrain.py)')
class CoraPoodleTerrain(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.t = Terrain.load(default_base())

    def test_player_start_ground(self):
        # capsule centre settles at z 1687; the capsule half height is inferred ~90 -> ground ~1597 (+-30)
        z = self.t.ground_z(24832.8, 8957.6)
        self.assertIsNotNone(z)
        self.assertGreater(z, 1567.0)
        self.assertLess(z, 1627.0)

    def test_rock_spawn_points_on_ground(self):
        for x, y in [(24805.0, 10157.0), (23819.0, 8316.0)]:
            z = self.t.ground_z(x, y)
            self.assertIsNotNone(z, (x, y))
            self.assertGreater(z, 1450.0)
            self.assertLess(z, 1750.0)

    def test_player_start_walkable(self):
        self.assertTrue(self.t.walkable(24832.8, 8957.6))

    def test_path_between_spawn_points(self):
        a, b = (24832.8, 8957.6), (24805.0, 10157.0)
        p = self.t.path(a, b)
        self.assertIsNotNone(p)
        self.assertGreaterEqual(len(p), 2)                  # smoothed: open ground is one straight leg
        self.assertEqual((p[0], p[-1]), (a, b))
        for u, v in zip(p, p[1:]):
            self.assertTrue(self.t.clear_line(u, v), (u, v))

    def test_far_outside_island(self):
        self.assertIsNone(self.t.ground_z(-1.0e7, -1.0e7))


if __name__ == '__main__':
    unittest.main()
