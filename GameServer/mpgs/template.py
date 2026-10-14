"""Readable world template: the parsed replication model as a JSON data file.

The world is stored as actors, blocks and decoded property values. The server
builds every world from this file; session code then replaces template values
with server-derived data.

Value encoding (lossless, round-trips to the exact model objects):
  str / float / bool / None / list / small int  -> native JSON
  int beyond +-2**53                            -> {"$int": "0x..."}
  tuple                                         -> {"$tuple": [...]}
  decode.OrderedFields (wire order matters)      -> {"$ordered": [[key, value], ...]} (+ "$order" when it differs)
  dict with any non-str key (array elements)     -> {"$map": [[key, value], ...]}
  dict with str keys                             -> JSON object
Block fields are {index, name, kind, payload}: kind 'fast' (decoded fast
array), 'rpc' (decoded RPC parameters, mpgs.repl.rpc) or 'raw' (bit string).
Bit strings (raw/tail payloads) are {"nbits": n, "hex": "..."} (little-endian
bit order: bit 0 is the first wire bit).
"""
import hashlib
import json
import os

from .repl import model
from .repl.decode import OrderedFields
from .repl.guid import GuidInfo

FORMAT = 'mpgs-world-template'
VERSION = 1
_SAFE_INT = 1 << 53


def _enc(v):
    if v is None or isinstance(v, (bool, str, float)):
        return v
    if isinstance(v, int):
        return v if -_SAFE_INT < v < _SAFE_INT else {'$int': hex(v)}
    if isinstance(v, tuple):
        return {'$tuple': [_enc(x) for x in v]}
    if isinstance(v, list):
        return [_enc(x) for x in v]
    if isinstance(v, OrderedFields):
        # .order is stored verbatim: the encoder reads it, and maps filled via
        # dict methods (array element containers) legitimately keep it empty.
        out = {'$ordered': [[_enc(k), _enc(x)] for k, x in v.items()]}
        if list(v.order) != list(v.keys()):
            out['$order'] = [_enc(k) for k in v.order]
        return out
    if isinstance(v, dict):
        if type(v) is not dict:
            raise TypeError('unsupported mapping type %s' % type(v).__name__)
        if all(isinstance(k, str) for k in v):
            if any(k.startswith('$') for k in v):
                raise ValueError('template object keys must not start with $')
            return {k: _enc(x) for k, x in v.items()}
        return {'$map': [[_enc(k), _enc(x)] for k, x in v.items()]}
    raise TypeError('unsupported template value %s' % type(v).__name__)


def _dec(v):
    if isinstance(v, list):
        return [_dec(x) for x in v]
    if not isinstance(v, dict):
        return v
    if '$ordered' in v and set(v) <= {'$ordered', '$order'}:
        out = OrderedFields()
        for k, x in v['$ordered']:
            dict.__setitem__(out, _dec(k), _dec(x))
        out.order = [_dec(k) for k in v['$order']] if '$order' in v else [_dec(k) for k, _ in v['$ordered']]
        return out
    if len(v) == 1:
        (tag, body), = v.items()
        if tag == '$int':
            return int(body, 16)
        if tag == '$tuple':
            return tuple(_dec(x) for x in body)
        if tag == '$map':
            return {_dec(k): _dec(x) for k, x in body}
    return {k: _dec(x) for k, x in v.items()}


def _bits_enc(pair):
    if pair is None:
        return None
    bits, n = pair
    return {'nbits': n, 'hex': format(bits, 'x') if bits else '0'}


def _bits_dec(obj):
    if obj is None:
        return None
    return (int(obj['hex'], 16), obj['nbits'])


def _guid_enc(info):
    if info is None:
        return None
    return {'guid': info.guid, 'flags': info.flags, 'name': info.name,
            'checksum': info.checksum, 'outer': _guid_enc(info.outer_info)}


def _guid_dec(obj):
    if obj is None:
        return None
    info = GuidInfo()
    info.guid, info.flags, info.name, info.checksum = obj['guid'], obj['flags'], obj['name'], obj['checksum']
    info.outer_info = _guid_dec(obj['outer'])
    return info


_BLOCK_SCALARS = ('actor', 'rep', 'guid', 'stable', 'destroy', 'cls', 'outer', 'cls_name', 'partial_properties')


