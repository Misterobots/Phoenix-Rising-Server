"""Write the game data the server reads at runtime, from the atlas (Atlas branch), into data/2.1.1.

python -m tools.export_runtime_data [atlas dir]   (default: data/atlas)

- class_layouts.json: per Blueprint/native class, what mpgs.loadout needs to give a class the schema lacks a network
  layout: its first parent, its replicated properties and its RPCs with their parameters.
- talents/<table>.json: the tables mpgs.talents reads (copied as dumped).
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
    print('%d class layouts, %d talent tables -> %s' % (len(layouts), len(TALENT_TABLES), OUT))


if __name__ == '__main__':
    main(sys.argv[1:])
