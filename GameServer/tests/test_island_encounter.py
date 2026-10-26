import json
import math
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mpgs import island_world, island_encounter, island_arrival, template
from mpgs.genworld import GenWorld
from mpgs.repl import model, compose
from mpgs.repl.chan import Reassembler
from mpgs.repl.rpc import function_params
from mpgs.repl.values import Unsupported
from mpgs.server import GameServer
from mpgs.wire import Bunch
from types import SimpleNamespace


# These tests cover the scripted encounter (MPGS_BEHEMOTH_ENGINE unset/0). The engine path is in test_island_encounter_engine.py.
_engine_flag = patch.dict(os.environ, {island_encounter.ENGINE_FLAG: '0',
                                       'MPGS_WEAPON_PARTS': '0'})   # the spawn wire alone (weapon parts: test_weapon_parts.py)


def setUpModule():
    _engine_flag.start()


def tearDownModule():
    _engine_flag.stop()


class IslandEncounterTests(unittest.TestCase):
    def test_client_actor_rejection_identifies_encounter_channel(self):
        # Actual client control payload from the failed October 18 join.
        payload = bytes.fromhex('1013000000')
        bunch = Bunch(0, 40, int.from_bytes(payload, 'little'))
        state = {'world': SimpleNamespace(chan_cls={19: 'lerawr_beta_bp_C'})}
        with patch('mpgs.server.log') as log:
            GameServer.on_control(None, None, None, state, bunch)
        self.assertEqual(state['failed_actor_channels'], {19: 'lerawr_beta_bp_C'})
        self.assertIn('client rejected actor ch=19 class=lerawr_beta_bp_C', log.call_args.args[0])

    @classmethod
    def setUpClass(cls):
        cls.contract = json.loads((ROOT / 'data/2.1.1/islands/Adventure_IslandA_D24.contract.json').read_text())
        cls.world = island_world.build_world(GenWorld.from_template(ROOT / 'data/2.1.1/ramsgate_world.json'), cls.contract)

    def session(self, world=None):
        world = world or self.world
        with patch.dict(os.environ, {k: '0' for k in ('MPGS_INITIAL_DATA', 'MPGS_PLAYER_DATA',
                        'MPGS_PROGRESSION', 'MPGS_JOURNEY_PERSIST', 'MPGS_QUESTS',
                        'MPGS_RAMSGATE_ATMOSPHERE', 'MPGS_ENCOUNTER_REWARDS')}):
            session = world.new_session(dict(world.old_ids))
        session.due(1000000)
        return session

    def test_spawn_waits_for_arrival_and_only_happens_once(self):
        session = self.session()
        self.assertEqual(island_encounter.start(session), [])
        session._arrival_finished = True
        entries = island_encounter.start(session)
        self.assertEqual(len(entries), 1)
        actor = entries[0]
        self.assertEqual(actor.cls, 'lerawr_beta_bp_C')
        self.assertTrue(actor.must_map)
        self.assertEqual(actor.parsed.must_map, [actor.parsed.open['archetype']])
        vals = actor.parsed.blocks[0].values
        self.assertEqual(vals['BehemothLevel'], 1)
        self.assertFalse(vals['bImageRevealed'])
        self.assertFalse(vals['InCombat'])
        self.assertTrue(vals['DisableIkinema'])
        self.assertEqual(vals['ReplicatedMovementMode'], 0)
        self.assertEqual(actor.parsed.open['loc'], [-10033.564, 4907.358, 167.325516])
        self.assertEqual(island_encounter.start(session), [])

    def test_spawn_wire_has_real_properties_and_disjoint_dynamic_ids(self):
        sessions = [self.session(), self.session()]
        ids = []
        for session in sessions:
            session._arrival_finished = True
            entries = island_encounter.start(session)
            ids.append(entries[0].parsed.open['guid'])
            for entry in entries:
                bits = model.encode(entry.parsed, session.ctx, entry.cls)
                assembler = Reassembler()
                parts = compose.compose(entry.ch, bits.n, bits.nbits, entry.exports,
                                        open=entry.open, must_map=entry.must_map)
                for part in parts:
                    full = assembler.feed(part)
                decoded, cls = model.parse(full, session.ctx, entry.cls)
                self.assertEqual(decoded.must_map, entry.parsed.must_map)
                if entry.open:
                    self.assertTrue(parts[-1].must_map)
                    self.assertFalse(any(p.must_map for p in parts[:-1]))
                    self.assertEqual(decoded.must_map, [decoded.open['archetype']])
                self.assertEqual(decoded.blocks[0].values, entry.parsed.blocks[0].values)
                encoded = model.encode(decoded, session.ctx, cls)
                self.assertEqual((bits.n, bits.nbits), (encoded.n, encoded.nbits))
        self.assertNotEqual(*ids)

    def test_disk_template_installs_authored_class_chain(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'island.json')
            template.write_template(self.world, path, {'islandContract': self.contract})
            world = GenWorld.from_template(path)
        chain = world.schema.class_chain('lerawr_beta_bp_C')
        self.assertIn('ArchonBehemoth', chain)
        self.assertIn('Bp_Behemoth_C', chain)
        fields = world.schema.field_index('lerawr_beta_bp_C')
        self.assertIn(('func', 'MulticastEnterFlameState'), fields)
        self.assertIn(('prop', 'BehemothLevel'), fields)
        with self.assertRaises(Unsupported):
            function_params(world.schema, 'lerawr_beta_bp_C', 'MulticastEnterFlameState')
        session = self.session(world)
        session._arrival_finished = True
        self.assertEqual(len(island_encounter.start(session)), 1)

    def test_early_gameplay_request_does_not_lose_arrival_spawn(self):
        session = self.session()
        pc = next(e for e in session.entries if e.open and e.cls == 'BPPC_Adventure_PlayerController_C')
        island_arrival.handle_rpc(session, pc.ch, pc.cls, 'Server_Gameplay_Start', {})
        self.assertFalse(getattr(session, '_encounter_started', False))
        island_arrival.handle_rpc(session, pc.ch, pc.cls, 'SetCinematicStateRPC', {'CinematicState': 2})
        self.assertTrue(session._encounter_started)
        self.assertEqual(sum(e.open and e.cls == 'lerawr_beta_bp_C'
                             for e in session.entries[session.pos:]), 1)

    def test_patrol_spotting_and_combat_movement(self):
        session = self.session()
        session._arrival_finished = True
        session.entries[session.pos:session.pos] = island_encounter.start(session)
        session.due(1000000)
        # While player is far away at the landing pad, Behemoth patrols without revealing on compass.
        session.pawn_state = {'loc': [-18045.47, 16508.562, 703.9], 'rot': [0.0, 0.0, 0.0], 'vel': [0.0, 0.0, 0.0], 'mode': 1}
        bunches = island_encounter.step_movement(session, 100.0)
        self.assertFalse(session._encounter['spotted'])
        self.assertEqual(len(bunches), 1)
        lay = session.world.schema.layout('lerawr_beta_bp_C')
        repmove_cmd = next(c for c in lay.cmds if c.name == 'ReplicatedMovement')
        self.assertEqual(repmove_cmd.type, 'NetSerialize:RepMovement_Behemoth')
        music_cmd = next(c for c in lay.cmds if c.name == 'BehemothMusicState')
        self.assertEqual(music_cmd.prop.en, 'behemoth_states_enum')
        self.assertEqual(music_cmd.prop.em, 29)
        assembler = Reassembler()
        full = assembler.feed(bunches[0])
        decoded, _ = model.parse(full, session.ctx, 'lerawr_beta_bp_C')
        self.assertEqual(decoded.blocks[0].values['ReplicatedMovementMode'], 0)
        self.assertGreater(sum(v * v for v in decoded.blocks[0].values['ReplicatedMovement']['vel']), 0.0)
        start_loc = session._encounter['center']
        first_patrol_loc = session._encounter['loc']
        first_patrol_vel = session._encounter['vel']
        self.assertLess(math.dist(start_loc[:2], first_patrol_loc[:2]), 20.0)
        self.assertAlmostEqual(first_patrol_vel[0], (first_patrol_loc[0] - start_loc[0]) / 0.066)
        self.assertAlmostEqual(first_patrol_vel[1], (first_patrol_loc[1] - start_loc[1]) / 0.066)

        # When player approaches within enterCombatRange (4000 units), Behemoth is spotted & revealed.
        session.pawn_state = {'loc': [-12000.0, 5000.0, 118.0], 'rot': [0.0, 0.0, 0.0], 'vel': [0.0, 0.0, 0.0], 'mode': 1}
        bunches = island_encounter.step_movement(session, 100.1)
        self.assertTrue(session._encounter['spotted'])
        self.assertTrue(session._encounter['in_combat'])
        self.assertEqual(bunches, [])
        # Deliver the queued reveal entries (GameState SpawnedBehemoths + Behemoth bImageRevealed/InCombat).
        reveal_entries = session.entries[session.pos:]
        self.assertEqual(len(reveal_entries), 2)
        gs_vals = reveal_entries[0].parsed.blocks[0].values
        self.assertEqual(gs_vals['BehemothState'], 1)
        self.assertEqual(gs_vals['SpawnedBehemoths']['elems'][0]['SpawnedBehemoths[]'], ('obj', session._encounter['guid']))
        beh_vals = reveal_entries[1].parsed.blocks[0].values
        self.assertTrue(beh_vals['bImageRevealed'])
        self.assertTrue(beh_vals['InCombat'])
        self.assertTrue(beh_vals['IsAlerted'])
        session.due(1000000)

        # Subsequent movement step stalks toward the player in combat.
        old_yaw = session._encounter['rot'][1]
        target_yaw = math.degrees(math.atan2(
            session.pawn_state['loc'][1] - session._encounter['loc'][1],
            session.pawn_state['loc'][0] - session._encounter['loc'][0])) % 360.0
        old_diff = ((target_yaw - old_yaw + 180.0) % 360.0) - 180.0
        combat_bunches = island_encounter.step_movement(session, 100.2)
        self.assertEqual(len(combat_bunches), 1)
        new_yaw = session._encounter['rot'][1]
        new_diff = ((target_yaw - new_yaw + 180.0) % 360.0) - 180.0
        self.assertLess(abs(new_diff), abs(old_diff))
        self.assertAlmostEqual(math.hypot(*session._encounter['vel'][:2]),
                               session._encounter['walk_speed'])

    def test_damage_applies_combat_text_and_defeat_dissolve(self):
        session = self.session()
        session._arrival_finished = True
        session.entries[session.pos:session.pos] = island_encounter.start(session)
        session.due(1000000)
        # First hit on an unspotted Behemoth reveals it and applies damage + floating combat text.
        self.assertTrue(island_encounter.handle_damage(session, 4, None, {'Damage': 1000.0}))
        self.assertTrue(session._encounter['spotted'])
        self.assertEqual(session._encounter['health'], 17900.0)
        for entry in session.entries[session.pos:]:
            bits = model.encode(entry.parsed, session.ctx, entry.cls)
            self.assertGreater(bits.nbits, 0)
        session.due(1000000)

        # Fatal hit reduces health to 0, sets IsDead=True, queues MultiDissolve, clears SpawnedBehemoths,
        # and grants defeat notifications, loot transaction, and Player/Weapon XP replication.
        with patch.dict(os.environ, {'MPGS_ENCOUNTER_REWARDS': '0'}):
            self.assertTrue(island_encounter.handle_damage(session, 4, None, {'Damage': 20000.0}))
        self.assertTrue(session._encounter['dead'])
        self.assertTrue(session._encounter['rewards_granted'])
        self.assertEqual(session._encounter['health'], 0.0)
        defeat_entries = session.entries[session.pos:]
        self.assertEqual(len(defeat_entries), 5)
        for entry in defeat_entries:
            bits = model.encode(entry.parsed, session.ctx, entry.cls)
            self.assertGreater(bits.nbits, 0)
        self.assertTrue(defeat_entries[1].parsed.blocks[0].values['IsDead'])
        self.assertEqual(defeat_entries[2].parsed.blocks[0].values['SpawnedBehemoths'], {'count': 0, 'elems': {}})
        ctrl_entry = defeat_entries[3]
        ctrl_rpcs = [f[1] for f in ctrl_entry.parsed.blocks[0].fields]
        self.assertIn('BehemothSlain_Notification', ctrl_rpcs)
        self.assertIn('Client_ShowBehemothSlainChatMessage', ctrl_rpcs)
        self.assertIn('ClientSetEncounteredState', ctrl_rpcs)
        self.assertEqual(ctrl_rpcs.count('NotifyOwningClientAboutConditionalLoot'), 3)
        self.assertEqual(ctrl_entry.parsed.blocks[1].values['LastExperienceGranted.IncreaseAmount'], 150)
        self.assertEqual(ctrl_entry.parsed.blocks[1].values['LastExperienceGranted.ItemId'], 'lerawr_beta')
        ps_entry = defeat_entries[4]
        self.assertEqual(ps_entry.parsed.blocks[0].values['WeaponExperienceDetails.TotalAmount'], 150)
        tx = session._encounter['reward_tx']
        self.assertIsNotNone(tx)
        stacked = {it['catalogId']: it['quantity'] for it in tx['addStackedItems']}
        self.assertEqual(stacked, {
            'CURRENCY_NOTES': 250,
            'CURRENCY_WEAPONXP_SMALL': 1,
            'ORB_FLAME': 10,
            'TOKEN_HUNT_COUNT_LERAWR_BETA': 1,
        })
        # Calling grant_defeat_rewards again is idempotent.
        self.assertEqual(island_encounter.grant_defeat_rewards(session), [])

    def test_defeat_advances_ftue_quest_to_redeemable(self):
        with patch.dict(os.environ, {
            'MPGS_INITIAL_DATA': '0', 'MPGS_PLAYER_DATA': '0', 'MPGS_PROGRESSION': '0',
            'MPGS_JOURNEY_PERSIST': '0', 'MPGS_QUESTS': '1', 'MPGS_RAMSGATE_ATMOSPHERE': '0',
            'MPGS_ENCOUNTER_REWARDS': '0',
        }):
            session = self.world.new_session(dict(self.world.old_ids))
        session.due(1000000)
        qg = session.quest_by_id['CR20_PJM_FTUE_01']
        self.assertEqual(session.quest_blocks[qg].values['Status'], 1)
        session._arrival_finished = True
        session.entries[session.pos:session.pos] = island_encounter.start(session)
        session.due(1000000)
        with patch.dict(os.environ, {'MPGS_ENCOUNTER_REWARDS': '0'}):
            self.assertTrue(island_encounter.handle_damage(session, 4, None, {'Damage': 20000.0}))
        self.assertEqual(session.quest_blocks[qg].values['Status'], 2)
        for entry in session.entries[session.pos:]:
            bits = model.encode(entry.parsed, session.ctx, entry.cls)
            self.assertGreater(bits.nbits, 0)


if __name__ == '__main__':
    unittest.main()

