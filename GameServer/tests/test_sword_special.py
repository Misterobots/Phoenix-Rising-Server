"""The sword special on the server: special_active_ge and the meter drain (mpgs/engine/sword_special.py)."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))  # GameServer/, for mpgs
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, '..', 'world', 'ramsgate_template.json')


@unittest.skipUnless(os.path.exists(TEMPLATE), 'world template missing')
class SwordSpecialTests(unittest.TestCase):
    def setUp(self):
        from mpgs.genworld import GenWorld
        from mpgs.engine.weapon_meter import WeaponMeter
        world = GenWorld.from_template(TEMPLATE)
        self.s = world.new_session({'name': 'T', 'char': 'C', 'acct': 'A', 'session': 'S'})
        self.meter = WeaponMeter(self.s, {}, lambda m: None)
        self.enc = {'meter': self.meter}

    def test_needs_a_full_meter_and_the_special_key(self):
        from mpgs.engine import sword_special
        self.assertEqual(sword_special.on_combo(self.s, self.enc, sword_special.SPECIAL_KEY, 0.0), [])
        self.meter.value = self.meter.max
        self.assertEqual(sword_special.on_combo(self.s, self.enc, 1, 0.0), [])           # a normal attack key
        self.assertFalse(self.enc.get('sword_special', {}).get('active'))

    def test_drains_with_compounding_and_ends_at_empty(self):
        from mpgs.engine import sword_special, effects
        self.meter.value = self.meter.max
        self.assertTrue(sword_special.on_combo(self.s, self.enc, sword_special.SPECIAL_KEY, 0.0))   # the effect entries
        self.assertTrue(self.meter.special_active)
        pe = effects.for_session(self.s, 'pawn')
        self.assertIn(sword_special.SPECIAL_GE, [a['cls'] for a in pe.active.values()])
        sword_special.step(self.s, self.enc, 1.0)
        self.assertAlmostEqual(self.meter.value, 100.0 - 5.2, places=6)                   # 5/s, +0.2/s after 1 s
        t = 1.0
        while self.enc['sword_special']['active'] and t < 60:
            t += 0.1
            sword_special.step(self.s, self.enc, t)
        self.assertEqual(self.meter.value, 0.0)
        self.assertFalse(self.meter.special_active)
        self.assertNotIn(sword_special.SPECIAL_GE, [a['cls'] for a in pe.active.values()])
        self.assertLess(t, 20.0)                     # 100 at 5/s growing 0.2/s: about 14 s

    def test_charges_from_hits_and_the_special(self):
        from mpgs.engine import sword_special
        from mpgs.repl import model
        two = ['Combat.Sword.TierAdvancer', 'Combat.Sword.TokenGranterTwo']
        e = sword_special.on_hit(self.s, self.enc, two, 0.0)
        self.assertEqual(self.enc['swordcharges']['n'], 2)
        model.encode(e[0].parsed, self.s.ctx, e[0].cls)              # encodes with the sword's replicated layout
        self.assertEqual(sword_special.on_hit(self.s, self.enc, two, 0.1), [])     # at most once per 0.25 s
        self.assertEqual(sword_special.on_hit(self.s, self.enc, ['Combat.Sword.TokenGranter'], 1.0), [])  # no TierAdvancer
        for t in (1.0, 2.0, 3.0):
            sword_special.on_hit(self.s, self.enc, two, t)
        self.assertEqual(self.enc['swordcharges']['n'], 6)            # capped
        self.meter.value = self.meter.max
        self.enc['swordcharges']['n'] = 0
        sword_special.on_combo(self.s, self.enc, sword_special.SPECIAL_KEY, 10.0)
        self.assertEqual(self.enc['swordcharges']['n'], 1)            # GenerateSwordcharge(1)
        sword_special.step(self.s, self.enc, 14.0)                    # 20 x 5% = one more charge
        self.assertEqual(self.enc['swordcharges']['n'], 2)

    def test_moves_spend_charges(self):
        from mpgs.engine import sword_special
        x6 = '/Game/Blueprints/Weapons/AnimStates/eblade_base_animstateset.eblade_base_animstateset:ComboAnimState_17'
        y5 = '/Game/Blueprints/Weapons/AnimStates/eblade_base_animstateset.eblade_base_animstateset:ComboAnimState_21'
        self.enc['swordcharges'] = {'n': 2, 'fill': 0.0, 'last_gain': -1e9}
        self.assertEqual(sword_special.on_move(self.s, self.enc, y5), [])  # 3 needed: the client would not allow it
        self.assertTrue(sword_special.on_move(self.s, self.enc, x6))
        self.assertEqual(self.enc['swordcharges']['n'], 1)

    def test_feast_drain_and_lifesteal(self):
        from mpgs.engine import sword_special
        self.enc.update(buffs={sword_special.DRAIN_BUFF: 1, sword_special.LIFESTEAL_BUFF: 1}, player_health=500.0,
                        player_max_health=1000.0)
        self.assertEqual(sword_special.lifesteal(self.s, self.enc, 100.0), [])          # Feast off: no heal
        self.meter.value = self.meter.max
        sword_special.on_combo(self.s, self.enc, sword_special.SPECIAL_KEY, 0.0)
        self.assertTrue(sword_special.lifesteal(self.s, self.enc, 100.0))
        self.assertAlmostEqual(self.enc['player_health'], 512.0)                          # 12% of the damage dealt
        sword_special.step(self.s, self.enc, 1.0)
        self.assertAlmostEqual(self.enc['player_health'], 512.0 - 5 * 7.0)                # 7 every 0.2 s
        self.enc['player_health'] = 4.0
        sword_special.step(self.s, self.enc, 2.0)
        self.assertEqual(self.enc['player_health'], 1.0)                                  # never below 1

    def test_no_drain_without_the_feast_buff(self):
        from mpgs.engine import sword_special
        self.enc.update(player_health=500.0, player_max_health=1000.0)
        self.meter.value = self.meter.max
        sword_special.on_combo(self.s, self.enc, sword_special.SPECIAL_KEY, 0.0)
        sword_special.step(self.s, self.enc, 1.0)
        self.assertEqual(self.enc['player_health'], 500.0)                                # the default sword's Overdrive


if __name__ == '__main__':
    unittest.main()
