"""Write world/player/gear.json: each armor piece's primary gear stat and set, and each set's bonus tiers (atlas topics/armor.md s.4).

  python -m tools.export_gear <all-datatables-211 dir>

Read from the exported data tables:
- Gear_Catalogs/armour_gameplay_catalog_composite rows: PrimaryGearStat {GearStat row, BaseMagnitude, PerLevelMagnitude},
  GearSetBonusId (gear_set_bonus_table row).
- gear_stat_table rows: Attribute + AdditionalAttributes (attribute set class, attribute name).
- gear_set_bonus_table rows: GearSetBonuses [{RequiredSources, GameplayModifiers (buffs, effects, abilities)}].
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, '..', 'world', 'player', 'gear.json')


def rows(path):
    with open(path, encoding='utf-8') as fh:
        d = json.load(fh)
    d = d[0] if isinstance(d, list) else d
    return d.get('Rows', d)


def _attr(a):
    owner = ((a or {}).get('AttributeOwner') or {}).get('ObjectName', '')
    return [owner.split("'")[1] if "'" in owner else owner, a.get('AttributeName')]


def _paths(refs):
    return [r.get('AssetPathName') for r in refs or [] if isinstance(r, dict) and r.get('AssetPathName') not in (None, '', 'None')]


def main():
    src = os.path.abspath(sys.argv[1])
    stats = rows(os.path.join(src, 'Archon_Content_Gameplay_gear_stat_table.json'))
    sets = rows(os.path.join(src, 'Archon_Content_Gameplay_gear_set_bonus_table.json'))
    armor = rows(os.path.join(src, 'Archon_Content_Gameplay_Backend_Catalogs_Gear_Catalogs_armour_gameplay_catalog_composite.json'))
    out = {'stats': {}, 'armor': {}, 'sets': {}}
    for name, r in stats.items():
        out['stats'][name] = [_attr(r['Attribute'])] + [_attr(a) for a in r.get('AdditionalAttributes') or []]
    for item, r in armor.items():
        pg = r.get('PrimaryGearStat') or {}
        stat = ((pg.get('GearStat') or {}).get('RowName'))
        out['armor'][item] = {'stat': None if stat in (None, 'None') else stat, 'base': pg.get('BaseMagnitude', 0.0),
                              'per_level': pg.get('PerLevelMagnitude', 0.0),
                              'set': ((r.get('GearSetBonusId') or {}).get('RowName')) or None}
    for name, r in sets.items():
        tiers = []
        for t in r.get('GearSetBonuses') or []:
            m = t.get('GameplayModifiers') or {}
            tiers.append({'required': t.get('RequiredSources', 0), 'buffs': _paths(m.get('BuffClasses')),
                          'effects': _paths(m.get('GameplayEffectClasses')), 'abilities': _paths(m.get('GameplayAbilityClasses'))})
        out['sets'][name] = tiers
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w', encoding='utf-8') as fh:
        json.dump(out, fh, indent=1)
    print('wrote %s: %d stats, %d armor, %d sets' % (OUT, len(out['stats']), len(out['armor']), len(out['sets'])))


if __name__ == '__main__':
    main()
