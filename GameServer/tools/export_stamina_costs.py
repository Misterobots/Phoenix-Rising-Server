"""Write world/player/combat_moves.json (every weapon combo state: montage, transitions, entry condition, every montage
notify with its time, class and properties) and world/player/stamina_costs.json (the stamina view of it).

  python -m tools.export_stamina_costs <scratch dir>

Dumps every Weapons/AnimStates/*animstateset with AssetDump, then each ComboAnimState's AnimMontage, and sums the
"Stamina Cost" of the montage's Notify_ConsumeStamina notifies (Notify_ConsumeStamina.Received_Notify ->
BP_PlayerCharacter.ConsumeStamina). Keys match ServerStartNextMove's ComboInputBuffer.DestState soft path:
'<package>.<asset>:ComboAnimState_N'.
"""
import glob
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETDUMP = os.path.normpath(os.path.join(HERE, '..', '..', '..', '..', 'Mystic-Paradox', 'tools', 'AssetDump', 'bin', 'AssetDump.dll'))
OUT = os.path.join(HERE, '..', 'world', 'player', 'stamina_costs.json')
MOVES = os.path.join(HERE, '..', 'world', 'player', 'combat_moves.json')


def _ref(v):
    """An object reference -> its object path (or name); other values unchanged."""
    if isinstance(v, dict) and ('ObjectPath' in v or 'ObjectName' in v):
        return v.get('ObjectName')
    if isinstance(v, dict) and 'AssetPathName' in v:
        return v['AssetPathName'] + ((':' + v['SubPathString']) if v.get('SubPathString') else '')
    if isinstance(v, dict):
        return {k: _ref(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_ref(x) for x in v]
    return v


def _transition(t):
    keep = ('bEnabled', 'DestState', 'TransitionEvent', 'NextMontageSection', 'Context', 'Tags', 'PlayRate', 'ComboInputKey',
            'BufferMethod', 'BufferPriority', 'PressAndHoldTime', 'Condition', 'bAllowWhileStaggered', 'FlyingMove')
    return {k: _ref(t[k]) for k in keep if k in t}


def run(*args):
    return subprocess.run(['dotnet', ASSETDUMP] + list(args), capture_output=True, text=True, timeout=900).stdout


def game_to_pak(path):
    return 'Archon/Content/' + path[len('/Game/'):]


def main():
    scratch = os.path.abspath(sys.argv[1])
    os.makedirs(scratch, exist_ok=True)
    sets = [l.strip() for l in run('find', 'Weapons/AnimStates/', 'animstateset').splitlines() if l.startswith('Archon/')]
    run('dump', scratch, *sets)
    states, montages = {}, set()
    for f in glob.glob(os.path.join(scratch, '*animstateset*.json')):
        with open(f, encoding='utf-8') as fh:
            exports = json.load(fh)
        pkg = next((e.get('Package') for e in exports if isinstance(e, dict) and e.get('Package')), None)
        if not pkg:
            continue
        asset = pkg.rsplit('/', 1)[1]
        for e in exports:
            if e.get('Type') != 'ComboAnimState':
                continue
            m = (e.get('Properties') or {}).get('AnimMontage')
            mpath = m.get('ObjectPath', '').rsplit('.', 1)[0] if isinstance(m, dict) else None
            p = e.get('Properties') or {}
            states['%s.%s:%s' % (pkg, asset, e['Name'])] = {
                'state': p.get('StateName'), 'montage': mpath, 'set': pkg,
                'entry_condition': _ref(p.get('EntryCondition')),
                'transitions': [_transition(t) for t in p.get('Transitions') or []],
                'other': {k: _ref(v) for k, v in p.items() if k not in ('StateName', 'AnimMontage', 'EntryCondition', 'Transitions')}}
            if mpath:
                montages.add(mpath)
    mdir = os.path.join(scratch, 'montages')
    os.makedirs(mdir, exist_ok=True)
    paks = sorted(game_to_pak(m) for m in montages)
    for i in range(0, len(paks), 60):
        run('dump', mdir, *paks[i:i + 60])
    costs, montage_info = {}, {}
    for f in glob.glob(os.path.join(mdir, '*.json')):
        with open(f, encoding='utf-8') as fh:
            exports = json.load(fh)
        m = next((e for e in exports if e.get('Type') == 'AnimMontage'), None)
        if m is None:
            continue
        pkg, mp = m.get('Package'), m.get('Properties') or {}
        objs = {e.get('Name'): e for e in exports}
        notifies = []
        for n in mp.get('Notifies') or []:
            obj = n.get('Notify') or n.get('NotifyStateClass') or {}
            name = (obj.get('ObjectName') or '').split("'")[1].split(':')[-1] if "'" in (obj.get('ObjectName') or '') else None
            inst = objs.get(name, {}) if name else {}
            notifies.append({'name': n.get('NotifyName'), 'class': inst.get('Type'), 'time': n.get('LinkValue'),
                             'duration': n.get('Duration'), 'track': n.get('TrackIndex'),
                             'properties': _ref(inst.get('Properties') or {})})
        costs[pkg] = sum(float((x['properties'] or {}).get('Stamina Cost', 0.0)) for x in notifies if 'ConsumeStamina' in (x['class'] or ''))
        montage_info[pkg] = {'length': mp.get('SequenceLength'), 'rate_scale': mp.get('RateScale'),
                             'sections': [{'name': c.get('SectionName'), 'next': c.get('NextSectionName'), 'start': c.get('SegmentBeginTime')}
                                          for c in mp.get('CompositeSections') or []],
                             'segments': [{'slot': t.get('SlotName'), 'anims': [{'anim': _ref(sg.get('AnimReference')), 'start': sg.get('StartPos'),
                                           'length': sg.get('AnimEndTime'), 'rate': sg.get('AnimPlayRate'), 'loops': sg.get('LoopingCount')}
                                          for sg in (t.get('AnimTrack') or {}).get('AnimSegments') or []]} for t in mp.get('SlotAnimTracks') or []],
                             'notifies': notifies, 'stamina_cost': costs[pkg]}
    for v in states.values():
        v['cost'] = costs.get(v['montage'], 0.0) if v['montage'] else 0.0
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(MOVES, 'w', encoding='utf-8') as fh:
        json.dump({'source': 'AssetDump of Weapons/AnimStates/*animstateset and their ComboAnimState montages (tools/export_stamina_costs.py)',
                   'states': states, 'montages': montage_info}, fh, indent=1, sort_keys=True)
    with open(OUT, 'w', encoding='utf-8') as fh:
        json.dump({'source': 'AnimStateSet ComboAnimState.AnimMontage -> Notify_ConsumeStamina "Stamina Cost" (tools/export_stamina_costs.py)',
                   'states': {k: {'state': v['state'], 'montage': v['montage'], 'cost': v['cost']} for k, v in states.items()}},
                  fh, indent=1, sort_keys=True)
    print('%s: %d states, %d montages; %d states with a stamina cost' % (
        os.path.normpath(MOVES), len(states), len(montage_info), sum(1 for v in states.values() if v['cost'])))


if __name__ == '__main__':
    main()
