"""Repeater heavy (dp_x_ability_piercing_ab): the activation request's leading params, SpawnProjectile on the ability instance,
and the projectile's ray hit."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mpgs.engine import abilities, projectiles  # noqa: E402
from mpgs.loadout import register_atlas_class_full  # noqa: E402
from mpgs.repl import rpc  # noqa: E402
from mpgs.repl.schema import Schema  # noqa: E402
from mpgs.bitio import BitWriter  # noqa: E402

LIVE = '1b0000001c02c00140190200000030000000000000'   # ServerTryActivateAbilityWithEventData, 166 bits (live 2026-10-26)


class PartAbilityRpcTests(unittest.TestCase):
    def test_activation_prefix_matches_live_capture(self):
        p = abilities.activation_prefix((int.from_bytes(bytes.fromhex(LIVE), 'little'), 166))
        self.assertEqual(p['AbilityToActivate.Handle'], 13)            # Q (the grip's ability)
        self.assertEqual(p['PredictionKey'], {'server': 0, 'current': 8, 'base': 7})

    def test_spawn_projectile_rpc_layout(self):
        s = Schema()
        self.assertTrue(register_atlas_class_full(s, 'dp_x_ability_piercing_ab_C'))
        self.assertEqual(s.field_index('dp_x_ability_piercing_ab_C')[-1], ('func', 'SpawnProjectile'))
        lay = rpc.rpc_layout(s, 'dp_x_ability_piercing_ab_C', 'SpawnProjectile')
        w = rpc.encode(lay, {'Loc': [1.0, 2.0, 3.0], 'Rot': [0.0, 90.0, 0.0]})
        back = rpc.try_decode(s, 'dp_x_ability_piercing_ab_C', 'SpawnProjectile', w.n, w.nbits)
        self.assertEqual(list(back['Loc']), [1.0, 2.0, 3.0])

    def test_ray_hits_body_in_front(self):
        bodies = {'head_jnt': [{'pos': [300.0, 0.0, 0.0], 'radius': 50.0, 'half_length': 0.0}]}
        hit = projectiles.ray_hit(bodies, [1000.0, 0.0, 100.0], 90.0, [1000.0, -1000.0, 100.0], [0.0, 90.0, 0.0], 1000.0)
        self.assertIsNone(hit)              # the body sits at +y 300 when the actor faces +y: 1300 cm away, past this range
        hit = projectiles.ray_hit(bodies, [1000.0, 0.0, 100.0], 90.0, [1000.0, -1000.0, 100.0], [0.0, 90.0, 0.0], 2000.0)
        self.assertEqual(hit[0], 'head_jnt')
        self.assertIsNone(projectiles.ray_hit(bodies, [1000.0, 0.0, 100.0], 90.0, [1000.0, -1000.0, 100.0], [0.0, 0.0, 0.0]))


if __name__ == '__main__':
    unittest.main()
