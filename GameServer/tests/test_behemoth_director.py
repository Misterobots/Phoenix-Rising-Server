"""ENGINE_PORT_PLAN.md step 4: the generic BehemothDirector drives the Embermane through its real behaviour tree (patrol, aggro,
attacks) and what it emits encodes onto the wire with the Embermane's own schema. Decorator/service semantics here were read from
the cooked Blueprint bytecode (kismet_pseudo.py); see mpgs/behemoth/bt.py."""
import json
import math
import os
import random
import sys
import unittest
from types import SimpleNamespace

SERVER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SERVER)

from mpgs.behemoth import bt, spawn  # noqa: E402
from mpgs.behemoth.director import BehemothDirector, Species, montage_package  # noqa: E402
from mpgs.behemoth.mover import montage_curve  # noqa: E402
from mpgs.repl import compose, model  # noqa: E402
from mpgs.repl.chan import Reassembler  # noqa: E402
from mpgs.repl.guid import flatten  # noqa: E402

ISLANDS = os.path.join(SERVER, 'data', '2.1.1', 'islands')
HUNT = 'Adventure_IslandA_D24'
with open(os.path.join(ISLANDS, HUNT + '.engine_data.json'), encoding='utf-8') as _f:
    MANIFEST = json.load(_f)
HAVE_DATA = all(os.path.exists(os.path.join(SERVER, p)) for p in MANIFEST['files'])
CLS = 'lerawr_beta_bp_C'
# Nodes the trace reaches that are deliberately left as "succeed at once": cosmetic only.
COSMETIC = {'audio_exit_flee_state_bttask_C'}


class Bb(bt.Context):
    def __init__(self, bb, classes=None, pos=None):
        self.bb, self.classes, self.pos = bb, classes or {}, pos or {}

    def action_class(self, name):
        return self.classes.get(name)

    def target_pos(self, key):
        return self.pos.get(key)

    def target_vel(self, key):
        return (0.0, 0.0)


class DecoratorSemanticsTests(unittest.TestCase):
    def test_check_chosen_action_with_unset_class_means_no_attack_chosen(self):
        deco = bt.DECORATORS['BTDeco_CheckChosenAction_C']
        none = Bb({'ChosenAttack': None})
        chosen = Bb({'ChosenAttack': 'Bite'}, {'Bite': 'lerawr_short_bite_atk_C'})
        self.assertTrue(deco(none, {}, {}, False))        # approach branch runs
        self.assertFalse(deco(chosen, {}, {}, False))     # inverted copy (bInverseCondition) lets PerformActionClass run
        want = {'ActionClass': {'ObjectName': "Class'lerawr_short_bite_atk_C'"}}
        self.assertTrue(deco(chosen, want, {}, False))

    def test_archon_distance_check_is_inside_max_and_outside_min(self):
        deco = bt.DECORATORS['archon_distance_check_btdeco_C']
        p = {'Origin': {'SelectedKeyName': 'SelfPos'}, 'Target': {'SelectedKeyName': 'AttackTarget'},
             'MaxDistance': {'BlackboardKeyName_36_X': 'EnterCombatRange'},
             'MinDistance': {'BlackboardKeyName_36_X': 'None', 'ConstantFloat_30_X': 500.0}}
        ctx = lambda d: Bb({'EnterCombatRange': 4000.0}, pos={'SelfPos': (0.0, 0.0, 0.0), 'AttackTarget': (d, 0.0, 0.0)})
        self.assertFalse(deco(ctx(300.0), p, {}, False))
        self.assertTrue(deco(ctx(3000.0), p, {}, False))
        self.assertFalse(deco(ctx(4500.0), p, {}, False))

    def test_distance_service_writes_the_target_distance_or_zero(self):
        ctx = Bb({'AttackTarget': 'player'}, pos={'SelfPos': (0.0, 0.0, 0.0), 'AttackTarget': (300.0, 400.0, 0.0)})
        p = {'Distance_key': {'SelectedKeyName': 'DistanceFromAttackTarget'}, 'Target_key': {'SelectedKeyName': 'AttackTarget'}}
        bt.SERVICES['distance_from_target_service_C'](ctx, p)
        self.assertAlmostEqual(ctx.bb['DistanceFromAttackTarget'], 500.0)
        ctx.bb['AttackTarget'] = None
        bt.SERVICES['distance_from_target_service_C'](ctx, p)
        self.assertEqual(ctx.bb['DistanceFromAttackTarget'], 0.0)


