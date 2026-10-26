"""Write world/player/weapon_parts.json: each weapon's default parts and, per part, its component class and abilities.

  python -m tools.export_weapon_parts <scratch dir>

Read from the paks with AssetDump:
- Gear_Catalogs/weapon_*_gameplay_catalog rows: WeaponPartSlots [{SlotIndex, WeaponPartType, DefaultWeaponPartID, bIsLocked}].
- Gear_Catalogs/weapon_part_catalog rows: WeaponPartType, PartComponent (the weapon_part_base_bpc subclass the server spawns on
  the weapon, BP_BaseArchonWeapon SpawnAndAddComponents), Abilities (granted with the part; the DP X/Y abilities are triggered by
  the gameplay events Combat.Shoot.X / .Y that the part component sends), Buffs.
"""
import glob
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETDUMP = os.path.normpath(os.path.join(HERE, '..', '..', '..', '..', 'Mystic-Paradox', 'tools', 'AssetDump', 'bin', 'AssetDump.dll'))
OUT = os.path.join(HERE, '..', 'world', 'player', 'weapon_parts.json')
CATALOGS = 'Archon/Content/Gameplay/Backend/Catalogs/Gear_Catalogs/'


def run(*args):
    return subprocess.run(['dotnet', ASSETDUMP] + list(args), capture_output=True, text=True, timeout=900).stdout


def _path(ref):
    return ref.get('AssetPathName') if isinstance(ref, dict) and ref.get('AssetPathName') not in (None, '', 'None') else None


def main():
    scratch = os.path.abspath(sys.argv[1])
    os.makedirs(scratch, exist_ok=True)
    found = [l.strip() for l in run('find', 'Gear_Catalogs/', '').splitlines() if l.startswith(CATALOGS)]
    wanted = [p for p in found if p.endswith('_gameplay_catalog') or p.endswith('/weapon_part_catalog')]
    run('dump', scratch, *wanted)
    weapons, parts = {}, {}
    for f in glob.glob(os.path.join(scratch, '*.json')):
        with open(f, encoding='utf-8') as fh:
            table = json.load(fh)[0]
        rows = table.get('Rows') or {}
        if f.endswith('weapon_part_catalog.json'):
            for k, r in rows.items():
                parts[k] = {'type': r.get('WeaponPartType'), 'component': _path(r.get('PartComponent')),
                            'abilities': [p for p in map(_path, r.get('Abilities') or []) if p],
                            'buffs': [p for p in map(_path, r.get('Buffs') or []) if p]}
        else:
            for k, r in rows.items():
                slots = r.get('WeaponPartSlots')
                if slots:
                    weapons[k] = [{'slot': s['SlotIndex'], 'type': s['WeaponPartType'], 'default': s.get('DefaultWeaponPartID') or None,
                                   'locked': bool(s.get('bIsLocked'))} for s in slots]
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w', encoding='utf-8') as fh:
        json.dump({'source': 'weapon_*_gameplay_catalog WeaponPartSlots + weapon_part_catalog (tools/export_weapon_parts.py)',
                   'weapons': weapons, 'parts': parts}, fh, indent=1, sort_keys=True)
    print('%s: %d weapons, %d parts' % (os.path.normpath(OUT), len(weapons), len(parts)))


if __name__ == '__main__':
    main()
