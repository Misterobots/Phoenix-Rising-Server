"""Phase 3: matchmaking, player data and the game-server API.

Client routes: tuning tables, balances, progression config/objectives, Slayer's Path, mailbox, guild, party,
breadcrumbs, encountered content, matchmaking (regions -> join -> status -> key).
Game-server routes (authenticated with the game-server API key header): session key consume, inventory
(read + transactions), loadouts, character profile, progression (read + XP grant), entitlements.

New characters receive the starter kit (the equipment the Ramsgate world template is built around) and a
loadout using it on their first inventory read."""
import json
import logging
import os
import secrets
import time

from aiohttp import web

from . import ids
from .routes import envelope, iso

log = logging.getLogger('phoenix')

BUILD_ID = '682486_2.1.1_shipping'          # the client's engine build (party/join requests)
SERVER_BUILD_ID = '682875_2.1.1_shipping'   # content changelist: what serverInfo.buildId carries
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GAMESERVER_HEADERS = ('x-phoenixrising-gameserver-apikey', 'x-gameserver-apikey')
STARTER = [('weapon', 'WP_EB_BEGINNER'), ('helmet', 'AR_UNEQUIPPED_HELM'), ('chest', 'AR_BEGINNER_CHEST'),
           ('arms', 'AR_BEGINNER_ARMS'), ('legs', 'AR_BEGINNER_LEGS'), ('lantern', 'LT_BASIC'),
           ('player_role', 'PR_DARKNESS'), (None, 'FL_HEALING_DEFAULT'),
           (None, 'WP_GA_BEGINNER'), (None, 'WP_IH_BEGINNER'), (None, 'WP_CB_BEGINNER'), (None, 'WP_MS_BEGINNER'),
           (None, 'WP_DP_BEGINNER'), (None, 'WP_AC_BEGINNER'),
           # default weapon parts (every weapon carries an equipped part), glider frame, banner
           (None, 'PART_EB_SPECIAL_DEFAULT'), (None, 'PART_IH_SPECIAL_DEFAULT'), (None, 'PART_GA_SPECIAL_DEFAULT'),
           (None, 'PART_CB_SPECIAL_DEFAULT'), (None, 'PART_MS_SPECIAL_PROJECTILE'), (None, 'PART_DP_RECEIVER_DEFAULT'),
           (None, 'PART_DP_GRIP_DEFAULT'), (None, 'PART_AC_SPECIAL_DEFAULT'), (None, 'GD_FRAME_STARTER_BASE'),
           (None, 'BN_BEGINNER_00')]
# Test kit (private test server): the 2.x weapons the loadout lists can show - AArchonInventory::CanEquipItem drops catalog
# items tagged 'legacy' or 'hidden', which covers every WP_*_BEGINNER and the old behemoth lines (atlas native_notes, 297899d) -
# plus the Embermane armour set and lantern, unequipped. PHOENIX_TEST_KIT=0 leaves it out; granted once per catalog id.
TEST_KIT = [(None, c) for c in ('WP_AC_TERRA_01', 'WP_AC_SHOCK_01', 'WP_AC_SPRINGTIDE_L1', 'WP_CB_UMBRAL_01', 'WP_CB_FROST_01', 'WP_CB_BANEANDBALM', 'WP_DP_UMBRAL_01', 'WP_DP_RADIANT_01', 'WP_DP_EXOTIC_BOMBS', 'WP_EB_SHOCK_01', 'WP_EB_RADIANT_01', 'WP_EB_FROSTFALL_L1', 'WP_EB_CROWBEAR_EXOTIC', 'WP_GA_BLAZE_01', 'WP_GA_FROST_01', 'WP_GA_REAPERS_EDGE', 'WP_IH_SHOCK_01', 'WP_IH_FROST_01', 'WP_IH_SALLY_EXOTIC', 'WP_MS_TERRA_01', 'WP_MS_BLAZE_01', 'WP_MS_BUGBEAR_EXOTIC')] +            [(None, c) for c in ('AR_LERAWR_HELM', 'AR_LERAWR_CHEST', 'AR_LERAWR_ARMS', 'AR_LERAWR_LEGS', 'LT_LERAWR')]
STARTER_STACKS = [('CURRENCY_NOTES', 5000), ('CURRENCY_PJM_WEAPON', 50), ('CURRENCY_PJM_ARMOR', 50),
                  ('CURRENCY_PJM_ALCHEMY', 50), ('CURRENCY_PJM_AIRSHIP', 50), ('CURRENCY_PJM_FISHING', 50),
                  # flare, beginner emotes, banner customization, dyes
                  ('QI_BASIC_FLARE_DURABLE', 1), ('EM_INTRO_BEGINNER_01', 1), ('EM_END_BEGINNER_01', 1),
                  ('EM_PLAYER_BEGINNER_01', 1), ('EM_PLAYER_BEGINNER_02', 1), ('EM_PLAYER_BEGINNER_03', 1),
                  ('EM_PLAYER_BEGINNER_04', 1), ('BNC_MESH_BEGINNER_00', 1), ('BNC_FABRIC_BEGINNER_00', 1),
                  ('BNC_SIGIL_BEGINNER_00', 1), ('BNC_ANIMATION_BEGINNER_00', 1), ('BNC_VFX_BEGINNER_00', 1),
                  ('DYE_GREEN04_DURABLE', 1), ('DYE_BROWN07_DURABLE', 1)]
# Every currency the client shows a balance for (Store/Balance.md); missing ones are reported as 0.
CURRENCIES = ['CURRENCY_CELLDUST', 'CURRENCY_EVENT_DARKHARVEST', 'CURRENCY_EVENT_FROSTFALL', 'CURRENCY_EVENT_RAMSGIVING',
              'CURRENCY_EVENT_SAINTSBOND', 'CURRENCY_EVENT_SPRINGTIDE', 'CURRENCY_GAUNTLET_COIN',
              'CURRENCY_GAUNTLET_COIN_FADED', 'CURRENCY_MARKS_GILDED', 'CURRENCY_MARKS_STEEL', 'CURRENCY_NOTES',
              'CURRENCY_PJM_AIRSHIP', 'CURRENCY_PJM_ALCHEMY', 'CURRENCY_PJM_ARMOR', 'CURRENCY_PJM_FISHING',
              'CURRENCY_PJM_WEAPON', 'CURRENCY_PLATINUM', 'CURRENCY_PRESTIGE', 'CURRENCY_REWARDCACHE',
              'CURRENCY_S13_COIN', 'CURRENCY_S13_DAILY', 'CURRENCY_S14_COIN', 'CURRENCY_S15_COIN', 'CURRENCY_S16_COIN',
              'CURRENCY_S17_COIN', 'CURRENCY_S18_COIN', 'CURRENCY_S19_COIN', 'CURRENCY_S20_COIN',
              'CURRENCY_SEASONAL_COIN', 'CURRENCY_WEAPON_TOKEN']
