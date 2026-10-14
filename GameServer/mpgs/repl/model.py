"""Parsed form of one complete actor-channel bunch, and its exact re-encoding.

A ParsedBunch round-trips bit-exactly: ``encode(parse(b)) == b``.  Property blocks are held as python values (decoded
with schema.Layout) plus an opaque tail (custom-delta / RPC section, not yet modelled)."""
import re

from ..bitio import BitReader, BitWriter
from .chan import read_new_actor, read_content_block
from .decode import Decoder
from .encode import Encoder
from .guid import is_dynamic
from .quant import write_quantized_vector, write_rotator, write_int
from . import fastarray
from . import rpc
from .chan import read_field_entries


class Block:
    __slots__ = ('actor', 'rep', 'guid', 'stable', 'destroy', 'cls', 'outer', 'cls_name', 'values', 'tail', 'raw', 'fields', 'partial_properties', 'ref', 'ref_name')

    def __init__(self):
        # Generated blocks represent complete property lists by default. The
        # parser marks a block partial only when it encounters an unknown tail.
        self.partial_properties = False

    def __repr__(self):
        return 'Block(%s guid=%s cls=%s rep=%d values=%s tail=%s)' % (
            'actor' if self.actor else 'sub', self.guid, self.cls_name, self.rep,
            None if self.values is None else len(self.values), None if self.tail is None else self.tail[1])


class Parsed:
    def __init__(self):
        self.must_map = []
        self.open = None          # dict from read_new_actor (+ 'extra')
        self.blocks = []


def _cond_vec(w, v, default, scale):
    if v == default or v is None:
        w.bit(0)
        return
    w.bit(1)
    w.bit(1)
    write_quantized_vector(w, scale, v)


def write_new_actor(w, a):
    w.write_packed(a['guid'])
    if is_dynamic(a['guid']):
        w.write_packed(a['archetype'])
        w.write_packed(a['level'])
        _cond_vec(w, a['loc'], [0, 0, 0], 10)
        rot = a['rot']
        if any(rot):
            w.bit(1)
            write_rotator(w, rot)
        else:
            w.bit(0)
        _cond_vec(w, a['scale'], [1, 1, 1], 10)
        _cond_vec(w, a['vel'], [0, 0, 0], 10)


def _parse_fields(schema, cls, tail):
    """Tail -> [(field index, kind, payload)]: kind 'fast' (decoded fast array dict) or 'raw' ((bits, nbits)); None if the tail cannot be split."""
    if not tail or tail[1] == 0:
        return []
    fi = schema.field_index(cls)
    try:
        ents = read_field_entries(tail[0], tail[1], len(fi))
    except (EOFError, ValueError):
        return None
    out = []
    layout = schema.layout(cls)
    for idx, bits, n in ents:
        name = fi[idx][1] if idx < len(fi) else None
        kind, payload = 'raw', (bits, n)
        if name and fi[idx][0] == 'prop':
            try:
                fa = fastarray.decode_legacy(layout, name, bits, n)
                enc = fastarray.encode_legacy(layout, name, fa)
                if enc.nbits == n and enc.n == bits:
                    kind, payload = 'fast', fa
            except Exception:
                pass
        elif name and fi[idx][0] == 'func':
            params = rpc.try_decode(schema, cls, name, bits, n)   # None unless bit-exact
            if params is not None:
                kind, payload = 'rpc', params
        out.append((idx, name, kind, payload))
    # verify the whole tail re-encodes exactly, otherwise keep it opaque
    w = _encode_fields(schema, cls, out)
    if w.nbits != tail[1] or w.n != tail[0]:
        return None
    return out


def _encode_fields(schema, cls, fields):
    fi = schema.field_index(cls)
    layout = schema.layout(cls)
    w = BitWriter()
    for idx, name, kind, payload in fields:
        write_int(w, idx, len(fi) + 1)
        if kind == 'fast':
            pw = fastarray.encode_legacy(layout, name, payload)
            bits, n = pw.n, pw.nbits
        elif kind == 'rpc':
            pw = rpc.encode(rpc.rpc_layout(schema, cls, name), payload)
            bits, n = pw.n, pw.nbits
        else:
            bits, n = payload
        w.write_packed(n)
        w.write(bits, n)
    return w


