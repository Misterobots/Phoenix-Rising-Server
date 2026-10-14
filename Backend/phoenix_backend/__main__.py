"""python -m phoenix_backend [--data DIR] [--http 3001] [--launcher 3000] [--tls 443]

Port 3001: game services. Port 3000: launcher (exchange codes), QoS and XMPP over WebSocket.
Port 443 (TLS, phoenix_backend/tls.py): the hostnames the hosts file sends here, same routes plus WebSockets.
All ports serve the same HTTP routes (the client and the EOS SDK reach some on either)."""
import argparse
import asyncio
import logging
import os

from aiohttp import web

from .db import DB
from .routes import Routes
from .tokens import Tokens
from .xmpp import XMPP

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@web.middleware
async def trace(request, handler):
    """One line per request: method, path, auth, status (the session trace the spec is checked against)."""
    resp = await handler(request)
    if request.path.strip('/') or request.headers.get('Upgrade'):
        logging.getLogger('phoenix.req').info('%s %s bearer=%s -> %s', request.method, request.path[:120],
                                              'Y' if request.headers.get('Authorization') else 'N', resp.status)
    return resp


async def banner(request):
    return web.Response(text='PhoenixBackend')


async def idle_socket(request):
    """EOS notification / connect sockets nothing is pushed on yet: accept and hold open until the client closes."""
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    async for _ in ws:
        pass
    return ws


def upgrades(xmpp):
    @web.middleware
    async def middleware(request, handler):
        if request.headers.get('Upgrade', '').lower() == 'websocket':
            protos = request.headers.get('Sec-WebSocket-Protocol', '')
            if not request.path.strip('/') or 'xmpp' in protos:
                return await xmpp.handler(request)
            return await idle_socket(request)
        return await handler(request)
    return middleware


def allowed_hosts(hostnames):
    @web.middleware
    async def middleware(request, handler):
        if request.host.split(':')[0].lower() not in hostnames:
            return web.json_response({'error': 'unknown host'}, status=421)
        return await handler(request)
    return middleware


def build_app(db, tokens, with_xmpp, data_dir=None, hostnames=None):
    from .gameplay import Gameplay
    middlewares = [trace]
    if hostnames:
        middlewares.append(allowed_hosts(set(hostnames)))
    if with_xmpp:
        middlewares.append(upgrades(XMPP(db, tokens)))
    app = web.Application(client_max_size=16 * 1024 * 1024, middlewares=middlewares)
    routes = Routes(db, tokens)
    gameplay = Gameplay(db, tokens, routes, data_dir or os.path.join(HERE, 'data'))
    routes.is_gameserver = gameplay.is_gameserver
    app.router.add_get('/', banner)
    routes.add_to(app)
    gameplay.add_to(app)
    app.router.add_get('/{slashes:/+}', banner)   # health probes ("////")
    app.router.add_route('*', '/{tail:.*}', routes.unhandled)
    return app


async def serve(args):
    db = DB(os.path.join(args.data, 'phoenix.sqlite'))
    tokens = Tokens(os.path.join(args.data, 'token.secret'))
    runners = []
    for port, xmpp in ((args.http, False), (args.launcher, True)):
        runner = web.AppRunner(build_app(db, tokens, xmpp, args.data), access_log=None)
        await runner.setup()
        await web.TCPSite(runner, args.host, port).start()
        runners.append(runner)
        logging.getLogger('phoenix').info('listening on %s:%d%s', args.host, port, ' (+ XMPP)' if xmpp else '')
    if args.tls:
        from . import tls
        ctx = tls.server_context(args.cert_dir or os.path.join(args.data, 'tls'))
        runner = web.AppRunner(build_app(db, tokens, True, args.data, tls.HOSTNAMES), access_log=None)
        await runner.setup()
        await web.TCPSite(runner, args.host, args.tls, ssl_context=ctx).start()
        runners.append(runner)
        logging.getLogger('phoenix').info('listening on %s:%d (TLS, %d hostnames)', args.host, args.tls, len(tls.HOSTNAMES))
    await asyncio.Event().wait()


def main():
    ap = argparse.ArgumentParser(description='Dauntless - Phoenix Rising backend')
    ap.add_argument('--data', default=os.path.join(HERE, 'data'))
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--http', type=int, default=3001)
    ap.add_argument('--launcher', type=int, default=3000)
    ap.add_argument('--tls', type=int, default=443, help='HTTPS/WSS port for the client (0: off)')
    ap.add_argument('--cert-dir', default=None, help='certificates (default <data>/tls)')
    ap.add_argument('--log', default='INFO')
    args = ap.parse_args()
    logging.basicConfig(level=args.log, format='[%(asctime)s] %(name)s %(levelname)s %(message)s', datefmt='%H:%M:%S')
    try:
        asyncio.run(serve(args))
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
