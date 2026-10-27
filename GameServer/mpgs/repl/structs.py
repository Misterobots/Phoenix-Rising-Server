"""STRUCT_NetSerializeNative struct readers (UE5.1.1 + Dauntless). Added as the recorded data demands."""
from .values import Unsupported, read_name, write_name
import math
import struct

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


@reader('RepMovement_Behemoth')
def _repmove_behemoth(r):
    """Bp_Behemoth_C CDO overrides ReplicatedMovement to RoundOneDecimal (loc/vel) and ShortComponents (rot)."""
    flags = r.read(4)
    loc = read_quantized_vector(r, 10)
    rot = read_rotator(r)
    vel = read_quantized_vector(r, 10)
    out = {'loc': loc, 'rot': rot, 'vel': vel, 'sleep': flags & 1, 'phys': (flags >> 1) & 1}
    if flags & 2:
        out['angvel'] = read_quantized_vector(r, 10)
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
    """FRepMovement::NetSerialize. The quantization levels are properties of the receiving actor's CDO, not on the wire: the player pawn
    uses location x100, velocity x1, byte rotation (the defaults below, confirmed live). ArchonBehemoth classes are routed to
    RepMovement_Behemoth by the schema; an explicit v['quant'] = {'loc': 10, 'vel': 10, 'rot': 'short'} (ported Mystic-Paradox
    callers) selects the same encoding here."""
    q = v.get('quant') or {}
    flags = (v.get('sleep', 0)) | (v.get('phys', 0) << 1) | ((1 << 2) if 'frame' in v else 0) | ((1 << 3) if 'handle' in v else 0)
    w.write(flags, 4)
    write_quantized_vector(w, q.get('loc', 100), v['loc'])
    if q.get('rot') == 'short':                      # FRotator::SerializeCompressedShort: per axis [nonzero][uint16]
        write_rotator(w, v['rot'])
    else:
        for a in v['rot']:                           # FRotator::SerializeCompressed: per axis [nonzero][byte]
            s = int(round(a * 256.0 / 360.0)) & 0xFF
            if s:
                w.bit(1)
                w.write(s, 8)
            else:
                w.bit(0)
    write_quantized_vector(w, q.get('vel', 1), v['vel'])
    if v.get('phys'):
        write_quantized_vector(w, q.get('vel', 1), v['angvel'])
    if 'frame' in v:
        w.write_packed(v['frame'])
    if 'handle' in v:
        w.write_packed(v['handle'])


@writer('RepMovement_Behemoth')
def _w_repmove_behemoth(w, v):
    flags = (v.get('sleep', 0)) | (v.get('phys', 0) << 1) | ((1 << 2) if 'frame' in v else 0) | ((1 << 3) if 'handle' in v else 0)
    w.write(flags, 4)
    write_quantized_vector(w, 10, v['loc'])
    write_rotator(w, v['rot'])
    write_quantized_vector(w, 10, v['vel'])
    if v.get('phys'):
        write_quantized_vector(w, 10, v['angvel'])
    if 'frame' in v:
        w.write_packed(v['frame'])
    if 'handle' in v:
        w.write_packed(v['handle'])


# ----------------------------------------------------------------------------- root motion (Character.RepRootMotion)
# FRootMotionSourceGroup::NetSerialize, checked against the UE 5.1.1 source (D:\UnrealEngine-5.1.1-git, RootMotionSource.cpp):
#   bHasAdditiveSources, bHasOverrideSources, bHasOverrideSourcesWithIgnoreZAccumulate   32 bits each (see below)
#   LastPreAdditiveVelocity                                                              FVector_NetQuantize10
#   bIsAdditiveVelocityApplied                                                           32 bits
#   LastAccumulatedSettings.Flags                                                        uint8
#   NetSerializeRMSArray(RootMotionSources)            uint8 count, then each source (script struct + its NetSerialize)
#   NetSerializeRMSArray(PendingAddRootMotionSources)  uint8 count, then each source
# The flags use FArchive_Serialize_BitfieldBool, i.e. `Ar << bool`, which calls FArchive::SerializeBool: a legacy UBOOL, 32 bits.
# No net archive (FBitWriter/FBitReader/FNetBitWriter) overrides SerializeBool in 5.1.1, so on the wire `Ar << bool` is 32 bits;
# only SerializeBits/WriteBit/ReadBit are single bits. RULE for every native serializer we port: check which of the two it uses.
# Live 2026-10-16: two wrong versions (one count byte; 1-bit flags) were rejected by the client (ObjectReplicatorReceivedBunchFail);
# a member-by-member probe (session._rm_probe_step) showed every other RepRootMotion member was accepted and this one was not.
# The server never runs root motion *sources* (ability-driven forces); montage root motion travels in the other RepRootMotion members,
# so only an empty group is written. A non-empty group from a capture is refused rather than misread.

