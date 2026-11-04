import unittest

from mpgs.behemoth.aggro import ACCESSIBLE, CLOSE_BUT_LONG_PATH, INACCESSIBLE, Aggro, calculate_accessibility
from mpgs.behemoth.targeting import TargetData


class FakeNav:
    def __init__(self, project=True, cost=100.0):
        self.project, self.cost = project, cost

    def project_point(self, point, extent):
        return (0, tuple(point)) if self.project else None

    def find_path(self, start, end):
        return {'cost': self.cost} if self.cost is not None else None


def combat_aggro():
    bb = {'HasEnteredCombatSinceLastDisengage': True}
    return Aggro(bb), bb


class AggroTests(unittest.TestCase):
    def test_accessibility_rules(self):
        owner, near, far = (0, 0, 0), (1000, 0, 0), (2000, 0, 0)
        self.assertEqual(calculate_accessibility(FakeNav(project=False), owner, near)[0], INACCESSIBLE)
        self.assertEqual(calculate_accessibility(FakeNav(cost=None), owner, near)[:2], (INACCESSIBLE, False))
        self.assertEqual(calculate_accessibility(FakeNav(cost=12000.0), owner, far)[:2], (INACCESSIBLE, True))
        self.assertEqual(calculate_accessibility(FakeNav(cost=4000.0), owner, near)[0], CLOSE_BUT_LONG_PATH)
        self.assertEqual(calculate_accessibility(FakeNav(cost=4000.0), owner, far)[0], ACCESSIBLE)
        self.assertEqual(calculate_accessibility(FakeNav(cost=100.0), owner, near)[0], ACCESSIBLE)

    def test_inaccessible_target_reaches_ready_to_deaggro(self):
        aggro, bb = combat_aggro()
        target, now = TargetData('player', accessibility=INACCESSIBLE), 0.0
        while now < 100.5:
            aggro.update(now, 0.25, True, target, {9})
            now += 0.25
        self.assertEqual(aggro.current, 100.0)
        self.assertTrue(bb['ReadyToDeaggro'])

    def test_no_frustration_before_combat(self):
        aggro, bb = combat_aggro()
        bb['HasEnteredCombatSinceLastDisengage'] = False
        aggro.increase(10.0, 0.0, {9})
        self.assertEqual(aggro.current, 0.0)

    def test_degen_after_delay_and_warning_keys(self):
        aggro, bb = combat_aggro()
        bb['CanDeaggroWarning'] = True
        aggro.increase(60.0, 0.0, {9})
        self.assertTrue(bb['ReadyToDeaggroWarning'])
        aggro.update(2.0, 0.25, False, None, {9})
        self.assertEqual(aggro.current, 60.0)
        now = 3.0
        while now <= 5.0:
            aggro.update(now, 0.25, False, None, {9})
            now += 0.25
        self.assertLess(aggro.current, 50.0)
        self.assertFalse(bb['ReadyToDeaggroWarning'])

    def test_reset_clears_ready_to_deaggro(self):
        aggro, bb = combat_aggro()
        aggro.increase(150.0, 0.0, {9})
        aggro.reset()
        self.assertEqual(aggro.current, 0.0)
        self.assertFalse(bb['ReadyToDeaggro'])
        self.assertTrue(bb['CanDeaggroWarning'])


if __name__ == '__main__':
    unittest.main()
