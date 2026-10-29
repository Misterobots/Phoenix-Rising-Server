"""The custom data the client gives an item that arrives without any.

The client adds a V2 AddItem with empty CustomData by building the item and serializing it (the item classes' ToCustomData,
0x28d7b95 armour, 0x28d8a25 lantern, 0x28d8fd5 player role, 0x28d9610 weapon), and its inventory state hash covers that
string's CRC, not the empty one. Each string below reproduces the CRC the client held for every item of that kind (live
2026-10-28, EAC-off client memory, 48 items): with the empty string the server's postOperationHash never matched again
and the client dropped every live inventory update (crafts, talents) until a rejoin.
"""
import json
import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEVELS = os.path.join(HERE, 'data', '2.1.1', 'item_levels.json')      # tools/export_runtime_data.py
BANNERS = os.path.join(HERE, 'data', '2.1.1', 'banner_defaults.json')  # tools/export_banners.py
_levels = _banners = None
# FBannerCustomizationItemIDs in member order; the catalog's CustomData keys fill the ones they name (its VFXItemID
# names none, so both VFX ids stay empty), then the three EArmourDyeSurfaceType sheens as numbers
BANNER_KEYS = ('BannerMeshItemID', 'FabricMaterialItemID', 'SigilTextureItemID', 'PlantVFXItemID',
               'PersistantStandardVFXItemID', 'AnimationItemID', 'BackgroundColourItemID', 'BorderColourItemID',
               'SigilColourItemID')

CELLS = '"EquippedCells":[],"EquippedCellsv2":[]'
TRANSMOG = '"TransmogCatalogID":"None","UnlockedAsTransmog":false'


def _level(catalog_id):
    global _levels
    if _levels is None:
        with open(LEVELS, encoding='utf-8') as f:
            _levels = json.load(f)
    return _levels.get(catalog_id, 0)


def _banner(catalog_id):
    global _banners
    if _banners is None:
        with open(BANNERS, encoding='utf-8') as f:
            _banners = json.load(f)
    defaults = _banners.get(catalog_id, {})
    return '{%s,"BackgroundSheenType":0,"BorderSheenType":0,"SigilSheenType":0}' % ','.join(
        '"%s":"%s"' % (k, defaults.get(k, '')) for k in BANNER_KEYS)


def default_custom_data(catalog_id):
    """The client's custom data for an item of `catalog_id` that has none; '' for kinds it leaves empty (stacks,
    gliders)."""
    kind = catalog_id.split('_', 1)[0]
    if kind == 'WP':
        return ('{%s,"CurrentLevel":%d,"EquippedWeaponParts":[],"CurrentXP":0,"ItemProgressionLevel":1,'
                '"ItemProgressionXP":0,"WeaponTalentProgression":{},%s}' % (TRANSMOG, _level(catalog_id), CELLS))
    if kind == 'AR':
        return ('{"Primary":"None","Secondary":"None","Tertiary":"None",%s,"CurrentLevel":%d,"CurrentXP":0,%s}'
                % (TRANSMOG, _level(catalog_id), CELLS))
    if kind == 'LT':
        return '{"LanternData":[],%s,"CurrentLevel":%d,%s}' % (TRANSMOG, _level(catalog_id), CELLS)
    if kind in ('PART', 'FL'):
        return '{"CurrentLevel":0,%s}' % CELLS
    if kind == 'BN':
        return _banner(catalog_id)
    if kind == 'PR':
        return '{"CurrentLevel":0,"CurrentXP":0,"PlayerRoleType":0,%s}' % CELLS
    return ''


# Items the client adds to its own V2 item state; none of them is in the backend inventory (EAC-off client memory,
# 2026-10-29: the 21 weapon trackers, the lantern capacitor, the two default flasks and the random intro emote). Each is
# keyed by its catalog id with quantity 1; the weapon trackers carry the weapon-part default custom data, the rest none.
CLIENT_ITEMS = ('PART_WT_BIGHIT', 'PART_WT_CHESTS', 'PART_WT_DAMAGE', 'PART_WT_DODGES', 'PART_WT_DOGS_PET',
                'PART_WT_DOWNED', 'PART_WT_EVENTS', 'PART_WT_FAUNA', 'PART_WT_GATHERABLES',
                'PART_WT_GAUNTLET_LEVELS_UNIQUE', 'PART_WT_HESCA_COUNT', 'PART_WT_INTERRUPTS', 'PART_WT_KILLS',
                'PART_WT_LONGEST_GLIDE', 'PART_WT_PARTS', 'PART_WT_RAMS_HEADBUTTED', 'PART_WT_REVIVES',
                'PART_WT_SHORTEST_HESCA', 'PART_WT_SHORTEST_TRIAL', 'PART_WT_STAGGERS', 'PART_WT_WOUNDS',
                'PART_LANTERN_CAPACITOR', 'QI_FLASK_HEAL_00', 'QI_FLASK_REVIVE_00', 'EM_INTRO_RANDOM')


def client_items(owned=()):
    """V2 state records of CLIENT_ITEMS not already in `owned` (instance ids)."""
    from .inventory_wire import item_identity_crc
    return [{'instanceId': c, 'catalogId': c, 'catalogIdCrc': item_identity_crc(c), 'quantity': 1,
             'customData': default_custom_data(c) if c.startswith('PART_WT_') else ''}
            for c in CLIENT_ITEMS if c not in owned]