@reader('RootMotionSourceGroup')
def _r_rms_group(r):
    out = {'additive': r.read(32) != 0, 'override': r.read(32) != 0, 'override_ignore_z': r.read(32) != 0,
           'last_pre_additive_velocity': read_quantized_vector(r, 10), 'additive_velocity_applied': r.read(32) != 0,
           'settings_flags': r.read(8)}
    for what in ('sources', 'pending'):
        n = r.read(8)
        if n:
            raise Unsupported('RootMotionSourceGroup with %d %s' % (n, what))
    out['sources'] = []
    return out


@writer('RootMotionSourceGroup')
def _w_rms_group(w, v):
    v = v or {}
    if v.get('sources'):
        raise Unsupported('RootMotionSourceGroup with sources')
    w.write(1 if v.get('additive') else 0, 32)                 # Ar << bool: 32-bit UBOOL
    w.write(1 if v.get('override') else 0, 32)
    w.write(1 if v.get('override_ignore_z') else 0, 32)
    write_quantized_vector(w, 10, v.get('last_pre_additive_velocity', [0.0, 0.0, 0.0]))
    w.write(1 if v.get('additive_velocity_applied') else 0, 32)
    w.write(v.get('settings_flags', 0), 8)
    w.write(0, 8)                                   # RootMotionSources count
    w.write(0, 8)                                   # PendingAddRootMotionSources count


# ----------------------------------------------------------------------------- GameplayAbilities / GameplayTags
# Tag net-index configuration of the game (UGameplayTagsManager): container size bits, first bit segment, true bit count.
# DefaultGameplayTags.ini: FastReplication=True, NumBitsForContainerSize=6, NetIndexFirstBitSegment=16. NetIndexTrueBitNum =
# ceil(log2(nodes + 2)) (GameplayTagsManager.cpp): ~2,090 tag nodes (1,785 ini tags + parents + native strings in the exe) -> 12,
# and the first segment is min(16, 12) = 12, so an index is a plain 12-bit value. The old 16 made InternalReviveFromBleedOut
# undecodable: the client skipped it and stayed downed (live 2026-10-22).
TAG_CFG = {'size_bits': 6, 'first': 12, 'true': 12, 'by_name': False}


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


# A tag net index past the end of the client's NetworkGameplayTagNodeIndex: GetTagFromNetIndex returns the empty tag. The real
# table (sorted tag nodes, CommonlyReplicatedTags first) is not rebuilt yet, so a specific tag cannot be named on the wire.
TAG_NONE_INDEX = (1 << 12) - 1           # >= InvalidTagNetIndex (nodes + 1): decodes as the empty tag


_TAG_TABLE = None


def tag_index(name):
    """Net index of a gameplay tag by name (data/2.1.1/gameplay_tags.json, tools/export_gameplay_tags), or None if unknown."""
    global _TAG_TABLE
    if _TAG_TABLE is None:
        import json as _json
        import os as _os
        path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..', '..', 'data', '2.1.1', 'gameplay_tags.json')
        try:
            with open(path, encoding='utf-8') as f:
                _TAG_TABLE = {n.lower(): i for i, n in enumerate(_json.load(f))}
        except OSError:
            _TAG_TABLE = {}
    return _TAG_TABLE.get(name.lower())


MINIMAL_TAG_COUNT_BITS = 5      # UAbilitySystemGlobals::MinimalReplicationTagCountBits default (DefaultGame.ini sets none)


@reader('MinimalReplicationTagCountMap')
def _r_minimal_tags(r):
    """FMinimalReplicationTagCountMap::NetSerialize: a CountBits count, then that many FGameplayTag::NetSerialize."""
    n = r.read(MINIMAL_TAG_COUNT_BITS)
    return [_read_tag_index(r) for _ in range(n)]


@writer('MinimalReplicationTagCountMap')
def _w_minimal_tags(w, v):
    v = list(v or [])
    w.write(len(v), MINIMAL_TAG_COUNT_BITS)
    for i in v:
        _write_tag_index(w, i)


