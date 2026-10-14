"""UDP game server loop: stateless handshake -> encrypted control channel -> world."""
import select
import socket
import time
import os

from . import backend, control, handshake
from .connection import Connection

MAP_PATH = '/Game/Maps/ramsgate/ramsgate_01_persistent'
GAME_MODE_PATH = '/Game/Blueprints/GameMode/BPGM_City.BPGM_City_C'


def log(*a):
    print('[mpgs %s]' % time.strftime('%H:%M:%S'), *a, flush=True)


def _short(v, limit=400):
    """Readable RPC parameters for the log (path references shown by name)."""
    def conv(x):
        if isinstance(x, tuple) and x and x[0] == 'objpath':
            return 'path:%s' % x[3]
        if isinstance(x, dict):
            return {k: conv(y) for k, y in x.items()}
        if isinstance(x, list):
            return [conv(y) for y in x]
        return x
    t = str(conv(v))
    return t if len(t) <= limit else t[:limit] + '...'


class GameServer:
    def __init__(self, port, world):
        self.port = port
        self.world = world
        self.t0 = time.monotonic()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(('0.0.0.0', port))
        self.sock.setblocking(False)
        self.pending = {}        # addr -> cookie issued by our challenge
        self.conns = {}          # addr -> Connection
        self.sessions = {}       # addr -> dict(join_time, ids, welcomed ...)

    # ------------------------------------------------------------------ helpers
    def send(self, addr, datagrams):
        for d in datagrams:
            try:
                self.sock.sendto(d, addr)
            except OSError:
                pass

    # ------------------------------------------------------------------ handshake
    def on_handshake(self, addr, hs):
        t = hs['type']
        if t == handshake.INITIAL:
            if addr in self.conns and self.conns[addr].stats['rx'] > 0:
                return            # established connection: a stray initial packet; ignore
            cookie = handshake.new_cookie()
            self.pending[addr] = cookie
            self.send(addr, [handshake.build_challenge(hs, cookie, time.monotonic() - self.t0)])
            log('handshake: challenge ->', addr, 'client id', hs['cid'], 'netcl %#x' % hs['netcl'])
        elif t == handshake.RESPONSE:
            cookie = self.pending.get(addr)
            if cookie is None or hs['cookie'] != cookie:
                return
            if addr not in self.conns or self.conns[addr].stats['rx'] == 0:
                self.conns[addr] = Connection(addr, hs['sid'], hs['cid'], cookie, hs['netcl'])
                self.sessions[addr] = {'state': 'handshaken', 'join_t': None}
                s, c = handshake.sequences(cookie)
                log('handshake: ack ->', addr, 'server seq', s, 'client seq', c)
            self.send(addr, [handshake.build_ack(hs)])

    # ------------------------------------------------------------------ control channel
    def on_control(self, addr, conn, sess, b):
        t = control.message_type(b)
        if t == control.NMT_HELLO and sess['state'] == 'handshaken':
            token = control.hello_token(b)
            key = backend.consume_key(token)
            conn.queue_bunches(control.encryption_ack())
            self.send(addr, conn.pump())             # EncryptionAck goes out unencrypted
            conn.enable_encryption(key)
            conn.queue_bunches(control.challenge())
            sess['state'] = 'challenged'
            log('NMT_Hello: key fetched, encryption on, Challenge sent')
        elif t == control.NMT_LOGIN and sess['state'] == 'challenged':
            sess['ids'], sess['auth_token'] = control.login_options_with_auth(b)
            for i in (0, 1):
                conn.queue_bunches(control.game_specific(i))
            conn.queue_bunches(control.welcome(MAP_PATH, GAME_MODE_PATH))
            session_ids = {**sess['ids'], 'auth_token': sess['auth_token']}
            sess['world'] = self.world.new_session(session_ids) if self.world else None
            sess['state'] = 'welcomed'
            log('NMT_Login: %s -> Welcome sent' % sess['ids']['name'])
        elif t == control.NMT_JOIN and sess['state'] == 'welcomed':
            sess['state'] = 'joined'
            sess['join_t'] = time.monotonic()
            log('NMT_Join: world starts')
        elif t not in (control.NMT_NETSPEED,):
            log('control message', t, '(%d bits)' % b.nbits, b.payload()[:64].hex() if b.nbits else '', repr(b.payload()[:64]) if b.nbits else '')

    # ------------------------------------------------------------------ client -> server actor traffic
    def on_actor_bunch(self, addr, sess, b):
        w = sess.get('world')
        if os.environ.get('MPGS_TRACE_ACTOR_RX') == '1':
            n = sess['_actor_trace_count'] = sess.get('_actor_trace_count', 0) + 1
            if n <= 120 or n % 250 == 0:
                joined = sess.get('join_t')
                age = (time.monotonic() - joined) if joined is not None else -1.0
                log('client actor bunch #%d age=%.3f ch=%d bits=%d reliable=%s seq=%s open=%s close=%s' % (
                    n, age, b.ch, b.nbits, b.reliable, b.chseq, b.open, b.close))
        if w is None or not hasattr(w, 'on_client_bunch'):
            return
        for ch, cls, who, fields in w.on_client_bunch(b):
            for name, kind, payload in fields:
                if name == 'ServerMovePacked' and kind == 'raw':
                    mv = w.on_move(payload)
                    n = sess['moves'] = sess.get('moves', 0) + 1
                    if mv and (mv['flags'] or mv['mode'] != 1 or abs(mv['vel'][2]) > 100) and sess.setdefault('odd_moves', 0) < 80:       # jump / non-walking moves (diagnosis)
                        sess['odd_moves'] += 1
                        log('move* %s #%d loc=(%.1f,%.1f,%.1f) vel=(%.0f,%.0f,%.0f) mode=%s flags=%#x accel=(%.0f,%.0f,%.0f)' % (
                            sess['ids']['name'], n, *mv['loc'], *mv['vel'], mv['mode'], mv['flags'], *mv['accel']))
                    if mv and (n <= 3 or n % 600 == 0):
                        log('move %s #%d t=%.3f loc=(%.1f,%.1f,%.1f) vel=(%.0f,%.0f,%.0f) yaw=%.1f mode=%s flags=%#x' % (
                            sess['ids']['name'], n, mv['time'], *mv['loc'], *mv['vel'], mv['ctrl_rot'][1], mv['mode'], mv['flags']))
                key = (ch, who, name)
                seen = sess.setdefault('rx_seen', {})
                seen[key] = seen.get(key, 0) + 1
                if seen[key] <= 3 or seen[key] % 200 == 0 or (name != 'ServerMovePacked' and seen[key] <= 50):
                    nb = payload[1] if kind == 'raw' else '-'
                    extra = (' ' + _short(payload)) if kind == 'rpc' else ''
                    log('client->server %s ch%d %s: %s (%s bits) #%d%s' % (sess['ids']['name'], ch, who, name, nb, seen[key], extra))
            if not fields:
                key = (ch, who, 'block')
                seen = sess.setdefault('rx_seen', {})
                seen[key] = seen.get(key, 0) + 1
                if seen[key] <= 2:
                    log('client->server %s ch%d %s (%s): %s' % (sess['ids']['name'], ch, who, cls, 'no fields'))

    # ------------------------------------------------------------------ main loop
    def run(self):
        log('listening on udp', self.port)
        last_stat = time.monotonic()
        while True:
            r, _, _ = select.select([self.sock], [], [], 0.004)
            now = time.monotonic()
            while r:
                try:
                    raw, addr = self.sock.recvfrom(4096)
                except (BlockingIOError, ConnectionResetError):
                    break
                hs = handshake.parse(raw)
                if hs is not None:
                    self.on_handshake(addr, hs)
                    continue
                conn = self.conns.get(addr)
                if conn is None:
                    continue
                for b in conn.on_datagram(raw, now):
                    if b.ch == 0:
                        if b.nbits < 8 or b.close:
                            log('control bunch detail', addr, 'bits', b.nbits, 'type', control.message_type(b),
                                'reliable', b.reliable, 'seq', b.chseq, 'open', b.open, 'close', b.close,
                                'reason', b.close_reason, 'control', b.control, 'payload', b.payload().hex())
                        if b.close:
                            # Client closed its control channel (disconnect, or travel such
                            # as starting a hunt): drop now instead of resending until timeout.
                            conn.closed_by_client = True
                            continue
                        self.on_control(addr, conn, self.sessions[addr], b)
                    else:
                        self.on_actor_bunch(addr, self.sessions[addr], b)
            # players see each other: every joined connection gets the simulated-proxy view of every other joined connection (once)
            joined = [(a, c, self.sessions[a]) for a, c in self.conns.items() if self.sessions[a]['state'] == 'joined' and self.sessions[a].get('world')]
            for a, c, sa in joined:
                for b2, c2, sb in joined:
                    if a == b2 or id(sb['world']) in sa.setdefault('seen', {}):
                        continue
                    slot = len(sa['seen'])
                    sa['seen'][id(sb['world'])] = sb['world']
                    sa['world'].add_remote(sb['world'], slot, (now - sa['join_t']) * 1000)
                    log('player %s now sees player %s (slot %d)' % (sa['ids']['name'], sb['ids']['name'], slot))
            # relay each player's movement to everyone who sees them (~15 Hz, unreliable)
            if now - getattr(self, '_last_relay', 0) >= 0.066:
                self._last_relay = now
                for a, c, sa in joined:
                    for b2, c2, sb in joined:
                        if a != b2 and id(sb['world']) in sa.get('seen', {}) and sb['world'].pawn_state is not None:
                            ub = sa['world'].remote_update(sb['world'])
                            c.queue_bunches(ub)
                            c.queue_bunches(sa['world'].remote_state(sb['world']))
                            st_ = sa.setdefault('relay_stat', [0, 0])
                            st_[0 if ub else 1] += 1
                            if st_[0] in (1, 2, 3, 100, 1000) and ub and st_[0] != sa.get('relay_logged'):
                                sa['relay_logged'] = st_[0]
                                log('relay %s <- %s: update #%d %d bunch(es) loc=%s mode=%s' % (sa['ids']['name'], sb['ids']['name'], st_[0], len(ub), [round(v, 1) for v in sb['world'].pawn_state['loc']], sb['world'].pawn_state['mode']))
            for addr, conn in list(self.conns.items()):
                sess = self.sessions[addr]
                w = sess.get('world')
                if sess['state'] == 'joined' and w and not w.finished:
                    conn.queue_bunches(w.due((now - sess['join_t']) * 1000))
                    if w.finished:
                        log('recorded world delivered to', addr, '(%d identity patches)' % w.patch_hits)
                self.send(addr, conn.pump(now))
                closed = getattr(conn, 'closed_by_client', False)
                if closed or now - conn.last_rx > 30:
                    log('connection closed by client' if closed else 'connection timed out', addr, conn.stats)
                    gone = self.sessions[addr].get('world')
                    del self.conns[addr], self.sessions[addr]
                    for a2, c2 in self.conns.items():       # remove the departed player from everybody else's world
                        s2 = self.sessions[a2]
                        if gone is not None and s2.get('world') is not None and id(gone) in s2.get('seen', {}):
                            c2.queue_bunches(s2['world'].close_remote(gone))
                            s2['seen'].pop(id(gone), None)
            if now - last_stat > 10:
                last_stat = now
                for addr, conn in self.conns.items():
                    log('stats', addr, self.sessions[addr]['state'], conn.stats, self.sessions[addr].get('relay_stat'))
