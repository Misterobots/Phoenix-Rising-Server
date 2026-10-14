"""Generic RPC parameter codec (FRepLayout::SendPropertiesForRPC / ReceivePropertiesForRPC, UE 5.1.1).

Wire format of one RPC's parameter payload, per parameter in declaration order:
  non-bool parameter: [send bit = value differs from default] then, if set, the value
  bool parameter:     the value bit only (no send bit)
Values are serialized without handles: scalars via FProperty::NetSerializeItem,
non-native structs member by member (offset order), native structs through
their NetSerialize, dynamic arrays as uint16 count then each element.

Parameter types come from Dumpspace FunctionsInfo; struct layouts and enum
sizes from the live repdump / EnumsInfo. A payload is only modelled when it
re-encodes bit-exactly, otherwise the caller keeps it opaque.

Decoded form: OrderedFields {param name: value}; a non-bool parameter absent
from the map was not sent (default). Arrays decode as Python lists of
OrderedFields (one per element; non-struct elements use the key '').
"""
from ..bitio import BitReader, BitWriter
from .decode import OrderedFields
from .schema import Layout, Prop
from .structs import STRUCT_READERS, STRUCT_WRITERS
from .values import read_prop, write_prop, Unsupported

_SCALARS = {
    'bool': 'BoolProperty', 'float': 'FloatProperty', 'double': 'DoubleProperty',
    'int32': 'IntProperty', 'uint32': 'UInt32Property', 'int64': 'Int64Property', 'uint64': 'UInt64Property',
    'int16': 'Int16Property', 'uint16': 'UInt16Property', 'int8': 'Int8Property', 'uint8': 'ByteProperty',
    'FName': 'NameProperty', 'FString': 'StrProperty',
}
_OBJECTS = {'TSubclassOf': 'ClassProperty', 'TWeakObjectPtr': 'WeakObjectProperty',
            'TSoftObjectPtr': 'SoftObjectProperty', 'TSoftClassPtr': 'SoftClassProperty'}


def _prop_dict(schema, t, name=''):
    """Dumpspace type entry [type, kind letter, pointer, subtypes] -> repdump-style prop dict."""
    base = {'n': name, 'ri': 0, 'off': 0, 'dim': 1, 'fl': '0'}
    tname, letter, ptr, subs = t
    if tname in _SCALARS:
        return dict(base, k=_SCALARS[tname])
    if letter == 'E':
        info = schema.enums.get(tname)
        if info is None:
            raise Unsupported('enum ' + tname)
        values, under = info
        em = max(list(v.values())[0] for v in values)
        under_kind = 'ByteProperty' if under in ('uint8', 'int8') else 'IntProperty'
        return dict(base, k='EnumProperty', en=tname, em=em, under=under_kind)
    if letter == 'S':
        return dict(base, k='StructProperty', st=tname[1:] if tname[:1] == 'F' else tname)
    if tname == 'TArray':
        return dict(base, k='ArrayProperty', inner=_prop_dict(schema, subs[0]))
    if tname in _OBJECTS:
        return dict(base, k=_OBJECTS[tname])
    if letter == 'C' and ptr == '*':
        return dict(base, k='ObjectProperty')
    raise Unsupported('parameter type %s' % tname)


def function_params(schema, cls, func):
    """Parameter list [(name, type entry)] of net function `func` as seen from class `cls`."""
    for c in [cls] + schema.inherit.get(cls, []):
        for fn, _flags, params in schema.funcs.get(c, []):
            if fn == func:
                return [(p[2], p[0]) for p in params]
    return None


class RpcLayout:
    def __init__(self, schema, cls, func):
        params = function_params(schema, cls, func)
        if params is None:
            raise Unsupported('unknown net function %s.%s' % (cls, func))
        self.func = func
        self.layout = Layout(func)
        self.params = []           # (name, prop, cmd lo, cmd hi)
        rel = 0
        for i, (name, t) in enumerate(params):
            prop = Prop(_prop_dict(schema, t, name))
            lo = len(self.layout.cmds)
            self.layout.parents.append((prop, 0))
            rel = schema._init_prop(self.layout, i, prop, rel, name)
            self.params.append((name, prop, lo, len(self.layout.cmds)))
            self.layout.parent_cmd_range.append((lo, len(self.layout.cmds)))
        self.layout.cmds.append(_Return())


class _Return:
    type, prop, name, end = 'Return', None, '', 0


def _read_range(r, layout, lo, hi, out):
    i = lo
    while i < hi:
        cmd = layout.cmds[i]
        if cmd.type == 'Return':
            i += 1
            continue
        if cmd.type == 'DynamicArray':
            count = r.read(16)
            elems = []
            for _ in range(count):
                elem = OrderedFields()
                _read_range(r, layout, i + 1, cmd.end - 1, elem)
                elems.append(elem)
            out[cmd.name] = elems
            i = cmd.end
            continue
        if cmd.type.startswith('NetSerialize:'):
            fn = STRUCT_READERS.get(cmd.type[len('NetSerialize:'):])
            if fn is None:
                raise Unsupported(cmd.type)
            out[cmd.name] = fn(r)
        else:
            out[cmd.name] = read_prop(r, cmd.prop)
        i += 1


def _write_range(w, layout, lo, hi, values):
    i = lo
    while i < hi:
        cmd = layout.cmds[i]
        if cmd.type == 'Return':
            i += 1
            continue
        if cmd.type == 'DynamicArray':
            elems = values[cmd.name]
            w.write(len(elems), 16)
            for elem in elems:
                _write_range(w, layout, i + 1, cmd.end - 1, elem)
            i = cmd.end
            continue
        v = values[cmd.name]
        if cmd.type.startswith('NetSerialize:'):
            fn = STRUCT_WRITERS.get(cmd.type[len('NetSerialize:'):])
            if fn is None:
                raise Unsupported(cmd.type)
            fn(w, v)
        else:
            write_prop(w, cmd.prop, v)
        i += 1


def decode(rpc, bits, nbits):
    r = BitReader(bits.to_bytes((nbits + 7) // 8, 'little'), nbits)
    out = OrderedFields()
    for name, prop, lo, hi in rpc.params:
        if prop.kind != 'BoolProperty' and not r.bit():
            continue
        sub = OrderedFields()
        _read_range(r, rpc.layout, lo, hi, sub)
        for k, v in sub.items():
            out[k] = v
    if r.left():
        raise ValueError('%d unread bits after %s parameters' % (r.left(), rpc.func))
    return out


def encode(rpc, values):
    w = BitWriter()
    for name, prop, lo, hi in rpc.params:
        names = [rpc.layout.cmds[i].name for i in range(lo, hi)
                 if rpc.layout.cmds[i].type != 'Return']
        present = any(n in values for n in names)
        if prop.kind != 'BoolProperty':
            w.bit(1 if present else 0)
            if not present:
                continue
        _write_range(w, rpc.layout, lo, hi, values)
    return w


_CACHE = {}


def rpc_layout(schema, cls, func):
    key = (id(schema), cls, func)
    if key not in _CACHE:
        _CACHE[key] = RpcLayout(schema, cls, func)
    return _CACHE[key]


def try_decode(schema, cls, func, bits, nbits):
    """-> decoded params when they re-encode bit-exactly, else None."""
    try:
        rpc = rpc_layout(schema, cls, func)
        values = decode(rpc, bits, nbits)
        w = encode(rpc, values)
    except (Unsupported, ValueError, EOFError, KeyError, IndexError, TypeError):
        return None
    if (w.n, w.nbits) != (bits, nbits):
        return None
    return values