CITY_WELCOME_MAP = '/Game/Maps/ramsgate/ramsgate_01_persistent'
CITY_WELCOME_GAME_MODE = '/Game/Blueprints/GameMode/BPGM_City.BPGM_City_C'
CITY_MAP = CITY_WELCOME_MAP + '?bUseIPSockets'
HUNTS_CATALOG_PATH = os.path.join(HERE, '..', 'GameServer', 'data', '2.1.1', 'hunts.json')
HUNTPASS_SEASON = 'season19'


def travel_url_for_map(welcome_map):
    if not isinstance(welcome_map, str) or not welcome_map.startswith('/Game/') or '?' in welcome_map:
        raise ValueError(f'invalid NMT_Welcome map path: {welcome_map!r}')
    return welcome_map + '?bUseIPSockets'


def verify_travel_url(travel_url, welcome_map):
    """Ensure the backend travel URL in serverInfo.map matches the GameServer NMT_Welcome map."""
    if not isinstance(travel_url, str) or not isinstance(welcome_map, str):
        raise ValueError('travel URL and Welcome map must be strings')
    expected = travel_url_for_map(welcome_map)
    if travel_url != expected:
        raise ValueError(f'backend travel URL {travel_url!r} does not match NMT_Welcome map {welcome_map!r}')
    return travel_url

# The character-wide ("persistent") loadout a new character starts with: flare, glider, intro emote and the
# beginner banner, all from the starter kit; empty emote/emoji/quick-chat/curiosity slots.
BANNER_STARTER = {'BannerMeshItemID': 'BNC_MESH_BEGINNER_00', 'FabricMaterialItemID': 'BNC_FABRIC_BEGINNER_00',
                  'SigilTextureItemID': 'BNC_SIGIL_BEGINNER_00', 'PlantVFXItemID': '', 'PersistantStandardVFXItemID': '',
                  'AnimationItemID': 'BNC_ANIMATION_BEGINNER_00', 'BackgroundColourItemID': 'DYE_GREEN04_DURABLE',
                  'BorderColourItemID': 'DYE_BROWN07_DURABLE', 'SigilColourItemID': 'DYE_BROWN07_DURABLE',
                  'BackgroundSheenType': 0, 'BorderSheenType': 0, 'SigilSheenType': 0}


def starter_persistent():
    return {'manual_emotes': [''] * 6, 'intro_emote': 'EM_INTRO_BEGINNER_01', 'banner': '',
            'bannerCustomization': json.dumps(BANNER_STARTER, separators=(',', ':')), 'flare': 'QI_BASIC_FLARE_DURABLE',
            'title': '', 'head_accessory': '', 'back_accessory': '', 'pet': '', 'glider': 'GD_FRAME_STARTER_BASE',
            'update_version': 0, 'quick_chats': [''] * 9, 'emojis': [''] * 9,
            'quick_curiosities_items': [{'item_index': i, 'item_id': '', 'instance_id': ''} for i in range(8)],
            'quickwheel': []}


def default_appearance():
    return {'CreationState': 'EArchonCharacterCreationState::FaceComplete',
            'Data': [{'SkeletalMeshComponentName': 'Head Slot', 'MorphData': []}], 'AssetReferences': [],
            'StringData': [{'Key': 'BodyType', 'Data': 'Feminine'}, {'Key': 'Hair', 'Data': 'Hair12'},
                           {'Key': 'SkinName', 'Data': 'Tan'}, {'Key': 'Beard', 'Data': 'NoBeard'},
                           {'Key': 'Facepaint', 'Data': 'NoFacepaint'}, {'Key': 'Makeup', 'Data': 'NoMakeup'}]}
LOADOUT_SLOTS = ('weapon', 'subweapon', 'helmet', 'chest', 'arms', 'legs', 'lantern', 'player_role')


def load_gameserver_key(data_dir):
    """The shared secret the game server sends. data/gameserver.key; created on first run
    (PHOENIX_GAMESERVER_KEY, else random); the game server reads the same file."""
    path = os.path.join(data_dir, 'gameserver.key')
    if not os.path.exists(path):
        key = os.environ.get('PHOENIX_GAMESERVER_KEY', '')
        with open(path, 'w') as f:
            f.write(key or secrets.token_hex(24))
    return open(path).read().strip()


def complete_creation(data):
    """A new character leaves the intro with AppearanceData.CreationState = NewCharacter; the face step belongs
    to the new-player island, which is skipped (matchmaking sends everyone to Ramsgate). Loaded into Ramsgate in
    that state the client never leaves its loading screen, so the game server is given the
    character as FaceComplete. The client's own stored data is not changed."""
    try:
        d = json.loads(data)
        app = d.get('AppearanceData')
        app_obj = json.loads(app) if isinstance(app, str) else app
    except (TypeError, ValueError):
        return data
    if not isinstance(app_obj, dict) or app_obj.get('CreationState') not in ('EArchonCharacterCreationState::NewCharacter', 'NewCharacter'):
        return data
    app_obj['CreationState'] = 'EArchonCharacterCreationState::FaceComplete'
    d['AppearanceData'] = json.dumps(app_obj) if isinstance(app, str) else app_obj
    return json.dumps(d)


