"""Session base: per-connection cursor over the world's events, identity handling and client RPC labelling.
GenWorld / GenSession (genworld.py) build on it."""
import math
import re
import sys
import os

from . import crypto, wire
from .control import message_type, login_options, NMT_JOIN, NMT_LOGIN



def records(recording):
    raise NotImplementedError('worlds are built from data/2.1.1/ramsgate_world.json')


def patch_bits(data: int, nbits: int, pairs):
    """Replace same-length byte strings inside a bit string at ANY bit offset. Returns (data, hits)."""
    hits = 0
    nbytes = (nbits + 7) // 8 + 1
    for old, new in pairs:
        for sft in range(8):
            while True:
                sh = (data >> sft).to_bytes(nbytes, 'little')
                i = sh.find(old)
                if i < 0:
                    break
                pos = i * 8 + sft
                mask = ((1 << (8 * len(old))) - 1) << pos
                data = (data & ~mask) | (int.from_bytes(new, 'little') << pos)
                hits += 1
                if old == new:
                    break
    return data, hits


class ReplayWorld:
    def __init__(self, recording, recorded_key_hex):
        key = bytes.fromhex(recorded_key_hex)
        self.events = []                  # (t_ms relative to the recorded Join, Bunch)
        join_t = None
        recs = list(records(recording))
        self.old_ids = None
        seen = set()
        for r in recs:
            if r['kind'] == 2:
                res = crypto.decrypt(r['pay'], key)
                if not res:
                    continue
                pkt = wire.parse_packet(res[1], res[2])
                for b in pkt['bunches']:
                    if b.ch == 0:
                        t = message_type(b)
                        if t == NMT_LOGIN and self.old_ids is None:
                            self.old_ids = login_options(b)
                        if t == NMT_JOIN and join_t is None:
                            join_t = r['tick']
        t0 = join_t if join_t is not None else recs[0]['tick']
        for r in recs:
            if r['kind'] != 3:
                continue
            res = crypto.decrypt(r['pay'], key)
            if not res:
                continue
            for b in wire.parse_packet(res[1], res[2])['bunches']:
                if b.ch == 0:
                    continue
                if b.reliable:
                    k = (b.ch, b.chseq)
                    if k in seen:
                        continue
                    seen.add(k)
                self.events.append((max(0, r['tick'] - t0), b))
        self.events.sort(key=lambda e: e[0])
        # Optional read-only client RPC decoder for raw-replay diagnostics.
        # It never changes the recorded server bunches or the wire behavior.
        self.rx_decoder = _RawReplayRxDecoder(self.events) if os.environ.get('MPGS_TRACE_ACTOR_RPC') == '1' else None

    def new_session(self, live_ids):
        """Per-connection cursor over the shared recorded events (a second player must get the world too)."""
        return ReplaySession(self, live_ids)


class ReplaySession:
    def __init__(self, world, live_ids):
        self.world = world
        self.pos = 0
        self.patch_hits = 0
        self.pairs = []
        self._prev_move = None
        self.pawn_state = None
        self.rx_decoder = world.rx_decoder.new_session() if world.rx_decoder else None
        for k in ('name', 'char', 'acct'):
            if world.old_ids[k] != live_ids[k]:
                # Raw replay cannot resize an FString in-place. Replace only
                # byte strings of equal length; leave other captured identity
                # fields intact so a diagnostic replay can still run.
                if len(world.old_ids[k]) == len(live_ids[k]):
                    self.pairs.append((world.old_ids[k].encode(), live_ids[k].encode()))

    def on_client_bunch(self, bunch):
        """Optionally decode client RPC names while leaving raw replay behavior unchanged."""
        if self.rx_decoder is None:
            return []
        return self.rx_decoder.on_client_bunch(bunch)

    def on_move(self, payload):
        """Decode movement for server diagnostics; raw replay still sends no corrections."""
        from .repl import moves

        current = moves.parse_server_move_packed(*payload)
        if not current:
            return None
        previous = self._prev_move
        velocity = [0.0, 0.0, 0.0]
        if previous is not None:
            dt = current['time'] - previous['time']
            if 0.001 < dt < 0.5:
                velocity = [(current['loc'][i] - previous['loc'][i]) / dt for i in range(3)]
        if current['mode'] == 1 and abs(velocity[2]) > 60.0:
            current['mode'] = 3
        current['vel'] = velocity
        self._prev_move = current
        self.pawn_state = current
        return current

    def due(self, elapsed_ms):
        out = []
        ev = self.world.events
        while self.pos < len(ev) and ev[self.pos][0] <= elapsed_ms:
            b = ev[self.pos][1].copy()
            b.chseq = None                      # the connection assigns its own reliable sequence
            if self.pairs:
                b.data, hits = patch_bits(b.data, b.nbits, self.pairs)
                self.patch_hits += hits
            out.append(b)
            self.pos += 1
        return out

    @property
    def finished(self):
        return self.pos >= len(self.world.events)


