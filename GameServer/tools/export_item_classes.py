"""Generate world/item_classes.json (catalogId -> equipment actor class path, and its ELoadoutItemSlotType)
from the 2.1.1 gear catalog exports (game data: generated locally, not distributed).
python -m tools.export_item_classes [all-datatables-211 directory]
"""
import glob
import json
import os
import sys

DEFAULT = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'debug', 'exports', 'all-datatables-211')
OUT = os.path.join(os.path.dirname(__file__), '..', 'world', 'item_classes.json')
# catalog file pattern -> slot type (armour: per row ArmourSlot)
PATTERNS = (('*Gear_Catalogs_weapon_*_gameplay_catalog.json', 1), ('*Gear_Catalogs_armour_gameplay_catalog.json', None),
            ('*Gear_Catalogs_lantern_gameplay_catalog.json', 6), ('*Gear_Catalogs_player_role_catalog.json', 8))
ARMOUR_SLOTS = {'HELMET': 2, 'CHEST': 3, 'ARMS': 4, 'LEGS': 5}


def main(src=DEFAULT):
    classes, slots = {}, {}
    for pat, slot in PATTERNS:
        for path in sorted(glob.glob(os.path.join(src, pat))):
            for cid, row in json.load(open(path, encoding='utf-8'))[0]['Rows'].items():
                cls = ((row.get('ItemClass') or {}).get('AssetPathName') or '')
                if not cls or cls == 'None':
                    continue
                classes[cid] = cls
                s = slot if slot else ARMOUR_SLOTS.get(str(row.get('ArmourSlot', '')).rsplit('_', 1)[-1])
                if s:
                    slots[cid] = s
    json.dump({'source': 'gear catalogs ItemClass / ArmourSlot (2.1.1 export)', 'classes': classes, 'slots': slots},
              open(OUT, 'w'), indent=1, sort_keys=True)
    print('item classes', len(classes), 'with slots', len(slots))


if __name__ == '__main__':
    main(*sys.argv[1:])
