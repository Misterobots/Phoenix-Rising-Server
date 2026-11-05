"""The axe's determination meter and charge levels on the server (mpgs/engine/axe.py)."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))  # GameServer/, for mpgs
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, '..', 'world', 'ramsgate_template.json')


class Meter:
    """The WeaponMeter fields axe.py uses; the test world's weapon is a sword."""
    def __init__(self):
        from mpgs.engine.weapon_meter import AXE_BASE, METER_MAX
        self.base, self.max, self.value, self.multiplier = AXE_BASE, METER_MAX[AXE_BASE], 0.0, 1.0

    @property
    def full(self):
        return self.value >= self.max

    def update_entry(self):
        return ('Determination', self.value)


@unittest.skipUnless(os.path.exists(TEMPLATE), 'world template missing')
class AxeTests(unittest.TestCase):
    def setUp(self):
        from mpgs.genworld import GenWorld
        world = GenWorld.from_template(TEMPLATE)
        self.s = world.new_session({'name': 'T', 'char': 'C', 'acct': 'A', 'session': 'S'})
        self.enc = {'meter': Meter()}

    def test_gain_by_total_charge_level(self):
        from mpgs.engine import axe
        self.assertEqual(axe.on_hit(self.s, self.enc, [], 0.0), [])             # total charge 0: no gain
        axe._state(self.enc)['total'] = 2
        axe.on_hit(self.s, self.enc, [], 1.0)
        self.assertEqual(self.enc['meter'].value, 6.0)
        self.assertEqual(axe.on_hit(self.s, self.enc, [axe.NO_METER_TAG], 2.0), [])

    def test_ready_then_level_up(self):
        from mpgs.engine import axe
        axe._state(self.enc)['total'] = 3
        axe.on_hit(self.s, self.enc, [], 0.0)
        axe.on_hit(self.s, self.enc, [], 0.1)                                    # 9 + 9 -> 12: full
        a = axe._state(self.enc)
        self.assertTrue(a['ready'])
        axe.on_hit(self.s, self.enc, [], 0.2)                                    # the next hit levels up
        self.assertEqual((a['level'], a['ready'], self.enc['meter'].value), (1, False, 0.0))
        self.assertEqual(axe.on_hit(self.s, self.enc, [], 0.5), [])             # hits ignored for 1 s
        axe.on_hit(self.s, self.enc, [], 1.3)
        self.assertEqual(self.enc['meter'].value, 9.0)

    def test_sword_meter_is_not_an_axe(self):
        from mpgs.engine import axe
        self.enc['meter'].base = 'BP_EB_Weapon_C'
        self.assertEqual(axe.on_hit(self.s, self.enc, [], 0.0), [])


if __name__ == '__main__':
    unittest.main()
