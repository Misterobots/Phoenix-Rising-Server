import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mpgs.engine import stamina  # noqa: E402

Y1 = '/Game/Blueprints/Weapons/AnimStates/eblade_rework_base_animstateset.eblade_rework_base_animstateset:ComboAnimState_4'


class StaminaTests(unittest.TestCase):
    def test_heavy_attack_costs_its_notify_and_light_ones_nothing(self):
        s = stamina.Stamina()
        if not s.costs:
            self.skipTest('world/player/stamina_costs.json not generated')
        self.assertEqual(s.move_cost(Y1), 5.0)
        self.assertEqual(s.move_cost(Y1.upper()), 5.0)          # the client's path case differs from the pak's
        self.assertEqual(s.move_cost(Y1.replace('_4', '_10')), 0.0)

    def test_regen_waits_for_the_delay_then_refills(self):
        s = stamina.Stamina(costs={})
        self.assertTrue(s.spend(40.0, 0.0))
        s.tick(1.0)
        self.assertEqual(s.value, 60.0)                         # inside StaminaRegenDelayAfterUse
        s.tick(2.0)
        self.assertAlmostEqual(s.value, 69.0)                   # 9/s for the second after the delay
        s.tick(100.0)
        self.assertEqual(s.value, 100.0)

    def test_sprint_drains_and_blocks_regen(self):
        s = stamina.Stamina(costs={})
        s.tick(0.0)
        s.sprinting = True
        s.tick(4.0)
        self.assertAlmostEqual(s.value, 85.0)

    def test_overspend_empties_and_reports(self):
        s = stamina.Stamina(value=10.0, costs={})
        self.assertFalse(s.spend(40.0, 0.0))
        self.assertEqual(s.value, 0.0)


if __name__ == '__main__':
    unittest.main()
