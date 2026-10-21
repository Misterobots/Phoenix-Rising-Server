"""ENGINE_PORT_PLAN.md step 5: the island encounter with MPGS_BEHEMOTH_ENGINE=1. The behemoth director decides and moves; the
encounter adapter turns its events into encounter-channel bunches. Spawn, reveal, damage and rewards are shared with the scripted
path (test_island_encounter.py, flag 0). Skipped where the local engine game data has not been generated."""
import json
import math
import os
import time
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
_engine_flag = patch.dict(os.environ, {island_encounter.ENGINE_FLAG: '1', island_encounter.ASC_FLAG: '1'})


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


@unittest.skipUnless(HAVE_DATA, 'Embermane/island engine data not generated locally (see the engine_data manifest)')
class HealthAndHitTests(EngineEncounterTests):
    """Step 6: the behemoth's ability system and health set ride in the spawn bunch; hits update CurrentHealth; damage windows
    send ClientTryHit and only an accepted hit reduces the player's health."""

    # the inherited path tests run once, in EngineEncounterTests
    test_spawn_stands_on_the_landscape_and_walks = None
    test_unspotted_while_the_player_is_at_the_landing_pad = None
    test_tree_aggro_reveals_then_attacks_with_montage_and_root_motion = None
    test_hits_count_for_the_tree_and_death_stops_the_director = None

    def roundtrip(self, session, entry):
        from mpgs.repl import compose
        bits = model.encode(entry.parsed, session.ctx, entry.cls)
        ra, full = Reassembler(), None
        for part in compose.compose(entry.ch, bits.n, bits.nbits, entry.exports, open=entry.open, must_map=entry.must_map,
                                    reliable=entry.reliable):
            full = ra.feed(part) or full
        decoded, cls = model.parse(full, session.ctx, entry.cls)
        again = model.encode(decoded, session.ctx, cls)
        self.assertEqual((bits.n, bits.nbits), (again.n, again.nbits), 'bit-exact re-encode')
        return decoded

    def test_spawn_carries_ability_system_and_health_set(self):
        s = self.session()
        enc = s._encounter
        actor = next(e for e in s.entries if e.open and e.cls == CLS)
        decoded = self.roundtrip(s, actor)
        by_cls = {b.cls_name: b for b in decoded.blocks}
        hs = by_cls['ArchonHealthAttributeSet']
        self.assertFalse(hs.stable)
        self.assertEqual(hs.cls, island_encounter.HEALTH_SET_CLASS_GUID)
        self.assertEqual(hs.values['CurrentHealth'], 18900.0)
        self.assertEqual(hs.values['MaxHealth'], 18900.0)
        self.assertEqual(by_cls['ArchonDamageAttributeSet'].cls, island_encounter.DAMAGE_SET_CLASS_GUID)
        asc = by_cls['ArchonAbilitySystemComponent']
        self.assertEqual(asc.values['OwnerActor'], ('obj', enc['guid']))
        spawned = [e['SpawnedAttributes[]'] for _, e in sorted(asc.values['SpawnedAttributes']['elems'].items())]
        self.assertEqual(spawned, [('obj', hs.guid), ('obj', by_cls['ArchonDamageAttributeSet'].guid)])
        self.assertIn(('ArchonAbilitySystem', enc['guid']),
                      [(x.name, x.outer_info.guid if x.outer_info else None) for x in actor.exports])

    def test_player_hit_updates_the_health_set(self):
        s = self.session()
        self.assertTrue(island_encounter.handle_damage(s, 4, None, {'Damage': 1000.0}))
        update = next(e for e in s.entries[s.pos:]
                      if any(b.cls_name == 'ArchonHealthAttributeSet' for b in e.parsed.blocks))
        blk = self.roundtrip(s, update).blocks[0]
        self.assertEqual(blk.guid, s._encounter['health_set'].guid)
        self.assertEqual(blk.values['CurrentHealth'], 17900.0)

    def attack_until_hit(self, s):
        enc = s._encounter
        near = [enc['loc'][0] + 300.0, enc['loc'][1], enc['loc'][2]]
        hittable = lambda step: enc.get('hittable_guid') is not None and any(
            b.guid == enc['hittable_guid'] for e in step[2] for b in e.parsed.blocks)
        steps = self.run_until(s, 60.0, player=lambda t: near, stop=hittable)
        tries = [e for _, _, q in steps for e in q if any(b.guid == enc.get('hittable_guid') for b in e.parsed.blocks)]
        self.assertTrue(tries, 'a damage window caught the player within 60 s')
        return tries[0]

    def test_damage_window_sends_client_try_hit(self):
        s = self.session()
        enc = s._encounter
        entry = self.attack_until_hit(s)
        self.assertEqual(entry.exports[0].name, 'player_hittable_bpc')        # exported by name on first use
        blk = self.roundtrip(s, entry).blocks[0]
        name, params = next((f[1], f[3]) for f in blk.fields if f[2] == 'rpc')
        self.assertEqual(name, 'ClientTryHit')
        self.assertEqual(params['Damager'], ('obj', enc['guid']))
        self.assertGreater(params['Params_0.DamageRow.PhysicalDamage'] + params.get('Params_0.DamageRow.FlameDamage', 0), 0)
        self.assertEqual(len(enc['pending_hits']), 1)

    def test_stagger_probe_targets_the_joined_hit_reaction_component(self):
        """With each ClientTryHit: ClientStartStaggerAction on hit_reaction_bpc, the replicated component the join already maps."""
        from mpgs.repl.guid import flatten
        with patch.dict(os.environ, {island_encounter.STAGGER_PROBE_FLAG: '1'}):
            s = self.session()
            enc = s._encounter
            self.attack_until_hit(s)
        pawn = next(e for e in s.entries if e.open and e.cls == 'BP_PlayerCharacter_C')
        joined = next(i.guid for r in pawn.exports for i in flatten(r) if i.name == 'hit_reaction_bpc')
        stagger = [e for e in s.entries if any(f[1] == 'ClientStartStaggerAction' for b in e.parsed.blocks for f in (b.fields or []))]
        self.assertTrue(stagger)
        blk = self.roundtrip(s, stagger[0]).blocks[0]
        self.assertEqual(blk.guid, joined)
        self.assertEqual(stagger[0].exports, [])                       # no new export: the client knows it from the join
        self.assertGreater(next(f[3] for f in blk.fields if f[1] == 'ClientStartStaggerAction')['DamageMagnitude'], 0.0)

    def test_stagger_probe_is_off_by_default(self):
        with patch.dict(os.environ, {}):
            s = self.session()
            self.attack_until_hit(s)
            self.assertFalse(any(f[1] == 'ClientStartStaggerAction' for e in s.entries for b in e.parsed.blocks
                                 for f in (b.fields or [])))

    def test_hit_plays_a_flinch_montage_on_the_player(self):
        from mpgs.repl.guid import flatten
        s = self.session()
        blk = self.attack_until_hit(s).parsed.blocks[0]
        pawn = next(e for e in s.entries if e.open and e.cls == 'BP_PlayerCharacter_C')
        self.assertFalse([e for e in s.entries if e.ch == pawn.ch and any(f[1] == 'MulticastPlayMontage'
                          for b in e.parsed.blocks for f in (b.fields or []))])       # nothing before the client answers
        params = next(f[3] for f in blk.fields if f[1] == 'ClientTryHit')
        island_encounter.handle_hit_reply(s, 'ServerAcceptHit', {'HitID.' + k: params['HitID.' + k] for k in 'ABCD'})
        comp = next(i.guid for r in pawn.exports for i in flatten(r) if i.name == 'NetworkedMontage')
        plays = [e for e in s.entries if e.ch == pawn.ch and any(f[1] == 'MulticastPlayMontage' for b in e.parsed.blocks
                                                                 for f in (b.fields or []))]
        self.assertTrue(plays)
        self.assertEqual(plays[0].parsed.blocks[0].guid, comp)
        self.assertRegex(plays[0].exports[0].name, r'h_mr_ua_stagger_(lgt|med)_atk_')
        self.roundtrip(s, plays[0])
        text = [e for e in s.entries if e.ch == pawn.ch and any(f[1] == 'MulticastSendMultitypeCombatText'
                                                                for b in e.parsed.blocks for f in (b.fields or []))]
        self.assertTrue(text)
        self.roundtrip(s, text[0])

    def test_an_unanswered_hit_is_applied_after_the_timeout(self):
        s = self.session()
        enc = s._encounter
        self.attack_until_hit(s)
        start = enc.get('player_health')
        with patch.object(island_encounter.time, 'monotonic', return_value=time.monotonic() + 5.0):
            out = island_encounter._expire_hits(s, enc)
        self.assertTrue(any(b.cls_name == 'ArchonHealthAttributeSet' for e in out for b in e.parsed.blocks))
        self.assertLess(enc['player_health'], start if start is not None else 1e9)
        self.assertEqual(len(enc['pending_hits']), 0)

    def test_only_an_accepted_hit_damages_the_player(self):
        from mpgs.engine import hits
        s = self.session()
        enc = s._encounter
        blk = self.attack_until_hit(s).parsed.blocks[0]
        params = next(f[3] for f in blk.fields if f[1] == 'ClientTryHit')
        reply = {'HitID.' + k: params['HitID.' + k] for k in 'ABCD'}
        self.assertFalse(island_encounter.handle_hit_reply(s, 'ServerAcceptHit', {'HitID.A': 1}))   # unknown hit: ignored
        before = len(s.entries)
        self.assertTrue(island_encounter.handle_hit_reply(s, 'ServerAcceptHit', reply))
        queued = s.entries[s.pos:]
        self.assertEqual(len(s.entries) - before, 4)        # flinch, damage number, health, damage summary
        health = next(e for e in queued if any(b.cls_name == 'ArchonHealthAttributeSet' for b in e.parsed.blocks))
        v = self.roundtrip(s, health).blocks[0].values
        pawn = next(e for e in s.entries if e.open and e.cls == 'BP_PlayerCharacter_C')
        start = next(b for b in pawn.parsed.blocks if b.cls_name == 'ArchonHealthAttributeSet').values['CurrentHealth']
        self.assertLess(v['CurrentHealth'], start)
        self.assertEqual(v['CurrentHealth'], enc['player_health'])
        pm = next(e for e in queued if any(f[1] == 'ClientHandlePostMitigationDamage' for b in e.parsed.blocks
                                           for f in (b.fields or [])))
        self.roundtrip(s, pm)
        # the same hit cannot be applied twice, and a refused hit changes nothing
        self.assertFalse(island_encounter.handle_hit_reply(s, 'ServerAcceptHit', reply))
        enc['pending_hits'].add({'A': 1, 'B': 2, 'C': 3, 'D': 4}, {'damage': 99.0, 'elemental': 0.0, 'action': 'x', 'key': 'y',
                                                              'raw': 1.0})
        n = len(s.entries)
        self.assertTrue(island_encounter.handle_hit_reply(s, 'ServerRefuseHit', {'HitID.A': 1, 'HitID.B': 2, 'HitID.C': 3,
                                                                                 'HitID.D': 4}))
        self.assertEqual(len(s.entries), n)
        self.assertEqual(hits.key_of(reply), tuple(reply['HitID.' + k] for k in 'ABCD'))


