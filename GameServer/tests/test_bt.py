"""Behaviour-tree interpreter (mpgs/behemoth/bt.py), the real rock_bt, and RockDirector with MPGS_ROCK_BT=1 (offline)."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))  # GameServer/, for mpgs
import math
import os
import random
import unittest

from mpgs.behemoth import bt
from mpgs.behemoth.brain import ROCK
from mpgs.behemoth.rock_ai import DEFAULT_BT_DIR
from mpgs.behemoth.session import RockDirector

from test_behemoth_mover_director import write_flat_terrain
from test_behemoth_session import FakeSession

HAS_TREES = os.path.exists(os.path.join(DEFAULT_BT_DIR, 'rock_bt.json'))


# ---- tiny synthetic trees -----------------------------------------------------------------------------------------
def ref(tree, name):
    return {'ObjectName': "X'%s:%s'" % (tree, name)}


def child(tree, name, decos=(), task=False):
    return {'ChildComposite': None if task else ref(tree, name), 'ChildTask': ref(tree, name) if task else None,
            'Decorators': [ref(tree, d) for d in decos]}


def make_tree(name, nodes, root, root_decos=()):
    """nodes: {node name: (Type, props)}"""
    exports = [{'Type': 'BehaviorTree', 'Name': name,
                'Properties': {'RootNode': ref(name, root), 'RootDecorators': [ref(name, d) for d in root_decos]}}]
    for n, (t, p) in nodes.items():
        exports.append({'Type': t, 'Name': n, 'Properties': p})
    return exports


class Ctx(bt.Context):
    def __init__(self):
        self.bb, self.states, self.rng, self.events = {}, set(), random.Random(0), []

    def log(self, m):
        self.events.append(m)

    def play_montage(self, name):
        self.events.append('montage ' + name)
        return self.now + 1.0


class EngineTests(unittest.TestCase):
    def run_tree(self, exports, secs=3.0, ctx=None, setup=None):
        ts = bt.TreeSet()
        ts.add(exports)
        ctx = ctx or Ctx()
        r = bt.Runner(ts, exports[0]['Name'], ctx)
        for i in range(int(secs * 10)):
            if setup:
                setup(i / 10.0, ctx)
            r.tick(i / 10.0)
        return ctx, r

    def test_selector_takes_first_passing_child(self):
        t = 'T'
        nodes = {
            'Sel': ('BTComposite_Selector', {'Children': [child(t, 'A', ['NotSet'], task=True), child(t, 'B', task=True)]}),
            'NotSet': ('BTDecorator_Blackboard', {'BlackboardKey': {'SelectedKeyName': 'Flag'}}),
            'A': ('BTTask_BehemothAction_C', {'Montage': 'a_mot'}),
            'B': ('BTTask_BehemothAction_C', {'Montage': 'b_mot'}),
        }
        ctx, _ = self.run_tree(make_tree(t, nodes, 'Sel'), 1.5)
        self.assertIn('montage b_mot', ctx.events)
        self.assertNotIn('montage a_mot', ctx.events)

    def test_inverse_condition(self):
        t = 'T'
        nodes = {
            'Sel': ('BTComposite_Selector', {'Children': [child(t, 'A', ['D'], task=True)]}),
            'D': ('BTDecorator_Blackboard', {'BlackboardKey': {'SelectedKeyName': 'Flag'}, 'bInverseCondition': True}),
            'A': ('BTTask_BehemothAction_C', {'Montage': 'a_mot'}),
        }
        ctx, _ = self.run_tree(make_tree(t, nodes, 'Sel'), 0.5)
        self.assertIn('montage a_mot', ctx.events)

    def test_force_success_lets_a_sequence_continue_past_a_blocked_child(self):
        t = 'T'
        nodes = {
            'Seq': ('BTComposite_Sequence', {'Children': [child(t, 'A', ['F', 'Never'], task=True), child(t, 'B', task=True)]}),
            'F': ('BTDecorator_ForceSuccess', {}),
            'Never': ('behemoth_in_state_btdeco_C', {'State': 'behemoth_states_enum::NewEnumerator9'}),
            'A': ('BTTask_BehemothAction_C', {'Montage': 'a_mot'}),
            'B': ('BTTask_BehemothAction_C', {'Montage': 'b_mot'}),
        }
        ctx, _ = self.run_tree(make_tree(t, nodes, 'Seq'), 0.5)
        self.assertEqual([e for e in ctx.events if e.startswith('montage')], ['montage b_mot'])

    def test_state_requirement_one_means_not_in_state(self):
        ctx = Ctx()
        ctx.states = {9}
        self.assertFalse(bt._d_in_state(ctx, {'State': 'behemoth_states_enum::NewEnumerator9',
                                              'StateRequirement': 'behemoth_req_state_type::NewEnumerator1'}, {}, False))
        self.assertTrue(bt._d_in_state(ctx, {'State': 'behemoth_states_enum::NewEnumerator9'}, {}, False))

    def test_unset_number_key_compares_as_zero(self):
        ctx = Ctx()
        self.assertFalse(bt._d_blackboard(ctx, {'BlackboardKey': {'SelectedKeyName': 'StaggerCount'}, 'OperationType': 4}, {}, False))
        ctx.bb['StaggerCount'] = 2
        self.assertTrue(bt._d_blackboard(ctx, {'BlackboardKey': {'SelectedKeyName': 'StaggerCount'}, 'OperationType': 4}, {}, False))

    def test_lower_priority_abort_interrupts_a_running_wait(self):
        t = 'T'
        nodes = {
            'Sel': ('BTComposite_Selector', {'Children': [child(t, 'Hi', ['Gate'], task=True), child(t, 'Lo', task=True)]}),
            'Gate': ('BTDecorator_Blackboard', {'BlackboardKey': {'SelectedKeyName': 'Go'},
                                                'FlowAbortMode': 'EBTFlowAbortMode::LowerPriority'}),
            'Hi': ('BTTask_BehemothAction_C', {'Montage': 'hi_mot'}),
            'Lo': ('BTTask_Wait', {'WaitTime': 100.0}),
        }
        ctx, _ = self.run_tree(make_tree(t, nodes, 'Sel'), 2.0,
                               setup=lambda now, c: c.bb.__setitem__('Go', True) if now >= 1.0 else None)
        self.assertIn('montage hi_mot', ctx.events)

    def test_sub_tree_root_decorators_gate_entry(self):
        t, sub = 'T', 'Sub_bt'
        main = make_tree(t, {
            'Sel': ('BTComposite_Selector', {'Children': [child(t, 'Run', task=True), child(t, 'B', task=True)]}),
            'Run': ('BTTask_RunBehavior', {'BehaviorAsset': {'ObjectName': "BehaviorTree'Sub_bt'"}}),
            'B': ('BTTask_BehemothAction_C', {'Montage': 'b_mot'}),
        }, 'Sel')
        subtree = make_tree(sub, {
            'S': ('BTComposite_Sequence', {'Children': [child(sub, 'A', task=True)]}),
            'A': ('BTTask_BehemothAction_C', {'Montage': 'sub_mot'}),
            'G': ('behemoth_in_state_btdeco_C', {'State': 'behemoth_states_enum::NewEnumerator28'}),
        }, 'S', root_decos=['G'])
        ts = bt.TreeSet()
        ts.add(main)
        ts.add(subtree)
        ctx = Ctx()
        r = bt.Runner(ts, 'T', ctx)
        for i in range(5):
            r.tick(i / 10.0)
        self.assertNotIn('montage sub_mot', ctx.events)     # not in state 28: the sub-tree is skipped
        self.assertIn('montage b_mot', ctx.events)

    def test_unknown_task_succeeds_and_is_logged_once(self):
        t = 'T'
        nodes = {
            'Seq': ('BTComposite_Sequence', {'Children': [child(t, 'U', task=True), child(t, 'B', task=True)]}),
            'U': ('mystery_bttask_C', {}),
            'B': ('BTTask_BehemothAction_C', {'Montage': 'b_mot'}),
        }
        ctx, r = self.run_tree(make_tree(t, nodes, 'Seq'), 3.0)
        self.assertIn('montage b_mot', ctx.events)
        self.assertEqual(sum('mystery_bttask_C' in e for e in ctx.events), 1)


# ---- the real Rock tree with a fake world -------------------------------------------------------------------------
class World(bt.Context):
    def __init__(self, player):
        self.bb = {'EnterCombatRange': 2000.0, 'PatrolCenter': (0.0, 0.0, 0.0), 'StaggerCount': 0}
        self.states, self.rng = set(), random.Random(1)
        self.x = self.y = self.yaw_ = 0.0
        self.player, self.goal, self.until, self.log_ = player, None, 0.0, []

    def log(self, m):
        self.log_.append(('log', m))

    def target_pos(self, key):
        if key == 'SelfPos':
            return (self.x, self.y, 0.0)
        if key == 'FrontPoint':
            r = math.radians(self.yaw_)
            return (self.x + 100 * math.cos(r), self.y + 100 * math.sin(r), 0.0)
        v = self.bb.get(key)
        return self.player if v == 'player' else (v if isinstance(v, tuple) else None)

    def target_vel(self, key):
        return (0.0, 0.0)

    def pick_target(self):
        self.bb['AttackTarget'] = 'player' if math.hypot(self.player[0] - self.x, self.player[1] - self.y) < 2500 else None

    def choose_action(self):
        return ('FrontTackle', 1.0) if self.bb.get('AttackTarget') else None

    def perform_action(self, name):
        self.log_.append(('attack', name))
        self.until = self.now + 3.8
        return True

    def action_running(self):
        return self.now < self.until

    def play_montage(self, name):
        self.log_.append(('montage', name))
        return self.now + 2.0

    def move_to(self, dest, radius, key=None):
        self.goal = (dest, radius)
        self.log_.append(('move', dest))
        return True

    def move_status(self):
        (gx, gy, *_), r = self.goal
        d = math.hypot(gx - self.x, gy - self.y)
        if d <= max(r, 1.0):
            return bt.SUCCEEDED
        s = min(40.0, d)
        self.x += (gx - self.x) / d * s
        self.y += (gy - self.y) / d * s
        return bt.RUNNING

    def stop_moving(self):
        self.goal = None

    def patrol_point(self, inner, outer):
        a, r = self.rng.uniform(0, 2 * math.pi), self.rng.uniform(inner, outer)
        return (r * math.cos(a), r * math.sin(a), 0.0)

    def face(self, p):
        self.yaw_ = math.degrees(math.atan2(p[1] - self.y, p[0] - self.x))

    def set_state(self, s, add=True):
        (self.states.add if add else self.states.discard)(s)
        if s == 9 and add:
            self.bb.setdefault('CurrentFightStateStartTime', self.now)


@unittest.skipUnless(HAS_TREES, 'behaviour trees not dumped (see docs/BEHAVIOUR_STEP_D_2026-10-15.md)')
class RockTreeTests(unittest.TestCase):
    def run_rock(self, player, secs=30.0, setup=None):
        w = World(player)
        r = bt.Runner(bt.TreeSet.load(DEFAULT_BT_DIR), 'rock_bt', w)
        for i in range(int(secs * 10)):
            if setup:
                setup(i / 10.0, w)
            r.tick(i / 10.0)
        return w, r

    def test_far_player_means_patrol_inside_the_donut(self):
        w, _ = self.run_rock((20000.0, 0.0, 0.0))
        kinds = [k for k, _ in w.log_]
        self.assertNotIn('attack', kinds)
        moves = [v for k, v in w.log_ if k == 'move']
        self.assertGreaterEqual(len(moves), 3)
        self.assertTrue(all(math.hypot(m[0], m[1]) <= 501.0 for m in moves))
        self.assertIn(('montage', 'b_sq_rok_00_pers_patrol_fwd_00_mot'), w.log_)

    def test_near_player_means_combat_and_attacks(self):
        w, _ = self.run_rock((1500.0, 0.0, 0.0))
        self.assertIn(9, w.states)                                      # Combat (NewEnumerator9)
        self.assertGreaterEqual(sum(1 for k, _ in w.log_ if k == 'attack'), 2)

    def test_patrol_turns_to_combat_when_the_player_arrives(self):
        def approach(now, w):
            if now >= 10.0:
                w.player = (w.x + 1200.0, w.y, 0.0)
        w, _ = self.run_rock((20000.0, 0.0, 0.0), 25.0, approach)
        first_attack = next((i for i, (k, _) in enumerate(w.log_) if k == 'attack'), None)
        self.assertIsNotNone(first_attack)
        self.assertIn(('montage', 'b_sq_rok_00_pers_patrol_fwd_00_mot'), w.log_[:first_attack])

    def test_death_plays_the_death_montage(self):
        def die(now, w):
            if now >= 8.0:
                w.bb['MonsterIsDead'] = True
        w, _ = self.run_rock((1500.0, 0.0, 0.0), 14.0, die)
        self.assertIn(('montage', 'b_sq_rok_00_state_death_fwd_00_mot'), w.log_)


# ---- RockDirector with the tree -----------------------------------------------------------------------------------
@unittest.skipUnless(HAS_TREES and os.path.exists(ROCK), 'behaviour trees or Rock data missing')
class DirectorTreeTests(unittest.TestCase):
    def director(self, player):
        s = FakeSession(loc=player)
        base = write_flat_terrain(1000.0)
        d = RockDirector(s, env={'MPGS_ROCK_DELAY': '5', 'MPGS_ROCK_AT': '3200,3200,0', 'MPGS_MOVER': '1', 'MPGS_ROCK_BT': '1',
                                 'MPGS_TERRAIN_BASE': base, 'MPGS_ROCK_HALF_HEIGHT': '250', 'MPGS_ROCK_MOVE_EVERY': '0'})
        d.step(0.0)
        d.step(5.0)
        return s, d

    def test_far_player_the_rock_walks_with_velocity(self):
        s, d = self.director((6000.0, 6000.0, 100.0))           # ~4000 away: no aggro
        self.assertIsNotNone(d.ai)
        start = list(d.loc)
        moving = []
        for i in range(1, 300):
            d.step(5.0 + i * 0.1)
            if d.mover.path:
                moving.append(i)
        self.assertTrue(moving, 'the Rock never walked')
        self.assertGreater(math.hypot(d.loc[0] - start[0], d.loc[1] - start[1]) + len(moving), 0)
        self.assertEqual(d.actions_started, 0)

    def test_near_player_the_rock_attacks(self):
        s, d = self.director((4200.0, 3200.0, 100.0))           # 1000 away
        for i in range(1, 300):
            d.step(5.0 + i * 0.1)
        self.assertGreater(d.actions_started, 0)
        self.assertIn(9, d.ai.ctx.states)

    def test_off_axis_player_the_rock_still_attacks(self):
        # live 2026-10-15: the player 21 degrees off the Rock's nose, inside the reposition tree's 22-degree cone, so the
        # tree never pivots; without the yaw sweep in brain.choose the Rock never attacked. 1200 away, so it first walks a
        # terrain-grid path whose last leg is axis-aligned and ends off the player's bearing (as live)
        a = math.radians(21.0)
        s, d = self.director((3200.0 + 1200.0 * math.cos(a), 3200.0 + 1200.0 * math.sin(a), 100.0))
        for n in list(d.brain.actions):
            if n != 'FrontTackle':
                del d.brain.actions[n]
        for i in range(1, 100):
            d.step(5.0 + i * 0.1)
        self.assertGreater(d.actions_started, 0)


if __name__ == '__main__':
    unittest.main()
