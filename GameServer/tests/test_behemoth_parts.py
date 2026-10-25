import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mpgs.behemoth import parts  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _tracker(max_health=10000.0):
    with open(os.path.join(HERE, 'world', 'behemoths', 'lerawr_beta_bodies.json'), encoding='utf-8') as f:
        joints = json.load(f)['joints']
    return parts.PartTracker(parts.load('lerawr_beta'), joints, max_health)


class PartTrackerTests(unittest.TestCase):
    def test_bones_map_to_their_part(self):
        t = _tracker()
        self.assertEqual(t.part_of('horn_jnt'), 'Head')
        self.assertEqual(t.part_of('head_jnt'), 'Head')
        self.assertEqual(t.part_of('tail_05_jnt'), 'Tail')
        self.assertEqual(t.part_of('l_wrist_jnt'), 'LeftFrontLeg')
        self.assertEqual(t.part_of('r_knee_jnt'), 'RightHindLeg')
        self.assertEqual(t.part_of('spine_03_jnt'), 'Body')
        self.assertEqual(t.part_of(None), 'Body')

    def test_tail_breaks_at_its_share_of_health_with_the_tailcut(self):
        t = _tracker()
        part, out = t.hit('tail_05_jnt', 699.0, 'left')
        self.assertEqual((part, [k for k, _, _ in out]), ('Tail', []))
        part, out = t.hit('tail_05_jnt', 2.0, 'left')
        self.assertEqual(out[0][:2], ('break', 'b_aq_lerawr_00_tailcut_fwd_00_mot'))
        self.assertIn('Tail', t.broken)
        _, out = t.hit('tail_05_jnt', 1.0, 'left')
        self.assertFalse([r for r in out if r[0] == 'break'])          # breaks once

    def test_stagger_meter_fills_then_grows(self):
        t = _tracker()
        threshold = t.stagger_threshold
        _, out = t.hit('spine_03_jnt', threshold / parts.STAGGER_RATIO, 'right')
        self.assertEqual(out[0][:2], ('stagger', 'b_leRawr_stagger_right_mot'))
        self.assertEqual(t.stagger, 0.0)
        self.assertGreater(t.stagger_threshold, threshold)


class DirectorStaggerTests(unittest.TestCase):
    def test_stagger_interrupts_the_attack_and_pauses_the_tree(self):
        from types import SimpleNamespace
        from mpgs.behemoth.director import BehemothDirector
        d = BehemothDirector.__new__(BehemothDirector)
        d.dead, d._events, d._windows, d._window_bodies, d._hit_in_window = False, [], {('a', 'k'): {}}, {}, set()
        d._ignore_pawn, d.staggered_until = True, 0.0
        d.brain = SimpleNamespace(run=SimpleNamespace(name='pounce'))
        d.mover = SimpleNamespace(montage=object(), path=[1], play=lambda c, n: True)
        d.ctx = SimpleNamespace(bb={})
        d._log = lambda m: None
        self.assertTrue(d.stagger('b_leRawr_stagger_left_mot', 6.7, 10.0))
        self.assertIsNone(d.brain.run)
        self.assertEqual(d._windows, {})
        self.assertAlmostEqual(d.staggered_until, 16.7)
        self.assertEqual(d.ctx.bb['StaggerCount'], 1)
        self.assertIn('b_leRawr_stagger_left_mot', [e[1] for e in d._events if e[0] == 'montage'])


if __name__ == '__main__':
    unittest.main()
