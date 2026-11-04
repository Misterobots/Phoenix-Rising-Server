"""Server-side area damage (island_encounter.apply_aoe, engine.projectiles.area_hit) and the hammer salvo blast."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mpgs import island_encounter
from mpgs.engine import hammer, projectiles

BODIES = {'head': [{'pos': [300.0, 0.0, 100.0], 'radius': 50.0}], 'tail': [{'pos': [-300.0, 0.0, 50.0], 'radius': 40.0}]}


class AreaHitTests(unittest.TestCase):
    def test_sphere_reaches_the_nearest_body(self):
        hit = projectiles.area_hit(BODIES, (0.0, 0.0, 0.0), 0.0, (500.0, 0.0, 100.0), 180.0, 200.0)
        self.assertEqual(hit[0], 'head')

    def test_out_of_range_misses(self):
        self.assertIsNone(projectiles.area_hit(BODIES, (0.0, 0.0, 0.0), 0.0, (600.0, 0.0, 100.0), 180.0, 200.0))

    def test_wedge_keeps_only_bodies_in_front(self):
        self.assertIsNotNone(projectiles.area_hit(BODIES, (0.0, 0.0, 0.0), 0.0, (500.0, 0.0, 100.0), 180.0, 200.0, (-30, 30)))
        self.assertIsNone(projectiles.area_hit(BODIES, (0.0, 0.0, 0.0), 0.0, (500.0, 0.0, 100.0), 0.0, 200.0, (-30, 30)))

    def test_actor_yaw_moves_the_bodies(self):
        hit = projectiles.area_hit(BODIES, (0.0, 0.0, 0.0), 90.0, (0.0, 450.0, 100.0), 270.0, 150.0, (-30, 30))
        self.assertEqual(hit[0], 'head')                                 # head at (0, 300) once the actor faces +y


class ApplyAoeTests(unittest.TestCase):
    def setUp(self):
        director = SimpleNamespace(species=SimpleNamespace(bodies=BODIES), loc=(0.0, 0.0, 0.0), yaw=0.0)
        self.enc = {'director': director, 'dead': False,
                    'damage_model': {'rows': {'IH.Blast.LVL1': {'BaseDamage': 120}, 'IH.Blast.LVL4': {'BaseDamage': 360}}}}
        self.session = SimpleNamespace(_encounter=self.enc, pawn_state={'loc': [500.0, 0.0, 100.0], 'ctrl_rot': [0.0, 180.0, 0.0]})
        self.calls = []
        self.patch = patch.object(island_encounter, 'handle_damage', lambda s, ch, blk, p: self.calls.append(p))
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def test_hit_goes_through_the_damage_path_with_row_bone_and_scale(self):
        self.assertEqual(island_encounter.apply_aoe(self.session, 'IH.Blast.LVL1', (500.0, 0.0, 100.0), 180.0, 200.0,
                                                    (-30, 30), 1.75), 'head')
        p = self.calls[0]
        self.assertEqual(p['Payload.DamageTypeName'][0], 'IH.Blast.LVL1')
        self.assertEqual(p['Payload.Hit']['bone_name'], ['head'])
        self.assertEqual(p['DamageScale'], 1.75)

    def test_missing_row_deals_nothing(self):
        self.assertIsNone(island_encounter.apply_aoe(self.session, 'IH.Blast.LVL7', (500.0, 0.0, 100.0), 180.0, 500.0))
        self.assertEqual(self.calls, [])

    def test_salvo_blast_row_and_radius_follow_the_charge_level(self):
        weapons = {12: (900, 'AmmoComponent', 7)}
        with patch.object(island_encounter, '_ammo_weapons', lambda s: weapons):
            self.enc.update(ammo={12: 7}, hammer_charge=4)
            with patch.object(island_encounter, 'apply_aoe', wraps=island_encounter.apply_aoe) as aoe:
                self.assertEqual(hammer.on_salvo_shoot(self.session, self.enc, 0.0), 3)
            args = aoe.call_args[0]
            self.assertEqual((args[1], args[4], args[5]), ('IH.Blast.LVL4', 350.0, (-30.0, 30.0)))
            self.assertEqual(self.calls[0]['Payload.DamageTypeName'][0], 'IH.Blast.LVL4')


if __name__ == '__main__':
    unittest.main()
