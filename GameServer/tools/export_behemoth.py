"""Generate world/behemoths/rock.json from AssetDump output of Content/Monsters/sq/rock (game data: generated locally, not distributed).

python -m tools.export_behemoth [dump root]
Dump root (default debug/runs/hunt_assets) must contain, from `tools/AssetDump`:
  rock/       dumpprefix archon/content/monsters/sq/rock/  (action/damage tables are DataTables there)
  rock_atk/   dumpprefix archon/content/monsters/sq/rock/blueprints/   (no type filter)
  rock_mont/  dumpprefix archon/content/monsters/sq/rock/montages/     (no type filter)
  enums/      dumpprefix archon/content/monsters/_shared/ UserDefinedEnum
See docs/BEHEMOTH_SPEC_ROCK_2026-10-14.md for what each field means.
"""
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ROOT = os.path.join(HERE, '..', '..', '..', 'debug', 'runs', 'hunt_assets')
OUT = os.path.join(HERE, '..', 'world', 'behemoths', 'rock.json')


def key(k):
    """Strip the editor GUID suffix from a user-defined-struct field name."""
    return re.sub(r'_\d+_[0-9A-F]{32}$', '', k)


def ref(v):
    """ObjectName 'Class'Name' -> Name."""
    if isinstance(v, dict) and 'ObjectName' in v:
        s = v['ObjectName']
        return s.split("'")[-2].split(':')[-1] if "'" in s else s
    return v


def clean(o):
    if isinstance(o, dict):
        if 'ObjectName' in o:
            return ref(o)
        if 'AssetPathName' in o:
            return o['AssetPathName']
        return {key(k): clean(v) for k, v in o.items() if k not in ('Flags', 'Outer', 'Class')}
    if isinstance(o, list):
        return [clean(x) for x in o]
    return o


def enum_n(s):
    m = re.search(r'NewEnumerator(\d+)$', s) if isinstance(s, str) else None
    return int(m.group(1)) if m else s


def load(path):
    with open(path) as f:
        return json.load(f)


def state_names(root):
    f = glob.glob(os.path.join(root, 'enums', '*behemoth_states_enum.json'))[0]
    for e in load(f):
        if e['Type'] == 'UserDefinedEnum':
            return {int(x['Key'].replace('NewEnumerator', '')): x['Value'].get('CultureInvariantString') or x['Value'].get('SourceString')
                    for x in e['Properties']['DisplayNameMap']}


def table_rows(path):
    for e in load(path):
        if e['Type'] == 'DataTable':
            return clean(e.get('Rows') or {})
    return {}


def box(b):
    # OptimalHitArea has no Offset (it is centred on the behemoth); a missing offset is zero
    off = b.get('Offset') or {'X': 0.0, 'Y': 0.0, 'Z': 0.0}
    return {'offset': [off['X'], off['Y'], off['Z']],
            'extent': [b['Extent']['X'], b['Extent']['Y'], b['Extent']['Z']], 'yaw': b.get('HitAreaYaw', 0.0)}


def attack_bp(path):
    for e in load(path):
        if e['Name'].startswith('Default__'):
            p = clean(e['Properties'])
            if not p:
                continue
            arcs = []
            for d in p.get('TargettingData') or []:
                a = d.get('ArcData')
                if a and d.get('TargettingType') == 'behemoth_targetting_type::NewEnumerator0':
                    arcs.append({'range': a['Range'], 'angle': a['Angle'], 'center_angle': a['ArcCenterAngle'],
                                 'offset': [a['CenterOffset']['X'], a['CenterOffset']['Y']]})
            req = p.get('BehemothRequirements') or {}
            return {
                'eval_type': enum_n(p.get('TargettingType')) if p.get('TargettingType') else 0,
                'arcs': arcs,
                'boxes': [box(b) for b in p.get('HitAreaBoxes', []) if any(b['Extent'].values())],
                'optimal': box(p['OptimalHitArea']) if p.get('OptimalHitArea') else None,
                'excluded_states': [enum_n(s) for s in p.get('ExcludedStates', [])],
                'require_states': [enum_n(s) for s in p['RequireStates']] if 'RequireStates' in p else [9],
                'classification': req.get('RequireBehemothClassificationName'),
                'part_requirements': req.get('PartRequirements'),
                # Authored vectors only: the action recomputes AttackVectorsN_Simple at init, so serialized _Simple is stale.
                'attack_vectors1': [[v['X'], v['Y'], v['Z']] for v in p.get('AttackVectors1') or []],
                'attack_vectors2': [[v['X'], v['Y'], v['Z']] for v in p.get('AttackVectors2') or []],
                'scale_hit_area': p.get('ScaleHitAreaToBehemothSize'),
                'debug_scale': p.get('DebugScale'),
                'allow_nav_links': p.get('AllowNavLinks'),
                'create_helper': p.get('CreateHelper'),
            }
    return None