class RootMotionOriginTests(unittest.TestCase):
    """Live 2026-10-20 rubber banding: a root track that does not start at the origin must be sampled relative to its start."""

    def test_offset_root_track_starts_at_zero(self):
        from mpgs.behemoth.mover import RootCurve
        # 30 frames, root starts 900 cm forward and moves 300 more; start yaw 0
        c = RootCurve(30, [[0, 900.0, 0.0, 0.0], [30, 1200.0, 0.0, 0.0]], [[0, 0.0, 0.0, 0.0, 1.0], [30, 0.0, 0.0, 0.0, 1.0]])
        self.assertEqual(c.sample(0.0)[:3], (0.0, 0.0, 0.0))
        self.assertAlmostEqual(c.sample(c.duration)[0], 300.0)

    def test_start_rotation_is_removed_from_translation(self):
        from mpgs.behemoth.mover import RootCurve
        s = 2 ** -0.5                                   # start yawed +90: moving along world +Y is "forward" (+X) locally
        c = RootCurve(30, [[0, 0.0, 0.0, 0.0], [30, 0.0, 200.0, 0.0]], [[0, 0.0, 0.0, s, s], [30, 0.0, 0.0, s, s]])
        dx, dy, _, dyaw = c.sample(c.duration)
        self.assertAlmostEqual(dx, 200.0, places=3)
        self.assertAlmostEqual(dy, 0.0, places=3)
        self.assertAlmostEqual(dyaw, 0.0, places=3)


@unittest.skipUnless(HAVE_DATA, 'Embermane/island engine data not generated locally')
class PatrolExtrapolationTests(unittest.TestCase):
    def test_client_extrapolation_matches_the_server_while_patrolling(self):
        """Live 2026-10-20 idle rubber banding. A simulated proxy extrapolates the last update by its velocity. With the old
        nominal walk velocity and no stop update, that guess drifted up to 6.7 m at every patrol stop. Measured velocity plus
        a zero-velocity stop keep it on the server position."""
        from mpgs.world.terrain import Terrain
        import math
        with open(os.path.join(ISLANDS, HUNT + '.contract.json'), encoding='utf-8') as f:
            enc = json.load(f)['encounter']
        center = next(c for c in enc['centers'] if c['name'] == 'BP_PatrolCenterPoint_263')['location']
        terrain = Terrain.load(os.path.join(SERVER, 'world', 'maps', MANIFEST['terrain']))
        far = {'loc': [-18045.47, 16508.562, 703.9], 'vel': [0.0, 0.0, 0.0]}      # landing pad: never spotted
        d = BehemothDirector(Species.load(HUNT), terrain, center, 0.0, lambda: far, rng=random.Random(1))
        t, sent, worst, stops = 0.0, None, 0.0, 0
        while t < 40.0:
            for ev in d.tick(t):
                if ev[0] == 'move':
                    if sent and math.hypot(*sent[2][:2]) > 0 and math.hypot(*ev[3][:2]) == 0:
                        stops += 1
                    sent = (t, ev[1], ev[3])
            if sent:
                st, loc, vel = sent
                worst = max(worst, math.dist((loc[0] + vel[0] * (t - st), loc[1] + vel[1] * (t - st)), d.loc[:2]))
            t += 1.0 / 15.0
        self.assertGreater(stops, 3, 'patrol legs end with a zero-velocity update')
        self.assertLess(worst, 60.0)


@unittest.skipUnless(HAVE_DATA, 'Embermane/island engine data not generated locally')
class SmoothWalkTests(unittest.TestCase):
    """Live 2026-10-20 "the walk/path is very abrupt": full speed instantly, dead stops at every point, on-the-spot turns. The
    Embermane now walks with its authored CharMoveComp (CalcVelocity steering, ApplyVelocityBraking, 200 cm path braking,
    orient rotation to movement at 720 deg/s)."""

    def test_authored_movement_values(self):
        from mpgs.behemoth.mover import Movement
        m = Movement.from_props(Species.load(HUNT).move_props)
        self.assertEqual((m.max_accel, m.ground_friction, m.braking_friction, m.braking_decel, m.rotation_rate,
                          m.braking_distance), (4500.0, 8.0, 4.0, 1024.0, 720.0, 200.0))

    def test_patrol_has_no_dead_stops_or_snap_turns(self):
        from mpgs.world.terrain import Terrain
        import math
        with open(os.path.join(ISLANDS, HUNT + '.contract.json'), encoding='utf-8') as f:
            enc = json.load(f)['encounter']
        center = next(c for c in enc['centers'] if c['name'] == 'BP_PatrolCenterPoint_263')['location']
        terrain = Terrain.load(os.path.join(SERVER, 'world', 'maps', MANIFEST['terrain']))
        far = {'loc': [-18045.47, 16508.562, 703.9], 'vel': [0.0, 0.0, 0.0]}
        d = BehemothDirector(Species.load(HUNT), terrain, center, 0.0, lambda: far, rng=random.Random(1))
        dt, t, prev, dead, turn, moved = 1.0 / 15.0, 0.0, None, 0, 0.0, 0.0
        while t < 40.0:
            d.tick(t)
            speed = math.hypot(*d._tick_vel[:2])
            if prev:
                dead += prev[0] > 300.0 and speed < 20.0
                turn = max(turn, abs(((d.yaw - prev[1] + 180.0) % 360.0) - 180.0) / dt)
            moved += speed * dt
            prev, t = (speed, d.yaw), t + dt
        self.assertGreater(moved, 2000.0, 'it patrols')
        self.assertEqual(dead, 0)
        self.assertLessEqual(turn, 720.0 + 1.0)


