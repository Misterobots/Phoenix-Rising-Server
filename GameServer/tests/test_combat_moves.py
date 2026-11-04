"""Combo moves on the server (mpgs.engine.combat_moves) over world/player/combat_moves.json."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mpgs import island_encounter
from mpgs.engine import combat_moves
from mpgs.engine.stamina import Stamina

SET = '/Game/Blueprints/Weapons/AnimStates/ihammer_base_animstateset.ihammer_base_animstateset:ComboAnimState_%d'
X1, Y1_CHARGE, RELOAD, RELOAD_SUCCESS = SET % 15, SET % 23, SET % 28, SET % 29
Y1_AERIAL, SHOOT_LONG = SET % 1, SET % 33
HAMMER_CH = 12


class CombatMovesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not combat_moves.load()[0]:
            raise unittest.SkipTest('world/player/combat_moves.json not generated (tools/export_combat_moves.py)')

    def setUp(self):
        weapons = {HAMMER_CH: (900, 'AmmoComponent', 7)}
        self.patch = patch.object(island_encounter, '_ammo_weapons', lambda session: weapons)
        self.patch.start()
        self.enc = {'ammo': {HAMMER_CH: 2}, 'stamina': Stamina(100.0, 100.0, 0.0),
                    'parts_by_type': {'EWeaponPartType::Weapon_Part_IH_Special': 'ih_special_default_bpc_C'}}
        self.session = SimpleNamespace(_encounter=self.enc)
        self.moves = combat_moves.Moves()

    def tearDown(self):
        self.patch.stop()

    def test_states_resolve_whatever_the_path_case(self):
        self.moves.start(self.session, self.enc, X1.upper(), 0.0)
        self.assertEqual(self.moves.ctx['state'], 'X1')

    def test_charge_spends_stamina_at_each_notify_and_loops(self):
        self.moves.start(self.session, self.enc, Y1_CHARGE, 0.0)
        self.moves.step(self.session, self.enc, 0.4)
        self.assertEqual(self.enc['stamina'].value, 100.0)               # first Notify_ConsumeStamina at 0.5 s
        self.moves.step(self.session, self.enc, 2.3)
        self.assertEqual(self.enc['stamina'].value, 85.0)                # 0.5, 1.35, 2.2
        self.moves.step(self.session, self.enc, 3.5)
        self.assertEqual(self.enc['stamina'].value, 80.0)                # 3.03, then the Loop section (2.66-3.4) repeats
        self.moves.step(self.session, self.enc, 3.5 + 0.74)
        self.assertEqual(self.enc['stamina'].value, 75.0)

    def test_active_reload_fills_the_clip_at_its_notify(self):
        self.moves.start(self.session, self.enc, RELOAD, 0.0)
        self.moves.step(self.session, self.enc, 2.0)
        self.assertEqual(self.enc['ammo'][HAMMER_CH], 2)
        self.moves.step(self.session, self.enc, 2.2)                     # reload_ammo_hammer_bpn at 2.1914 s
        self.assertEqual(self.enc['ammo'][HAMMER_CH], 7)

    def test_fast_reload_fills_the_clip_at_once(self):
        self.moves.start(self.session, self.enc, RELOAD, 0.0)
        self.moves.start(self.session, self.enc, RELOAD_SUCCESS, 1.0)    # second press inside the window
        self.moves.step(self.session, self.enc, 1.05)                    # notify at 0.0209 s of the Success section
        self.assertEqual(self.enc['ammo'][HAMMER_CH], 7)

    def test_unused_primed_shell_goes_when_the_strike_window_ends(self):
        self.moves.start(self.session, self.enc, X1, 0.0)
        self.enc['hammer_primed'] = True
        self.moves.step(self.session, self.enc, 0.9)
        self.assertTrue(self.enc['hammer_primed'])                        # window 0.2341 + 0.7975 s
        self.moves.step(self.session, self.enc, 1.1)
        self.assertFalse(self.enc['hammer_primed'])

    def test_a_new_move_ends_the_active_windows(self):
        self.moves.start(self.session, self.enc, X1, 0.0)
        self.enc['hammer_primed'] = True
        self.moves.step(self.session, self.enc, 0.5)
        self.moves.start(self.session, self.enc, Y1_CHARGE, 0.6)
        self.assertFalse(self.enc['hammer_primed'])

    def test_out_of_stamina_is_reported(self):
        self.enc['stamina'] = Stamina(3.0, 100.0, 0.0)
        told = []
        self.moves.start(self.session, self.enc, Y1_CHARGE, 0.0, out_of_stamina=lambda: told.append(1))
        self.moves.step(self.session, self.enc, 0.6)
        self.assertEqual(told, [1])


    def test_salvo_spends_the_charged_shells_when_the_shot_fires(self):
        self.enc['ammo'][HAMMER_CH] = 7
        self.moves.start(self.session, self.enc, Y1_CHARGE, 0.0)
        combat_moves.on_charge(self.session, self.enc, 'ServerStartCharge', {'PrimaryKey': 2}, 0.1)
        for level in range(1, 5):
            combat_moves.on_charge(self.session, self.enc, 'ServerProcessCharge',
                                   {'PreviousChargeLevel': level - 1, 'NewChargeLevel': level}, 0.1 + level * 0.5)
        combat_moves.on_charge(self.session, self.enc, 'ServerEndCharge', {'PrimaryKey': 2}, 2.2)
        self.moves.start(self.session, self.enc, SHOOT_LONG, 2.2)
        self.assertEqual(self.enc['ammo'][HAMMER_CH], 7)
        self.moves.step(self.session, self.enc, 2.3)                     # 'Shoot' part event at 0.0681 s
        self.assertEqual(self.enc['ammo'][HAMMER_CH], 3)

    def test_uncharged_blast_spends_one_shell_and_none_when_empty(self):
        self.moves.start(self.session, self.enc, Y1_AERIAL, 0.0)
        self.moves.step(self.session, self.enc, 2.0)
        self.assertEqual(self.enc['ammo'][HAMMER_CH], 1)
        self.enc['ammo'][HAMMER_CH] = 0
        self.moves.start(self.session, self.enc, Y1_AERIAL, 3.0)
        self.moves.step(self.session, self.enc, 5.0)
        self.assertEqual(self.enc['ammo'][HAMMER_CH], 0)

    def test_part_events_without_the_part_do_nothing(self):
        self.enc['parts_by_type'] = {}
        self.moves.start(self.session, self.enc, Y1_AERIAL, 0.0)
        self.moves.step(self.session, self.enc, 2.0)
        self.assertEqual(self.enc['ammo'][HAMMER_CH], 2)


    def test_charge_is_released_when_the_move_chain_ends(self):
        combat_moves.on_charge(self.session, self.enc, 'ServerStartCharge', {}, 0.0)
        combat_moves.on_charge(self.session, self.enc, 'ServerProcessCharge', {'PreviousChargeLevel': 0, 'NewChargeLevel': 2}, 0.5)
        self.moves.start(self.session, self.enc, X1, 1.0)
        self.assertEqual(self.enc['hammer_charge'], 2)
        self.moves.step(self.session, self.enc, 4.0)                     # X1 (2.8 s) ends with no next move
        self.assertIsNone(self.enc['hammer_charge'])


if __name__ == '__main__':
    unittest.main()
