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
CITY_MAP = '/Game/Maps/ramsgate/ramsgate_01_persistent?bUseIPSockets'
HUNTPASS_SEASON = 'season19'

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
        for name, catalog in STARTER:
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

    # -- matchmaking -----------------------------------------------------------------------------------------------
    async def regions(self, request):
        self.routes.require(request)
        return web.json_response({'code': 200, 'message': 'success', 'payload': {
            'maxPingingStepTime': 3, 'pingCount': 5, 'pingFrequency': 0.25,
            'regionUrls': ['http://127.0.0.1:3000/QoS']}})

    async def join(self, request):
        acc = self.routes.require(request)
        body = await self.routes.body(request)
        # The game server hosts Ramsgate only. Island requests (the new-player FTUE island, hunts) are routed to
        # Ramsgate as CITY: an ISLAND session makes the stock client require an anti-cheat session
        # (ControlChannelEasyAntiCheatSessionFail) that a CITY session does not.
        cand = {'candidateId': 'local$' + secrets.token_hex(16), 'gameMode': 'CITY', 'huntId': '',
                'gameSessionId': secrets.token_hex(16), 'started': time.time()}
        self.candidates[acc['account_id']] = cand
        log.info('matchmaking %s: asked %s %s -> CITY', acc['display_name'], body.get('gameMode'), body.get('gameType'))
        return web.json_response({'candidateId': cand['candidateId'], 'gameMode': cand['gameMode'],
                                  'huntId': cand['huntId'], 'status': 'MATCHING', 'statusReason': None})

    async def status(self, request):
        acc = self.routes.require(request)
        cand = self.candidates.get(acc['account_id'])
        if cand is None:
            return web.json_response({'error': 'no candidate'}, status=404)
        return web.json_response({
            'candidateId': cand['candidateId'], 'candidateStatusPeriodMillis': 1000, 'gameMode': cand['gameMode'],
            'huntId': cand['huntId'], 'playerStates': {acc['account_id']: {}},
            # serverInfo.map is the travel URL; ?bUseIPSockets puts the client's net driver on the plain IP path
            # (without it the stock client fails its anti-cheat session and never finishes loading).
            'serverInfo': {'buildId': SERVER_BUILD_ID, 'gameSessionId': cand['gameSessionId'], 'host': self.game_host,
                           'port': self.game_port, 'map': CITY_MAP},
            'status': 'IN_PROGRESS', 'statusDuration': int(time.time() - cand['started']), 'statusReason': None})

    async def key_generate(self, request):
        acc = self.routes.require(request)
        cand = self.candidates.get(acc['account_id']) or {'gameSessionId': secrets.token_hex(16)}
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
            for path in (os.path.join(self.data_dir, 'progression_config.json'),):
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
        total = self.db.add_progress(acc['account_id'], request.match_info['track'], amount)
        return envelope({'progression_id': request.match_info['track'], 'progress': total})

    async def progression_config(self, request):
        for path in (os.path.join(self.data_dir, 'progression_config.json'),):
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
        party_id = self.db.get(acc['account_id'], 'party_id')
        if not party_id:
            party_id = '%s_%s' % (secrets.token_hex(16), base64.b64encode(build.encode()).decode())
            self.db.put(acc['account_id'], 'party_id', party_id)
        return web.json_response({
            'candidateId': cand['candidateId'] if cand else None, 'candidateState': 'IN_PROGRESS' if cand else None,
            'gauntletLevel': None, 'leaderPlayerId': acc['account_id'], 'partyId': party_id,
            'playerHuntId': (cand['huntId'] or None) if cand else None,
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