@unittest.skipUnless(HAVE_DATA, 'Embermane/island engine data not generated locally (see the engine_data manifest)')
class NoAbilitySystemTests(HealthAndHitTests):
    """MPGS_BEHEMOTH_ASC=0: the engine path without the step 6 ASC/attribute-set blocks, for bisecting a live rejection."""
    test_spawn_carries_ability_system_and_health_set = None
    test_player_hit_updates_the_health_set = None
    test_damage_window_sends_client_try_hit = None
    test_only_an_accepted_hit_damages_the_player = None
    test_stagger_probe_targets_the_joined_hit_reaction_component = None
    test_stagger_probe_is_off_by_default = None
    test_hit_plays_a_flinch_montage_on_the_player = None
    test_an_unanswered_hit_is_applied_after_the_timeout = None

    def session(self):
        with patch.dict(os.environ, {island_encounter.ASC_FLAG: '0'}):
            return super().session()

    def test_spawn_has_no_ability_system(self):
        s = self.session()
        actor = next(e for e in s.entries if e.open and e.cls == CLS)
        names = {b.cls_name for b in self.roundtrip(s, actor).blocks}
        self.assertFalse(names & {'ArchonAbilitySystemComponent', 'ArchonHealthAttributeSet', 'ArchonDamageAttributeSet'})
        self.assertNotIn('ArchonAbilitySystem', [x.name for x in actor.exports])
        self.assertIsNotNone(s._encounter['director'])
        self.assertEqual(actor.parsed.blocks[0].values['ReplicatedMovementMode'], 1)

    def test_spawn_differs_from_the_full_one_only_by_the_asc_blocks(self):
        lean = next(e for e in self.session().entries if e.open and e.cls == CLS)
        full = next(e for e in EngineEncounterTests.session(self).entries if e.open and e.cls == CLS)
        step6 = {'ArchonAbilitySystemComponent', 'ArchonHealthAttributeSet', 'ArchonDamageAttributeSet'}
        self.assertEqual([b.cls_name for b in lean.parsed.blocks],
                         [b.cls_name for b in full.parsed.blocks if b.cls_name not in step6])
        self.assertEqual(lean.parsed.blocks[0].values, full.parsed.blocks[0].values)

    def test_hits_still_work_without_a_health_update(self):
        s = self.session()
        self.assertTrue(island_encounter.handle_damage(s, 4, None, {'Damage': 1000.0}))
        self.assertFalse(any(b.cls_name == 'ArchonHealthAttributeSet' for e in s.entries[s.pos:] for b in e.parsed.blocks))
        self.assertEqual(s._encounter['health'], 17900.0)
        s.due(1000000)
        self.assertTrue(self.attack_until_hit(s).reliable)          # behemoth -> player hits do not depend on the ASC


class FlagTests(unittest.TestCase):
    def test_asc_flag_values(self):
        self.assertTrue(island_encounter.asc_enabled({}))
        self.assertTrue(island_encounter.asc_enabled({'MPGS_BEHEMOTH_ASC': '1'}))
        self.assertFalse(island_encounter.asc_enabled({'MPGS_BEHEMOTH_ASC': '0'}))

    def test_flag_values(self):
        self.assertTrue(island_encounter.engine_enabled({'MPGS_BEHEMOTH_ENGINE': '1'}))
        self.assertFalse(island_encounter.engine_enabled({'MPGS_BEHEMOTH_ENGINE': '0'}))
        self.assertFalse(island_encounter.engine_enabled({}))


if __name__ == '__main__':
    unittest.main()
