import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mpgs import control, hunts, initialization, island_world, server_values
from mpgs.bitio import BitReader
from mpgs.genworld import GenWorld
from mpgs.repl import model
from mpgs.server import GameServer, MAP_PATH, GAME_MODE_PATH
from tools.export_hunts import export, TABLE, SPECS

DOJO = 'CR19_MatchmakerHunt_ShatteredIsles_TrainingDojo'
ISLAND_A = 'Adventure_IslandA_D24'


class HuntTests(unittest.TestCase):
    def test_authored_training_ground(self):
        selected = hunts.select(DOJO)
        self.assertEqual(selected['map'], '/Game/Maps/islands/dojo/training_dojo_persistent')
        self.assertEqual(selected['gameMode'], '/Game/Blueprints/GameMode/BPGM_ArchonTrainingGrounds.BPGM_ArchonTrainingGrounds_C')
        self.assertEqual(selected['definition']['MaxPlayers'], 12)
        self.assertEqual(selected['definition']['SpecificBehemoth']['BehemothName'], 'dummy')

    def test_unknown_hunt_does_not_fall_back_to_city(self):
        with self.assertRaisesRegex(ValueError, 'Unknown hunt'):
            hunts.select('invalid')

    def test_multiple_maps_require_selection(self):
        catalog = copy.deepcopy(hunts.load())
        second = copy.deepcopy(catalog[DOJO]['maps'][0])
        second.update(name='second', package='/Game/Maps/second')
        catalog[DOJO]['maps'].append(second)
        with self.assertRaisesRegex(ValueError, 'choose one map'):
            hunts.select(DOJO, catalog=catalog)
        self.assertEqual(hunts.select(DOJO, 'second', catalog)['map'], '/Game/Maps/second')
        with self.assertRaisesRegex(ValueError, 'no eligible'):
            hunts.select(DOJO, 'invalid', catalog)
        second['weight'] = 0
        self.assertEqual(hunts.select(DOJO, 'second', catalog)['map'], '/Game/Maps/second')

    def test_authored_exploration_zero_weight_is_selectable(self):
        self.assertEqual(hunts.select('Adventure_IslandA_D24')['map'],
                         '/Game/Maps/islands/adventure/Moss_Triforce/adventure_moss_triforce')

    def test_missing_mode_is_not_guessed(self):
        catalog = copy.deepcopy(hunts.load())
        catalog[DOJO]['gameModeOverride'] = None
        with self.assertRaisesRegex(ValueError, 'unresolved'):
            hunts.select(DOJO, catalog=catalog)

    def test_city_template_cannot_start_as_hunt(self):
        world = GenWorld.from_template(ROOT / 'data/2.1.1/ramsgate_world.json')
        with self.assertRaisesRegex(ValueError, 'does not describe'):
            hunts.validate_world(hunts.select(DOJO), world)

    def test_selected_world_references(self):
        selected = hunts.select(DOJO)
        package, cls = selected['gameMode'].rsplit('.', 1)
        names = {1: (0, selected['map']), 3: (0, package), 5: (3, cls)}
        world = SimpleNamespace(ctx=SimpleNamespace(_names=names))
        hunts.validate_world(selected, world)
        names[7] = (0, MAP_PATH)
        with self.assertRaisesRegex(ValueError, 'Ramsgate'):
            hunts.validate_world(selected, world)

    def test_login_welcome_uses_instance_destination(self):
        selected = hunts.select(DOJO)
        server = GameServer(0, None, selected['map'], selected['gameMode'])
        queued = []
        conn = SimpleNamespace(queue_bunches=lambda bunches: queued.extend(bunches))
        try:
            with patch('mpgs.server.control.login_options_with_auth', return_value=({'name': 'test'}, '')):
                server.on_control(('127.0.0.1', 1), conn, {'state': 'challenged'},
                                  SimpleNamespace(data=control.NMT_LOGIN, nbits=8))
            welcome = queued[-1]
            reader = BitReader(welcome.payload(), welcome.nbits)
            self.assertEqual(reader.read(8), control.NMT_WELCOME)
            self.assertEqual(reader.read_fstring(), selected['map'])
            self.assertEqual(reader.read_fstring(), selected['gameMode'])
        finally:
            server.sock.close()

    def test_city_defaults_preserved(self):
        server = GameServer(0, None)
        try:
            self.assertEqual((server.map_path, server.game_mode_path), (MAP_PATH, GAME_MODE_PATH))
        finally:
            server.sock.close()

    def test_export_preserves_authored_rules_and_spec(self):
        definition = copy.deepcopy(hunts.load()[DOJO]['definition'])
        definition['GameModeSpecificData'] = {'DataTable': {'ObjectPath': '/Game/x/exploration_island_spec_table.0'},
                                             'RowName': 'test'}
        spec = {'IslandSpecAsset': {'AssetPathName': '/Game/spec.test'}, 'bShowInMap': False}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / TABLE).write_text(json.dumps([{'Rows': {'hunt': definition}}]), encoding='utf-8')
            (root / SPECS).write_text(json.dumps({'Rows': {'test': spec}}), encoding='utf-8')
            result = export(root)
            self.assertEqual(result['hunts']['hunt']['definition'], definition)
            self.assertEqual(result['hunts']['hunt']['islandSpec'], spec)
            self.assertEqual(export(root), result)
            (root / SPECS).write_text('{"Rows": {}}', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'missing exploration spec'):
                export(root)

    def test_cli_inspection_and_startup_guard(self):
        inspect = subprocess.run([sys.executable, str(ROOT / 'run.py'), '--hunt', DOJO, '--inspect-hunt'],
                                 capture_output=True, text=True, timeout=30)
        self.assertEqual(inspect.returncode, 0, inspect.stderr)
        self.assertEqual(json.loads(inspect.stdout)['huntId'], DOJO)
        start = subprocess.run([sys.executable, str(ROOT / 'run.py'), '--hunt', DOJO, '--port', '0'],
                               capture_output=True, text=True, timeout=30)
        self.assertEqual(start.returncode, 2)
        self.assertIn('World template does not describe', start.stderr)

    def test_island_contract_extraction(self):
        selected = hunts.select(ISLAND_A)
        contract = island_world.extract_contract(selected, ROOT / 'world/hunt_source')
        self.assertEqual(contract['huntId'], ISLAND_A)
        self.assertEqual(contract['map'], '/Game/Maps/islands/adventure/Moss_Triforce/adventure_moss_triforce')
        self.assertEqual(contract['worldObject'], 'adventure_moss_triforce')
        self.assertEqual(contract['classes']['PlayerControllerClass'],
                         ['/Game/Blueprints/BPPC_Adventure_PlayerController', 'BPPC_Adventure_PlayerController_C'])
        self.assertEqual(contract['classes']['PlayerStateClass'],
                         ['/Game/Character/Player/blueprints/adventure_player_state_bp', 'adventure_player_state_bp_C'])
        self.assertEqual(contract['classes']['GameStateClass'],
                         ['/Game/Blueprints/BPGS_ArchonExploration_GameState', 'BPGS_ArchonExploration_GameState_C'])
        self.assertEqual([s['slot'] for s in contract['starts']], [0, 1, 2, 3])
        always_loaded = [x['package'] for x in contract['streamingLevels'] if x['alwaysLoaded']]
        self.assertEqual(always_loaded, [
            '/Game/Maps/islands/adventure/Moss_Triforce/adventure_moss_triforce_design',
            '/Game/Maps/islands/adventure/Moss_Triforce/adventure_moss_triforce_sound',
            '/Game/Maps/islands/adventure/Moss_Triforce/adventure_moss_triforce_tutorial',
        ])

    def test_island_world_build_and_encode(self):
        selected = hunts.select(ISLAND_A)
        contract = island_world.extract_contract(selected, ROOT / 'world/hunt_source')
        base = GenWorld.from_template(ROOT / 'data/2.1.1/ramsgate_world.json')
        world = island_world.build_world(base, contract, slot=1)
        hunts.validate_world(selected, world)
        spawn = contract['starts'][1]
        pawns = [e for e in world.entries if e.cls == 'BP_PlayerCharacter_C' and e.parsed.open]
        self.assertEqual(len(pawns), 1)
        self.assertEqual(pawns[0].parsed.open['loc'], spawn['location'])
        ps = [b for e in world.entries for b in e.parsed.blocks
              if b.cls_name == 'adventure_player_state_bp_C' and b.values is not None]
        self.assertEqual(len(ps), 1)
        self.assertFalse(ps[0].values['InCity'])
        self.assertEqual(ps[0].values['PlayerStartSlot'], 1)
        gs = [b for e in world.entries for b in e.parsed.blocks
              if b.cls_name == 'BPGS_ArchonExploration_GameState_C' and b.values is not None]
        self.assertEqual(len(gs), 1)
        self.assertEqual(gs[0].values['InProgressGameSubState'], 2)
        profile = {'id': 'c1', 'name': 'Slayer', 'data': {'AppearanceData': {
            'CreationState': 'FaceComplete', 'Data': [], 'StringData': []}}}
        self.assertEqual(initialization.apply_character_profile(world.entries, world.schema, profile, 'c1'), 1)
        records = {'active_loadout': {'player_role': {'item_id': 'PR_DARKNESS'}, 'weapon': {'item_id': 'WP_EB_BEGINNER'}},
                   'persistent': {}}
        server_values.apply_all(world.entries, records, [{'name': 'ent_migration_d24_complete'}], 1700000000)
        for entry in world.entries:
            model.encode(entry.parsed, world.ctx, entry.cls)

    def test_matchmaking_island_routing_and_welcome_alignment(self):
        import os
        backend_root = ROOT.parent / 'Backend'
        if str(backend_root) not in sys.path:
            sys.path.insert(0, str(backend_root))
        from phoenix_backend.db import DB
        from phoenix_backend.gameplay import Gameplay
        from phoenix_backend.routes import Routes
        from phoenix_backend.tokens import Tokens

        async def call(handler, *, json_body=None, headers=None, match_info=None):
            raw = b'' if json_body is None else json.dumps(json_body).encode('utf-8')

            async def _read():
                return raw

            async def _json():
                return {} if json_body is None else json_body

            req = SimpleNamespace(
                headers=headers or {},
                match_info=match_info or {},
                query={},
                read=_read,
                json=_json,
            )
            resp = await handler(req)
            data = json.loads(resp.body.decode('utf-8')) if resp.body else None
            return resp.status, data

        async def run():
            with tempfile.TemporaryDirectory() as tmp:
                old_key = os.environ.get('PHOENIX_GAMESERVER_KEY')
                os.environ['PHOENIX_GAMESERVER_KEY'] = 'test-gs-key'
                db = DB(str(Path(tmp) / 'db.sqlite'))
                tokens = Tokens(str(Path(tmp) / 'secret'))
                routes = Routes(db, tokens)
                gameplay = Gameplay(db, tokens, routes, tmp)
                routes.is_gameserver = gameplay.is_gameserver
                try:
                    acc = db.create_account('Slayer')
                    token = tokens.mint('session_token', 3600, sub=acc['account_id'], acc=acc['account_id'], name='Slayer')
                    session = {'Authorization': 'BEARER ' + token}
                    gsh = {'x-phoenixrising-gameserver-apikey': 'test-gs-key'}

                    # 1. FTUE compatibility: title-screen FTUE without explicit hunt routes to CITY.
                    _, ftue = await call(gameplay.join, json_body={'gameMode': 'ISLAND', 'gameType': 'FTUE'}, headers=session)
                    self.assertEqual((ftue['gameMode'], ftue['huntId'], ftue['status']), ('CITY', '', 'MATCHING'))
                    _, ftue_st = await call(gameplay.status, headers=session)
                    self.assertEqual(ftue_st['status'], 'IN_PROGRESS')
                    self.assertEqual(ftue_st['serverInfo']['map'], MAP_PATH + '?bUseIPSockets')

                    # 2. Unknown hunt ID is rejected (never routed to CITY).
                    unk_status, _ = await call(gameplay.join, json_body={'gameMode': 'ISLAND', 'gameType': 'HUNTING_GROUND', 'hunts': ['Unknown_Hunt']}, headers=session)
                    self.assertEqual(unk_status, 404)

                    # 3. Unresolved default game mode in catalog is rejected.
                    unres_status, _ = await call(gameplay.join, json_body={'gameMode': 'ISLAND', 'gameType': 'HUNTING_GROUND', 'hunts': ['CR19_MatchmakerHunt_Escalation_Armstrong_Easy']}, headers=session)
                    self.assertEqual(unres_status, 400)

                    # 4. Catalog hunt without an available instance stays MATCHING (not IN_PROGRESS).
                    _, j = await call(gameplay.join, json_body={
                        'gameMode': 'ISLAND', 'gameType': 'HUNTING_GROUND',
                        'hunts': [ISLAND_A], 'playerHuntId': 'ShatteredIsles_IslandA_D24'}, headers=session)
                    self.assertEqual((j['gameMode'], j['huntId'], j['status']), ('ISLAND', ISLAND_A, 'MATCHING'))
                    _, st_unavail = await call(gameplay.status, headers=session)
                    self.assertEqual(st_unavail['status'], 'MATCHING')
                    self.assertEqual(st_unavail['statusReason'], 'NO_AVAILABLE_INSTANCE')
                    self.assertNotIn('serverInfo', st_unavail)
                    _, party_unavail = await call(gameplay.party, json_body={}, headers=session)
                    self.assertEqual(party_unavail['candidateState'], 'MATCHING')
                    self.assertEqual(party_unavail['playerHuntId'], 'ShatteredIsles_IslandA_D24')
                    key_unavail, _ = await call(gameplay.key_generate, json_body={}, headers=session)
                    self.assertEqual(key_unavail, 409)

                    # 5. Mismatched NMT_Welcome map registration is rejected.
                    bad_reg, _ = await call(gameplay.instances_register, json_body={
                        'gameMode': 'ISLAND', 'huntId': ISLAND_A, 'port': 8791,
                        'map': MAP_PATH, 'welcomeGameMode': GAME_MODE_PATH}, headers=gsh)
                    self.assertEqual(bad_reg, 400)

                    # 6. Register a real GameServer instance for ISLAND_A and verify travel URL matches NMT_Welcome.
                    selected = hunts.select(ISLAND_A)
                    island_srv = GameServer(0, None, selected['map'], selected['gameMode'], selected['huntId'])
                    try:
                        reg_status, _ = await call(gameplay.instances_register, json_body=island_srv.instance_info(), headers=gsh)
                        self.assertEqual(reg_status, 200)
                        _, st_ready = await call(gameplay.status, headers=session)
                        self.assertEqual(st_ready['status'], 'IN_PROGRESS')
                        self.assertEqual((st_ready['gameMode'], st_ready['huntId']), ('ISLAND', ISLAND_A))
                        self.assertEqual(st_ready['serverInfo']['port'], island_srv.port)
                        self.assertEqual(st_ready['serverInfo']['map'], selected['map'] + '?bUseIPSockets')

                        # Decode wire NMT_Welcome from the registered GameServer instance and compare to travel URL.
                        queued = []
                        conn = SimpleNamespace(queue_bunches=lambda bunches: queued.extend(bunches))
                        with patch('mpgs.server.control.login_options_with_auth', return_value=({'name': 'Slayer'}, '')):
                            island_srv.on_control(('127.0.0.1', 1), conn, {'state': 'challenged'},
                                                  SimpleNamespace(data=control.NMT_LOGIN, nbits=8))
                        welcome = queued[-1]
                        reader = BitReader(welcome.payload(), welcome.nbits)
                        self.assertEqual(reader.read(8), control.NMT_WELCOME)
                        welcome_map = reader.read_fstring()
                        welcome_mode = reader.read_fstring()
                        self.assertEqual(st_ready['serverInfo']['map'], welcome_map + '?bUseIPSockets')
                        self.assertEqual(welcome_mode, selected['gameMode'])

                        _, party_ready = await call(gameplay.party, json_body={}, headers=session)
                        self.assertEqual(party_ready['candidateState'], 'IN_PROGRESS')
                        self.assertEqual(party_ready['playerHuntId'], 'ShatteredIsles_IslandA_D24')
                        key_ok, _ = await call(gameplay.key_generate, json_body={}, headers=session)
                        self.assertEqual(key_ok, 200)

                        # 7. Explicit CITY return travel from an island routes back to Ramsgate even if PHOENIX_HUNT_ID is set.
                        with patch.dict(os.environ, {'PHOENIX_HUNT_ID': ISLAND_A}):
                            _, ret = await call(gameplay.join, json_body={'gameMode': 'CITY', 'gameType': 'CITY'}, headers=session)
                            self.assertEqual((ret['gameMode'], ret['huntId'], ret['status']), ('CITY', '', 'MATCHING'))
                            _, ret_st = await call(gameplay.status, headers=session)
                            self.assertEqual(ret_st['status'], 'IN_PROGRESS')
                            self.assertEqual(ret_st['serverInfo']['map'], MAP_PATH + '?bUseIPSockets')

                        # 8. Idempotent progression XP grant with x-transaction-id deduplicates retries.
                        tx_hdr = dict(gsh, **{'x-transaction-id': 'ENCOUNTER_XP_TX_001'})
                        match_xp = {'account': acc['account_id'], 'track': 'ExperienceTrack_PlayerLevel', 'amount': '150'}
                        _, xp1 = await call(gameplay.grant_progression, headers=tx_hdr, match_info=match_xp)
                        _, xp2 = await call(gameplay.grant_progression, headers=tx_hdr, match_info=match_xp)
                        self.assertEqual(xp1['payload']['progress'], 150)
                        self.assertEqual(xp2['payload']['progress'], 150)
                    finally:
                        island_srv.sock.close()
                finally:
                    db.conn.close()
                    if old_key is None:
                        os.environ.pop('PHOENIX_GAMESERVER_KEY', None)
                    else:
                        os.environ['PHOENIX_GAMESERVER_KEY'] = old_key

        coro = run()
        try:
            coro.send(None)
        except StopIteration:
            pass


if __name__ == '__main__':
    unittest.main()
