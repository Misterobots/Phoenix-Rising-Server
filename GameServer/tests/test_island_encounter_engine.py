"""ENGINE_PORT_PLAN.md step 5: the island encounter with MPGS_BEHEMOTH_ENGINE=1. The behemoth director decides and moves; the
encounter adapter turns its events into encounter-channel bunches. Spawn, reveal, damage and rewards are shared with the scripted
path (test_island_encounter.py, flag 0). Skipped where the local engine game data has not been generated."""
import json
import math
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mpgs import island_encounter, island_world  # noqa: E402
from mpgs.genworld import GenWorld  # noqa: E402
from mpgs.repl import model  # noqa: E402
from mpgs.repl.chan import Reassembler  # noqa: E402

MANIFEST = json.loads((ROOT / 'data/2.1.1/islands/Adventure_IslandA_D24.engine_data.json').read_text())
HAVE_DATA = all((ROOT / p).exists() for p in MANIFEST['files'])
CLS = 'lerawr_beta_bp_C'
LANDING = [-18045.47, 16508.562, 703.9]
_engine_flag = patch.dict(os.environ, {island_encounter.ENGINE_FLAG: '1'})


def setUpModule():
    _engine_flag.start()


def tearDownModule():
    _engine_flag.stop()


@unittest.skipUnless(HAVE_DATA, 'Embermane/island engine data not generated locally (see the engine_data manifest)')
class EngineEncounterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = json.loads((ROOT / 'data/2.1.1/islands/Adventure_IslandA_D24.contract.json').read_text())
        cls.world = island_world.build_world(GenWorld.from_template(ROOT / 'data/2.1.1/ramsgate_world.json'), cls.contract)

    def session(self):
        with patch.dict(os.environ, {k: '0' for k in ('MPGS_INITIAL_DATA', 'MPGS_PLAYER_DATA', 'MPGS_PROGRESSION',
                                                      'MPGS_JOURNEY_PERSIST', 'MPGS_QUESTS', 'MPGS_RAMSGATE_ATMOSPHERE',
                                                      'MPGS_ENCOUNTER_REWARDS')}):
            s = self.world.new_session(dict(self.world.old_ids))
        s.due(1000000)
        s._arrival_finished = True
        s.entries[s.pos:s.pos] = island_encounter.start(s)
        s.due(1000000)                                   # the spawn has been sent
        return s

    def decode(self, session, bunches):
        """One step's bunches (a large update spans several) -> the values of each complete update."""
        ra, out = Reassembler(), []
        for b in bunches:
            full = ra.feed(b)
            if full is not None:
                out.append(model.parse(full, session.ctx, CLS)[0].blocks[0].values)
        return out

    def run_until(self, session, until, start=0.0, player=None, stop=None):
        """Step the encounter at 15 Hz with the player at `player` (callable t -> loc); deliver queued entries every tick."""
        enc, t, out = session._encounter, start, []
        while t < until:
            if player is not None:
                session.pawn_state = {'loc': list(player(t)), 'rot': [0.0, 0.0, 0.0], 'vel': [0.0, 0.0, 0.0], 'mode': 1}
            before = len(session.entries)
            bunches = island_encounter.step_movement(session, enc['spawn_mono'] + t)
            queued = session.entries[session.pos:]
            out.append((t, bunches, list(queued)))
            session.due(1000000)
            if stop and stop(out[-1]):
                break
            t += 1.0 / 15.0
        return out

    def test_spawn_stands_on_the_landscape_and_walks(self):
        s = self.session()
        enc = s._encounter
        self.assertIsNotNone(enc['director'])
        vals = next(e for e in s.entries if e.open and e.cls == CLS).parsed.blocks[0].values
        self.assertEqual(vals['ReplicatedMovementMode'], 1)
        self.assertNotIn('DisableIkinema', vals)
        species, terrain, _ = island_encounter._engine_resources(s.world)
        ground = terrain.ground_z(enc['loc'][0], enc['loc'][1])
        self.assertAlmostEqual(enc['loc'][2] - ground, self.contract['encounter']['capsuleOffset'], places=3)
        self.assertLess(enc['loc'][2], 167.3)           # no longer vent Z + capsule offset (~43 cm above the floor)

    def test_unspotted_while_the_player_is_at_the_landing_pad(self):
        s = self.session()
        steps = self.run_until(s, 20.0, player=lambda t: LANDING)
        self.assertFalse(s._encounter['spotted'])
        moves = [v for _, bunches, _ in steps for v in self.decode(s, bunches)]
        self.assertTrue(moves, 'the director patrols')
        v = moves[-1]
        self.assertEqual(v['ReplicatedMovementMode'], 1)
        self.assertIn('ReplicatedServerLastTransformUpdateTimeStamp', v)

    def test_tree_aggro_reveals_then_attacks_with_montage_and_root_motion(self):
        s = self.session()
        enc = s._encounter
        center = enc['loc']
        near = [center[0] + 600.0, center[1], center[2]]
        steps = self.run_until(s, 45.0, player=lambda t: near,
                               stop=lambda step: any(e.cls == CLS and any(b.guid == enc['montage_comp_guid']
                                                                          for b in e.parsed.blocks) for e in step[2]))
        self.assertTrue(enc['spotted'], 'the tree acquired the player -> compass reveal')
        reveal_t = next(t for t, _, q in steps if any(e.cls != CLS for e in q))
        montage_steps = [(t, q) for t, _, q in steps
                         if any(b.guid == enc['montage_comp_guid'] for e in q for b in e.parsed.blocks)]
        self.assertTrue(montage_steps, 'an attack montage was sent')
        t_m, queued = montage_steps[0]
        self.assertGreaterEqual(t_m, reveal_t)
        play = next(e for e in queued if any(b.guid == enc['montage_comp_guid'] for b in e.parsed.blocks))
        self.assertTrue(play.reliable)
        self.assertEqual(play.exports[0].guid, enc['montage_comp_guid'])         # component export on first use
        self.assertEqual(play.exports[0].name, 'NetworkedMontage')
        rpc = next(f for f in play.parsed.blocks[0].fields if f[1] == 'MulticastPlayMontage')
        montage_guid = rpc[3]['NewAnimMontage'][1]
        bits = model.encode(play.parsed, s.ctx, CLS)
        self.assertGreater(bits.nbits, 0)
        # Following movement carries RepRootMotion for that montage while its root motion plays.
        after = self.run_until(s, t_m + 1.5, start=t_m + 1.0 / 15.0, player=lambda t: near)
        rm = [v for _, bunches, _ in after for v in self.decode(s, bunches)]
        active = [v for v in rm if v.get('RepRootMotion.bIsActive') == 1]
        self.assertTrue(active, 'root-motion movement updates were sent')
        self.assertEqual(active[0]['RepRootMotion.AnimMontage'], ('obj', montage_guid))

    def test_hits_count_for_the_tree_and_death_stops_the_director(self):
        s = self.session()
        enc = s._encounter
        self.assertTrue(island_encounter.handle_damage(s, 4, None, {'Damage': 1000.0}))
        self.assertEqual(enc['director'].health.hits, 1)
        s.due(1000000)
        with patch.dict(os.environ, {'MPGS_ENCOUNTER_REWARDS': '0'}):
            self.assertTrue(island_encounter.handle_damage(s, 4, None, {'Damage': 20000.0}))
        self.assertTrue(enc['director'].dead)
        self.assertTrue(enc['rewards_granted'])
        s.due(1000000)
        self.assertEqual(island_encounter.step_movement(s, enc['spawn_mono'] + 5.0), [])


class FlagTests(unittest.TestCase):
    def test_flag_values(self):
        self.assertTrue(island_encounter.engine_enabled({'MPGS_BEHEMOTH_ENGINE': '1'}))
        self.assertFalse(island_encounter.engine_enabled({'MPGS_BEHEMOTH_ENGINE': '0'}))
        self.assertFalse(island_encounter.engine_enabled({}))


if __name__ == '__main__':
    unittest.main()
