"""STRUCT_NetSerializeNative struct readers (UE5.1.1 + Dauntless). Added as the recorded data demands."""
from .values import Unsupported, read_name, write_name

STRUCT_READERS = {}


def reader(name):
    def deco(fn):
        STRUCT_READERS[name] = fn
        return fn
    return deco


@reader('UniqueNetIdRepl')
def _uid(r):
    flags = r.read(8)
    if flags & 1 and flags & 2:
        return None                         # encoded + empty
    th = flags >> 3
    if not flags & 1 and th not in (30,):
        typ = r.read_fstring() if th == 31 else 'hash%d' % th
        return ('uid', typ, r.read_fstring())
    raise Unsupported('UniqueNetIdRepl flags %#x' % flags)


@reader('ReplicatedItem')
def _replicated_item(r):
    """FReplicatedItem::NetSerialize (Dauntless 2.1.1): u32, packed u32, u16."""
    return {
        'CatalogIdCrc': r.read(32),
        'Quantity': r.read_packed(),
        'InstanceId': r.read(16),
    }


from .quant import read_quantized_vector, read_rotator, read_int


@reader('Vector_NetQuantize')
def _vq(r):
    return read_quantized_vector(r, 1)


@reader('Vector_NetQuantize10')
def _vq10(r):
    return read_quantized_vector(r, 10)


@reader('Vector_NetQuantize100')
def _vq100(r):
    return read_quantized_vector(r, 100)


@reader('Vector_NetQuantizeNormal')
def _vqn(r):
    return [(read_int(r, 1 << 16) - (1 << 15)) / 32767.0 for _ in range(3)]


@reader('Rotator')
def _rot(r):
    return read_rotator(r)


@reader('RepMovement')
def _repmove(r):
    flags = r.read(4)
    loc = read_quantized_vector(r, 100)                # UE 5.1 FRepMovement receiver defaults: RoundTwoDecimals location (confirmed live: client divides by 100), RoundWholeNumber velocity, ByteComponents rotation
    rot = [r.read(8) * 360.0 / 256.0 if r.bit() else 0.0 for _ in range(3)]
    vel = read_quantized_vector(r, 1)
    out = {'loc': loc, 'rot': rot, 'vel': vel, 'sleep': flags & 1, 'phys': (flags >> 1) & 1}
    if flags & 2:
        out['angvel'] = read_quantized_vector(r, 1)
    if flags & 4:
        out['frame'] = r.read_packed()
    if flags & 8:
        out['handle'] = r.read_packed()
    return out


# ----------------------------------------------------------------------------- writers
from .quant import write_quantized_vector, write_rotator, write_int

STRUCT_WRITERS = {}


def writer(name):
    def deco(fn):
        STRUCT_WRITERS[name] = fn
        return fn
    return deco


@writer('UniqueNetIdRepl')
def _w_uid(w, v):
    if v is None:
        w.write(0x03, 8)                 # encoded + empty
        return
    _, typ, contents = v                 # unencoded: [flags = typeHash << 3][(type FString if hash 31)][FString]
    if typ.startswith('hash'):
        w.write(int(typ[4:]) << 3, 8)
    else:
        w.write(31 << 3, 8)
        w.write_fstring(typ)
    w.write_fstring(contents)


@writer('ReplicatedItem')
def _w_replicated_item(w, v):
    """Mirror the native field order and widths; reject values that cannot fit."""
    crc = int(v['CatalogIdCrc'])
    quantity = int(v['Quantity'])
    instance_id = int(v['InstanceId'])
    if not 0 <= crc <= 0xFFFFFFFF:
        raise ValueError('CatalogIdCrc must fit u32')
    if not 0 <= quantity <= 0xFFFFFFFF:
        raise ValueError('Quantity must fit u32')
    if not 0 <= instance_id <= 0xFFFF:
        raise ValueError('InstanceId must fit u16')
    w.write(crc, 32)
    w.write_packed(quantity)
    w.write(instance_id, 16)


for _n, _s in (('Vector_NetQuantize', 1), ('Vector_NetQuantize10', 10), ('Vector_NetQuantize100', 100)):
    STRUCT_WRITERS[_n] = (lambda sc: (lambda w, v: write_quantized_vector(w, sc, v)))(_s)
STRUCT_WRITERS['Vector_NetQuantizeNormal'] = lambda w, v: [write_int(w, int(round(c * 32767.0)) + (1 << 15), 1 << 16) for c in v]
STRUCT_WRITERS['Rotator'] = write_rotator


