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

    def test_body_never_breaks(self):
        t = _tracker()
        _, out = t.hit('spine_03_jnt', 5000.0, 'left')
        self.assertNotIn('Body', t.broken)
        self.assertFalse([r for r in out if r[0] == 'break'])

    def test_stagger_follows_the_stage_table(self):
        """EBRework.X1 (BaseDamage 100, WeaponImpact): 100 - stage 1 per-hit armor 80 = 20 stagger a hit; 500 needed."""
        t = _tracker()
        row = {'BaseDamage': 100, 'tags': ['Combat.Weapon.WeaponImpact']}
        self.assertEqual(t.stagger_damage(row), 20.0)
        out = []
        for i in range(25):
            _, out = t.hit('spine_03_jnt', 100.0, 'right', row=row, now=0.0)
        self.assertEqual(out[0][:2], ('stagger', 'b_leRawr_stagger_right_mot'))
        self.assertEqual((t.stage, t.stagger_max), (1, 600.0))
        self.assertEqual(round(sum(a[0] for a in t.armor)), 4000)      # the new stage's temporary armor
        _, out = t.hit('spine_03_jnt', 100.0, 'right', row=row, now=0.0)
        self.assertEqual(t.stagger, 600.0)                              # soaked by armor

    def test_meter_regenerates_and_blunt_hits_harder(self):
        t = _tracker()
        hammer = {'BaseDamage': 100, 'tags': ['Combat.Weapon.WeaponImpact', 'DamageType.Blunt']}
        self.assertAlmostEqual(t.stagger_damage(hammer), 100 * 1.333 - 80, places=1)
        t.hit('spine_03_jnt', 1.0, 'left', row=hammer, now=0.0)
        low = t.stagger
        t.tick(2.0)
        self.assertAlmostEqual(t.stagger, min(500.0, low + 30.0), places=3)
        self.assertEqual(t.stagger_damage({'BaseDamage': 100, 'tags': []}), 0.0)    # no WeaponImpact: no stagger


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