@reader('GameplayTag')
def _r_tag(r):
    """FGameplayTag::NetSerialize_Packed with fast replication: one net index (SerializeTagNetIndexPacked)."""
    if TAG_CFG.get('by_name'):
        return read_name(r)
    return _read_tag_index(r)


@writer('GameplayTag')
def _w_tag(w, v):
    if TAG_CFG.get('by_name'):
        write_name(w, v)
    else:
        _write_tag_index(w, TAG_NONE_INDEX if v is None else v)


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


@reader('Quat')
def _quat(r):
    # UE 5.1.1 Core/Private/Math/UnrealMath.cpp, TQuat<double>::NetSerialize:
    # transmit XYZ as doubles, force W >= 0, reconstruct W on receipt.
    xyz = [r.read_double() for _ in range(3)]
    magnitude = sum(c * c for c in xyz)
    if magnitude <= 1.0:
        return xyz + [math.sqrt(1.0 - magnitude)]
    inv = 1.0 / math.sqrt(magnitude)
    return [c * inv for c in xyz] + [0.0]


@writer('Quat')
def _w_quat(w, v):
    if len(v) != 4 or not all(math.isfinite(c) for c in v):
        raise ValueError('Quat requires four finite components')
    q = list(v)
    norm = sum(c * c for c in q)
    if norm <= 1e-8:
        q = [0.0, 0.0, 0.0, 1.0]
    elif abs(1.0 - norm) > 0.01:  # UE_DOUBLE_THRESH_QUAT_NORMALIZED
        q = [c / math.sqrt(norm) for c in q]
    if q[3] < 0:
        q = [-c for c in q]
    for c in q[:3]:
        w.write_double(c)


@reader('DateTime')
def _datetime(r):
    v = r.read(64)                                      # FDateTime::NetSerialize: Ar << Ticks (int64, 100 ns since 0001-01-01)
    return v - (1 << 64) if v >> 63 else v


@writer('DateTime')
def _w_datetime(w, v):
    w.write(v & ((1 << 64) - 1), 64)


# ----------------------------------------------------------------------------- FHitResult
# FHitResult::NetSerialize, UE 5.1.1 Engine/Private/Engine/HitResult.cpp (fields: Classes/Engine/HitResult.h):
#   uint8 Flags (SerializeBits 8): bBlockingHit, bStartPenetrating, ImpactPoint==Location, ImpactNormal==Normal,
#                                  Item==INDEX_NONE, FaceIndex==INDEX_NONE, PenetrationDepth==0, ElementIndex==INDEX_NONE
#   float Time; Location (FVector_NetQuantize = SerializePackedVector<1,20>); Normal (FVector_NetQuantizeNormal =
#   SerializeFixedVector<1,16>); [ImpactPoint]; [ImpactNormal]; TraceStart; TraceEnd; [float PenetrationDepth]; [int32 Item];
#   PhysMaterial (object); HitObjectHandle = Actor (object), Manager (object), int32 InstanceIndex (ActorInstanceHandle.cpp);
#   Component (object); FName BoneName; [int32 FaceIndex]; [uint8 ElementIndex].
# ElementIndex is a uint8 compared with INDEX_NONE (-1), so a UE sender never sets flag bit 7; this writer does the same.
# Defaults follow FHitResult::Init (memzero, Time = 1): an empty dict is a default-constructed hit.
#
# Dauntless 2.1.1 differs from stock 5.1.1 (verified 2026-10-21 against 9 live ServerTryDoDamage payloads, logs/rpc_capture.jsonl,
# which decode end to end with these rules): Flags are 16 bits; bit 8 is set in every sample and an extra FVector_NetQuantize
# ('extra_point', small actor-relative values) follows ImpactNormal, before TraceStart. The writer always sets bit 8 and writes
# the extra point, as the client does. A stock 8-bit hit makes the client mark the whole RPC incompatible and skip it silently
# (DataReplication.cpp HANDLE_INCOMPATIBLE_RPC) - that is why ClientTryHit went unanswered.
# Object fields are read with the InternalLoadObject format (a client sends default GUID 1 plus a path for assets).
_HIT_DEFAULTS = {'blocking': 0, 'start_penetrating': 0, 'time': 1.0, 'location': [0.0, 0.0, 0.0], 'normal': [0.0, 0.0, 0.0],
                 'trace_start': [0.0, 0.0, 0.0], 'trace_end': [0.0, 0.0, 0.0], 'penetration': 0.0, 'item': 0,
                 'phys_material': 0, 'actor': 0, 'manager': 0, 'instance_index': 0, 'component': 0, 'bone_name': ('ename', 0),
                 'face_index': 0, 'element_index': 0}


