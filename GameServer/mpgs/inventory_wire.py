"""Verified conversions used by the Dauntless inventory replication bridge."""

import zlib


def compact_instance_id(instance_id):
    """Convert the game's decimal instance-ID string to FReplicatedItem's u16.

    The 2.1.1 client calls ``_wtoi`` on ``UArchonInventoryItem::ItemInstanceID``
    before storing the result in ``FReplicatedItem::InstanceId``.  Be stricter
    than ``_wtoi`` here: malformed opaque IDs must not silently collapse to 0.
    """
    if not isinstance(instance_id, str) or not instance_id or not instance_id.isascii() or not instance_id.isdecimal():
        raise ValueError('item instance ID must contain only ASCII decimal digits')
    value = int(instance_id, 10)
    if not 0 < value <= 0xFFFF:
        raise ValueError('item instance ID must fit in the nonzero u16 range')
    return value


def item_identity_crc(value):
    """2.1.1 RVA 0x1A4E800, seed zero, for ASCII catalog/instance IDs.

    The native routine processes each UTF-16 code unit as four bytes. ASCII
    IDs therefore match CRC32 over UTF-32LE. Reject other inputs rather than
    silently assuming Unicode normalization or embedded-NUL semantics.
    """
    if not isinstance(value, str) or not value or not value.isascii() or '\0' in value:
        raise ValueError('identity must be nonempty ASCII without NUL')
    return zlib.crc32(value.encode('utf-32le'))


def build_identity_fixture(items, equipment):
    """Build an OFFLINE, fresh-session identity fixture; never publish it.

    Each item explicitly supplies catalogId, instanceId and wire Quantity.
    Quantity conversion from backend RemainingUses is outside this fixture.
    Equipment maps slot names to owned item_id/instance_id pairs. Persistent
    IDs remain unchanged; sorted IDs receive temporary nonzero u16 aliases.
    Rebuilding this mapping during a session is not supported. Outer array
    encoding, item defaults, ordering and live acceptance need separate checks.
    """
    owned = {}
    catalog_crcs = {}
    for item in items:
        persistent = item['instanceId']
        catalog = item['catalogId']
        quantity = item['Quantity']
        if not isinstance(persistent, str) or not persistent or persistent in owned:
            raise ValueError('persistent instance IDs must be nonempty and unique')
        if type(quantity) is not int or not 0 <= quantity <= 0xFFFFFFFF:
            raise ValueError('explicit wire Quantity must fit u32')
        crc = item_identity_crc(catalog)
        if crc in catalog_crcs and catalog_crcs[crc] != catalog:
            raise ValueError('catalog CRC collision')
        catalog_crcs[crc] = catalog
        owned[persistent] = (catalog, quantity, crc)
    if len(owned) > 0xFFFF:
        raise ValueError('too many instances for nonzero u16 aliases')

    aliases, reverse, rows, instance_crcs = {}, {}, [], {}
    for number, persistent in enumerate(sorted(owned), 1):
        local = str(number)
        crc = item_identity_crc(local)
        if crc == 0 or crc in instance_crcs:
            raise ValueError('instance CRC is zero or collides')
        aliases[persistent] = local
        reverse[local] = persistent
        instance_crcs[crc] = local
        _, quantity, catalog_crc = owned[persistent]
        rows.append({'CatalogIdCrc': catalog_crc, 'Quantity': quantity, 'InstanceId': number})

    slots = {}
    for slot, reference in equipment.items():
        if reference is None:
            slots[slot] = 0
            continue
        persistent = reference['instance_id']
        if persistent not in owned or owned[persistent][0] != reference['item_id']:
            raise ValueError('equipment reference must match an owned catalog/instance pair')
        slots[slot] = item_identity_crc(aliases[persistent])
    return {'rows': rows, 'slot_crcs': slots, 'aliases': aliases, 'reverse': reverse}