def _block_enc(b):
    out = {k: getattr(b, k) for k in _BLOCK_SCALARS}
    out['values'] = _enc(b.values)
    out['tail'] = _bits_enc(b.tail)
    out['raw'] = _bits_enc(b.raw)
    out['fields'] = None if b.fields is None else [
        {'index': idx, 'name': name, 'kind': kind,
         'payload': _bits_enc(payload) if kind == 'raw' else _enc(payload)}
        for idx, name, kind, payload in b.fields]
    return out


def _block_dec(obj):
    b = model.Block()
    for k in _BLOCK_SCALARS:
        setattr(b, k, obj[k])
    b.values = _dec(obj['values'])
    b.tail = _bits_dec(obj['tail'])
    b.raw = _bits_dec(obj['raw'])
    b.fields = None if obj['fields'] is None else [
        (f['index'], f['name'], f['kind'],
         _bits_dec(f['payload']) if f['kind'] == 'raw' else _dec(f['payload']))
        for f in obj['fields']]
    return b


def dump_world(world, source=None):
    """GenWorld -> template dict (JSON-ready)."""
    entries = []
    for e in world.entries:
        p = e.parsed
        entries.append({
            't': e.t, 'ch': e.ch, 'cls': e.cls,
            'open': e.open, 'close': e.close, 'reliable': e.reliable, 'must_map': e.must_map,
            'exports': [_guid_enc(x) for x in e.exports],
            'bunch': {'must_map': list(p.must_map), 'open': _enc(p.open),
                      'blocks': [_block_enc(b) for b in p.blocks]},
        })
    names = [[g, o, n] for g, (o, n) in world.ctx._names.items()]
    return {'format': FORMAT, 'version': VERSION, 'source': source or {},
            'identities': dict(world.old_ids), 'names': names, 'entries': entries}


def summarize(template):
    """Counts of decoded vs still-opaque content, for reports."""
    blocks = [b for e in template['entries'] for b in e['bunch']['blocks']]
    opaque = [b for b in blocks if b['raw'] is not None and b['raw']['nbits']]
    fields = [f for b in blocks for f in (b['fields'] or [])]
    tails = [b for b in blocks if b['values'] is not None and b['fields'] is None
             and b['tail'] is not None and b['tail']['nbits']]
    kinds = {}
    for f in fields:
        kinds[f['kind']] = kinds.get(f['kind'], 0) + 1
    return {
        'entries': len(template['entries']), 'blocks': len(blocks),
        'decoded_property_blocks': sum(1 for b in blocks if b['values'] is not None),
        'empty_subobject_headers': sum(1 for b in blocks if b['raw'] is not None and not b['raw']['nbits']),
        'fields_by_kind': kinds,
        'opaque_rpc_fields': sorted('%s.%s' % (b['cls_name'], f['name']) for b in blocks
                                    for f in (b['fields'] or []) if f['kind'] == 'raw'),
        'opaque_property_tails': [{'class': b['cls_name'], 'nbits': b['tail']['nbits']} for b in tails],
        'opaque_blocks': [{'class': b['cls_name'], 'nbits': b['raw']['nbits']} for b in opaque],
    }


def write_template(world, path, source=None):
    template = dump_world(world, source)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(template, f, indent=1)
        f.write('\n')
    os.replace(tmp, path)
    return template


def read_template(path):
    with open(path, encoding='utf-8') as f:
        template = json.load(f)
    if template.get('format') != FORMAT or template.get('version') != VERSION:
        raise ValueError('not a v%d %s file: %s' % (VERSION, FORMAT, path))
    return template


def load_entries(template, entry_cls):
    """Template dict -> list of entry_cls (genworld.Entry) objects."""
    out = []
    for obj in template['entries']:
        e = entry_cls()
        e.t, e.ch, e.cls = obj['t'], obj['ch'], obj['cls']
        e.open, e.close, e.reliable, e.must_map = obj['open'], obj['close'], obj['reliable'], obj['must_map']
        e.exports = [_guid_dec(x) for x in obj['exports']]
        p = model.Parsed()
        p.must_map = list(obj['bunch']['must_map'])
        p.open = _dec(obj['bunch']['open'])
        p.blocks = [_block_dec(b) for b in obj['bunch']['blocks']]
        e.parsed = p
        e.patch_pairs = ()
        out.append(e)
    return out


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()