@unittest.skipUnless(HAVE_DATA, 'Embermane/island engine data not generated locally')
class PawnSpacingTests(unittest.TestCase):
    """Live 2026-10-20: the Embermane walked into the player and stood inside their capsule (52-67 cm apart); the client's
    collision pushed it out against the server position. UE stops a MoveTo at 5 + goal radius + agent radius x 1.1 and
    CharacterMovement blocks root motion at capsule contact (100 + 50)."""

    def run_with_player(self, offset, seconds):
        from mpgs.world.terrain import Terrain
        import math
        with open(os.path.join(ISLANDS, HUNT + '.contract.json'), encoding='utf-8') as f:
            enc = json.load(f)['encounter']
        center = next(c for c in enc['centers'] if c['name'] == 'BP_PatrolCenterPoint_263')['location']
        terrain = Terrain.load(os.path.join(SERVER, 'world', 'maps', MANIFEST['terrain']))
        player = {'loc': [center[0] + offset[0], center[1] + offset[1], 134.0], 'vel': [0.0, 0.0, 0.0]}
        d = BehemothDirector(Species.load(HUNT), terrain, center, 0.0, lambda: player, rng=random.Random(1))
        t, closest = 0.0, 1e9
        while t < seconds:
            d.tick(t)
            if not d._ignore_pawn:
                closest = min(closest, math.dist(d.loc[:2], player['loc'][:2]))
            t += 1.0 / 15.0
        return d, closest

    def test_reach_radius_follows_ue(self):
        d, _ = self.run_with_player((2000.0, 0.0), 0.1)
        d.ctx.bb['AttackTarget'] = 'player'
        self.assertAlmostEqual(d.ctx.reach_radius(5.0, 'AttackTarget'), 5.0 + 50.0 + 100.0 * 1.1)
        self.assertAlmostEqual(d.ctx.reach_radius(5.0, 'MoveDestination'), 5.0 + 100.0 * 1.1)

    def test_never_inside_the_player_capsule(self):
        """Live positions from session 091521 (player stood ~2,100 cm away): approach, bites and lunges stay at contact or more."""
        _, closest = self.run_with_player((-806.0, 1941.0), 40.0)
        self.assertGreaterEqual(closest, 149.0)


@unittest.skipUnless(HAVE_DATA, 'Embermane/island engine data not generated locally')
class DamageReachTests(unittest.TestCase):
    """Live 2026-10-22: hits landed from far away; the hit test used the attack areas the Brain aims with (JumpReposition: 20 m
    square). A damage window now also needs the player within reach of its damaging bodies."""

    def director(self, player):
        from mpgs.world.terrain import Terrain
        with open(os.path.join(ISLANDS, HUNT + '.contract.json'), encoding='utf-8') as f:
            enc = json.load(f)['encounter']
        center = next(c for c in enc['centers'] if c['name'] == 'BP_PatrolCenterPoint_263')['location']
        terrain = Terrain.load(os.path.join(SERVER, 'world', 'maps', MANIFEST['terrain']))
        return BehemothDirector(Species.load(HUNT), terrain, center, 0.0, lambda: player, rng=random.Random(1)), center

    def test_body_reach_per_attack(self):
        d, _ = self.director({'loc': [0.0, 0.0, 0.0], 'vel': [0.0, 0.0, 0.0]})
        if not d.species.bodies:
            self.skipTest('no <variant>_bodies.json (tools.export_behemoth_bodies)')
        head = d._body_reach(['head_jnt'])
        self.assertTrue(350.0 < head < 550.0)
        self.assertGreater(d._body_reach(['tail_0%d_jnt' % i for i in range(9)]), head)     # the tail sweeps farther
        self.assertIsNone(d._body_reach(['no_such_jnt']))

    def test_jump_reposition_does_not_hit_from_ten_metres(self):
        player = {'loc': [0.0, 0.0, 134.0], 'vel': [0.0, 0.0, 0.0]}
        d, center = self.director(player)
        if not d.species.bodies:
            self.skipTest('no <variant>_bodies.json (tools.export_behemoth_bodies)')
        d._windows[('JumpReposition', 'SideGore')] = {'row': {}}
        d._window_reach[('JumpReposition', 'SideGore')] = d._body_reach(['head_jnt'])

        def hits_at(dist):
            player['loc'][:2] = [d.brain.pos[0] + dist * math.cos(d.brain.yaw), d.brain.pos[1] + dist * math.sin(d.brain.yaw)]
            self.assertTrue(d.brain.in_area('JumpReposition', tuple(player['loc'][:2])))   # inside the aiming area
            d._events, d._hit_in_window = [], set()
            d._check_hits()
            return [e for e in d._events if e[0] == 'hit']
        self.assertFalse(hits_at(1000.0))                                  # out of the head's reach
        self.assertTrue(hits_at(250.0))


