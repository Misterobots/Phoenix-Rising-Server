"""Offline primitives for the 2.1.1 ArchonInventory V2 state hash.

Static analysis of the stock client found that HandleItemStateUpdates builds
sorted 16-byte records (instance CRC, catalog CRC, quantity, custom-data CRC)
and compares CityHash64 of that byte array with PostOperationHash. This module
computes that expected hash for analysis only; it does not send inventory RPCs.
"""

import struct
import zlib

from .bitio import BitWriter

_MASK64 = 0xFFFFFFFFFFFFFFFF
_K0 = 0xC3A5C85C97CB3127
_K1 = 0xB492B66FBE98F273
_K2 = 0x9AE16A3B2F90404F
_KMUL = 0x9DDFEA08EB382D69


def _u64(x):
    return x & _MASK64


def _fetch64(data, offset):
    return struct.unpack_from('<Q', data, offset)[0]


def _fetch32(data, offset):
    return struct.unpack_from('<I', data, offset)[0]


def _rotate(value, shift):
    value &= _MASK64
    return value if shift == 0 else _u64((value >> shift) | (value << (64 - shift)))


def _shift_mix(value):
    return value ^ (value >> 47)


def _hash_len16(u, v, mul):
    a = _u64((u ^ v) * mul)
    a ^= a >> 47
    b = _u64((v ^ a) * mul)
    b ^= b >> 47
    return _u64(b * mul)


def _hash_len16_default(u, v):
    return _hash_len16(u, v, _KMUL)


def _hash_len0to16(data):
    length = len(data)
    if length >= 8:
        mul = _K2 + length * 2
        a, b = _u64(_fetch64(data, 0) + _K2), _fetch64(data, length - 8)
        c = _u64(_rotate(b, 37) * mul + a)
        d = _u64((_rotate(a, 25) + b) * mul)
        return _hash_len16(c, d, mul)
    if length >= 4:
        mul = _K2 + length * 2
        a = _fetch32(data, 0)
        return _hash_len16(length + (a << 3), _fetch32(data, length - 4), mul)
    if length:
        a, b, c = data[0], data[length >> 1], data[-1]
        y = a + (b << 8)
        z = length + (c << 2)
        return _u64(_shift_mix(_u64(y * _K2) ^ _u64(z * _K0)) * _K2)
    return _K2


def _hash_len17to32(data):
    length = len(data)
    mul = _K2 + length * 2
    a = _u64(_fetch64(data, 0) * _K1)
    b = _fetch64(data, 8)
    c = _u64(_fetch64(data, length - 8) * mul)
    d = _u64(_fetch64(data, length - 16) * _K2)
    return _hash_len16(
        _u64(_rotate(_u64(a + b), 43) + _rotate(c, 30) + d),
        _u64(a + _rotate(_u64(b + _K2), 18) + c), mul)


def _weak_hash32(data, offset, a, b):
    w, x, y, z = (_fetch64(data, offset + n) for n in (0, 8, 16, 24))
    a = _u64(a + w)
    b = _rotate(_u64(b + a + z), 21)
    c = a
    a = _u64(a + x + y)
    b = _u64(b + _rotate(a, 44))
    return _u64(a + z), _u64(b + c)


def _bswap64(value):
    return int.from_bytes(value.to_bytes(8, 'little'), 'big')


def _hash_len33to64(data):
    length = len(data)
    mul = _K2 + length * 2
    a = _u64(_fetch64(data, 0) * _K2)
    b = _fetch64(data, 8)
    c = _fetch64(data, length - 24)
    d = _fetch64(data, length - 32)
    e = _u64(_fetch64(data, 16) * _K2)
    f = _u64(_fetch64(data, 24) * 9)
    g = _fetch64(data, length - 8)
    h = _u64(_fetch64(data, length - 16) * mul)
    u = _u64(_rotate(_u64(a + g), 43) + (_rotate(b, 30) + c) * 9)
    v = _u64(((a + g) ^ d) + f + 1)
    w = _u64(_bswap64(_u64((u + v) * mul)) + h)
    x = _u64(_rotate(_u64(e + f), 42) + c)
    y = _u64(_bswap64(_u64((v + w) * mul)) + g) * mul & _MASK64
    z = _u64(e + f + c)
    a = _u64(_bswap64(_u64((x + z) * mul + y)) + b)
    b = _u64(_shift_mix(_u64((z + a) * mul + d + h)) * mul)
    return _u64(b + x)


def city_hash64(data):
    """UE 5.1.1's CityHash64, ported from its bundled CityHash.cpp."""
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError('CityHash64 input must be bytes-like')
    data = bytes(data)
    length = len(data)
    if length <= 16:
        return _hash_len0to16(data)
    if length <= 32:
        return _hash_len17to32(data)
    if length <= 64:
        return _hash_len33to64(data)

    x = _fetch64(data, length - 40)
    y = _u64(_fetch64(data, length - 16) + _fetch64(data, length - 56))
    z = _hash_len16_default(_u64(_fetch64(data, length - 48) + length),
                            _fetch64(data, length - 24))
    v = _weak_hash32(data, length - 64, length, z)
    w = _weak_hash32(data, length - 32, _u64(y + _K1), x)
    x = _u64(x * _K1 + _fetch64(data, 0))
    remaining = (length - 1) & ~63
    offset = 0
    while remaining:
        x = _u64(_rotate(_u64(x + y + v[0] + _fetch64(data, offset + 8)), 37) * _K1)
        y = _u64(_rotate(_u64(y + v[1] + _fetch64(data, offset + 48)), 42) * _K1)
        x ^= w[1]
        y = _u64(y + v[0] + _fetch64(data, offset + 40))
        z = _u64(_rotate(_u64(z + w[0]), 33) * _K1)
        v = _weak_hash32(data, offset, _u64(v[1] * _K1), _u64(x + w[0]))
        w = _weak_hash32(data, offset + 32, _u64(z + w[1]),
                         _u64(y + _fetch64(data, offset + 16)))
        z, x = x, z
        offset += 64
        remaining -= 64
    return _hash_len16_default(
        _u64(_hash_len16_default(v[0], w[0]) + _shift_mix(y) * _K1 + z),
        _u64(_hash_len16_default(v[1], w[1]) + x))


