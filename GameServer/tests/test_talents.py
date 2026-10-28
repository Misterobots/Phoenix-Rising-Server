"""Weapon talents (mpgs/talents.py): the game's talent data, unlock / equip in the city, and the RPCs that encode."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))  # GameServer/, for mpgs
import os
import unittest
from unittest.mock import patch

from mpgs import talents

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, '..', 'world', 'ramsgate_template.json')
MOONS = 'WP_DP_UMBRAL_01'
SPECIAL = 'WTP_DPISTOLS_ALLURING_TALENT2'


class CatalogTests(unittest.TestCase):
    def test_alluring_moons_talents_from_the_game_tables(self):
        c = talents.catalog()
        self.assertEqual(c.weapons[MOONS], ['WTP_DPISTOLS_ALLURING_TALENT', SPECIAL, 'WTP_DPISTOLS_ALLURING_TALENT3',
                                            'WTP_DPISTOLS_ALLURING_TALENT4', 'WTP_DPISTOLS_ALLURING_TALENT5'])
        self.assertEqual(c.talents[SPECIAL]['category'], 'WeaponSpecial')
        self.assertEqual(c.talents[SPECIAL]['perks'][2]['effects'], ['d24_weapon_talent_sp_03_ge_C'])
        self.assertEqual(c.cost(SPECIAL, 0), [('CURRENCY_TALENT_ESSENCE_PRIMARY', 1), ('CURRENCY_NOTES', 1500)])

    def test_equipped_perk_tags_and_replay(self):
        st = talents.set_flag({}, 'W1', SPECIAL, 2, 'unlocked', True)
        st = talents.set_flag(st, 'W1', SPECIAL, 2, 'equipped', True)
        self.assertEqual(talents.active_tags(st, 'W1'), {'weapon.talent.d24.sp03'})
        self.assertEqual([f for f, _ in talents.replay_rpcs(st)],
                         ['ClientSetWeaponTalentPerkUnlockState', 'ClientSetWeaponTalentPerkEquipState'])
        self.assertEqual(talents.active_tags(talents.set_flag(st, 'W1', SPECIAL, 2, 'equipped', False), 'W1'), set())


@unittest.skipUnless(os.path.exists(TEMPLATE), 'world template missing')
class CityTests(unittest.TestCase):
    def setUp(self):
        from mpgs.genworld import GenWorld
        world = GenWorld.from_template(TEMPLATE)
        self.s = world.new_session({'name': 'T', 'char': 'C', 'acct': 'A', 'session': 'S'})
        self.s.talent_state = {}
        self.s.player_records = {'inventory': {'instancedItems': [{'instanceId': 'W1', 'catalogId': MOONS}]}}
        from mpgs.rewards import item_identity_crc
        self.s.v2_state = [{'instanceId': c, 'catalogIdCrc': item_identity_crc(c), 'quantity': q, 'customData': ''}
                           for c, q in (('CURRENCY_TALENT_ESSENCE_PRIMARY', 5), ('CURRENCY_NOTES', 9000))]
        self.inv = next((e.ch, e.cls) for e in self.s.entries for b in e.parsed.blocks
                        if b.actor and b.cls_name == 'bp_archon_inventory_C')

    def _run(self, func, params):
        import threading
        with patch('mpgs.rewards.post_transaction', return_value=(True, 'ok')) as tx, \
                patch('mpgs.talents.store', return_value={}), \
                patch.object(threading.Thread, 'start', lambda t: t.run()):
            self.s._talent_change(self.inv[0], self.inv[1], func, params)
            self.s._drain_main_queue()
        return tx

    def _sent(self, func):
        from mpgs.repl import model
        out = [e for e in self.s.entries[self.s.pos:] for b in e.parsed.blocks for f in b.fields or () if f[1] == func]
        for e in out:
            model.encode(e.parsed, self.s.ctx, e.cls)                       # encodes with the game's layout
        return out

    def test_unlock_by_index_spends_the_recipe_then_equip(self):
        tx = self._run('ServerSetWeaponTalentPerkUnlockedByIndex',
                       {'ItemId': 'W1', 'TalentIndex': 1, 'TalentPerkIndex': 0, 'NewUnlockValue': 1})
        body = tx.call_args[0][0]
        self.assertEqual(body['removeStackedItems'], [{'catalogId': 'CURRENCY_TALENT_ESSENCE_PRIMARY', 'quantity': 1},
                                                      {'catalogId': 'CURRENCY_NOTES', 'quantity': 1500}])
        self.assertEqual(len(self._sent('ClientSetWeaponTalentPerkUnlockState')), 1)
        self.assertTrue(talents.is_unlocked(self.s.talent_state, 'W1', SPECIAL, 0))
        self._run('ServerSetWeaponTalentPerkEquippedById',
                  {'ItemId': 'W1', 'TalentId': SPECIAL, 'TalentPerkIndex': 0, 'NewEquipValue': 1})
        self.assertEqual(len(self._sent('ClientSetWeaponTalentPerkEquipState')), 1)
        self.assertEqual(talents.active_tags(self.s.talent_state, 'W1'), {'weapon.talent.d24.sp01'})

    def test_refusals(self):
        self._run('ServerSetWeaponTalentPerkEquippedById',
                  {'ItemId': 'W1', 'TalentId': SPECIAL, 'TalentPerkIndex': 3, 'NewEquipValue': 1})   # not unlocked
        tx = self._run('ServerSetWeaponTalentPerkUnlockedById',
                       {'ItemId': 'W1', 'TalentId': SPECIAL, 'TalentPerkIndex': 4, 'NewUnlockValue': 1})  # 30 essence
        tx.assert_not_called()
        self.assertEqual(self._sent('ClientSetWeaponTalentPerkEquipState') + self._sent('ClientSetWeaponTalentPerkUnlockState'), [])


if __name__ == '__main__':
    unittest.main()
