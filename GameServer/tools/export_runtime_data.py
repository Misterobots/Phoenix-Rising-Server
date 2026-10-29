"""Write the game data the server reads at runtime, from the atlas (Atlas branch), into data/2.1.1.

python -m tools.export_runtime_data [atlas dir]   (default: data/atlas)

- class_layouts.json: per Blueprint/native class, what mpgs.loadout needs to give a class the schema lacks a network
  layout: its first parent, its replicated properties and its RPCs with their parameters.
- talents/<table>.json: the tables mpgs.talents reads (copied as dumped).
- weapon_power.json: each weapon's power-row abilities (mpgs.engine.weapon_parts).
The server only reads these files; the atlas itself is not needed to run it.
"""
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, 'data', '2.1.1')
TALENT_TABLES = ('weapon_dp_gameplay_catalog', 'weapon_talent_catalog', 'talent_crafting_requirements',
                 'crafting_recipes')


def power_rows(assets):
    """{catalog id: {RefAbilities, RefSpecialAbilities1-5}} from weapon_progression_rework_power_table, the table in use
    (atlas weapon_power_and_abilities.md), for the rows that grant any ability (mpgs/engine/weapon_parts.py)."""
    with open(os.path.join(assets, 'weapon_progression_rework_power_table.json'), encoding='utf-8') as f:
        rows = next(iter(json.load(f)['exports'].values()))['props']['Rows']
    keys = ['RefAbilities'] + ['RefSpecialAbilities%d' % n for n in range(1, 6)]
    out = {}
    for item, row in rows.items():
        got = {k: [p for p in row.get(k) or () if p] for k in keys}      # unset soft references dump as None
        got = {k: v for k, v in got.items() if v}
        if got:
            out[item] = got
    return out


def layout(info):
    return {
        'parent': (info.get('parents') or [None])[0],
        'properties': [{'name': p['name'], 'type': p.get('type')} for p in info.get('properties') or () if p.get('net')],
        'rpcs': [{'name': fn['name'], 'bp_flags': fn.get('bp_flags') or [],
                  'params': [{'name': p['name'], 'type': p.get('type')} for p in fn.get('params') or ()]}
                 for fn in info.get('functions') or () if fn.get('rpc')],
    }


def main(argv):
    atlas = argv[0] if argv else os.path.join(HERE, 'data', 'atlas')
    classes = os.path.join(atlas, 'classes')
    layouts = {}
    for name in sorted(os.listdir(classes)):
        if name.endswith('.json'):
            with open(os.path.join(classes, name), encoding='utf-8') as f:
                layouts[name[:-5]] = layout(json.load(f))
    with open(os.path.join(OUT, 'class_layouts.json'), 'w', encoding='utf-8') as f:
        json.dump(layouts, f, separators=(',', ':'), sort_keys=True)
    os.makedirs(os.path.join(OUT, 'talents'), exist_ok=True)
    for t in TALENT_TABLES:
        shutil.copyfile(os.path.join(atlas, 'assets', t + '.json'), os.path.join(OUT, 'talents', t + '.json'))
    power = power_rows(os.path.join(atlas, 'assets'))
    with open(os.path.join(OUT, 'weapon_power.json'), 'w', encoding='utf-8') as f:
        json.dump(power, f, indent=0, sort_keys=True)
    print('%d class layouts, %d talent tables, %d power rows -> %s' % (len(layouts), len(TALENT_TABLES), len(power), OUT))


if __name__ == '__main__':
    main(sys.argv[1:])
