"""Gear stats and set bonuses (mpgs/engine/gear.py, atlas topics/armor.md s.4)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mpgs.engine import gear  # noqa: E402

DATA = gear.load()


@unittest.skipUnless(DATA, 'world/player/gear.json not generated (tools/export_gear.py)')
class GearTests(unittest.TestCase):
    def test_embermane_set_gives_crit_stats_and_no_set_effects(self):
        pieces = [('AR_LERAWR_HELM', 0), ('AR_LERAWR_ARMS', 2), ('AR_LERAWR_CHEST', 0), ('AR_LERAWR_LEGS', 0)]
        mods, tiers = gear.compute(DATA, pieces)
        self.assertAlmostEqual(mods[('ArchonDamageAttributeSet', 'CriticalStrikeBonusDamage')], 0.2 + 0.22)
        self.assertAlmostEqual(mods[('ArchonDamageAttributeSet', 'CriticalStrikeChance')], 0.1)
        self.assertEqual([t['required'] for t in tiers['LERAWR']], [2, 4])
        self.assertFalse(any(t['buffs'] or t['effects'] or t['abilities'] for t in tiers['LERAWR']))

    def test_set_counts_distinct_pieces(self):
        _, tiers = gear.compute(DATA, [('AR_BUGBEAR_HELM', 0)])
        self.assertNotIn('BUGBEAR', tiers)
        _, tiers = gear.compute(DATA, [('AR_BUGBEAR_HELM', 0), ('AR_BUGBEAR_ARMS', 0)])
        self.assertEqual(len(tiers['BUGBEAR']), 1)
        self.assertTrue(tiers['BUGBEAR'][0]['buffs'])

    def test_level_index(self):
        self.assertEqual(gear.level_of('EUpgradeableItemLevel::Level_03'), 3)
        self.assertEqual(gear.level_of(None), 0)


if __name__ == '__main__':
    unittest.main()
