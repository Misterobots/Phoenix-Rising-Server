"""War hammer server state (mpgs.engine.hammer; atlas topics/hammer_server.md)."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mpgs import island_encounter
from mpgs.engine import hammer
from mpgs.engine.stamina import Stamina

HAMMER_CH = 12
PRIME = {'InputAction.Action': ('obj', 501, '/Game/Blueprints/Weapons/prime_hammer_input_action_bp.prime_hammer_input_action_bp_C')}
RELOAD = {'InputAction.Action': '/Game/Blueprints/Weapons/reload_hammer_shell_input_action_bp.reload_hammer_shell_input_action_bp_C'}


def hammer_session(ammo=None, stamina=100.0):
    enc = {'ammo': {} if ammo is None else {HAMMER_CH: ammo}, 'stamina': Stamina(stamina, 100.0)}
    return SimpleNamespace(_encounter=enc), enc


class HammerTests(unittest.TestCase):
    def setUp(self):
        weapons = {HAMMER_CH: (900, 'AmmoComponent', 7)}
        self.patch = patch.object(island_encounter, '_ammo_weapons', lambda session: weapons)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def test_hammer_ammo_component_is_full_on_equip(self):
        session, enc = hammer_session()
        self.assertEqual(island_encounter.ammo_max(session, HAMMER_CH), 7)
        self.assertEqual(island_encounter.adjust_ammo(session, HAMMER_CH, 0), 7)

    def test_prime_spends_a_shell_and_stamina_and_arms_the_next_impact(self):
        session, enc = hammer_session(ammo=7)
        self.assertEqual(hammer.on_input_action(session, enc, PRIME, 0.0), 'prime')
        self.assertEqual(enc['ammo'][HAMMER_CH], 6)
        self.assertAlmostEqual(enc['stamina'].value, 90.0)
        self.assertTrue(hammer.on_damage_given(session, enc))
        self.assertFalse(hammer.on_damage_given(session, enc))       # one shell, one empowered hit

    def test_prime_needs_ammo(self):
        session, enc = hammer_session(ammo=0)
        self.assertIsNone(hammer.on_input_action(session, enc, PRIME, 0.0))
        self.assertFalse(enc.get('hammer_primed'))

    def test_inline_reload_adds_one_shell_until_full(self):
        session, enc = hammer_session(ammo=5)
        self.assertEqual(hammer.on_input_action(session, enc, RELOAD, 0.0), 'reload_shell')
        self.assertEqual(enc['ammo'][HAMMER_CH], 6)
        self.assertAlmostEqual(enc['stamina'].value, 95.0)
        enc['ammo'][HAMMER_CH] = 7
        self.assertIsNone(hammer.on_input_action(session, enc, RELOAD, 0.0))

    def test_input_action_refused_without_stamina(self):
        session, enc = hammer_session(ammo=7, stamina=4.0)
        self.assertIsNone(hammer.on_input_action(session, enc, PRIME, 0.0))
        self.assertEqual(enc['ammo'][HAMMER_CH], 7)

    def test_blast_ability_spends_one_shell(self):
        session, enc = hammer_session(ammo=3)
        self.assertEqual(hammer.on_blast(session, 'ih_blast_shock_ab_C'), 2)
        self.assertIsNone(hammer.on_blast(session, 'ih_primed_ammo_shell_ab_C'))
        self.assertEqual(enc['ammo'][HAMMER_CH], 2)


if __name__ == '__main__':
    unittest.main()
