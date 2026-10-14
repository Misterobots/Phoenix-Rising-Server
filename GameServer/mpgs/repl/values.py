"""Per-property-type net serialization (FProperty::NetSerializeItem, UE5.1.1) -- reader side.

Every reader takes (BitReader, Prop) and returns a python value. Struct NetSerialize implementations are registered in
STRUCT_READERS by struct name.
"""
import struct

from .guid import read_load_object


def ceil_log2(n):
    return 0 if n <= 1 else (n - 1).bit_length()


class Unsupported(Exception):
    pass


def read_name(r):
    if r.bit():
        return ('ename', r.read_packed())
    s = r.read_fstring()
    return (s, r.read(32))


def read_float(r):
    return struct.unpack('<f', r.read_bytes(4))[0]


def enum_bits(prop, elem_bits):
    em = prop.em if prop.em is not None else 255
    return min(ceil_log2(em + 1), elem_bits)


def read_prop(r, prop):
    k = prop.kind
    if k == 'BoolProperty':
        return r.bit()
    if k == 'FloatProperty':
        return read_float(r)
    if k == 'DoubleProperty':
        return r.read_double()
    if k in ('IntProperty', 'Int32Property'):
        v = r.read(32)
        return v - (1 << 32) if v >> 31 else v
    if k == 'UInt32Property':
        return r.read(32)
    if k == 'Int64Property':
        v = r.read(64)
        return v - (1 << 64) if v >> 63 else v
    if k == 'UInt64Property':
        return r.read(64)
    if k == 'Int16Property':
        v = r.read(16)
        return v - (1 << 16) if v >> 15 else v
    if k == 'UInt16Property':
        return r.read(16)
    if k == 'Int8Property':
        v = r.read(8)
        return v - 256 if v >> 7 else v
    if k == 'ByteProperty':
        return r.read(enum_bits(prop, 8) if prop.en else 8)
    if k == 'EnumProperty':
        return r.read(enum_bits(prop, 8 * (1 if prop.under in ('ByteProperty', 'Int8Property') else 4)))
    if k == 'NameProperty':
        return read_name(r)
    if k == 'StrProperty':
        return r.read_fstring()
    if k in ('ObjectProperty', 'WeakObjectProperty', 'ClassProperty', 'InterfaceProperty', 'ClassPtrProperty', 'LazyObjectProperty'):
        start, infos = r.pos, []
        g = read_load_object(r, False, infos)
        if g == 1:          # referenced by path (guid 1 + outer + name): keep the exact bits
            n = r.pos - start
            return ('objpath', (r.n >> start) & ((1 << n) - 1), n, infos[0].name if infos else None)
        return ('obj', g)
    if k in ('SoftObjectProperty', 'SoftClassProperty'):
        # FNetBitWriter::operator<<(FSoftObjectPtr), net version with
        # SOFTOBJECTPTR_NETGUIDS: [bUsePath] then the path as an FString
        # (FSoftObjectPath::ToString) or, for dynamic objects, a NetGUID.
        if r.bit():
            return ('soft', r.read_fstring())
        return ('softobj', read_load_object(r, False, []))
    raise Unsupported(k)


def write_name(w, v):
    if isinstance(v, str):
        v = (v, 0)
    if v[0] == 'ename':
        w.bit(1)
        w.write_packed(v[1])
    else:
        w.bit(0)
        w.write_fstring(v[0])
        w.write(v[1], 32)


def write_prop(w, prop, v):
    k = prop.kind
    if k == 'BoolProperty':
        w.bit(v)
    elif k == 'FloatProperty':
        w.write_bytes(struct.pack('<f', v))
    elif k == 'DoubleProperty':
        w.write_double(v)
    elif k in ('IntProperty', 'Int32Property', 'UInt32Property'):
        w.write(v, 32)
    elif k in ('Int64Property', 'UInt64Property'):
        w.write(v, 64)
    elif k in ('Int16Property', 'UInt16Property'):
        w.write(v, 16)
    elif k == 'Int8Property':
        w.write(v, 8)
    elif k == 'ByteProperty':
        w.write(v, enum_bits(prop, 8) if prop.en else 8)
    elif k == 'EnumProperty':
        w.write(v, enum_bits(prop, 8 * (1 if prop.under in ('ByteProperty', 'Int8Property') else 4)))
    elif k == 'NameProperty':
        write_name(w, v)
    elif k == 'StrProperty':
        w.write_fstring(v)
    elif k in ('ObjectProperty', 'WeakObjectProperty', 'ClassProperty', 'InterfaceProperty', 'ClassPtrProperty', 'LazyObjectProperty'):
        if isinstance(v, tuple) and v[0] == 'objpath':
            w.write(v[1], v[2])
        else:
            w.write_packed(v[1] if isinstance(v, tuple) else v)
    elif k in ('SoftObjectProperty', 'SoftClassProperty'):
        if isinstance(v, tuple) and v[0] == 'softobj':
            w.bit(0)
            w.write_packed(v[1])
        else:
            path = v[1] if isinstance(v, tuple) else v
            w.bit(1)
            w.write_fstring('' if path in (0, None) else path)
    else:
        raise Unsupported(k)
