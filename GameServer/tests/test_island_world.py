import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mpgs import hunts, island_world, server_values, template
from mpgs.genworld import GenWorld
from mpgs.repl import compose, model
from mpgs.repl.chan import Reassembler

CONTRACT = ROOT / 'data/2.1.1/islands/Adventure_IslandA_D24.contract.json'


class IslandWorldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = json.loads(CONTRACT.read_text())
        cls.base = GenWorld.from_template(ROOT / 'data/2.1.1/ramsgate_world.json')

    def make_world(self):
        return island_world.build_world(self.base, self.contract)

    def test_authored_framework_and_no_city_exports(self):
        world = self.make_world()
        hunts.validate_world(hunts.select('Adventure_IslandA_D24'), world)
        names = {n for o, n in world.ctx._names.values()}
        self.assertFalse(any('ramsgate' in n.lower() or 'City' in n for n in names))
        for key in ('PlayerControllerClass', 'PlayerStateClass', 'GameStateClass'):
            self.assertIn('Default__' + self.contract['classes'][key][1], names)
        self.assertIn('WorldSettings', names)
        self.assertIn(island_world.CITY_MAP, {n for o, n in self.base.ctx._names.values()})

    def test_spawn_streaming_and_hunt_state(self):
        world = self.make_world()
        pawn = next(e for e in world.entries if e.cls == 'BP_PlayerCharacter_C' and e.open)
        self.assertEqual(pawn.parsed.open['loc'], [-18045.47, 16508.562, 703.90826])
        for e in world.entries:
            for b in e.parsed.blocks:
                if b.cls_name == 'adventure_player_state_bp_C' and b.values:
                    self.assertFalse(b.values['InCity'])
                if b.cls_name == 'BPGS_ArchonExploration_GameState_C' and b.values:
                    self.assertEqual(b.values['InProgressGameSubState'], 2)
                for index, name, kind, params in b.fields or []:
                    self.assertEqual(world.schema.field_index(b.cls_name)[index][1], name)
                    if name == 'ClientUpdateMultipleLevelsStreamingStatus':
                        packages = [item['LevelStatuses[].PackageName'][0] for item in params['LevelStatuses']]
                        self.assertEqual(packages, [x['package'] for x in self.contract['streamingLevels'] if x['alwaysLoaded']])
                        self.assertEqual(len(packages), 3)

    def test_all_entries_roundtrip_through_wire_bunches(self):
        world = self.make_world()
        for e in world.entries:
            with self.subTest(channel=e.ch, cls=e.cls, time=e.t):
                encoded = model.encode(e.parsed, world.ctx, e.cls)
                assembler = Reassembler()
                for bunch in compose.compose(e.ch, encoded.n, encoded.nbits, e.exports, open=e.open,
                                              close=e.close, reliable=e.reliable, must_map=e.must_map):
                    full = assembler.feed(bunch)
                self.assertIsNotNone(full)
                parsed, cls = model.parse(full, world.ctx, e.cls)
                again = model.encode(parsed, world.ctx, cls)
                self.assertEqual((encoded.n, encoded.nbits), (again.n, again.nbits))

    def test_player_data_uses_adventure_player_state(self):
        world = self.make_world()
        report = server_values.apply_player_state(world.entries, {'active_loadout': {
            'weapon': {'item_id': 'WP_EB_BEGINNER'}, 'player_role': {'item_id': 'PR_DARKNESS'}}}, [])
        self.assertEqual(report['role'], 'PR_DARKNESS')
        self.assertEqual(report['entitlements'], 0)

    def test_session_serialization_and_atmosphere(self):
        world = self.make_world()
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'world.json')
            template.write_template(world, path, {'islandContract': self.contract})
            world = GenWorld.from_template(path)
        env = {k: '0' for k in ('MPGS_INITIAL_DATA', 'MPGS_PLAYER_DATA', 'MPGS_PROGRESSION',
                               'MPGS_JOURNEY_PERSIST', 'MPGS_QUESTS')}
        env['MPGS_RAMSGATE_ATMOSPHERE'] = '1'
        with patch.dict(os.environ, env):
            session = world.new_session(dict(world.old_ids))
            self.assertGreater(len(session.due(1000000)), 36)
        atmospheres = [e for e in session.entries if e.cls == 'atmosphere_spawner_bp_C']
        self.assertEqual(len(atmospheres), 1)
        self.assertEqual(atmospheres[0].exports[0].name, 'atmosphere_spawner_bp2_2')
        values = atmospheres[0].parsed.blocks[0].values
        self.assertEqual(values['RequestedAtmosphereIndex'], 0)
        self.assertEqual(values['ServerAtmosphereSettings.ChangeValues.SunBrightness.Value'], 8)
        self.assertEqual(values['ServerAtmosphereSettings.ChangeValues.SkylightBrightness.Value'], 4)
        self.assertEqual(values['ServerAtmosphereSettings.ChangeValues.InterpolationCurve'], 0)

    def test_invalid_spawn_and_wrong_hunt_rejected(self):
        with self.assertRaisesRegex(ValueError, 'PlayerStart'):
            island_world.build_world(self.base, self.contract, slot=99)
        world = self.make_world()
        selected = hunts.select('Adventure_IslandA_D24')
        selected['huntId'] = 'different'
        with self.assertRaisesRegex(ValueError, 'contract'):
            hunts.validate_world(selected, world)

    def test_opaque_replaced_class_data_is_rejected(self):
        base = copy.deepcopy(self.base)
        actor = next(b for e in base.entries for b in e.parsed.blocks if b.cls_name == 'player_controller_city_bp_C')
        actor.raw = (123, 8)
        with self.assertRaisesRegex(ValueError, 'opaque'):
            island_world.build_world(base, self.contract)


if __name__ == '__main__':
    unittest.main()