class Gameplay:
    def __init__(self, db, tokens, routes, data_dir):
        self.db, self.tokens, self.routes = db, tokens, routes
        self.gs_key = load_gameserver_key(data_dir)
        self.game_host = os.environ.get('PHOENIX_GAME_HOST', '127.0.0.1')
        self.game_port = int(os.environ.get('PHOENIX_GAME_PORT', '8790'))
        self.data_dir = data_dir
        self.candidates = {}           # account -> candidate
        self.session_keys = {}         # token -> key hex
        self.city_instance = {
            'gameMode': 'CITY',
            'huntId': '',
            'host': self.game_host,
            'port': self.game_port,
            'welcomeMap': CITY_WELCOME_MAP,
            'welcomeGameMode': CITY_WELCOME_GAME_MODE,
            'map': verify_travel_url(CITY_MAP, CITY_WELCOME_MAP),
        }
        self.island_instances = {}     # huntId -> verified available island instance

    # -- auth ----------------------------------------------------------------------------------------------------
    def is_gameserver(self, request):
        return any(request.headers.get(h) == self.gs_key for h in GAMESERVER_HEADERS) and bool(self.gs_key)

    def account_for(self, request, account_id=None):
        """Game server: any account it names. Client: only its own."""
        if self.is_gameserver(request):
            acc = self.db.account(account_id) if account_id else self.routes.account_from(request)
            if acc is None:
                raise web.HTTPNotFound(text='{"error":"unknown account"}', content_type='application/json')
            return acc
        acc = self.routes.require(request)
        if account_id and account_id != acc['account_id']:
            raise web.HTTPForbidden(text='{"error":"forbidden"}', content_type='application/json')
        return acc

    def character_for(self, acc, character_id):
        row = self.db.character(character_id)
        if row is None or row['account_id'] != acc['account_id']:
            raise web.HTTPNotFound(text='{"error":"unknown character"}', content_type='application/json')
        return row

    # -- starter kit ---------------------------------------------------------------------------------------------
    def ensure_starter(self, character_id):
        """Give a character the starter kit: first time, every item plus a loadout using it; later, any starter
        item it has never owned. Idempotent per catalog id."""
        have = {r['catalog_id'] for r in self.db.items(character_id)}
        stacks = {r['catalog_id'] for r in self.db.stacks(character_id)}
        first = not have and not self.db.loadouts(character_id)
        granted = set(self.db.get(character_id, 'starter_granted', []))
        slot, add = {name: None for name in LOADOUT_SLOTS}, []
        kit = STARTER + (TEST_KIT if os.environ.get('PHOENIX_TEST_KIT', '1') != '0' else [])
        for name, catalog in kit:
            if catalog in have or catalog in granted:
                continue
            inst = ids.base32_id()
            add.append({'catalogId': catalog, 'instanceId': inst, 'itemData': None, 'updateVersion': 0})
            if name:
                slot[name] = {'item_id': catalog, 'instance_id': inst, 'instance_data': None}
        add_stacks = [{'catalogId': c, 'quantity': q} for c, q in STARTER_STACKS if c not in stacks and c not in granted]
        if not add and not add_stacks:
            return
        self.db.apply_transaction(character_id, 'starter:%s:%d' % (character_id, len(granted)),
                                  add_instanced=add, add_stacked=add_stacks)
        self.db.put(character_id, 'starter_granted', sorted(granted | {a['catalogId'] for a in add} |
                                                            {a['catalogId'] for a in add_stacks}))
        if first:
            slot.update({'slot_index': 0, 'custom_name': '', 'update_version': 0, 'flask': '', 'quick_items': [],
                         'persistent': starter_persistent(), 'appearance': None})
            self.db.save_loadout(character_id, 0, slot)
        log.info('starter kit %s for %s: %d items, %d stacks', 'granted' if first else 'topped up', character_id,
                 len(add), len(add_stacks))

    def inventory(self, character_id):
        self.ensure_starter(character_id)
        return {'characterId': character_id,
                'instancedItems': [{'catalogId': r['catalog_id'], 'instanceId': r['instance_id'], 'itemData': r['item_data'],
                                    'updateVersion': r['update_version']} for r in self.db.items(character_id)],
                'stackedItems': [{'catalogId': r['catalog_id'], 'quantity': r['quantity']} for r in self.db.stacks(character_id)]}

    def balances(self, acc):
        out = {c: 0 for c in CURRENCIES}
        for ch in self.db.characters(acc['account_id']):
            for r in self.db.stacks(ch['character_id']):
                if r['catalog_id'].startswith('CURRENCY_'):
                    out[r['catalog_id']] = out.get(r['catalog_id'], 0) + r['quantity']
        both = dict(out)
        both.update({'id_' + k.lower(): v for k, v in out.items()})
        return both

    # -- authored hunt catalog & instance registry ----------------------------------------------------------------
    def hunt_catalog(self):
        if not hasattr(self, '_hunt_catalog'):
            with open(HUNTS_CATALOG_PATH, encoding='utf-8') as f:
                data = json.load(f)
            if (data.get('format'), data.get('version'), data.get('build')) != ('phoenix-hunts', 1, '2.1.1'):
                raise ValueError('Unsupported hunt catalog format or build')
            self._hunt_catalog = data['hunts']
        return self._hunt_catalog

    def select_hunt(self, hunt_id, map_name=None):
        catalog = self.hunt_catalog()
        if not isinstance(hunt_id, str) or hunt_id not in catalog:
            raise KeyError(f'Unknown hunt: {hunt_id}')
        hunt = catalog[hunt_id]
        maps = [m for m in hunt['maps'] if
                map_name is None or m['name'] == map_name or m['package'] == map_name]
        if not maps:
            raise ValueError(f'{hunt_id}: no eligible authored map for {map_name!r}')
        if len(maps) != 1:
            raise ValueError(f'{hunt_id}: choose one map explicitly: ' + ', '.join(m['package'] for m in maps))
        if not hunt['gameModeOverride']:
            raise ValueError(f'{hunt_id}: default game mode is unresolved; no override in the authored table')
        return {
            'huntId': hunt_id,
            'map': maps[0]['package'],
            'gameMode': hunt['gameModeOverride'],
            'mapDefinition': maps[0]['definition'],
            'islandSpec': hunt['islandSpec'],
            'definition': hunt['definition'],
        }

    def register_instance(self, info):
        """Register and validate a live GameServer instance (CITY or ISLAND)."""
        if not isinstance(info, dict):
            raise ValueError('instance registration payload must be an object')
        host = info.get('host') or self.game_host
        if not isinstance(host, str) or not host:
            raise ValueError('instance host must be a non-empty string')
        try:
            port = int(info['port'])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError('instance port must be an integer') from exc
        if not 1 <= port <= 65535:
            raise ValueError('instance port out of range')
        hunt_id = info.get('huntId') or ''
        mode = info.get('gameMode') or ('ISLAND' if hunt_id else 'CITY')
        if mode == 'CITY' and not hunt_id:
            welcome_map = info.get('map') or info.get('welcomeMap') or CITY_WELCOME_MAP
            welcome_mode = info.get('welcomeGameMode') or info.get('gameModePath') or CITY_WELCOME_GAME_MODE
            if welcome_map != CITY_WELCOME_MAP or welcome_mode != CITY_WELCOME_GAME_MODE:
                raise ValueError('CITY instance Welcome map/mode does not match Ramsgate')
            travel_url = verify_travel_url(info.get('travelUrl') or travel_url_for_map(welcome_map), welcome_map)
            for hid, existing in list(self.island_instances.items()):
                if (existing['host'], existing['port']) == (host, port):
                    del self.island_instances[hid]
            self.city_instance = {
                'gameMode': 'CITY',
                'huntId': '',
                'host': host,
                'port': port,
                'welcomeMap': welcome_map,
                'welcomeGameMode': welcome_mode,
                'map': travel_url,
            }
            return dict(self.city_instance)
        if mode != 'ISLAND' or not hunt_id:
            raise ValueError('ISLAND instance registration requires gameMode=ISLAND and huntId')
        map_hint = info.get('huntMap') or info.get('map') or info.get('welcomeMap')
        selected = self.select_hunt(hunt_id, map_hint)
        welcome_map = info.get('map') or info.get('welcomeMap') or selected['map']
        welcome_mode = info.get('welcomeGameMode') or info.get('gameModePath') or selected['gameMode']
        if welcome_map != selected['map']:
            raise ValueError(f'{hunt_id}: instance Welcome map {welcome_map!r} does not match authored map {selected["map"]!r}')
        if welcome_mode != selected['gameMode']:
            raise ValueError(f'{hunt_id}: instance Welcome game mode {welcome_mode!r} does not match authored mode {selected["gameMode"]!r}')
        travel_url = verify_travel_url(info.get('travelUrl') or travel_url_for_map(welcome_map), welcome_map)
        for hid, existing in list(self.island_instances.items()):
            if (existing['host'], existing['port']) == (host, port):
                del self.island_instances[hid]
        if self.city_instance and (self.city_instance['host'], self.city_instance['port']) == (host, port):
            self.city_instance = None
        record = {
            'gameMode': 'ISLAND',
            'huntId': selected['huntId'],
            'host': host,
            'port': port,
            'welcomeMap': welcome_map,
            'welcomeGameMode': welcome_mode,
            'map': travel_url,
        }
        self.island_instances[selected['huntId']] = record
        return dict(record)

    def unregister_instance(self, info):
        if not isinstance(info, dict):
            raise ValueError('instance unregister payload must be an object')
        hunt_id = info.get('huntId')
        host = info.get('host')
        port = info.get('port')
        removed = []
        if isinstance(hunt_id, str) and hunt_id in self.island_instances:
            removed.append(self.island_instances.pop(hunt_id))
        if host and port is not None:
            pair = (str(host), int(port))
            for hid, existing in list(self.island_instances.items()):
                if (existing['host'], existing['port']) == pair:
                    removed.append(self.island_instances.pop(hid))
            if self.city_instance and (self.city_instance['host'], self.city_instance['port']) == pair and not hunt_id:
                removed.append(self.city_instance)
                self.city_instance = None
        return {'removed': len(removed)}

    async def instances_get(self, request):
        if not self.is_gameserver(request):
            self.routes.require(request)
        return web.json_response({'city': self.city_instance, 'islands': self.island_instances})

    async def instances_register(self, request):
        if not self.is_gameserver(request):
            raise web.HTTPForbidden()
        body = await self.routes.body(request)
        try:
            record = self.register_instance(body)
        except KeyError as exc:
            return web.json_response({'error': str(exc)}, status=404)
        except ValueError as exc:
            return web.json_response({'error': str(exc)}, status=400)
        log.info('registered %s instance: hunt=%s %s:%d map=%s',
                 record['gameMode'], record['huntId'] or 'CITY', record['host'], record['port'], record['welcomeMap'])
        return web.json_response(record)

    async def instances_unregister(self, request):
        if not self.is_gameserver(request):
            raise web.HTTPForbidden()
        body = await self.routes.body(request)
        try:
            result = self.unregister_instance(body)
        except ValueError as exc:
            return web.json_response({'error': str(exc)}, status=400)
        return web.json_response(result)

    def _resolve_candidate_instance(self, cand):
        """Return the verified available server instance for a candidate, or None if unavailable."""
        if not isinstance(cand, dict):
            return None
        if cand.get('gameMode') == 'CITY':
            inst = self.city_instance
            if inst is None:
                return None
            verify_travel_url(inst['map'], inst['welcomeMap'])
            return inst
        if cand.get('gameMode') == 'ISLAND':
            requested = cand.get('requestedHunts') or ([cand['huntId']] if cand.get('huntId') else [])
            for hunt_id in requested:
                inst = self.island_instances.get(hunt_id)
                if inst is None:
                    continue
                selected = self.select_hunt(inst['huntId'], inst['welcomeMap'])
                if inst['welcomeMap'] != selected['map'] or inst['welcomeGameMode'] != selected['gameMode']:
                    raise ValueError('Registered island instance no longer matches authored hunt selection')
                verify_travel_url(inst['map'], inst['welcomeMap'])
                cand['huntId'] = inst['huntId']
                if not cand.get('playerHuntId'):
                    cand['playerHuntId'] = inst['huntId']
                return inst
        return None

    # -- matchmaking -----------------------------------------------------------------------------------------------
    async def regions(self, request):
        self.routes.require(request)
        return web.json_response({'code': 200, 'message': 'success', 'payload': {
            'maxPingingStepTime': 3, 'pingCount': 5, 'pingFrequency': 0.25,
            'regionUrls': ['http://127.0.0.1:3000/QoS']}})

    async def join(self, request):
        acc = self.routes.require(request)
        body = await self.routes.body(request)
        if not isinstance(body, dict):
            return web.json_response({'error': 'join body must be an object'}, status=400)
        req_mode = body.get('gameMode') or 'CITY'
        req_type = body.get('gameType') or req_mode
        raw_hunts = body.get('hunts')
        single_hunt = body.get('huntId')
        req_player_hunt = body.get('playerHuntId') if isinstance(body.get('playerHuntId'), str) else ''
        forced_hunt = os.environ.get('PHOENIX_HUNT_ID', '').strip()
        skip_ftue = os.environ.get('PHOENIX_SKIP_FTUE', '1') == '1'

        # Explicit CITY requests (including return travel from an island to Ramsgate) always route to CITY.
        # Title-screen FTUE requests without an explicit hunt also route to CITY when PHOENIX_SKIP_FTUE=1
        # and no PHOENIX_HUNT_ID override is active.
        if (req_mode == 'CITY' or req_type == 'CITY' or
                (not forced_hunt and req_type == 'FTUE' and skip_ftue and not raw_hunts and not single_hunt)):
            cand = {
                'candidateId': 'local$' + secrets.token_hex(16),
                'gameMode': 'CITY',
                'gameType': req_type,
                'huntId': '',
                'playerHuntId': req_player_hunt,
                'requestedHunts': [],
                'gameSessionId': secrets.token_hex(16),
                'started': time.time(),
            }
            self.candidates[acc['account_id']] = cand
            log.info('matchmaking %s: asked %s %s (playerHuntId=%s) -> CITY',
                     acc['display_name'], req_mode, req_type, req_player_hunt or '-')
            return web.json_response({'candidateId': cand['candidateId'], 'gameMode': cand['gameMode'],
                                      'huntId': cand['huntId'], 'status': 'MATCHING', 'statusReason': None})

        if req_mode not in ('ISLAND', 'CITY'):
            return web.json_response({'error': f'unsupported gameMode: {req_mode!r}'}, status=400)

        if raw_hunts is not None:
            if not isinstance(raw_hunts, list) or not raw_hunts or not all(isinstance(h, str) and h for h in raw_hunts):
                return web.json_response({'error': 'hunts must be a non-empty list of hunt IDs'}, status=400)
            requested_hunts = list(raw_hunts)
        elif isinstance(single_hunt, str) and single_hunt:
            requested_hunts = [single_hunt]
        elif forced_hunt:
            requested_hunts = [forced_hunt]
        elif req_type == 'FTUE' and os.environ.get('PHOENIX_FTUE_HUNT_ID', '').strip():
            requested_hunts = [os.environ['PHOENIX_FTUE_HUNT_ID'].strip()]
        else:
            return web.json_response({'error': 'ISLAND join requires hunts or huntId'}, status=400)

        map_hint = body.get('huntMap')
        for hunt_id in requested_hunts:
            inst = self.island_instances.get(hunt_id)
            effective_hint = map_hint or (inst['welcomeMap'] if inst else None)
            try:
                self.select_hunt(hunt_id, effective_hint)
            except KeyError as exc:
                return web.json_response({'error': str(exc)}, status=404)
            except ValueError as exc:
                return web.json_response({'error': str(exc)}, status=400)

        chosen_hunt = next((h for h in requested_hunts if h in self.island_instances), requested_hunts[0])
        player_hunt_id = body.get('playerHuntId') if isinstance(body.get('playerHuntId'), str) and body.get('playerHuntId') else chosen_hunt
        cand = {
            'candidateId': 'local$' + secrets.token_hex(16),
            'gameMode': 'ISLAND',
            'gameType': req_type,
            'huntId': chosen_hunt,
            'playerHuntId': player_hunt_id,
            'requestedHunts': requested_hunts,
            'gameSessionId': secrets.token_hex(16),
            'started': time.time(),
        }
        self.candidates[acc['account_id']] = cand
        log.info('matchmaking %s: asked %s %s (%s) -> ISLAND %s (available=%s)',
                 acc['display_name'], req_mode, req_type, ','.join(requested_hunts),
                 chosen_hunt, chosen_hunt in self.island_instances)
        return web.json_response({'candidateId': cand['candidateId'], 'gameMode': cand['gameMode'],
                                  'huntId': cand['huntId'], 'status': 'MATCHING', 'statusReason': None})

    async def status(self, request):
        acc = self.routes.require(request)
        cand = self.candidates.get(acc['account_id'])
        if cand is None:
            return web.json_response({'error': 'no candidate'}, status=404)
        inst = self._resolve_candidate_instance(cand)
        duration = int(time.time() - cand['started'])
        if inst is None:
            # Do not mark a candidate IN_PROGRESS merely because a map exists in the catalog.
            return web.json_response({
                'candidateId': cand['candidateId'],
                'candidateStatusPeriodMillis': 1000,
                'gameMode': cand['gameMode'],
                'huntId': cand['huntId'],
                'playerStates': {acc['account_id']: {}},
                'status': 'MATCHING',
                'statusDuration': duration,
                'statusReason': 'NO_AVAILABLE_INSTANCE',
            })
        return web.json_response({
            'candidateId': cand['candidateId'],
            'candidateStatusPeriodMillis': 1000,
            'gameMode': cand['gameMode'],
            'huntId': cand['huntId'],
            'playerStates': {acc['account_id']: {}},
            # serverInfo.map is the travel URL; ?bUseIPSockets puts the client's net driver on the plain IP path
            # and verify_travel_url ensures its map path matches the GameServer's NMT_Welcome map.
            'serverInfo': {'buildId': SERVER_BUILD_ID, 'gameSessionId': cand['gameSessionId'],
                           'host': inst['host'], 'port': inst['port'], 'map': inst['map']},
            'status': 'IN_PROGRESS', 'statusDuration': duration, 'statusReason': None})

    async def key_generate(self, request):
        acc = self.routes.require(request)
        cand = self.candidates.get(acc['account_id'])
        if cand is not None and self._resolve_candidate_instance(cand) is None:
            return web.json_response({'error': 'candidate has no available server instance'}, status=409)
        cand = cand or {'gameSessionId': secrets.token_hex(16)}
        session = self.tokens.mint('sessionid', 6 * 3600, acc=acc['account_id'], sub=acc['account_id'])
        token = '%s=%s' % (session, cand['gameSessionId'])
        key = secrets.token_hex(32)
        self.session_keys[token] = key
        nonce = secrets.token_urlsafe(32)
        return web.json_response({'key': key, 'nonce': nonce, 'token': token})

    async def key_consume(self, request):
        if not self.is_gameserver(request):
            raise web.HTTPForbidden()
        body = await self.routes.body(request)
        key = self.session_keys.pop(body.get('token', ''), None)
        if key is None:
            return web.json_response({'error': 'unknown token'}, status=404)
        return web.json_response({'key': key})

    # -- game-server API (and the client's own reads) -------------------------------------------------------------
    async def get_inventory(self, request):
        acc = self.account_for(request, request.match_info['account'])
        ch = self.character_for(acc, request.match_info['character'])
        return web.json_response(self.inventory(ch['character_id']))

    async def post_inventory(self, request):
        if not self.is_gameserver(request):
            raise web.HTTPForbidden()
        body = await self.routes.body(request)
        acc = self.account_for(request, body.get('accountId'))
        ch = self.character_for(acc, body.get('characterId'))
        self.ensure_starter(ch['character_id'])
        result = self.db.apply_transaction(ch['character_id'], body.get('transactionId'),
                                           body.get('addInstancedItems') or [], body.get('addStackedItems') or [],
                                           body.get('removeStackedItems') or [], body.get('removeInstancedItems') or [])
        if result.startswith('insufficient'):
            return web.json_response({'error': result}, status=409)
        return web.json_response(self.inventory(ch['character_id']))

    def _appearance(self, ch):
        try:
            data = json.loads(ch['data'])
            app = data.get('AppearanceData')
            app = json.loads(app) if isinstance(app, str) else app
        except (TypeError, ValueError):
            app = None
        app = app if isinstance(app, dict) and app.get('StringData') else default_appearance()
        if os.environ.get('PHOENIX_SKIP_FTUE', '1') == '1':
            app = dict(app, CreationState='EArchonCharacterCreationState::FaceComplete')
        return json.dumps(app, separators=(',', ':'))

    async def get_loadouts(self, request):
        acc = self.account_for(request, request.match_info['account'])
        ch = self.character_for(acc, request.match_info['character'])
        self.ensure_starter(ch['character_id'])
        persistent = self.db.get(ch['character_id'], 'persistent') or starter_persistent()
        appearance = self._appearance(ch)
        loadouts = []
        for slot in self.db.loadouts(ch['character_id']):
            slot = dict(slot)
            slot.setdefault('flask', '')
            slot.setdefault('quick_items', [])
            slot['persistent'] = persistent
            if not slot.get('appearance'):
                slot['appearance'] = appearance
            loadouts.append(slot)
        return envelope({'loadouts': loadouts, 'persistent': persistent, 'num_account_slots': 1, 'max_account_slots': 1,
                         'num_character_slots': max(0, len(loadouts) - 1), 'max_character_slots': 5,
                         'active_index': 0, 'needs_migration': False})

    async def post_loadout(self, request):
        acc = self.account_for(request, request.match_info['account'])
        ch = self.character_for(acc, request.match_info['character'])
        body = await self.routes.body(request)
        data = body.get('data')
        slot = json.loads(data) if isinstance(data, str) else data
        if not isinstance(slot, dict):
            return web.json_response({'error': 'data must be a loadout object'}, status=400)
        self.db.save_loadout(ch['character_id'], int(request.match_info['index']), slot)
        return await self.get_loadouts(request)

    async def post_persistent(self, request):
        acc = self.account_for(request, request.match_info['account'])
        ch = self.character_for(acc, request.match_info['character'])
        body = await self.routes.body(request)
        data = body.get('data')
        data = json.loads(data) if isinstance(data, str) else data
        if not isinstance(data, dict):
            return web.json_response({'error': 'data must be an object'}, status=400)
        self.db.put(ch['character_id'], 'persistent', data)
        return await self.get_loadouts(request)

    async def get_character_profile(self, request):
        acc = self.account_for(request, request.match_info['account'])
        ch = self.character_for(acc, request.match_info['character'])
        out = self.routes._character(ch, acc)
        if self.is_gameserver(request) and os.environ.get('PHOENIX_SKIP_FTUE', '1') == '1':
            out['data'] = complete_creation(out['data'])
        return web.json_response(out)

    def track_ids(self):
        if not hasattr(self, '_tracks'):
            ids_, cfg = set(), {}
            for path in (os.path.join(self.data_dir, 'progression_config.json'),
                         os.path.join(HERE, '..', 'GameServer', 'data', '2.1.1', 'progression.json')):
                if os.path.exists(path):
                    with open(path, encoding='utf-8-sig') as f:
                        cfg = json.load(f)
                    break
            for p in (cfg.get('payload') or cfg).get('paths') or []:
                if p.get('progression_id'):
                    ids_.add(p['progression_id'])
            ids_.add(HUNTPASS_SEASON)
            self._tracks = sorted(ids_)
        return self._tracks

    async def get_progression(self, request):
        acc = self.account_for(request, request.match_info.get('account') or None)
        have = self.db.progression(acc['account_id'])
        confirmed = self.db.get(acc['account_id'], 'confirmed_ranks', {})
        now = iso(time.time())
        rows = [{'phx_account_id': acc['account_id'], 'progression_id': k, 'progress': have.get(k, 0),
                 'confirmed_fremium_rank': confirmed.get(k, [0, 0])[0], 'confirmed_premium_rank': confirmed.get(k, [0, 0])[1],
                 'confirmed_date': now}
                for k in sorted(set(self.track_ids()) | set(have))]
        return envelope(rows)

    async def confirm_ranks(self, request):
        """Game server, after granting a track's rewards: {free_rank, premium_rank} become the confirmed ranks
        (never lowered, so a repeated claim cannot grant the same ranks twice)."""
        if not self.is_gameserver(request):
            raise web.HTTPForbidden()
        acc = self.account_for(request, request.match_info['account'])
        body = await self.routes.body(request)
        track = request.match_info['track']
        confirmed = self.db.get(acc['account_id'], 'confirmed_ranks', {})
        cur = confirmed.get(track, [0, 0])
        try:
            new = [max(cur[0], int(body.get('free_rank', 0))), max(cur[1], int(body.get('premium_rank', 0)))]
        except (TypeError, ValueError, AttributeError):
            return web.json_response({'error': 'free_rank / premium_rank must be integers'}, status=400)
        confirmed[track] = new
        self.db.put(acc['account_id'], 'confirmed_ranks', confirmed)
        return envelope({'progression_id': track, 'confirmed_fremium_rank': new[0], 'confirmed_premium_rank': new[1]})

    async def grant_progression(self, request):
        if not self.is_gameserver(request):
            raise web.HTTPForbidden()
        acc = self.account_for(request, request.match_info['account'])
        amount = int(request.match_info['amount'])
        if not 0 < amount <= 100000:
            return web.json_response({'error': 'amount out of range'}, status=400)
        body = await self.routes.body(request)
        tx_id = request.headers.get('x-transaction-id') or (body.get('transactionId') if isinstance(body, dict) else None)
        total = self.db.add_progress(acc['account_id'], request.match_info['track'], amount, tx_id=tx_id)
        return envelope({'progression_id': request.match_info['track'], 'progress': total})

    # -- weapon talents (game server only) ---------------------------------------------------------------------------
    # Our own store, not the game's: {weapon instance id: {talent id: {'unlocked': [perk index], 'equipped': [perk index]}}}.
    # The game keeps talents in the weapon item's itemData in a native format not read yet; the game server restores
    # this state on join with the game's own ClientSetWeaponTalentPerk*State RPCs instead.
    async def talents_get(self, request):
        if not self.is_gameserver(request):
            raise web.HTTPForbidden()
        acc = self.account_for(request, request.match_info['account'])
        ch = self.character_for(acc, request.match_info['character'])
        return envelope(self.db.get(ch['character_id'], 'weapon_talents', {}))

    async def talents_post(self, request):
        if not self.is_gameserver(request):
            raise web.HTTPForbidden()
        acc = self.account_for(request, request.match_info['account'])
        ch = self.character_for(acc, request.match_info['character'])
        body = await self.routes.body(request)
        if not isinstance(body, dict):
            return web.json_response({'error': 'body must be an object'}, status=400)
        self.db.put(ch['character_id'], 'weapon_talents', body)
        return envelope(body)

    async def itemdata_post(self, request):
        """Game server: set instanced items' itemData strings ({instanceId: str | null}). The game's own server does this
        with SetItemData; a weapon's itemData carries its talents and item level (GameServer/mpgs/talents.py)."""
        if not self.is_gameserver(request):
            raise web.HTTPForbidden()
        acc = self.account_for(request, request.match_info['account'])
        ch = self.character_for(acc, request.match_info['character'])
        body = await self.routes.body(request)
        if not isinstance(body, dict) or not all(isinstance(v, (str, type(None))) for v in body.values()):
            return web.json_response({'error': 'body must map instance ids to strings'}, status=400)
        n = self.db.set_item_data(ch['character_id'], body)
        return envelope({'updated': n})

    async def progression_config(self, request):
        for path in (os.path.join(self.data_dir, 'progression_config.json'),
                         os.path.join(HERE, '..', 'GameServer', 'data', '2.1.1', 'progression.json')):
            if os.path.exists(path):
                with open(path, encoding='utf-8-sig') as f:
                    data = json.load(f)
                return web.json_response(data if 'payload' in data else {'code': None, 'message': 'OK', 'payload': data})
        return envelope({'paths': []})

    # Every account: escalations unlocked, and the Awakening (D24) migration marked complete. These are the two the
    # Ramsgate template's PlayerState carries; a new account without them never finishes loading.
    ENTITLEMENTS = ('ent_escalations_unlocked', 'ent_migration_d24_complete')

    async def entitlements(self, request):
        return web.json_response({'entitlements': [{'activatedDate': None, 'duration': 0, 'name': n}
                                                   for n in self.ENTITLEMENTS]})

    # -- client: account data and tuning ---------------------------------------------------------------------------
    async def balance(self, request):
        return web.json_response(self.balances(self.routes.require(request)))

    async def reconcile(self, request):
        return web.json_response({'balances': self.balances(self.routes.require(request)), 'refreshInventory': True})

    async def objectives(self, request):
        self.routes.require(request)
        return envelope([])

    async def tracked_get(self, request):
        acc = self.routes.require(request)
        return envelope(self.db.get(acc['account_id'], 'tracked', {
            'current_set': '', 'omitted_quests': [], 'phx_account_id': acc['account_id'],
            'tracked_craftables': [], 'tracked_quests': []}))

    async def tracked_post(self, request):
        acc = self.routes.require(request)
        body = await self.routes.body(request)
        if isinstance(body, dict):
            cur = self.db.get(acc['account_id'], 'tracked', {})
            cur.update({k: v for k, v in body.items() if k != 'phx_account_id'}, phx_account_id=acc['account_id'])
            self.db.put(acc['account_id'], 'tracked', cur)
        return envelope(None)

    def journey_nodes(self, acc):
        saved = self.db.get(acc['account_id'], 'journey', {})
        return {n: {'node_id': n, 'node_status': st, 'objectives': []} for n, st in sorted(saved.items())}

    async def slayers_path(self, request):
        acc = self.account_for(request, request.match_info.get('account') or None)
        return envelope({'nodes': self.journey_nodes(acc)})

    async def slayers_path_post(self, request):
        """Game server: record a node status {node_id, node_status} after it has checked and charged the unlock."""
        acc = self.account_for(request, request.match_info.get('account') or None)
        body = await self.routes.body(request)
        if self.is_gameserver(request) and isinstance(body, dict) and body.get('node_id'):
            saved = self.db.get(acc['account_id'], 'journey', {})
            saved[str(body['node_id'])] = int(body.get('node_status', 2))
            self.db.put(acc['account_id'], 'journey', saved)
        return envelope({'nodes': self.journey_nodes(acc)})

    async def level_access(self, request):
        self.routes.require(request)
        return envelope(1)

    async def encountered_post(self, request):
        """Client hands in content it has seen {content_type, content_id}; the query returns it grouped by type."""
        acc = self.routes.require(request)
        body = await self.routes.body(request)
        if isinstance(body, dict) and body.get('content_id'):
            key = 'encountered:' + request.match_info['owner']
            seen = self.db.get(acc['account_id'], key, {})
            ids = seen.setdefault(str(int(body.get('content_type') or 0)), [])
            if body['content_id'] not in ids:
                ids.append(str(body['content_id']))
            self.db.put(acc['account_id'], key, seen)
        return envelope({})

    async def ok_empty(self, request):
        return envelope({})

    async def ok_list(self, request):
        return envelope([])

    async def mailbox(self, request):
        return envelope({'messages': [], 'survey': None})

    async def guild(self, request):
        return web.Response(status=204)

    async def guild_invites(self, request):
        return envelope({'invites': []})

    async def party(self, request):
        """A solo party (official shape: Matchmaking/GetParty.md). The party id is <hex>_<base64(buildId)>."""
        import base64
        acc = self.routes.require(request)
        body = await self.routes.body(request)
        build = (body.get('buildId') if isinstance(body, dict) else None) or BUILD_ID
        cand = self.candidates.get(acc['account_id'])
        inst = self._resolve_candidate_instance(cand) if cand else None
        cand_state = ('IN_PROGRESS' if inst is not None else 'MATCHING') if cand else None
        player_hunt_id = (cand.get('playerHuntId') or cand.get('huntId') or None) if cand else None
        party_id = self.db.get(acc['account_id'], 'party_id')
        if not party_id:
            party_id = '%s_%s' % (secrets.token_hex(16), base64.b64encode(build.encode()).decode())
            self.db.put(acc['account_id'], 'party_id', party_id)
        return web.json_response({
            'candidateId': cand['candidateId'] if cand else None, 'candidateState': cand_state,
            'gauntletLevel': None, 'leaderPlayerId': acc['account_id'], 'partyId': party_id,
            'playerHuntId': player_hunt_id,
            'playerStates': [{'consoleSessionId': None, 'displayName': acc['display_name'],
                              'isMemberOfCandidate': cand is not None, 'platform': 'win', 'playerId': acc['account_id']}]})

    async def party_invites(self, request):
        return web.json_response({'invitations': []})

    async def breadcrumbs(self, request):
        return envelope({'breadcrumbs': [], 'updateVersion': 0})

    async def encountered(self, request):
        acc = self.routes.require(request)
        seen = self.db.get(acc['account_id'], 'encountered:' + request.match_info['account'], {})
        return envelope({'content_types': [{'content_type': int(t), 'content': sorted(c)} for t, c in sorted(seen.items())],
                         'success': True})

    async def survey(self, request):
        return web.json_response({'Triggers': []})

    async def trials(self, request):
        return envelope({'difficulty': 1, 'guild': {}, 'page': 0, 'page_size': 100, 'trial_id': '',
                         'world': {'group': {'difficulty': 1, 'entries': []},
                                   'solo': {'all': {'difficulty': 1, 'entries': []}}}})

    async def skus(self, request):
        return web.json_response([])

    @staticmethod
    def tuning(payload):
        async def handler(request):
            return envelope(payload)
        return handler

    @staticmethod
    def bounty_tuning(token, slots, bronze, silver, gold, daily):
        payload = {'bounty_data': [], 'bounty_token_grant_hour': 0, 'bounty_token_id': token, 'bronze_count': bronze,
                   'silver_count': silver, 'gold_count': gold, 'history_length': 10, 'item_grant_data': [],
                   'max_slots': slots, 'new_season_reset_bounties': daily, 'num_draft_options': 3,
                   'num_spicy_options': 1, 'num_tokens_hp_start': slots, 'num_tokens_per_day': 0,
                   'premium_bounty_token_id': token + '_PREMIUM', 'token_rollover_warning_days': 1000}
        if daily:
            payload.update(automatic_claim=True, automatic_draft=True, delete_claimed_bounties=False)

        async def handler(request):
            return envelope(payload)
        return handler

    async def game_snapshot(self, request):
        return envelope({})

    async def huntpass(self, request):
        return envelope(HUNTPASS_SEASON)

    async def bounty(self, request):
        draft = lambda: {'current_draft_choices': [], 'previous_draft_selections': [], 'bronze_count': 0,
                         'silver_count': 0, 'gold_count': 0}
        return envelope({'season_start_date': '2020-08-23T00:00:00.000Z', 'season_end_date': '2099-01-01T00:00:00.000Z',
                         'bounties': [], 'draft_data': draft(), 'draft_data_daily': draft(), 'draft_data_weekly': draft()})

    def add_to(self, app):
        r = app.router
        get, post, put, delete = r.add_get, r.add_post, r.add_put, r.add_delete
        get('/instances', self.instances_get)
        post('/instances/register', self.instances_register)
        post('/instances/unregister', self.instances_unregister)
        get('/candidate/regions', self.regions)
        post('/candidate/join', self.join)
        get('/candidate/status', self.status)
        get('/key/generate', self.key_generate)
        post('/key/generate', self.key_generate)
        post('/key/consume', self.key_consume)
        get('/inventory/{account}/{character}', self.get_inventory)
        post('/inventory', self.post_inventory)
        get('/loadout/{account}/{character}/all', self.get_loadouts)
        post('/loadout/{account}/{character}/{index:\\d+}', self.post_loadout)
        post('/loadout/{account}/{character}/persistent', self.post_persistent)
        get('/character/{account}/{character}', self.get_character_profile)
        post('/itemdata/{account}/{character}', self.itemdata_post)
        get('/talents/{account}/{character}', self.talents_get)
        post('/talents/{account}/{character}', self.talents_post)
        get('/progression/config', self.progression_config)
        get('/progression/objectives/{account}', self.objectives)
        get('/progression/tracked_objectives/{account}', self.tracked_get)
        post('/progression/tracked_objectives/{account}', self.tracked_post)
        post('/progression/{account}/{track}/confirm', self.confirm_ranks)
        post('/progression/{account}/{track}/{amount:\\d+}', self.grant_progression)
        get('/progression/{account}', self.get_progression)
        get('/progression//{account}', self.get_progression)
        get('/entitlementsv2', self.entitlements)
        get('/balance', self.balance)
        post('/reconcile', self.reconcile)
        get('/pjm', self.slayers_path)
        get('/pjm/', self.slayers_path)
        get('/pjm/{account}', self.slayers_path)
        post('/pjm', self.slayers_path_post)
        post('/pjm/', self.slayers_path_post)
        post('/pjm/{account}', self.slayers_path_post)
        get('/progression/level_access/{season}', self.level_access)
        post('/encountered-content/{owner}', self.encountered_post)
        # seen from clients / older game servers; nothing to track yet
        for path in ('/escalation/{season}', '/escalation/{season}/{account}', '/playertreatments/',
                     '/playertreatments/{account}', '/tracker/{account}', '/rewards/personal/{a}/', '/rewards/personal/{a}/{b}',
                     '/rewards/guild/{a}/', '/rewards/guild/{a}/{b}', '/eventstats/'):
            get(path, self.ok_empty)
        get('/leaderboard/get_leaderboard/', self.ok_list)
        put('/cooldown/batch/', self.ok_empty)
        put('/cooldown/batch/{account}', self.ok_empty)
        post('/telemetry/data', self.ok_empty)
        get('/all/', self.mailbox)
        get('/guild', self.guild)
        get('/guild/invite/player', self.guild_invites)
        post('/party', self.party)
        get('/party/invites', self.party_invites)
        delete('/party/member', self.routes.empty_object)
        get('/breadcrumbs/{account}', self.breadcrumbs)
        post('/breadcrumbs/{account}', self.routes.empty_object)
        post('/encountered-content/query/{account}', self.encountered)
        get('/survey/config', self.survey)
        post('/trials/leaderboards/all', self.trials)
        get('/product/skus/public', self.skus)
        get('/game_tuning/huntpass_xp_config', self.tuning({'EventConfigs': [], 'GlobalConfig': {
            'DifficultyBias': 1, 'GlobalMultiplier': 1, 'MaxXPAwarded': 200}}))
        get('/game_tuning/seasonal_event_schedule', self.tuning({'ScheduledItems': []}))
        get('/game_tuning/island_content_config', self.tuning({'DisallowedIslandContentAssets': []}))
        get('/game_tuning/bounty_game_data', self.bounty_tuning('TOKEN_BOUNTY_DRAFT', 4, 9, 3, 1, False))
        get('/game_tuning/bounty_game_data_daily', self.bounty_tuning('TOKEN_DAILY_CHALLENGE_DRAFT', 1, 1, 0, 0, True))
        get('/game_tuning/bounty_game_data_weekly', self.bounty_tuning('TOKEN_WEEKLY_CHALLENGE_DRAFT', 1, 1, 0, 0, True))
        get('/bounty/{account}', self.bounty)
        get('/cooldown/{account}', self.game_snapshot)
        get('/huntpass/{account}', self.huntpass)
