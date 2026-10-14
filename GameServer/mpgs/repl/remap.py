"""Per-player instantiation of the world model: GUID remapping (dynamic GUIDs are per-actor/per-subobject and must be unique across
players) and structured string edits (identity). Static GUIDs (odd: level objects, CDOs, classes, paths) are never touched."""
import copy

from ..bitio import BitReader, BitWriter
from .decode import OrderedFields

# Legacy path for RPC payloads that stayed opaque (kind 'raw'): RPCs whose FIRST parameter is an object reference.
RPC_FIRST_OBJECT = {'ClientRestart', 'ClientRetryClientRestart', 'ClientSetViewTarget'}
GUID_KEYS = ('instigator', 'causer', 'ability_cdo', 'source')      # GameplayEffectContext fields (plain ints)


class GuidMap:
    """Allocates fresh even (dynamic) GUIDs; `shift` mode offsets every dynamic GUID (used to prove the remap is complete)."""

    def __init__(self, start=None, shift=None):
        self.t, self.next, self.shift = {}, start, shift

    def __call__(self, g):
        if g is None or g <= 1 or g & 1:
            return g
        if self.shift is not None:
            return g + self.shift
        v = self.t.get(g)
        if v is None:
            v = self.t[g] = self.next
            self.next += 2
        return v


def remap_value(v, m):
    if isinstance(v, tuple):
        if len(v) == 2 and v[0] in ('obj', 'softobj') and isinstance(v[1], int):
            return (v[0], m(v[1]))
        return tuple(remap_value(x, m) for x in v)
    if isinstance(v, list):
        return [remap_value(x, m) for x in v]
    if isinstance(v, dict):
        out = OrderedFields() if isinstance(v, OrderedFields) else {}
        for k, x in v.items():
            if k in GUID_KEYS and isinstance(x, int) and 'rep' in v:
                out[k] = m(x)
            else:
                out[k] = remap_value(x, m)
        if isinstance(out, OrderedFields):
            out.order = list(v.order)
        return out
    return v


def edit_strings(v, pairs):
    """Replace substrings in every str inside nested values; returns (new value, number of replaced strings)."""
    n = 0

    def rec(x):
        nonlocal n
        if isinstance(x, str):
            y = x
            for old, new in pairs:
                y = y.replace(old, new)
            if y != x:
                n += 1
            return y
        if isinstance(x, tuple):
            return tuple(rec(i) for i in x)
        if isinstance(x, list):
            return [rec(i) for i in x]
        if isinstance(x, dict):
            out = OrderedFields() if isinstance(x, OrderedFields) else {}
            for k, i in x.items():
                out[k] = rec(i)
            if isinstance(out, OrderedFields):
                out.order = list(x.order)
            return out
        return x
    return rec(v), n


def _remap_rpc_payload(name, payload, m):
    if name not in RPC_FIRST_OBJECT:
        return payload
    bits, n = payload
    r = BitReader(bits.to_bytes((n + 7) // 8, 'little'), n)
    if not r.bit():
        return payload
    g = r.read_packed()
    rest, rn = bits >> r.pos, n - r.pos
    w = BitWriter()
    w.bit(1)
    w.write_packed(m(g))
    w.write(rest, rn)
    return (w.n, w.nbits)


def _remap_info(info, m):
    cur = info
    while cur is not None:
        cur.guid = m(cur.guid)
        cur = cur.outer_info


def instantiate(entries, m, pairs=()):
    """Deep-copy world entries (genworld.Entry list) with remapped GUIDs and edited strings. -> (entries, stats)."""
    stats = {'strings': 0, 'rpc': 0}
    out = []
    for e in entries:
        ne = copy.copy(e)
        p = copy.deepcopy(e.parsed)
        p.must_map = [m(g) for g in p.must_map]
        if p.open:
            p.open['guid'] = m(p.open['guid'])
        for b in p.blocks:
            if b.guid is not None:
                b.guid = m(b.guid)
            if b.outer is not None:
                b.outer = m(b.outer)
            if b.values is not None:
                b.values = remap_value(b.values, m)
                if pairs:
                    b.values, k = edit_strings(b.values, pairs)
                    stats['strings'] += k
            if b.fields is not None:
                nf = []
                for idx, name, kind, payload in b.fields:
                    if kind in ('fast', 'rpc'):
                        # Decoded payloads (fast arrays, RPC parameters from
                        # repl.rpc): every object reference and string is reachable.
                        before = payload
                        payload = remap_value(payload, m)
                        if kind == 'rpc':
                            stats['rpc'] += payload != before
                        if pairs:
                            payload, k = edit_strings(payload, pairs)
                            stats['strings'] += k
                    else:
                        np_ = _remap_rpc_payload(name, payload, m)
                        stats['rpc'] += np_ != payload
                        payload = np_
                    nf.append((idx, name, kind, payload))
                b.fields = nf
        ne.parsed = p
        ne.exports = copy.deepcopy(e.exports)
        for root in ne.exports:
            _remap_info(root, m)
        out.append(ne)
    return out, stats
