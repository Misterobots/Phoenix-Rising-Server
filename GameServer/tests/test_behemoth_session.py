"""RockDirector against a fake session: spawn timing, facing, attacks, montage entries, failure isolation (offline)."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))  # GameServer/, for mpgs
import math
import os
import unittest
from types import SimpleNamespace

from mpgs.behemoth import bp_schema
from mpgs.behemoth.brain import ROCK
from mpgs.behemoth.session import RockDirector
from mpgs.repl.schema import Schema

READY = os.path.exists(ROCK) and os.path.exists(bp_schema.ROCK_SCHEMA)


class FakeSession:
    def __init__(self, loc=(0.0, 0.0, 100.0), yaw=0.0):
        schema = Schema()
        self.world = SimpleNamespace(schema=schema, ctx=SimpleNamespace(schema=schema))
        self.entries, self.pos, self.chan_cls = [], 0, {}
        self.pawn_state = {'loc': list(loc), 'vel': [0.0, 0.0, 0.0], 'ctrl_rot': [0.0, yaw, 0.0]}
        self._g = 5000

    def next_live_guid(self):
        self._g += 2
        return self._g


def director(session, **env):
    base = {'MPGS_ROCK_DELAY': '5', 'MPGS_ROCK_DISTANCE': '1500'}
    base.update(env)
    return RockDirector(session, env=base)


@unittest.skipIf(not READY, 'Rock data not generated (tools.export_behemoth, tools.export_behemoth_schema)')
class DirectorTests(unittest.TestCase):
    def test_waits_for_player_and_delivery(self):
        s = FakeSession()
        s.pawn_state = None
        d = director(s)
        d.step(1.0)
        self.assertEqual((d.state, s.entries), ('waiting', []))
        s.pawn_state = FakeSession().pawn_state
        s.entries = [object()]                                  # world still being delivered
        d.step(2.0)
        self.assertEqual(d.state, 'waiting')
        self.assertIsNone(d.t_ready)
        s.pos = 1
        d.step(3.0)
        self.assertEqual(d.t_ready, 8.0)                        # delay counted from when delivery finished
        d.step(7.9)
        self.assertEqual(d.state, 'waiting')

    def test_spawn_in_front_of_player_and_faces_back(self):
        s = FakeSession(loc=(100.0, 200.0, 50.0), yaw=90.0)
        d = director(s)
        d.step(0.0)
        d.step(5.0)
        self.assertEqual(d.state, 'active')
        spawn_entry = s.entries[0]
        o = spawn_entry.parsed.open
        self.assertAlmostEqual(o['loc'][0], 100.0, delta=1.0)
        self.assertAlmostEqual(o['loc'][1], 1700.0, delta=1.0)
        self.assertAlmostEqual(o['rot'][1] % 360, 270.0, delta=1.0)        # looking back at the player
        self.assertEqual(s.chan_cls[d.ch], 'sq_rock_normal_bp_C')
        self.assertEqual(d.sent['spawn'], 1)

    def test_fixed_spawn_point(self):
        s = FakeSession()
        d = director(s, MPGS_ROCK_AT='10,20,30')
        d.step(0.0)
        d.step(5.0)
        self.assertEqual(d.loc, [10.0, 20.0, 30.0])

    def _spawned(self, **kw):
        s = FakeSession(**kw)
        d = director(s)
        d.step(0.0)
        d.step(5.0)
        return s, d

    def test_attacks_when_a_player_is_in_range(self):
        s, d = self._spawned()
        n0 = len(s.entries)
        d.step(5.25)
        self.assertGreater(d.actions_started, 0)
        self.assertEqual(d.sent['montage'], d.actions_started)
        names = [fn[1] for e in s.entries[:len(s.entries) - n0 + 1] for b in e.parsed.blocks for fn in (b.fields or [])]
        self.assertIn('MulticastPlayMontage', names)

    def test_no_attack_when_out_of_range(self):
        s, d = self._spawned()
        s.pawn_state['loc'] = [60000.0, 60000.0, 100.0]
        d.step(5.25)
        self.assertEqual(d.actions_started, 0)

    def test_one_attack_at_a_time_and_events_flow(self):
        s, d = self._spawned()
        d.step(5.25)
        started = d.actions_started
        d.step(5.5)
        self.assertEqual(d.actions_started, started)             # still playing its montage
        run = d.brain.run
        self.assertIsNotNone(run)
        d.step(run.end + 0.1)
        self.assertTrue(d.brain.busy is False or d.actions_started > started)

    def test_turns_to_face_a_moving_player(self):
        s, d = self._spawned()
        d.step(5.25)
        run = d.brain.run
        s.pawn_state['loc'] = [d.loc[0], d.loc[1] + 800.0, 100.0]  # player moved to the Rock's right side during the attack
        moves0 = d.sent['move']
        d.step(run.end + 0.1)                                    # attack ends: it turns before choosing the next one
        self.assertGreater(d.sent['move'], moves0)
        self.assertAlmostEqual(d.yaw, 90.0, delta=1.0)

    def test_error_disables_instead_of_raising(self):
        s, d = self._spawned()
        s.pawn_state = {}                                        # malformed state -> KeyError inside step
        d.step(6.0)
        self.assertEqual(d.state, 'failed')
        self.assertFalse(d.alive)
        d.step(7.0)                                              # stays quiet

    def test_close_entry_queued(self):
        s, d = self._spawned()
        d.close()
        self.assertTrue(s.entries[0].close if hasattr(s.entries[0], 'close') else False)
        self.assertEqual(d.state, 'closed')



@unittest.skipIf(not READY, 'Rock data not generated')
class StageTests(unittest.TestCase):
    def _run(self, stage):
        s = FakeSession()
        d = director(s, MPGS_ROCK_STAGE=stage)
        d.step(0.0)
        d.step(5.0)
        for t in (5.25, 8.0, 9.0):
            d.step(t)
        return s, d

    def test_spawn_stage_sends_only_the_open_bunch(self):
        s, d = self._run('spawn')
        self.assertEqual(len(s.entries), 1)
        self.assertEqual((d.sent['move'], d.sent['montage']), (0, 0))

    def test_move_stage_sends_no_montage(self):
        s, d = self._run('move')
        self.assertEqual(d.sent['montage'], 0)
        self.assertGreater(d.sent['move'], 0)

    def test_montage_modes(self):
        for mode, want in (('rpc', 1), ('prop', 1), ('both', 2)):
            s = FakeSession()
            d = director(s, MPGS_ROCK_MONTAGE_MODE=mode)
            d.step(0.0)
            d.step(5.0)
            d.step(5.25)
            self.assertEqual(d.sent['montage'], want, mode)

    def test_attack_delay_lets_movement_run_first(self):
        s = FakeSession()
        d = director(s, MPGS_ROCK_ATTACK_DELAY='20')
        d.step(0.0)
        d.step(5.0)
        d.step(5.25)
        d.step(7.5)
        self.assertEqual(d.sent['montage'], 0)
        self.assertGreater(d.sent['move'], 0)
        d.step(25.5)
        self.assertEqual(d.sent['montage'], 1)

    def test_move_every_zero_sends_no_repeated_refresh(self):
        s = FakeSession()
        d = director(s, MPGS_ROCK_MOVE_EVERY='0', MPGS_ROCK_STAGE='move')
        d.step(0.0)
        d.step(5.0)
        for t in (7.0, 9.0, 11.0, 13.0):
            d.step(t)
        self.assertEqual(d.sent['move'], 0)                        # player never moved: nothing to refresh

    def test_action_allowlist_limits_the_attacks(self):
        s = FakeSession()
        d = director(s, MPGS_ROCK_ACTIONS='StoneSpire,FrontTackle')
        d.step(0.0)
        d.step(5.0)
        self.assertEqual(sorted(d.brain.actions), ['FrontTackle', 'StoneSpire'])
        d.step(5.25)
        self.assertIn(d.brain.run.name if hasattr(d.brain.run, 'name') else d.brain.run.action, ('StoneSpire', 'FrontTackle'))

    def test_default_refresh_still_repeats(self):
        s = FakeSession()
        d = director(s, MPGS_ROCK_STAGE='move')
        d.step(0.0)
        d.step(5.0)
        for t in (7.5, 10.0):
            d.step(t)
        self.assertGreaterEqual(d.sent['move'], 2)

    def test_attack_stage_sends_montage(self):
        s, d = self._run('attack')
        self.assertGreater(d.sent['montage'], 0)


if __name__ == '__main__':
    unittest.main()