def parse(full, ctx, actor_cls, from_client=False):
    """full: chan.Full; ctx: object with .schema, .names(guid)->(outer,name), .comp_class(actor_cls, guid)->class name.
    Returns (Parsed, actor_cls)."""
    p = Parsed()
    r = BitReader(full.bits.to_bytes((full.nbits + 7) // 8, 'little'), full.nbits)
    if full.must_map:
        p.must_map = [r.read_packed() for _ in range(r.read(16))]
    if full.open:
        p.open = read_new_actor(r, None)
        arch = p.open.get('archetype')
        if arch is not None:
            nm = ctx.names(arch)[1]
            actor_cls = nm[len('Default__'):] if nm.startswith('Default__') else nm
        elif actor_cls is None and p.open.get('guid') in ctx.known():
            # Static level actor (no archetype on the wire): its export name
            # is "<Class>_<n>", e.g. WorldSettings_1.
            base = re.sub(r'_\d+$', '', ctx.names(p.open['guid'])[1] or '')
            if base and ctx.schema.class_chain(base):
                actor_cls = base
        if any(c == 'PlayerController' for c in ctx.schema.class_chain(actor_cls)) or actor_cls == 'PlayerController':
            p.open['extra'] = r.read(8)          # APlayerController::OnActorChannelOpen: NetPlayerIndex
    while r.left() > 0:
        raw = read_content_block(r, from_client)
        b = Block()
        b.actor, b.rep, b.guid, b.stable, b.destroy, b.cls, b.outer = raw['actor'], raw['rep'], raw['guid'], raw['stable'], raw['destroy'], raw['cls'], raw['outer']
        b.values = b.tail = b.raw = b.fields = None
        b.partial_properties = False
        b.ref, b.ref_name = raw.get('ref'), raw.get('ref_name')
        if b.actor:
            b.cls_name = actor_cls
        elif b.cls is not None:
            b.cls_name = ctx.names(b.cls)[1] if b.cls in ctx.known() else None
            if b.cls_name:
                b.cls_name = b.cls_name.split('/')[-1]
        elif b.ref_name:                         # sub-object referenced by path
            b.cls_name = ctx.schema.member_class(actor_cls, b.ref_name)
        else:
            b.cls_name = ctx.comp_class(actor_cls, b.guid)
        bits, n = raw['bits'], raw['nbits']
        if b.rep and n and b.cls_name and ctx.schema.class_chain(b.cls_name):
            pr = BitReader(bits.to_bytes((n + 7) // 8, 'little'), n)
            decoder = Decoder(ctx.schema.layout(b.cls_name))
            try:
                b.values = decoder.read_block(pr, allow_bad_root_handle=(b.cls_name == 'player_state_bp_C'))
                tail_pos = decoder.stop_pos if decoder.stop_pos is not None else pr.pos
                b.tail = ((bits >> tail_pos), n - tail_pos)
                # On the one partially-known PlayerState layout, the bytes
                # after the valid prefix may use a different property map.
                # Keep the whole suffix opaque so re-encoding stays exact.
                b.fields = None if decoder.stop_pos is not None else _parse_fields(ctx.schema, b.cls_name, b.tail)
                b.partial_properties = decoder.stop_pos is not None
            except Exception:
                b.values = None
        if b.values is None and not b.rep and n and b.cls_name and ctx.schema.class_chain(b.cls_name):
            b.fields = _parse_fields(ctx.schema, b.cls_name, (bits, n))     # RPC-only block: [field index][numbits][params]*
        if b.values is None and b.fields is None:
            b.raw = (bits, n)
        p.blocks.append(b)
    return p, actor_cls


def encode(p, ctx, actor_cls):
    w = BitWriter()
    if p.must_map:
        w.write(len(p.must_map), 16)
        for g in p.must_map:
            w.write_packed(g)
    if p.open:
        write_new_actor(w, p.open)
        if 'extra' in p.open:
            w.write(p.open['extra'], 8)
    for b in p.blocks:
        w.bit(b.rep)
        w.bit(b.actor)
        if not b.actor:
            if getattr(b, 'ref', None) is not None and b.guid == 1:    # path reference: resend as written
                w.write(*b.ref)
            else:
                w.write_packed(b.guid)
            w.bit(b.stable)
            if not b.stable:
                if b.destroy:
                    w.bit(1)
                    w.write(0, 8)
                else:
                    w.bit(0)
                    w.write_packed(b.cls)
                if b.outer is None:
                    w.bit(1)
                else:
                    w.bit(0)
                    w.write_packed(b.outer)
        if b.destroy:
            continue
        if b.values is not None:
            pw = Encoder(ctx.schema.layout(b.cls_name)).write_block(
                b.values, terminate=not b.partial_properties)
            bits, n = pw.n, pw.nbits
            if b.fields is not None:
                tw = _encode_fields(ctx.schema, b.cls_name, b.fields)
                bits |= tw.n << n
                n += tw.nbits
            elif b.tail:
                bits |= b.tail[0] << n
                n += b.tail[1]
        elif b.fields is not None:
            tw = _encode_fields(ctx.schema, b.cls_name, b.fields)
            bits, n = tw.n, tw.nbits
        else:
            bits, n = b.raw
        w.write_packed(n)
        w.write(bits, n)
    return w