class _RawReplayRxDecoder:
    """Build a schema/name map from captured server bunches to label client RPCs."""
    def __init__(self, events):
        from .repl import model
        from .repl.chan import Reassembler
        from .repl.guid import flatten
        from .repl.schema import Schema

        self.model = model
        self.Reassembler = Reassembler
        self.flatten = flatten
        self.schema = Schema()
        self.names = {}
        self.chan_cls = {}
        self.events = events
        self._build_server_context()

    def _build_server_context(self):
        from .repl.chan import Reassembler

        class Ctx:
            def __init__(inner, schema, names):
                inner.schema, inner._names = schema, names
            def known(inner):
                return inner._names
            def names(inner, guid):
                return inner._names[guid]
            def comp_class(inner, actor_cls, guid):
                pair = inner._names.get(guid)
                return inner.schema.member_class(actor_cls, pair[1]) if pair else None

        ctx = Ctx(self.schema, self.names)
        chans = {}
        for _, bunch in self.events:
            full = chans.setdefault(bunch.ch, Reassembler()).feed(bunch)
            if not full:
                continue
            for root in full.exports:
                for item in self.flatten(root):
                    if item.name is not None:
                        self.names[item.guid] = (item.outer, item.name)
            parsed, cls = self.model.parse(full, ctx, self.chan_cls.get(bunch.ch))
            self.chan_cls[bunch.ch] = cls
        print('RAW-RX decoder ready: %d names, %d actor channels' % (len(self.names), len(self.chan_cls)), flush=True)

    def new_session(self):
        return _RawReplayRxSession(self)


class _RawReplayRxSession:
    def __init__(self, world):
        from .repl.chan import Reassembler

        self.world = world
        self.ctx_names = dict(world.names)
        self.reassemblers = {}

        class Ctx:
            def __init__(inner, schema, names):
                inner.schema, inner._names = schema, names
            def known(inner):
                return inner._names
            def names(inner, guid):
                return inner._names[guid]
            def comp_class(inner, actor_cls, guid):
                pair = inner._names.get(guid)
                return inner.schema.member_class(actor_cls, pair[1]) if pair else None

        self.ctx = Ctx(world.schema, self.ctx_names)

    def on_client_bunch(self, bunch):
        full = self.reassemblers.setdefault(bunch.ch, self.world.Reassembler()).feed(bunch)
        if not full:
            return []
        for root in full.exports:
            for item in self.world.flatten(root):
                if item.name is not None:
                    self.ctx_names[item.guid] = (item.outer, item.name)
        try:
            parsed, _ = self.world.model.parse(full, self.ctx, self.world.chan_cls.get(bunch.ch), from_client=True)
        except Exception as exc:
            return [(bunch.ch, self.world.chan_cls.get(bunch.ch), 'unparsed %s' % type(exc).__name__, [])]
        out = []
        for block in parsed.blocks:
            who = 'actor' if block.actor else self.ctx_names.get(block.guid, (0, 'guid%s' % block.guid))[1]
            fields = [] if not block.fields else [(entry[1], entry[2], entry[3]) for entry in block.fields]
            out.append((bunch.ch, block.cls_name, who, fields))
        return out