def _w_fixed_normal(w, vec):
    for c in vec:                                    # WriteFixedCompressedFloat<1, 16>: MaxBitValue 32767, Bias 32768
        delta = int(math.floor(32767 * c + 0.5)) + 32768
        write_int(w, min(max(delta, 0), 65535), 1 << 16)


def _r_fixed_normal(r):
    return [(read_int(r, 1 << 16) - 32768) / 32767.0 for _ in range(3)]


def _w_float(w, f):
    w.write(struct.unpack('<I', struct.pack('<f', float(f)))[0], 32)


def _r_float(r):
    return struct.unpack('<f', struct.pack('<I', r.read(32)))[0]


@writer('HitResult')
def _w_hit_result(w, v):
    h = dict(_HIT_DEFAULTS, **(v or {}))
    ip = h.get('impact_point', h['location'])
    inorm = h.get('impact_normal', h['normal'])
    eq_ip, eq_in = list(ip) == list(h['location']), list(inorm) == list(h['normal'])
    flags = (h['blocking'] & 1) | ((h['start_penetrating'] & 1) << 1) | (eq_ip << 2) | (eq_in << 3) | \
        ((h['item'] == -1) << 4) | ((h['face_index'] == -1) << 5) | ((h['penetration'] == 0.0) << 6)
    flags |= 1 << 8
    w.write(flags, 16)
    _w_float(w, h['time'])
    write_quantized_vector(w, 1, h['location'])
    _w_fixed_normal(w, h['normal'])
    if not eq_ip:
        write_quantized_vector(w, 1, ip)
    if not eq_in:
        _w_fixed_normal(w, inorm)
    write_quantized_vector(w, 1, h.get('extra_point', [0.0, 0.0, 0.0]))
    write_quantized_vector(w, 1, h['trace_start'])
    write_quantized_vector(w, 1, h['trace_end'])
    if h['penetration'] != 0.0:
        _w_float(w, h['penetration'])
    if h['item'] != -1:
        w.write(h['item'] & 0xFFFFFFFF, 32)
    for k in ('phys_material', 'actor', 'manager'):
        w.write_packed(h[k])
    w.write(h['instance_index'] & 0xFFFFFFFF, 32)
    w.write_packed(h['component'])
    write_name(w, h['bone_name'])
    if h['face_index'] != -1:
        w.write(h['face_index'] & 0xFFFFFFFF, 32)
    w.write(h['element_index'] & 0xFF, 8)


@reader('HitResult')
def _r_hit_result(r):
    flags = r.read(16)
    out = {'blocking': flags & 1, 'start_penetrating': (flags >> 1) & 1, 'time': _r_float(r),
           'location': read_quantized_vector(r, 1), 'normal': _r_fixed_normal(r)}
    out['impact_point'] = list(out['location']) if flags & 4 else read_quantized_vector(r, 1)
    out['impact_normal'] = list(out['normal']) if flags & 8 else _r_fixed_normal(r)
    if flags & (1 << 8):
        out['extra_point'] = read_quantized_vector(r, 1)
    out['trace_start'] = read_quantized_vector(r, 1)
    out['trace_end'] = read_quantized_vector(r, 1)
    out['penetration'] = 0.0 if flags & 64 else _r_float(r)
    out['item'] = -1 if flags & 16 else _signed32(r.read(32))
    for k in ('phys_material', 'actor', 'manager'):
        out[k] = _r_hit_object(r, out)
    out['instance_index'] = _signed32(r.read(32))
    out['component'] = _r_hit_object(r, out)
    out['bone_name'] = read_name(r)
    out['face_index'] = -1 if flags & 32 else _signed32(r.read(32))
    out['element_index'] = 255 if flags & 128 else r.read(8)
    return out


def _r_hit_object(r, out):
    exports = []
    g = read_load_object(r, False, exports)
    if exports:
        out.setdefault('object_exports', []).extend(exports)
    return g


def _signed32(u):
    return u - (1 << 32) if u & 0x80000000 else u