def unreal_tchar_crc32(value):
    """FCrc::StrCrc32<TCHAR> on Windows (each UTF-16 unit padded to 4 bytes)."""
    if not isinstance(value, str) or '\0' in value:
        raise ValueError('TCHAR string must be text without embedded NUL')
    utf16 = value.encode('utf-16le', errors='strict')
    units = struct.unpack('<' + 'H' * (len(utf16) // 2), utf16) if utf16 else ()
    canonical_bytes = b''.join(struct.pack('<I', unit) for unit in units)
    return zlib.crc32(canonical_bytes)


def operation_state_bytes(items):
    """Return sorted 16-byte records for the post-operation inventory state.

    Each input mapping supplies the persistent ``instanceId``, the
    already verified ``catalogIdCrc``, u32 ``quantity`` and exact FString
    ``customData``. Sorting is by the native map key: instance-ID CRC.
    """
    rows = []
    seen = set()
    for item in items:
        instance_id = item.get('instanceId')
        if not isinstance(instance_id, str) or not instance_id or '\0' in instance_id:
            raise ValueError('instanceId must be nonempty FString text without embedded NUL')
        instance_crc = unreal_tchar_crc32(instance_id)
        catalog_crc = item.get('catalogIdCrc')
        quantity = item.get('quantity')
        custom_data = item.get('customData', '')
        if type(catalog_crc) is not int or not 0 <= catalog_crc <= 0xFFFFFFFF:
            raise ValueError('catalogIdCrc must fit u32')
        if type(quantity) is not int or not 0 <= quantity <= 0xFFFFFFFF:
            raise ValueError('quantity must fit u32')
        if not isinstance(custom_data, str):
            raise ValueError('customData must be the exact FString text')
        if instance_crc in seen:
            raise ValueError('instance CRC collision or duplicate inventory instance')
        seen.add(instance_crc)
        rows.append((instance_crc, catalog_crc, quantity, unreal_tchar_crc32(custom_data)))
    rows.sort(key=lambda row: row[0])
    return b''.join(struct.pack('<IIII', *row) for row in rows)


def post_operation_hash(items):
    """Compute static-analysis PostOperationHash; never publishes an RPC."""
    return city_hash64(operation_state_bytes(items))


_OPERATION = {'Hello': 0, 'AddItem': 1, 'RemoveItem': 2, 'UpdateItem': 3}
_CLIENT_UPDATE_FIELD = 17  # ArchonInventory field list, verified from 2.1.1 SDK.


def encode_client_item_state_updates_field(updates):
    """Encode the raw RPC parameter payload for ClientHandleItemStateUpdates.

    UE 5.1.1 FRepLayout source establishes a nonempty TArray count as uint16,
    followed by the struct's reflected members in offset order. The caller must
    still place this raw payload in a reliable server-to-client actor-channel
    bunch. This helper does not queue or send it.
    """
    if not isinstance(updates, (list, tuple)) or not updates or len(updates) > 0xFFFF:
        raise ValueError('updates must be a nonempty TArray within the uint16 limit')
    writer = BitWriter()
    writer.bit(1)                    # the RPC's sole parameter differs from its empty default
    writer.write(len(updates), 16)   # FRepLayout dynamic-array Num
    for update in updates:
        mismatch = update.get('mismatchCount')
        operation = update.get('operation')
        if isinstance(operation, str):
            operation = _OPERATION.get(operation)
        post_hash = update.get('postOperationHash')
        instance_id = update.get('instanceId', '')
        catalog_crc = update.get('catalogIdCrc')
        quantity = update.get('quantity')
        custom_data = update.get('customData', '')
        if type(mismatch) is not int or not 0 <= mismatch <= 0xFFFFFFFF:
            raise ValueError('mismatchCount must fit u32')
        if type(operation) is not int or not 0 <= operation <= 3:
            raise ValueError('operation must be Hello, AddItem, RemoveItem or UpdateItem')
        if type(post_hash) is not int or not 0 <= post_hash <= _MASK64:
            raise ValueError('postOperationHash must fit u64')
        if not isinstance(instance_id, str) or not isinstance(custom_data, str):
            raise ValueError('instanceId and customData must be FString values')
        if type(catalog_crc) is not int or not 0 <= catalog_crc <= 0xFFFFFFFF:
            raise ValueError('catalogIdCrc must fit u32')
        if type(quantity) is not int or not 0 <= quantity <= 0xFFFFFFFF:
            raise ValueError('quantity must fit u32')

        # FInventoryItemOperationData fields, confirmed by SDK offsets/order.
        writer.write(mismatch, 32)
        writer.write(operation, 8)
        writer.write(post_hash, 64)
        writer.write_fstring(instance_id)
        writer.write(catalog_crc, 32)
        writer.write(quantity, 32)
        writer.write_fstring(custom_data)

    return (_CLIENT_UPDATE_FIELD, 'ClientHandleItemStateUpdates', 'raw',
            (writer.n, writer.nbits))
