"""XMPP over WebSocket (RFC 7395) for the client's Epic social connection: stream open, SASL PLAIN with the
client's bearer token, resource bind, and answers to iq requests (ping, session). Presence and messages are
accepted and ignored until social features are implemented."""
import base64
import logging
import secrets
import xml.etree.ElementTree as ET

from aiohttp import WSMsgType, web

log = logging.getLogger('phoenix.xmpp')

DOMAIN = 'prod.ol.epicgames.com'
FRAMING = 'urn:ietf:params:xml:ns:xmpp-framing'
SASL = 'urn:ietf:params:xml:ns:xmpp-sasl'
BIND = 'urn:ietf:params:xml:ns:xmpp-bind'
STREAM = 'http://etherx.jabber.org/streams'


def local(tag):
    return tag.rsplit('}', 1)[-1]


class XMPP:
    def __init__(self, db, tokens):
        self.db, self.tokens = db, tokens

    async def handler(self, request):
        ws = web.WebSocketResponse(protocols=('xmpp',), heartbeat=None)
        await ws.prepare(request)
        state = {'auth': None, 'jid': None}
        conn = secrets.token_hex(3)
        log.info('xmpp %s connected', conn)

        async def send(xml):
            await ws.send_str(xml)

        async def open_stream():
            await send('<open xmlns="%s" from="%s" id="%s" version="1.0" xml:lang="en"/>' % (FRAMING, DOMAIN, secrets.token_hex(8)))

        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                if msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                    break
                continue
            try:
                el = ET.fromstring(msg.data)
            except ET.ParseError:
                log.warning('xmpp %s unparsable frame: %s', conn, msg.data[:120])
                continue
            tag = local(el.tag)
            if tag == 'open':
                await open_stream()
                if state['auth'] is None:
                    await send('<stream:features xmlns:stream="%s"><mechanisms xmlns="%s"><mechanism>PLAIN</mechanism>'
                               '</mechanisms></stream:features>' % (STREAM, SASL))
                else:
                    await send('<stream:features xmlns:stream="%s"><bind xmlns="%s"/><session xmlns="urn:ietf:params:xml:ns:xmpp-session">'
                               '<optional/></session></stream:features>' % (STREAM, BIND))
            elif tag == 'auth':
                try:
                    _, user, password = base64.b64decode(el.text or '').decode('utf-8', 'replace').split('\0', 2)
                except ValueError:
                    user = password = ''
                claims = self.tokens.read(password)
                acc = self.db.account_by_epic(claims['epic']) if claims and claims.get('epic') else None
                if acc is None:
                    await send('<failure xmlns="%s"><not-authorized/></failure>' % SASL)
                    log.info('xmpp %s auth refused', conn)
                    continue
                state['auth'] = acc
                await send('<success xmlns="%s"/>' % SASL)
                log.info('xmpp %s auth ok %s', conn, acc['display_name'])
            elif tag == 'iq':
                iq_id, iq_type = el.get('id', ''), el.get('type')
                child = el[0] if len(el) else None
                if child is not None and local(child.tag) == 'bind' and state['auth'] is not None:
                    res = child.find('{%s}resource' % BIND)
                    resource = (res.text if res is not None and res.text else 'phoenix')
                    state['jid'] = '%s@%s/%s' % (state['auth']['epic_id'], DOMAIN, resource)
                    await send('<iq xmlns="jabber:client" type="result" id="%s"><bind xmlns="%s"><jid>%s</jid></bind></iq>'
                               % (iq_id, BIND, state['jid']))
                elif iq_type in ('get', 'set'):
                    to = el.get('from') or (state['jid'] or '')
                    await send('<iq xmlns="jabber:client" type="result" id="%s" from="%s" to="%s"/>' % (iq_id, DOMAIN, to))
            elif tag == 'close':
                await send('<close xmlns="%s"/>' % FRAMING)
                break
            # presence / message: accepted, no social features yet
        log.info('xmpp %s closed', conn)
        return ws
