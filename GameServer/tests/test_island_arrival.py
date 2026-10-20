import json
import math
import os
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mpgs import island_arrival, island_world
from mpgs.bitio import BitReader, BitWriter
from mpgs.genworld import GenWorld
from mpgs.repl import model, compose
from mpgs.repl.structs import STRUCT_READERS, STRUCT_WRITERS


class IslandArrivalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        contract = json.loads((ROOT / 'data/2.1.1/islands/Adventure_IslandA_D24.contract.json').read_text())
        cls.world = island_world.build_world(GenWorld.from_template(ROOT / 'data/2.1.1/ramsgate_world.json'), contract)

    def session(self):
        env = {k: '0' for k in ('MPGS_INITIAL_DATA', 'MPGS_PLAYER_DATA', 'MPGS_PROGRESSION',
                               'MPGS_JOURNEY_PERSIST', 'MPGS_QUESTS', 'MPGS_RAMSGATE_ATMOSPHERE')}
        with patch.dict(os.environ, env):
            return self.world.new_session(dict(self.world.old_ids))

    def test_arrival_loading_gate_has_mapped_actor_and_preload_data(self):
        actor = next(e for e in self.world.entries if e.cls == island_arrival.ACTOR and e.open)
        component = next(b for b in actor.parsed.blocks if not b.actor)
        link = next(b for e in self.world.entries for b in e.parsed.blocks
                    if b.values and 'ArrivalCinematicTrigger' in b.values)
        self.assertEqual(link.values['ArrivalCinematicTrigger'], ('obj', actor.parsed.open['guid']))
        self.assertEqual(link.values['CurrentCinematicPlayerInterface'], ('obj', component.guid))
        self.assertEqual(self.world.ctx._names[component.guid][0], actor.parsed.open['guid'])
        self.assertTrue(actor.parsed.blocks[0].values['LArrivalAnimSequence'][1].endswith('island_intro_gen_arrive_00.island_intro_gen_arrive_00'))
        self.assertTrue(any(k.startswith('PreloadParameters.SequenceActorInstances') for k in component.values))
        self.assertTrue(any(k.startswith('PreloadParameters.StageTransforms') for k in component.values))

    def test_client_completion_crosses_wire_and_is_idempotent(self):
        session = self.session()
        session.due(1000000)
        pc = next(e for e in session.entries if e.open and e.cls == 'BPPC_Adventure_PlayerController_C')
        fields = self.world.schema.field_index(pc.cls)
        request = model.Parsed()
        request.blocks = [island_arrival._block(pc.cls, fields=[
            (fields.index(('func', 'SetCinematicStateRPC')), 'SetCinematicStateRPC', 'rpc', {'CinematicState': 2})])]
        encoded = model.encode(request, session.ctx, pc.cls)
        bunch = compose.compose(pc.ch, encoded.n, encoded.nbits, [])[0]
        session.on_client_bunch(bunch)
        self.assertTrue(session._arrival_finished)
        self.assertTrue(session._island_gameplay_started)
        queued = session.entries[session.pos:]
        self.assertEqual(len(queued), 5)
        names = [field[1] for e in queued for b in e.parsed.blocks for field in b.fields or []]
        self.assertIn('ClientIslandGameplayStarted', names)
        self.assertIn('AllClientsCompleteOwnerOnly', names)
        reset = next((i, b) for i, e in enumerate(queued) for b in e.parsed.blocks
                     if b.values and 'ForceFadeFromBlack' in b.values)
        complete = next(i for i, e in enumerate(queued) for b in e.parsed.blocks
                        for f in b.fields or [] if f[1] == 'AllClientsCompleteOwnerOnly')
        self.assertLess(reset[0], complete)
        self.assertEqual(reset[1].values['ForceFadeFromBlack'], 0)
        for e in queued:
            encoded = model.encode(e.parsed, session.ctx, e.cls)
            from mpgs.repl.chan import Reassembler
            assembler = Reassembler()
            for part in compose.compose(e.ch, encoded.n, encoded.nbits, e.exports, open=e.open, must_map=e.must_map):
                full = assembler.feed(part)
            decoded, _ = model.parse(full, session.ctx, e.cls)
            if e is queued[reset[0]]:
                self.assertEqual(decoded.blocks[0].values['ForceFadeFromBlack'], 0)
        session.on_client_bunch(bunch)
        self.assertEqual(len(session.entries[session.pos:]), 5)

    def test_unrelated_controller_and_loading_state_do_not_start_gameplay(self):
        session = self.session()
        self.assertFalse(island_arrival.handle_rpc(session, 99, 'BPPC_Adventure_PlayerController_C',
                                                  'SetCinematicStateRPC', {'CinematicState': 2}))
        self.assertTrue(island_arrival.handle_rpc(session, 2, 'BPPC_Adventure_PlayerController_C',
                                                 'SetCinematicStateRPC', {'CinematicState': 0}))
        self.assertFalse(getattr(session, '_island_gameplay_started', False))

    def test_quaternion_matches_engine_double_xyz_wire(self):
        z = math.sqrt(0.5)
        writer = BitWriter()
        STRUCT_WRITERS['Quat'](writer, [0., 0., z, z])
        expected = struct.pack('<ddd', 0., 0., z)
        self.assertEqual(writer.nbits, 192)
        self.assertEqual(writer.n.to_bytes(24, 'little'), expected)
        decoded = STRUCT_READERS['Quat'](BitReader(expected, 192))
        self.assertAlmostEqual(decoded[3], z)
        writer = BitWriter()
        STRUCT_WRITERS['Quat'](writer, [0., 0., -z, -z])
        self.assertEqual(writer.n.to_bytes(24, 'little'), struct.pack('<ddd', -0., -0., z))

    def test_return_to_arrival_area_and_leave_match_rpcs(self):
        session = self.session()
        session.due(1000000)
        pc = next(e for e in session.entries if e.open and e.cls == 'BPPC_Adventure_PlayerController_C')
        ps = next(e for e in session.entries if e.open and e.cls == 'adventure_player_state_bp_C')
        start_loc = self.world.island_contract['starts'][0]['location']
        self.assertTrue(island_arrival.handle_rpc(session, pc.ch, pc.cls, 'Server_MovePlayersBackToArrivalArea', {}))
        queued = session.entries[session.pos:]
        self.assertEqual(len(queued), 2)
        self.assertEqual(queued[0].parsed.blocks[0].fields[0][1], 'ClientSetLocation')
        self.assertEqual(queued[0].parsed.blocks[0].fields[0][3]['NewLocation'], start_loc)
        self.assertEqual(queued[1].parsed.blocks[0].fields[0][1], 'TeleportOnOwningClient')
        self.assertEqual(queued[1].parsed.blocks[0].fields[0][3]['DestLocation'], start_loc)
        for e in queued:
            bits = model.encode(e.parsed, session.ctx, e.cls)
            self.assertGreater(bits.nbits, 0)
        session.due(1000000)

        self.assertTrue(island_arrival.handle_rpc(session, ps.ch, ps.cls,
                                                  'ServerSetUserWantsToLeaveMatch', {'bInUserWantsToLeaveMatch': True}))
        leave_entries = session.entries[session.pos:]
        self.assertEqual(len(leave_entries), 1)
        self.assertTrue(leave_entries[0].parsed.blocks[0].values['bUserWantsToLeaveMatch'])
        bits = model.encode(leave_entries[0].parsed, session.ctx, leave_entries[0].cls)
        self.assertGreater(bits.nbits, 0)


if __name__ == '__main__':
    unittest.main()

