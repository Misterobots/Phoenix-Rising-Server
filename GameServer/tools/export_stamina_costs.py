"""Write world/player/stamina_costs.json: the stamina each weapon combo state costs.

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
            states['%s.%s:%s' % (pkg, asset, e['Name'])] = {'state': e['Properties'].get('StateName'), 'montage': mpath}
            if mpath:
                montages.add(mpath)
    mdir = os.path.join(scratch, 'montages')
    os.makedirs(mdir, exist_ok=True)
    paks = sorted(game_to_pak(m) for m in montages)
    for i in range(0, len(paks), 60):
        run('dump', mdir, *paks[i:i + 60])
    costs = {}
    for f in glob.glob(os.path.join(mdir, '*.json')):
        with open(f, encoding='utf-8') as fh:
            exports = json.load(fh)
        pkg = next((e.get('Package') for e in exports if e.get('Type') == 'AnimMontage'), None)
        costs[pkg] = sum(float((e.get('Properties') or {}).get('Stamina Cost', 0.0))
                         for e in exports if 'ConsumeStamina' in (e.get('Type') or ''))
    for v in states.values():
        v['cost'] = costs.get(v['montage'], 0.0) if v['montage'] else 0.0
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w', encoding='utf-8') as fh:
        json.dump({'source': 'AnimStateSet ComboAnimState.AnimMontage -> Notify_ConsumeStamina "Stamina Cost" (tools/export_stamina_costs.py)',
                   'states': states}, fh, indent=1, sort_keys=True)
    print('%s: %d states, %d with a cost' % (os.path.normpath(OUT), len(states), sum(1 for v in states.values() if v['cost'])))


if __name__ == '__main__':
    main()
