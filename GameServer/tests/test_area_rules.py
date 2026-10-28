"""Area rules sheet (tools/atlas/area_rules.py): quick items usable in town come from the client's CanUseInCity."""
import json
import os
import unittest

from tools.atlas import area_rules

SHEET = area_rules.OUT_JSON


@unittest.skipUnless(os.path.exists(SHEET), 'area_rules.json not generated')
class AreaRules(unittest.TestCase):
    def setUp(self):
        self.qi = {r['item']: r for r in json.load(open(SHEET))['quick_items']}

    def test_combat_quick_items_hunt_only(self):
        for item in ('QI_HEALING_PYLON_BIG', 'QI_STAMINA_POTION_00', 'QI_GRENADE_CLUSTER', 'QI_DAMAGE_PYLON'):
            self.assertIs(self.qi[item]['in_town'], False, item)

    def test_curiosities_in_town(self):
        self.assertIs(self.qi['TY_FIREWORK_BASE']['in_town'], True)
        self.assertEqual(self.qi['TY_FIREWORK_BASE']['why'], 'Ability_Curiosity_Firework_Baseline_C')

    def test_everything_resolved(self):
        self.assertFalse([k for k, r in self.qi.items() if r['in_town'] is None])

    def test_inheritance(self):
        # Ability_QuickItem_Base sets no CanUseInCity, so a pylon falls through to the engine default (false)
        self.assertEqual(area_rules.can_use_in_city('Ability_QuickItem_Healing_Pylon_Big_C'), (False, 'engine default'))


if __name__ == '__main__':
    unittest.main()