def montage(path):
    d = load(path)
    by = {e['Name']: e for e in d}
    m = next(e for e in d if e['Type'] == 'AnimMontage')
    events = []
    for n in m['Properties'].get('Notifies', []):
        obj = ref(n.get('Notify')) or ref(n.get('NotifyStateClass'))
        ex = by.get(obj)
        props = clean(ex.get('Properties', {})) if ex else {}
        ev = {'t': round(n['LinkValue'], 4), 'duration': round(n['Duration'], 4), 'type': ex['Type'] if ex else n['NotifyName'],
              'server': bool(n.get('bTriggerOnDedicatedServer'))}
        t = ev['type']
        if t == 'archon_damage_window_C':
            ev['damage_keys'] = props.get('DamageKeys', [])
            bodies = set()
            for v in (props.get('PhysicsBodyNameMap') or []):
                bodies.update((v.get('Value') or {}).get('PhysicsBodies', []))
            ev['bodies'] = sorted(bodies)
        elif t == 'spawn_aoe_class_bpn_C':
            ev.update({k: props.get(k) for k in ('AoEClassToSpawn', 'BoneToAttachTo', 'SpawnOffset', 'DamageRadius')})
        elif t == 'spawn_actors_ns_C':
            gd = (props.get('ActorData') or {}).get('EQSGameplayData') or {}
            ev.update({'actor': (props.get('ActorData') or {}).get('ActorClass'), 'damage_row': gd.get('DamageRowName'),
                       'per_target': gd.get('ProjectilesPerTarget')})
        elif t == 'damage_guid_ns_C':
            ev['identifier'] = props.get('Identifier')
        elif t == 'Notify_StaggerProneBehemoth_C':
            ev.update({'threshold': props.get('StaggerProneDamageThreshold'), 'parts': props.get('StaggerPronePartNames')})
        elif t == 'call_behemoth_function_bpns_C':
            ev.update({'begin': props.get('FunctionName_Begin'), 'end': props.get('FunctionName_End')})
        elif t == 'behemoth_custom_notify_event_bpn_C':
            ev['event'] = props.get('CustomNotifyEventName')
        elif t == 'ModifyPartCollisionChannelNotify':
            ev['bones'] = sorted({b for i in props.get('Infos', []) for b in i.get('BoneNames', [])})
        events.append(ev)
    events.sort(key=lambda e: (e['t'], e['type']))
    return {'length': m['Properties']['SequenceLength'], 'events': events}


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_ROOT
    rock = os.path.join(root, 'rock')
    atk_dir = os.path.join(root, 'rock_atk')
    mont_dir = os.path.join(root, 'rock_mont')
    pre = 'Archon__Content__Monsters__sq__rock__'
    attacks = {}
    for f in glob.glob(os.path.join(atk_dir, '*.json')):
        name = os.path.basename(f).split('blueprints__')[1][:-5]
        a = attack_bp(f)
        if a:
            attacks[name] = a
    montages = {}
    for f in glob.glob(os.path.join(mont_dir, '*.json')):
        montages[os.path.basename(f).split('montages__')[1][:-5].lower()] = montage(f)

    def actions(table):
        out = {}
        for name, r in table_rows(os.path.join(rock, pre + table + '.json')).items():
            cls = r['Class']
            bp = cls[:-2] if cls.endswith('_C') else cls
            m = str(r['Montage'])
            mk = m.lower() if m.lower() in montages else None
            out[name] = {
                'class': cls, 'montage': m, 'montage_data': montages.get(mk),
                'play_rate': r['MontagePlayRate'], 'cooldown': r['Cooldown'], 'cooldown_variance': r['CooldownVariance'],
                'base_value': r['BaseValue'], 'stamina': r['StaminaDrainAmount'], 'usage_count': r['UsageCount'],
                'multi_behemoth_cooldown_delta': r['CooldownDeltaInMultiBehemothFights'],
                'min_intensity': enum_n(r['MinIntensity']), 'max_intensity': enum_n(r['MaxIntensity']),
                'attack': attacks.get(bp),
            }
        return out

    damage = {}
    for tbl in ('rock_damage_table', 'rock_alpha_damage_table'):
        damage[tbl] = {k: {'elements': {e: v for e, v in r.items() if e.endswith('Damage') and v},
                           'tags': [t['TagName'] for t in r.get('AssetTags', [])]}
                       for k, r in table_rows(os.path.join(rock, pre + tbl + '.json')).items()}
    data = {
        'source': 'Content/Monsters/sq/rock (2.1.1 paks) via tools/AssetDump',
        'states': state_names(root),
        'health': {'normal': 32000.0, 'archetype': 36000.0},
        'rage': {'duration': 40.0, 'cooldown': 210.0},
        'variants': {'normal': {'actions': actions('rock_action_set_table'), 'damage': damage['rock_damage_table']},
                     'alpha': {'actions': actions('rock_alpha_action_set_table'), 'damage': damage['rock_alpha_damage_table']}},
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w') as f:
        json.dump(data, f, indent=1)
    n = {v: len(d['actions']) for v, d in data['variants'].items()}
    miss = [(v, a) for v, d in data['variants'].items() for a, x in d['actions'].items() if not x['attack'] or not x['montage_data']]
    print('actions', n, 'without attack/montage data', miss)


if __name__ == '__main__':
    main()
