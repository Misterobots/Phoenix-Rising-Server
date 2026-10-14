"""HTTP routes for sign-in through the main menu: launcher codes, status, EOS/Epic sign-in, game login, characters.
Unknown routes are logged and answered 404 so gaps are visible."""
import base64
import json
import logging
import secrets
import time
from datetime import datetime, timezone

from aiohttp import web

from . import eos_config

log = logging.getLogger('phoenix')

DEPLOYMENT_ID = '53565ba467df4edbb6f5a3d939a8b4f2'
CLIENT_ID = '12c4279862ab4460a25c2e9fa535fb7e'
PRODUCT_ID = 'prod-jackal'
SANDBOX_ID = 'jackal'
SCOPE = 'basic_profile friends_list country openid presence'
EOS_TTL = 2 * 3600
SESSION_TTL = 3 * 24 * 3600
RELEASE = '2.1.1'


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.') + '%03dZ' % int(ts % 1 * 1000)


def envelope(payload):
    """The {code, message, payload} wrapper several Dauntless services use."""
    return web.json_response({'code': None, 'message': 'OK', 'payload': payload})


def short_date(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime('%b %d, %Y').replace(' 0', ' ')


class Routes:
    def __init__(self, db, tokens):
        self.db, self.tokens = db, tokens

    # -- helpers -----------------------------------------------------------------------------------------------
    def account_from(self, request):
        """The caller's account from its bearer token (EOS or game-session token), else None."""
        claims = self.tokens.from_request(request)
        if not claims:
            return None
        if claims.get('acc'):
            return self.db.account(claims['acc'])
        if claims.get('epic'):
            return self.db.account_by_epic(claims['epic'])
        return None

    def require(self, request):
        acc = self.account_from(request)
        if acc is None:
            raise web.HTTPUnauthorized(text=json.dumps({'error': 'unauthorized'}), content_type='application/json')
        return acc

    async def form(self, request):
        try:
            return dict(await request.post())
        except Exception:
            return {}

    async def body(self, request):
        try:
            return await request.json()
        except Exception:
            return {}

    def eos_token(self, acc, kind='eos'):
        return self.tokens.mint(kind, EOS_TTL, epic=acc['epic_id'], acc=acc['account_id'])

    # -- launcher ---------------------------------------------------------------------------------------------
    async def local_profiles(self, request):
        body = await self.body(request)
        name = str(body.get('displayName') or '').strip()
        if not name or len(name) > 32:
            return web.json_response({'error': 'displayName required (1-32 characters)'}, status=400)
        acc = self.db.account_by_name(name)
        if acc is not None and not body.get('reuse'):
            return web.json_response({'error': 'display name taken; pass "reuse": true to sign in as it'}, status=409)
        acc = acc or self.db.create_account(name)
        code = self.db.new_exchange_code(acc['account_id'])
        log.info('local profile %s (%s): exchange code issued', name, acc['account_id'])
        return web.json_response({'exchangeCode': code, 'accountId': acc['account_id'], 'displayName': name}, status=201)

    # -- status / config (no auth) -----------------------------------------------------------------------------
    async def status(self, request):
        return web.json_response({'show-status': False})

    # Login screen status line. Keeping this credit is a condition of the licence (LICENSE).
    LOGIN_STATUS = 'Dauntless - Phoenix Rising private server, by Misterobots and The Undaunted Team.'

    async def login_status(self, request):
        return web.json_response(dict({'show-status': True},
                                      **{lang: self.LOGIN_STATUS for lang in ('en', 'fr', 'it', 'es', 'de', 'pt', 'ru', 'ja')}))

    async def features(self, request):
        return envelope({'crossplay': True, 'crossprogression': True})

    async def patchnotes(self, request):
        return envelope({'date': iso(time.time()), 'description': '', 'language': request.match_info.get('lang', 'en'),
                         'notes': [], 'permalink': '', 'release_version': RELEASE, 'title': ''})

    async def qos(self, request):
        return web.Response(text='')

    async def sdk_config(self, request):
        return web.json_response(eos_config.product_config())

    # -- EOS / Epic sign-in --------------------------------------------------------------------------------------
    async def client_credentials(self, request):
        """EOS auth: client credentials, or a Connect login (grant_type=external_auth with the Epic token) that
        returns the product user id the anti-cheat session needs (ControlChannelEasyAntiCheatSessionFail without it)."""
        now = time.time()
        form = await self.form(request)
        if form.get('external_auth_token'):
            claims = self.tokens.read(form['external_auth_token'])
            acc = self.db.account_by_epic(claims['epic']) if claims and claims.get('epic') else None
            if acc is None:
                return web.json_response({'errorCode': 'errors.com.epicgames.eos.auth.invalid_token',
                                          'errorMessage': 'external auth token is not valid'}, status=400)
            return web.json_response({
                'access_token': self.tokens.mint('connect', 3600, epic=acc['epic_id'], acc=acc['account_id'], puid=acc['puid']),
                'token_type': 'bearer', 'expires_at': iso(now + 3600), 'features': ['AntiCheat', 'Connect', 'Ecom', 'Voice'],
                'organization_id': 'o-local', 'product_id': PRODUCT_ID, 'sandbox_id': SANDBOX_ID,
                'deployment_id': DEPLOYMENT_ID, 'expires_in': 3599,
                'refresh_token': self.tokens.mint('refresh', 8 * 3600, epic=acc['epic_id']),
                'refresh_expires_at': iso(now + 8 * 3600), 'account_id': acc['epic_id'],
                'product_user_id': acc['puid'], 'nonce': form.get('nonce', '')})
        return web.json_response({
            'access_token': self.tokens.mint('client', 3600, client=CLIENT_ID), 'token_type': 'bearer',
            'expires_at': iso(now + 3600), 'features': ['Achievements', 'AntiCheat', 'Ecom', 'Voice'],
            'organization_id': 'o-local', 'product_id': PRODUCT_ID, 'sandbox_id': SANDBOX_ID,
            'deployment_id': DEPLOYMENT_ID, 'expires_in': 3599})

    def _resolve_grant(self, form):
        grant = form.get('grant_type')
        if grant == 'exchange_code':
            account_id = self.db.take_exchange_code(form.get('exchange_code', ''))
            return self.db.account(account_id) if account_id else None
        if grant == 'refresh_token':
            claims = self.tokens.read(form.get('refresh_token', ''))
            return self.db.account_by_epic(claims['epic']) if claims and claims.get('epic') else None
        if grant in ('external_auth', 'token_to_token', 'eg1'):
            claims = self.tokens.read(form.get('token', '') or form.get('external_auth_token', ''))
            return self.db.account_by_epic(claims['epic']) if claims and claims.get('epic') else None
        return None

    async def epic_token(self, request):
        form = await self.form(request)
        acc = self._resolve_grant(form)
        if acc is None:
            return web.json_response({'error': 'invalid_grant', 'error_description': 'grant is not valid'}, status=400)
        now = time.time()
        return web.json_response({
            'access_token': self.eos_token(acc), 'token_type': 'bearer', 'expires_in': EOS_TTL - 1,
            'expires_at': iso(now + EOS_TTL), 'refresh_token': self.tokens.mint('refresh', 8 * 3600, epic=acc['epic_id']),
            'refresh_expires_in': 8 * 3600, 'refresh_expires_at': iso(now + 8 * 3600), 'account_id': acc['epic_id'],
            'client_id': CLIENT_ID, 'application_id': 'local', 'selected_account_id': acc['epic_id'],
            'scope': SCOPE, 'acr': 'urn:epic:loa:aal1', 'auth_time': iso(now)})

    async def account_token(self, request):
        form = await self.form(request)
        acc = self._resolve_grant(form)
        if acc is None:
            return web.json_response({'errorCode': 'errors.com.epicgames.account.auth_token.invalid_refresh_token',
                                      'errorMessage': 'invalid grant'}, status=400)
        now = time.time()
        return web.json_response({
            'access_token': self.eos_token(acc, 'account'), 'expires_in': EOS_TTL - 1, 'expires_at': iso(now + EOS_TTL),
            'token_type': 'bearer', 'refresh_token': self.tokens.mint('refresh', 8 * 3600, epic=acc['epic_id']),
            'refresh_expires': 8 * 3600, 'refresh_expires_at': iso(now + 8 * 3600), 'account_id': acc['epic_id'],
            'client_id': CLIENT_ID, 'internal_client': False, 'client_service': PRODUCT_ID,
            'displayName': acc['display_name'], 'app': PRODUCT_ID, 'in_app_id': acc['epic_id'],
            'product_id': PRODUCT_ID, 'application_id': 'local'})

    async def token_info(self, request):
        form = await self.form(request)
        claims = self.tokens.read(form.get('token', ''))
        if not claims:
            return web.json_response({'active': False})
        return web.json_response({'active': True, 'scope': SCOPE, 'token_type': 'bearer',
                                  'expires_in': int(claims['exp'] - time.time()), 'expires_at': iso(claims['exp']),
                                  'account_id': claims.get('epic'), 'client_id': CLIENT_ID, 'application_id': 'local'})

    async def verify(self, request):
        acc = self.require(request)
        claims = self.tokens.from_request(request)
        token = request.headers['Authorization'].split(None, 1)[1]
        return web.json_response({
            'token': token, 'session_id': claims.get('jti'), 'token_type': 'bearer', 'client_id': CLIENT_ID,
            'internal_client': False, 'client_service': PRODUCT_ID, 'account_id': acc['epic_id'],
            'expires_in': int(claims['exp'] - time.time()), 'expires_at': iso(claims['exp']), 'auth_method': 'exchange_code',
            'display_name': acc['display_name'], 'app': PRODUCT_ID, 'in_app_id': acc['epic_id'],
            'scope': SCOPE.split(), 'product_id': PRODUCT_ID, 'sandbox_id': SANDBOX_ID, 'deployment_id': DEPLOYMENT_ID,
            'application_id': 'local', 'acr': 'urn:epic:loa:aal1', 'auth_time': iso(claims['iat'])})

    async def no_content(self, request):
        return web.Response(status=204)

    async def empty_object(self, request):
        return web.json_response({})

    async def empty_list(self, request):
        return web.json_response([])

    async def epic_accounts(self, request):
        out = []
        for epic in request.query.getall('accountId', []):
            acc = self.db.account_by_epic(epic)
            if acc:
                out.append({'accountId': epic, 'displayName': acc['display_name'], 'preferredLanguage': 'en',
                            'linkedAccounts': []})
        return web.json_response(out)

    async def friends(self, request):
        return web.json_response({'friends': [], 'blockList': []})

    async def turn_credentials(self, request):
        acc = self.require(request)
        return web.json_response({'username': '%d:%s' % (int(time.time()) + 3600, acc['puid']),
                                  'password': base64.b64encode(secrets.token_bytes(20)).decode(),
                                  'ttl': 3600000, 'uris': []})

    def _public_account(self, acc):
        return {'id': acc['epic_id'], 'displayName': acc['display_name'], 'externalAuths': {}}

    async def public_account(self, request):
        acc = self.db.account_by_epic(request.match_info['epic'])
        if acc is None:
            return web.json_response({'errorCode': 'errors.com.epicgames.account.account_not_found'}, status=404)
        return web.json_response(self._public_account(acc))

    async def public_accounts(self, request):
        accs = [self.db.account_by_epic(e) for e in request.query.getall('accountId', [])]
        return web.json_response([self._public_account(a) for a in accs if a])

    # -- game login ----------------------------------------------------------------------------------------------
    async def login_queue(self, request):
        return web.json_response({'error_code': 'TicketRateOk', 'message': '', 'state': 'OPEN', 'timeout': 8000, 'title': ''})

    async def game_session(self, request):
        acc = self.require(request)
        sid = self.tokens.mint('sessionid', SESSION_TTL, acc=acc['account_id'], platform='win')
        token = self.tokens.mint('session', SESSION_TTL, sub=acc['account_id'], acc=acc['account_id'],
                                 epic=acc['epic_id'], roles='player')
        log.info('game session for %s (%s)', acc['display_name'], acc['account_id'])
        return envelope({'error_code': None, 'sessionid': sid, 'sessiontoken': token})

    async def epic_link(self, request):
        return envelope({'isLinked': True})

    async def account_info(self, request):
        acc = self.require(request)
        return web.json_response({'accountId': acc['account_id'],
                                  'creationDate': datetime.fromtimestamp(acc['created'], timezone.utc).strftime('%Y-%m-%d 00:00:00'),
                                  'email': None, 'preferredLanguage': None, 'username': None, 'verified': False})

    async def tags(self, request):
        acc = self.require(request)
        return web.json_response({'accountId': acc['account_id'], 'tags': []})

    async def is_banned(self, request):
        return web.json_response({'isBanned': False})

    async def account_info_public(self, request):
        body = await self.body(request)
        acc = self.db.account(body.get('accountId', '')) or self.require(request)
        return web.json_response({'accountId': acc['account_id'], 'isSubscribed': False, 'language': None,
                                  'linkedAccounts': [{'accountId': acc['epic_id'], 'accountType': 'epic'}],
                                  'username': acc['display_name']})

    async def migration(self, request):
        return web.json_response({'migration_failed': False, 'migration_finished': True})

    async def inventory_migration(self, request):
        return web.json_response({'code': 'NONE', 'message': ''})

    async def creator(self, request):
        return web.json_response({'expirationDate': '2099-01-01T00:00:00.000Z', 'slug': '', 'success': True})

    # -- characters ----------------------------------------------------------------------------------------------
    def _character(self, row, acc):
        return {'accountId': acc['account_id'], 'catalogDaoId': None, 'createdDate': short_date(row['created']),
                'data': row['data'], 'id': row['character_id'], 'lastModifiedDate': short_date(time.time()),
                'name': acc['display_name'], 'updateVersion': row['update_version']}

    async def get_characters(self, request):
        acc = self.require(request)
        return web.json_response([self._character(r, acc) for r in self.db.characters(acc['account_id'])])

    async def post_character(self, request):
        from . import ids
        body = await self.body(request)
        gs = getattr(self, 'is_gameserver', None)
        if gs is not None and gs(request) and body.get('accountId'):
            acc = self.db.account(body['accountId'])           # the game server saves on the player's behalf
            if acc is None:
                return web.json_response({'error': 'unknown account'}, status=404)
        else:
            acc = self.require(request)
        cid = body.get('characterId') or ids.base32_id()
        cur = self.db.character(cid)
        if cur is not None and cur['account_id'] != acc['account_id']:
            return web.json_response({'error': 'not your character'}, status=403)
        want = body.get('updateVersion')
        if cur is not None and want is not None and int(want) != cur['update_version'] + 1:
            return web.Response(status=409, text='Conflict')
        row = self.db.save_character(acc['account_id'], cid, body.get('data') if isinstance(body.get('data'), str)
                                     else json.dumps(body.get('data') or {}))
        return web.json_response(self._character(row, acc))

    async def put_character(self, request):
        """New-account character creation (the client PUTs /character when GET returned [])."""
        from . import ids
        acc = self.require(request)
        body = await self.body(request)
        log.info('PUT /character body keys=%s', sorted(body) if isinstance(body, dict) else type(body).__name__)
        body = body if isinstance(body, dict) else {}
        existing = self.db.characters(acc['account_id'])
        cid = body.get('characterId') or (existing[0]['character_id'] if existing else ids.base32_id())
        data = body.get('data')
        row = self.db.save_character(acc['account_id'], cid, data if isinstance(data, str) else json.dumps(data or {}))
        return web.json_response(self._character(row, acc))

    # -- telemetry -----------------------------------------------------------------------------------------------
    async def heartbeat(self, request):
        return web.json_response(20000)

    # -- fallback ------------------------------------------------------------------------------------------------
    async def unhandled(self, request):
        log.warning('UNHANDLED %s %s (bearer=%s)', request.method, request.path_qs[:160],
                    'Y' if request.headers.get('Authorization') else 'N')
        return web.json_response({'error': 'not implemented', 'path': request.path}, status=404)

    def add_to(self, app):
        r = app.router
        get, post, put, delete = r.add_get, r.add_post, r.add_put, r.add_delete
        post('/launcher/v1/local-profiles', self.local_profiles)
        get('/dauntless-status', self.login_status)
        get('/maintenance/status', self.status)
        get('/features/platform/{platform}', self.features)
        get('/patchnotes/{lang}/{build}', self.patchnotes)
        get('/patchnotes/{lang}', self.patchnotes)
        get('/QoS', self.qos)
        get('/sdk/v1/default', self.sdk_config)
        get('/sdk/v1/product/{product}', self.sdk_config)
        post('/auth/v1/oauth/token', self.client_credentials)
        post('/epic/oauth/v2/token', self.epic_token)
        post('/account/api/oauth/token', self.account_token)
        post('/epic/oauth/v2/tokenInfo', self.token_info)
        get('/account/api/oauth/verify', self.verify)
        post('/epic/oauth/v2/revoke', self.no_content)
        delete('/account/api/oauth/sessions/kill', self.empty_object)
        delete('/account/api/oauth/sessions/kill/{token}', self.empty_object)
        get('/epic/id/v2/sdk/accounts', self.epic_accounts)
        get('/epic/friends/v1/{epic}', self.friends)
        get('/epic/friends/v1/{epic}/blocklist', self.empty_list)
        post('/auth/v1/turn/credentials', self.turn_credentials)
        get('/account/api/public/account', self.public_accounts)
        get('/account/api/public/account/{epic}', self.public_account)
        get('/account/api/public/account/{epic}/externalAuths', self.empty_list)
        get('/stats/v1/{deployment}/achievements/{puid}', self.empty_list)
        post('/login', self.login_queue)
        put('/gamesession/{service}', self.game_session)
        get('/account/link/epic/{epic}', self.epic_link)
        get('/accountinfo', self.account_info)
        post('/accountinfo/public', self.account_info_public)
        get('/tags', self.tags)
        get('/isbanned', self.is_banned)
        post('/profile/update', self.empty_object)
        post('/migration/trigger', self.migration)
        post('/inventory/{character}/{patch}', self.inventory_migration)
        get('/creator', self.creator)
        post('/candidate/player/register', self.empty_object)
        get('/character', self.get_characters)
        post('/character', self.post_character)
        put('/character', self.put_character)
        post('/heartbeat', self.heartbeat)
        post('/event', self.empty_object)
        post('/telemetry/data/datarouter/api/v1/public/data', self.no_content)
        post('/datarouter/api/v1/public/data/clients', self.empty_object)
