"""Generate world/behemoths/rock_schema.json: the Blueprint class chain of Rock and its components (game data: generated locally).

python -m tools.export_behemoth_schema [dump root]
Reads AssetDump full-export dumps (debug/runs/hunt_assets/rock_full/): sq_rock_normal_bp, sq_rock_archetype_bp, sq_genus_bp
(and sample/Bp_Behemoth.json for the component list). Per class: super, net properties in ChildProperties order (that order is the
ClassReps order after the parent's reps, verified against Bp_Behemoth_C in the live repdump) and SCS components.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ROOT = os.path.join(HERE, '..', '..', '..', 'debug', 'runs', 'hunt_assets')
OUT = os.path.join(HERE, '..', 'world', 'behemoths', 'rock_schema.json')
CHAIN = ['sq_rock_normal_bp', 'sq_rock_archetype_bp', 'sq_genus_bp']

# EPropertyFlags bits used by the layout code (CPF_Net, CPF_RepNotify)
CPF_NET, CPF_REP_NOTIFY = 0x20, 0x100000


def ref(v):
    if isinstance(v, dict) and 'ObjectName' in v:
        s = v['ObjectName']
        return s.split("'")[-2].split(':')[-1] if "'" in s else s
    return v


def one(path):
    d = json.load(open(path))
    cls = next(e for e in d if e['Type'] == 'BlueprintGeneratedClass')
    props = []
    for p in cls.get('ChildProperties') or []:
        flags = str(p.get('PropertyFlags'))
        if 'Net' not in flags.split(' | '):
            continue
        fl = CPF_NET | (CPF_REP_NOTIFY if 'RepNotify' in flags else 0)
        item = {'n': p['Name'], 'k': p['Type'], 'fl': '%x' % fl, 'flags': flags}
        if p['Type'] == 'StructProperty':
            item['st'] = ref(p.get('Struct'))
        if p['Type'] in ('ObjectProperty', 'WeakObjectProperty', 'InterfaceProperty'):
            item['oc'] = ref(p.get('PropertyClass') or p.get('InterfaceClass'))
        if p['Type'] == 'BoolProperty':
            item['bsz'] = 1
        props.append(item)
    comps = []
    for e in d:
        if e['Type'] == 'SCS_Node':
            pr = e.get('Properties', {})
            comps.append({'name': pr.get('InternalVariableName'), 'class': ref(pr.get('ComponentClass')),
                          'parent': pr.get('ParentComponentOrVariableName')})
    return {'class': cls['Name'], 'package': cls.get('Package'), 'super': ref(cls.get('Super')), 'props': props,
            'components': comps}


def main():
    """Rock (default): python -m tools.export_behemoth_schema [dump root]
    Any species:     python -m tools.export_behemoth_schema --chain <most derived bp.json> ... --base <Bp_Behemoth.json> --out <file>
    (--chain lists AssetDump full-export files from the most derived class up to, not including, Bp_Behemoth)."""
    global OUT
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('root', nargs='?', default=DEFAULT_ROOT)
    ap.add_argument('--chain', nargs='+')
    ap.add_argument('--base')
    ap.add_argument('--out')
    ap.add_argument('--label', default='Rock')
    a = ap.parse_args()
    root = a.root
    paths = a.chain or [os.path.join(root, 'rock_full', n + '.json') for n in CHAIN]
    classes = [one(p) for p in paths]
    base = one(a.base or os.path.join(root, 'sample', 'Bp_Behemoth.json'))
    OUT = a.out or OUT
    data = {'source': '%s Blueprint chain via tools/AssetDump' % a.label, 'classes': classes,
            'base': {'class': base['class'], 'super': base['super'], 'net_props': len(base['props']), 'net_prop_names': [x['n'] for x in base['props']],
                     'components': base['components']}}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(data, open(OUT, 'w'), indent=1)
    print({c['class']: [p['n'] for p in c['props']] for c in classes}, 'base net props', len(base['props']),
          'base components', len(base['components']))


if __name__ == '__main__':
    main()
