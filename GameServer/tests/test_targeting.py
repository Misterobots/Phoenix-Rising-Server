import unittest

from mpgs.behemoth.aggro import ACCESSIBLE, INACCESSIBLE
from mpgs.behemoth.targeting import QUERY_RANGE, TargetData, Targeting


def run(t, start, end, owner, candidates, accessibility=INACCESSIBLE, step=0.25):
    now = start
    while now <= end + 1e-9:
        t.update(now, owner, candidates, object(), lambda loc: accessibility)
        now += step
    return now


class TargetingTests(unittest.TestCase):
    def test_visible_player_in_range_becomes_target_even_inaccessible(self):
        t = Targeting()
        run(t, 100.0, 101.0, (0, 0, 0), [('player', (5000.0, 0.0, 0.0), True)])
        self.assertEqual(t.current_key(), 'player')

    def test_player_beyond_query_range_is_not_added(self):
        t = Targeting()
        run(t, 100.0, 102.0, (0, 0, 0), [('player', (QUERY_RANGE + 10.0, 0.0, 0.0), True)])
        self.assertIsNone(t.current_key())

    def test_dead_player_is_not_picked(self):
        t = Targeting()
        run(t, 100.0, 102.0, (0, 0, 0), [('player', (1000.0, 0.0, 0.0), False)])
        self.assertIsNone(t.current_key())

    def test_remove_all_then_reacquire_within_a_second(self):
        t = Targeting()
        cands = [('player', (3000.0, 0.0, 0.0), True)]
        now = run(t, 100.0, 105.0, (0, 0, 0), cands)
        t.remove_all(now)
        self.assertIsNone(t.current_key())
        run(t, now, now + 1.0, (0, 0, 0), cands)
        self.assertEqual(t.current_key(), 'player')

    def test_accessible_target_beats_inaccessible_one(self):
        t = Targeting()
        t.data = [TargetData('a', score=1.0, last_seen=10.0, accessibility=INACCESSIBLE),
                  TargetData('b', score=0.5, last_seen=10.0, last_accessible=10.0, accessibility=ACCESSIBLE)]
        t.try_pick_new_target(10.0)
        self.assertEqual(t.current_key(), 'b')

    def test_stickiness_keeps_current_target_against_a_slightly_higher_score(self):
        t = Targeting()
        t.data = [TargetData('a', score=1.0, last_seen=10.0, accessibility=ACCESSIBLE),
                  TargetData('b', score=1.1, last_seen=10.0, accessibility=ACCESSIBLE)]
        t.current, t.changed_at = 0, 10.0
        t.tick(10.0)
        t.try_pick_new_target(10.0)
        self.assertEqual(t.current_key(), 'a')
        for d in t.data:
            d.last_seen = 40.0
        t.tick(40.0)                      # decayed to TargetStickinessMin
        t.try_pick_new_target(40.0)
        self.assertEqual(t.current_key(), 'b')


if __name__ == '__main__':
    unittest.main()