@unittest.skipUnless(HAVE_DATA, 'Embermane/island engine data not generated locally')
class JumpOverTurnTests(unittest.TestCase):
    NAME = 'b_aq_lerawr_00_loco_jumpoverturn_180_right_00_Montage'

    def test_two_segment_montage_has_no_teleport(self):
        """jump-over-turn (1.63 s) + short pounce (root track starting 911.8 cm forward): continuous at the seam and the turn
        happens during the jump, not at the end."""
        c = montage_curve(self.NAME)
        t, prev, worst = 0.0, None, 0.0
        while t <= c.duration:
            x, y, _, _ = c.sample(t)
            if prev:
                worst = max(worst, ((x - prev[0]) ** 2 + (y - prev[1]) ** 2) ** 0.5)
            prev, t = (x, y), t + 1.0 / 30.0
        self.assertLess(worst, 200.0)                   # the old seam jumped ~900 cm in one frame; the pounce lunge peaks ~124
        self.assertGreater(abs(c.sample(1.2)[3]), 150.0)   # turned by mid-jump
        self.assertLess(((c.sample(c.duration)[0]) ** 2 + c.sample(c.duration)[1] ** 2) ** 0.5, 300.0)


@unittest.skipUnless(HAVE_DATA, 'Embermane/island engine data not generated locally')
class EncounterPathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tools.trace_behemoth_bt import trace
        cls.r = trace(HUNT, seconds=120.0, seed=1)

    def test_patrols_then_aggroes_into_combat(self):
        self.assertIn('shared_patrol_bt', self.r['subtrees_entered'])
        self.assertIn(9, self.r['states_seen'])                      # behemoth_states_enum Combat
        self.assertEqual(self.r['events']['target'], 1)

    def test_attacks_with_root_motion(self):
        self.assertGreaterEqual(self.r['attacks'], 5)
        self.assertTrue(all(has_curve for _, _, has_curve in self.r['montages']))

    def test_only_cosmetic_nodes_are_unhandled_on_the_path(self):
        self.assertEqual({x['type'] for x in self.r['unhandled']} - COSMETIC, set())


class Ctx:
    """The world's name table for decoding, filled from the export headers of what was composed (as in test_behemoth_spawn)."""

    def __init__(self, schema):
        self.schema, self._names = schema, {}

    def learn(self, infos):
        for root in infos:
            for i in flatten(root):
                self._names[i.guid] = (i.outer, i.name)

    def known(self):
        return self._names

    def names(self, g):
        return self._names[g]

    def comp_class(self, actor_cls, g):
        n = self._names.get(g, (None, None))[1]
        return self.schema.member_class(actor_cls, n) if n else None


