"""Weapon parts (mpgs/engine/weapon_parts.py): the equipped weapon's default part components and the part abilities."""
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from mpgs import loadout  # noqa: E402
from mpgs.engine import weapon_parts  # noqa: E402

DATA = weapon_parts.load()


class PartDataTests(unittest.TestCase):
    def test_item_crc_matches_the_recorded_sword_part(self):
        self.assertEqual(weapon_parts.item_crc('PART_EB_SPECIAL_DEFAULT'), 1660171131)

    @unittest.skipUnless(DATA, 'world/player/weapon_parts.json not generated (tools/export_weapon_parts.py)')
    def test_umbral_repeater_parts_and_abilities(self):
        parts = weapon_parts.default_parts(DATA, 'WP_DP_UMBRAL_01')
        self.assertEqual([(s, p) for s, p, _ in parts], [(0, 'PART_DP_BARREL_UMBRAL'), (1, 'PART_DP_RECEIVER_PIERCING'),
                                                          (2, 'PART_DP_GRIP_ALLURING_MOONS'), (3, 'PART_DP_ACTIVE_UMBRAL_1')])
        self.assertEqual([c for _, c, _ in weapon_parts.part_abilities(parts)],
                         ['dp_x_ability_piercing_ab_C', 'dp_y_ability_spawn_raptor_ab_C'])


@unittest.skipUnless(DATA, 'world/player/weapon_parts.json not generated (tools/export_weapon_parts.py)')
class PartSetupTests(unittest.TestCase):
    def test_repeater_hunt_sends_components_and_grants_x_and_y(self):
        import test_island_encounter_engine as T
        if not T.HAVE_DATA:
            self.skipTest('island engine data not generated')
        T.setUpModule()
        self.addCleanup(T.tearDownModule)
        T.EngineEncounterTests.setUpClass()
        w = T.EngineEncounterTests.world
        swapped = loadout.swap_weapon_actor(w.entries, w.schema, 'WP_DP_UMBRAL_01')
        self.addCleanup(loadout.swap_weapon_actor, w.entries, w.schema, 'WP_EB_BEGINNER')
        self.assertIsNotNone(swapped)
        s = T.EngineEncounterTests('test_spawn_stands_on_the_landscape_and_walks').session()   # start() + everything sent
        pa = s._encounter['part_abilities']
        self.assertEqual(sorted(v['cls'] for v in pa.granted.values()),
                         ['dp_x_ability_piercing_ab_C', 'dp_y_ability_spawn_raptor_ab_C'])
        sent = [e for e in s.entries if e.ch == 16 and not e.open and e.parsed.blocks and e.parsed.blocks[-1].actor]
        actor = sent[-1].parsed.blocks[-1].values
        self.assertEqual(actor['WeaponPartIDs']['count'], 4)
        comps = [b for b in sent[-1].parsed.blocks if not b.actor]
        self.assertEqual([b.values['WeaponPartID'] for b in comps],
                         ['PART_DP_BARREL_UMBRAL', 'PART_DP_RECEIVER_PIERCING', 'PART_DP_GRIP_ALLURING_MOONS', 'PART_DP_ACTIVE_UMBRAL_1'])
        self.assertEqual([actor['WeaponPartComponents']['elems'][i]['WeaponPartComponents[]'][1] for i in range(4)],
                         [b.guid for b in comps])


if __name__ == '__main__':
    unittest.main()
