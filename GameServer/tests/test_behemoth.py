"""Rock behemoth decision loop (needs world/behemoths/rock.json: python -m tools.export_behemoth)."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))  # GameServer/, for mpgs
import math
import os
import random
import unittest

from mpgs.behemoth import Brain, Target, load_rock
from mpgs.behemoth import geometry
from mpgs.behemoth.brain import CUSTOM_STATE, RAGE, ROCK

DATA = load_rock() if os.path.exists(ROCK) else None


def brain(variant='normal', **kw):
    return Brain(DATA, variant, rng=random.Random(1), **kw)


@unittest.skipIf(DATA is None, 'world/behemoths/rock.json not generated (python -m tools.export_behemoth)')
class RockTests(unittest.TestCase):
    def test_geometry_conventions(self):
        arc = {'range': 700, 'angle': 70, 'center_angle': 55, 'offset': [-100, 0]}
        self.assertTrue(geometry.in_arc((0, 0), 0, (300, 500), arc))        # front right
        self.assertFalse(geometry.in_arc((0, 0), 0, (300, -500), arc))      # front left is a different arc
        self.assertFalse(geometry.in_arc((0, 0), 0, (300, 2000), arc))      # out of range
        self.assertTrue(geometry.in_arc((0, 0), 90, (-500, 0), arc))        # facing +Y: world -X is on its right
        self.assertFalse(geometry.in_arc((0, 0), 90, (500, 0), arc))
        box = {'offset': [-150, -750, 0], 'extent': [650, 450, 500], 'yaw': 90}
        self.assertTrue(geometry.in_box((0, 0), 0, (0, -750), box))
        self.assertFalse(geometry.in_box((0, 0), 0, (0, 750), box))

    def test_tables_loaded(self):
        self.assertEqual(len(DATA['variants']['normal']['actions']), 15)
        self.assertEqual(len(DATA['variants']['alpha']['actions']), 18)
        side = DATA['variants']['normal']['actions']['SideTackleL']
        self.assertEqual(side['cooldown'], 20.0)
        self.assertEqual(side['attack']['excluded_states'], [RAGE, CUSTOM_STATE])

    def test_state_rules(self):
        b = brain()
        targets = [Target('p1', (0.0, -750.0))]                      # inside SideTackleL's box
        ok, why = b.available('SideTackleL', 0, 2)
        self.assertTrue(ok, why)
        b.set_state(RAGE)
        self.assertEqual(b.available('SideTackleL', 0, 2), (False, 'excluded state'))
        # concealed strikes need Rage; without it they are unavailable
        b2 = brain()
        self.assertEqual(b2.available('ConcealedStrikeFwd', 0, 2), (False, 'required state'))
        b2.set_state(RAGE)
        self.assertTrue(b2.available('ConcealedStrikeFwd', 0, 2)[0])
        self.assertTrue(targets)

    def test_choose_picks_attack_covering_target(self):
        b = brain()
        t = Target('p1', (0.0, -750.0))
        name, score, hit = b.choose(0, [t])
        self.assertEqual(name, 'WhirlingFortress')                  # 360-degree range 4200 and BaseValue 1.1 beat 1.0
        self.assertAlmostEqual(score, 1.1)
        b.start(name, 0.0, t)
        b.run = None
        name2, score2, _ = b.choose(1.0, [t])                       # WhirlingFortress is now on a 120 s cooldown
        self.assertNotEqual(name2, 'WhirlingFortress')
        self.assertAlmostEqual(score2, 1.0)
        self.assertGreater(b.evaluate('SideTackleL', 1.0, [t])[0], 0.0)
        self.assertEqual(b.evaluate('SideTackleL', 1.0, [Target('p2', (0.0, 750.0))])[0], 0.0)

    def test_yaw_sweep_finds_an_attack_for_an_off_axis_target(self):
        # live 2026-10-15: player 893 units away, 21 degrees off the Rock's nose -> no attack at the exact facing
        a = math.radians(-21.0)
        t = Target('p1', (893.0 * math.cos(a), 893.0 * math.sin(a)))
        b = brain()
        for n in list(b.actions):
            if n != 'FrontTackle':
                del b.actions[n]
        self.assertIsNone(b.choose(0, [t]))
        b.yaw_sweep = 30.0
        name, _, _ = b.choose(0, [t])
        self.assertEqual(name, 'FrontTackle')
        self.assertIn(round(b.chosen_yaw), (-10, -20, -30))
        self.assertEqual(b.yaw, 0.0)                                 # the sweep does not turn the brain itself

    def test_no_target_no_choice(self):
        self.assertIsNone(brain().choose(0, [Target('far', (50000.0, 50000.0))]))

    def test_force_action_override(self):
        b = brain()
        b.force_action = 'ShellSlam'
        self.assertEqual(b.choose(0, [])[:2], ('ShellSlam', 99999.0))

    def test_cooldown_blocks_then_expires(self):
        b = brain()
        t = Target('p1', (0.0, -750.0))
        b.start('SideTackleL', 0.0, t)
        self.assertEqual(b.available('SideTackleL', 5.0, 2), (False, 'on cooldown'))
        self.assertTrue(b.on_cooldown('SideTackleL', 19.9))
        self.assertFalse(b.on_cooldown('SideTackleL', 20.0))
        self.assertTrue(b.available('SideTackleL', 20.0, 2)[0])

    def test_intensity_gates_actions(self):
        b = brain()
        self.assertEqual(b.intensity([Target('a', (0, 0), threat=0.0)]), 0)
        self.assertEqual(b.intensity([Target('a', (0, 0), threat=0.08 * b.max_health)]), 1)
        self.assertEqual(b.intensity([Target('a', (0, 0), threat=0.2 * b.max_health)]), 2)
        b.actions['SideTackleL']['min_intensity'] = 2
        self.assertEqual(b.available('SideTackleL', 0, 1), (False, 'intensity'))

    def test_montage_timeline_has_damage_window(self):
        b = brain()
        run = b.start('SideTackleL', 10.0, Target('p1', (0, -750)))
        kinds = [(round(t - 10.0, 2), k) for t, k, _ in run.events]
        self.assertIn((1.52, 'damage_window_open'), kinds)
        self.assertEqual(sorted(k for _, k in kinds if k.startswith('damage_window')),
                         ['damage_window_close', 'damage_window_open'])
        open_ev = next(p for _, k, p in run.events if k == 'damage_window_open')
        self.assertEqual(open_ev['keys'], ['sidetackle'])
        self.assertEqual(open_ev['rows']['sidetackle']['elements'], {'TerraDamage': 175})
        self.assertAlmostEqual(run.end - 10.0, 5.1667, places=3)
        events = b.tick(11.6)
        self.assertEqual([k for _, k, _ in events], ['damage_window_open', 'part_collision_ignore_pawn'])
        self.assertTrue(b.busy)
        self.assertEqual(b.tick(15.2)[-1][1], 'action_end')
        self.assertFalse(b.busy)

    def test_alpha_whirling_fortress_needs_head_armour_part(self):
        b = brain('alpha')
        self.assertEqual(b.available('WhirlingFortress', 0, 2), (False, 'part HeadArmour'))
        b.part_state['HeadArmour'] = 1
        self.assertTrue(b.available('WhirlingFortress', 0, 2)[0])

    def test_multi_behemoth_cooldown_delta(self):
        solo, multi = brain(), brain()
        multi.multiple_behemoths = True
        solo.start('StoneSpire', 0.0)
        multi.start('StoneSpire', 0.0)
        self.assertEqual(solo._cd['StoneSpire'].ready_at, 25.0)
        self.assertEqual(multi._cd['StoneSpire'].ready_at, 50.0)


if __name__ == '__main__':
    unittest.main()