@unittest.skipUnless(HAVE_DATA, 'Embermane/island engine data not generated locally')
class EmbermaneWireTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from mpgs import island_world
        from mpgs.genworld import GenWorld
        from mpgs.world.terrain import Terrain
        with open(os.path.join(ISLANDS, HUNT + '.contract.json'), encoding='utf-8') as f:
            cls.contract = json.load(f)
        world = island_world.build_world(GenWorld.from_template(os.path.join(SERVER, 'data', '2.1.1', 'ramsgate_world.json')),
                                         cls.contract)
        cls.schema = world.schema
        cls.terrain = Terrain.load(os.path.join(SERVER, 'world', 'maps', MANIFEST['terrain']))

    def setUp(self):
        self.n = [5000]
        self.guids = spawn.GuidSource(self._next)
        self.ctx = Ctx(self.schema)

    def _next(self):
        self.n[0] += 2
        return self.n[0]

    def roundtrip(self, entry):
        w = model.encode(entry.parsed, self.ctx, entry.cls)
        ra, full = Reassembler(), None
        for b in compose.compose(entry.ch, w.n, w.nbits, entry.exports, open=entry.open, close=entry.close,
                                 reliable=entry.reliable, must_map=entry.must_map):
            full = ra.feed(b) or full
        self.assertIsNotNone(full)
        self.ctx.learn(full.exports)
        parsed, _ = model.parse(SimpleNamespace(bits=full.bits, nbits=full.nbits, must_map=full.must_map, open=full.open,
                                                exports=full.exports), self.ctx, entry.cls)
        return parsed

    def first_attack(self):
        """Run the director against a player standing in front of it until it starts an attack."""
        enc = self.contract['encounter']
        center = next(c for c in enc['centers'] if c['name'] == 'BP_PatrolCenterPoint_263')
        loc = center['location']
        player = {'loc': [loc[0] + 300.0, loc[1], loc[2]], 'vel': [0.0, 0.0, 0.0]}
        d = BehemothDirector(Species.load(HUNT), self.terrain, loc, 0.0, lambda: player, rng=random.Random(3))
        t, events = 0.0, []
        while t < 60.0:
            evs = d.tick(t)
            events += evs
            m = next((e for e in evs if e[0] == 'montage'), None)
            if m:
                return d, m, events, t
            t += 1.0 / 15.0
        self.fail('no attack within 60 s')

    def test_director_spawns_on_the_landscape_floor(self):
        d, _, _, _ = self.first_attack()
        g = self.terrain.ground_z(d.loc[0], d.loc[1])
        self.assertAlmostEqual(d.loc[2] - g, self.contract['encounter']['capsuleOffset'], places=3)

    def test_attack_montage_and_root_motion_reach_the_wire(self):
        d, (_, name, rate, t0), events, _ = self.first_attack()
        self.assertIsNotNone(montage_curve(name))
        package = montage_package(name)
        self.assertTrue(package.startswith('/Game/Monsters/aq/lerawr/') or package.startswith('/Game/Monsters/aq/'))
        actor, ids = spawn.spawn_entry(self.schema, self.guids, ch=950, cls_name=CLS,
                                       package='/Game/Monsters/aq/lerawr/blueprints/lerawr_beta_bp', loc=tuple(d.loc),
                                       rot=(0.0, d.yaw, 0.0),
                                       components=[('NetworkedMontage', 'NetworkedMontageComponent', {'bReplicates': 1})])
        self.roundtrip(actor)
        cache = {}
        play = spawn.montage_play_entry(self.schema, self.guids, ch=950, actor_cls=CLS, montage_guid_cache=cache,
                                        component_guid=ids['NetworkedMontage'], component_cls='NetworkedMontageComponent',
                                        montage_package=package, montage_name=name, play_rate=rate)
        blk = self.roundtrip(play).blocks[0]
        rpc = next(f for f in blk.fields if f[1] == 'MulticastPlayMontage')
        (montage_guid, _), = cache.values()                         # (montage guid, package guid), allocated once per montage
        self.assertEqual(rpc[3]['NewAnimMontage'], ('obj', montage_guid))
        # The last transform sent, or the spawn transform when it attacked without moving (no 'move' event was due).
        mv = next((e for e in reversed(events) if e[0] == 'move'), ('move', list(d.loc), d.yaw, [0.0, 0.0, 0.0]))
        rm = spawn.root_motion_values(montage_guid, 0.0, mv[1], mv[2], vel=mv[3])
        upd = spawn.movement_entry(950, CLS, loc=mv[1], yaw=mv[2], vel=mv[3], server_time=t0, root_motion=rm)
        v = self.roundtrip(upd).blocks[0].values
        self.assertEqual(v['RepRootMotion.bIsActive'], 1)
        self.assertEqual(v['RepRootMotion.AnimMontage'], ('obj', montage_guid))
        self.assertAlmostEqual(v['ReplicatedMovement']['loc'][0], mv[1][0], delta=0.06)   # RoundOneDecimal (x10)
        self.assertAlmostEqual(v['ReplicatedMovement']['loc'][2], mv[1][2], delta=0.06)
        self.assertAlmostEqual(v['ReplicatedServerLastTransformUpdateTimeStamp'], t0, places=3)


if __name__ == '__main__':
    unittest.main()