@writer('RepMovement')
def _w_repmove(w, v):
    flags = (v.get('sleep', 0)) | (v.get('phys', 0) << 1) | ((1 << 2) if 'frame' in v else 0) | ((1 << 3) if 'handle' in v else 0)
    w.write(flags, 4)
    write_quantized_vector(w, 100, v['loc'])
    for a in v['rot']:                               # FRotator::SerializeCompressed: per axis [nonzero][byte]
        s = int(round(a * 256.0 / 360.0)) & 0xFF
        if s:
            w.bit(1)
            w.write(s, 8)
        else:
            w.bit(0)
    write_quantized_vector(w, 1, v['vel'])
    if v.get('phys'):
        write_quantized_vector(w, 1, v['angvel'])
    if 'frame' in v:
        w.write_packed(v['frame'])
    if 'handle' in v:
        w.write_packed(v['handle'])


# ----------------------------------------------------------------------------- GameplayAbilities / GameplayTags
# Tag net-index configuration of the game (UGameplayTagsManager): container size bits, first bit segment, true bit count.
TAG_CFG = {'size_bits': 6, 'first': 16, 'true': 16, 'by_name': False}   # stock UGameplayTagsManager defaults (live client memory); all recorded containers are empty


def _read_tag_index(r):
    first, true = TAG_CFG['first'], TAG_CFG['true']
    if first <= 0 or first >= true:
        return r.read(true)
    data = r.read(first + 1)
    if data & (1 << first):
        return (r.read(true - first) << first) | (data & ((1 << first) - 1))
    return data


def _write_tag_index(w, v):
    first, true = TAG_CFG['first'], TAG_CFG['true']
    if first <= 0 or first >= true:
        w.write(v, true)
    elif v > (1 << first) - 1:
        w.write(((v & ((1 << first) - 1)) | (1 << first)) | ((v >> first) << (first + 1)), true + 1)
    else:
        w.write(v, first + 1)


@reader('GameplayTagContainer')
def _r_tags(r):
    if r.bit():
        return []
    n = r.read(TAG_CFG['size_bits'])
    if TAG_CFG.get('by_name'):
        return [read_name(r) for _ in range(n)]
    return [_read_tag_index(r) for _ in range(n)]          # net indices (names need the live tag table)


@writer('GameplayTagContainer')
def _w_tags(w, v):
    if not v:
        w.bit(1)
        return
    w.bit(0)
    w.write(len(v), TAG_CFG['size_bits'])
    for i in v:
        if TAG_CFG.get('by_name'):
            write_name(w, i)
        else:
            _write_tag_index(w, i)


@reader('PredictionKey')
def _r_pk(r):
    valid = r.bit()
    has_base = r.bit() if valid else 0
    server = r.bit()
    out = {'server': server}
    if valid:
        out['current'] = r.read(16)
        if has_base:
            out['base'] = r.read(16)
    return out


@writer('PredictionKey')
def _w_pk(w, v):
    valid = 'current' in v
    w.bit(valid)
    if valid:
        w.bit('base' in v)
    w.bit(v.get('server', 0))
    if valid:
        w.write(v['current'], 16)
        if 'base' in v:
            w.write(v['base'], 16)


from .guid import read_load_object


def _obj(r):
    return read_load_object(r, False, [])


@reader('GameplayEffectContextHandle')
def _r_gech(r):
    if not r.bit():
        return None
    rep = r.read(7)                                   # FGameplayEffectContext::NetSerialize (base class)
    out = {'rep': rep}
    for bit, key in ((0, 'instigator'), (1, 'causer'), (2, 'ability_cdo'), (3, 'source')):
        if rep & (1 << bit):
            out[key] = _obj(r)
    if rep & ((1 << 4) | (1 << 5)):
        raise Unsupported('GameplayEffectContext actors/hit result')
    if rep & (1 << 6):
        out['origin'] = [r.read_double() for _ in range(3)]
    return out


@writer('GameplayEffectContextHandle')
def _w_gech(w, v):
    if v is None:
        w.bit(0)
        return
    w.bit(1)
    w.write(v['rep'], 7)
    for bit, key in ((0, 'instigator'), (1, 'causer'), (2, 'ability_cdo'), (3, 'source')):
        if v['rep'] & (1 << bit):
            w.write_packed(v[key])
    if v['rep'] & (1 << 6):
        for c in v['origin']:
            w.write_double(c)


@reader('Vector')
def _vec(r):
    return [r.read_double() for _ in range(3)]          # FVector::NetSerialize: three doubles (verified by plausible PC SpawnLocation)


@writer('Vector')
def _w_vec(w, v):
    for c in v:
        w.write_double(c)


@reader('DateTime')
def _datetime(r):
    v = r.read(64)                                      # FDateTime::NetSerialize: Ar << Ticks (int64, 100 ns since 0001-01-01)
    return v - (1 << 64) if v >> 63 else v


@writer('DateTime')
def _w_datetime(w, v):
    w.write(v & ((1 << 64) - 1), 64)
