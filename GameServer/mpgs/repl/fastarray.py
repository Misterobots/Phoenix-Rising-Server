"""FFastArraySerializer payload (LEGACY item framing is what Dauntless uses: [u32 id][all item properties, no handles]).
FFastArraySerializer payload (FastArrayDeltaSerialize_DeltaSerializeStructs + FRepLayout::DeltaSerializeFastArrayProperty, UE5.1.1).

payload = [i32 ArrayReplicationKey][i32 BaseReplicationKey][i32 NumDeleted][i32 NumChanged][i32 deletedID]*
          { [u32 ReplicationID][bit anything][item property block with handles relative to the item layout, ended by 0] }*
"""
from ..bitio import BitReader, BitWriter
from .decode import Decoder
from .encode import Encoder


def _i32(r):
    v = r.read(32)
    return v - (1 << 32) if v >> 31 else v


def items_cmd(layout, prop_name):
    """Index of the DynamicArray cmd holding the items of fast-array property `prop_name`."""
    for i, c in enumerate(layout.cmds):
        if c.type == 'DynamicArray' and (c.name == prop_name or c.name.startswith(prop_name + '.')):
            return i
    raise KeyError(prop_name)


def decode(layout, prop_name, bits, nbits):
    r = BitReader(bits.to_bytes((nbits + 7) // 8, 'little'), nbits)
    ci = items_cmd(layout, prop_name)
    cmd = layout.cmds[ci]
    out = {'key': _i32(r), 'base': _i32(r)}
    ndel, nchg = _i32(r), _i32(r)
    out['deleted'] = [_i32(r) for _ in range(ndel)]
    d = Decoder(layout)
    out['changed'] = []
    for _ in range(nchg):
        rid = r.read(32)
        vals = {}
        if r.bit():
            d.read_level(r, ci + 1, cmd.end - 1, vals, False)
        out['changed'].append((rid, vals))
    if r.left():
        raise ValueError('fast array: %d trailing bits' % r.left())
    return out


def encode(layout, prop_name, fa):
    w = BitWriter()
    ci = items_cmd(layout, prop_name)
    cmd = layout.cmds[ci]
    w.write(fa['key'] & 0xFFFFFFFF, 32)
    w.write(fa['base'] & 0xFFFFFFFF, 32)
    w.write(len(fa['deleted']), 32)
    w.write(len(fa['changed']), 32)
    for i in fa['deleted']:
        w.write(i & 0xFFFFFFFF, 32)
    e = Encoder(layout)
    for rid, vals in fa['changed']:
        w.write(rid, 32)
        if vals:
            w.bit(1)
            e.write_level(w, ci + 1, cmd.end - 1, vals, False)
        else:
            w.bit(0)
    return w


# ---------------------------------------------------------------------------------------------------------------
# Legacy framing, exactly as in stock 5.1.1 (FRepLayout::ReceiveCustomDeltaProperty + FastArrayDeltaSerialize legacy path):
#   [bit bSupportsFastArrayDeltaStructSerialization]            <- the "extra bit" I mis-attributed to the items for a day
#   [i32 ArrayReplicationKey][i32 BaseReplicationKey][i32 NumDeleted][i32 NumChanged][i32 deleted id]*
#   { [u32 ReplicationID] + FRepLayout::SerializePropertiesForStruct(item) }*NumChanged     (no handles, arrays = u16 count + elements)
# Verified against the live client: it builds exactly NumChanged items (IDs 1..9,11; item 0 = Default__event_heal_ab_C, Level 1, Input 3).
from .values import read_prop, write_prop, Unsupported
from .structs import STRUCT_READERS, STRUCT_WRITERS


def read_seq(layout, r, lo, hi):
    out, i = [], lo
    while i < hi:
        c = layout.cmds[i]
        if c.type == 'DynamicArray':
            n = r.read(16)
            elems = [read_seq(layout, r, i + 1, c.end - 1) for _ in range(n)]
            out.append((c.name, elems))
            i = c.end
        else:
            if c.type.startswith('NetSerialize:'):
                fn = STRUCT_READERS.get(c.type[len('NetSerialize:'):])
                if fn is None:
                    raise Unsupported(c.type)
                out.append((c.name, fn(r)))
            else:
                out.append((c.name, read_prop(r, c.prop)))
            i += 1
    return out


def write_seq(layout, w, lo, hi, vals):
    it = iter(vals)
    i = lo
    while i < hi:
        c = layout.cmds[i]
        name, v = next(it)
        assert name == c.name, (name, c.name)
        if c.type == 'DynamicArray':
            w.write(len(v), 16)
            for el in v:
                write_seq(layout, w, i + 1, c.end - 1, el)
            i = c.end
        else:
            if c.type.startswith('NetSerialize:'):
                STRUCT_WRITERS[c.type[len('NetSerialize:'):]](w, v)
            else:
                write_prop(w, c.prop, v)
            i += 1


def decode_legacy(layout, prop_name, bits, nbits):
    r = BitReader(bits.to_bytes((nbits + 7) // 8, 'little'), nbits)
    ci = items_cmd(layout, prop_name)
    cmd = layout.cmds[ci]
    out = {'delta_flag': r.bit(), 'key': _i32(r), 'base': _i32(r)}
    ndel, nchg = _i32(r), _i32(r)
    out['deleted'] = [_i32(r) for _ in range(ndel)]
    out['changed'] = [(r.read(32), read_seq(layout, r, ci + 1, cmd.end - 1)) for _ in range(nchg)]
    if r.left():
        raise ValueError('fast array: %d trailing bits' % r.left())
    return out


def encode_legacy(layout, prop_name, fa):
    w = BitWriter()
    ci = items_cmd(layout, prop_name)
    cmd = layout.cmds[ci]
    w.bit(fa.get('delta_flag', 0))
    w.write(fa['key'] & 0xFFFFFFFF, 32)
    w.write(fa['base'] & 0xFFFFFFFF, 32)
    w.write(len(fa['deleted']), 32)
    w.write(len(fa['changed']), 32)
    for i in fa['deleted']:
        w.write(i & 0xFFFFFFFF, 32)
    for rid, vals in fa['changed']:
        w.write(rid, 32)
        write_seq(layout, w, ci + 1, cmd.end - 1, vals)
    return w
