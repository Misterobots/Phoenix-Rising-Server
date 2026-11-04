import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mpgs.engine import stamina  # noqa: E402


class StaminaTests(unittest.TestCase):
    def test_regen_waits_for_the_delay_then_refills(self):
        s = stamina.Stamina()
        self.assertTrue(s.spend(40.0, 0.0))
        s.tick(1.0)
        self.assertEqual(s.value, 60.0)                         # inside StaminaRegenDelayAfterUse
        s.tick(2.0)
        self.assertAlmostEqual(s.value, 69.0)                   # 9/s for the second after the delay
        s.tick(100.0)
        self.assertEqual(s.value, 100.0)

    def test_sprint_drains_and_blocks_regen(self):
        s = stamina.Stamina()
        s.tick(0.0)
        s.sprinting = True
        s.tick(4.0)
        self.assertAlmostEqual(s.value, 85.0)

    def test_overspend_empties_and_reports(self):
        s = stamina.Stamina(value=10.0)
        self.assertFalse(s.spend(40.0, 0.0))
        self.assertEqual(s.value, 0.0)


if __name__ == '__main__':
    unittest.main()
