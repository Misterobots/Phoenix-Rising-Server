"""Write world/creatures/raptor/: what the server needs to run the ally raptors (repeater Q, dp_y_ability_spawn_raptor_ab, and the
Alluring Moons legendary part) from their own game data.

  python -m tools.export_raptors <scratch dir>

- bt/*.json: af_raptor_bt and the trees it runs (friendly, ally combat, enemy, combat, shared deaggro/despawn/death), AssetDump
  JSON as world/dumps/bt holds for the behemoth (mpgs.behemoth.bt.TreeSet.load reads the folder).
- montages.json: every montage those trees play: length and notifies (damage windows with their DamageKeys and bodies), read as
  tools.export_behemoth.montage reads behemoth montages.
- damage.json: af_raptor_damage_table rows (FDamageTableData).
- loco.json: af_raptor_loco_table: behemoth_loco_type index -> MovementSpeed (cm/s).
"""
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, '..')))
from tools.export_behemoth import montage, key  # noqa: E402
from tools.export_weapon_parts import ASSETDUMP  # noqa: E402

OUT = os.path.join(HERE, '..', 'world', 'creatures', 'raptor')
RAPTOR = 'Archon/Content/creatures/tp/raptor/'
TREES = [RAPTOR + '_shared/ai/' + n for n in ('af_raptor_bt', 'af_raptor_friendly_bt', 'af_raptor_ally_combat_bt',
                                               'af_raptor_enemy_bt', 'af_raptor_combat_bt')] + [
    'Archon/Content/creatures/_shared/ai/shared_creature_deaggro_bt',
    'Archon/Content/creatures/_shared/ai/shared_creature_forced_despawn_bt',
    'Archon/Content/_shared/blueprints/bt/shared_gen_death_bt']
TABLES = ['Archon/Content/creatures/_shared/data_tables/af_raptor_damage_table', RAPTOR + '_shared/ai/af_raptor_loco_table']


def dump(scratch, *pkgs):
    subprocess.run(['dotnet', ASSETDUMP, 'dump', scratch] + list(pkgs), capture_output=True, text=True, timeout=900)


def _load(scratch, pkg):
    with open(os.path.join(scratch, pkg.rsplit('/', 1)[1] + '.json'), encoding='utf-8') as f:
        return json.load(f)


def main():
    scratch = os.path.abspath(sys.argv[1])
    os.makedirs(os.path.join(OUT, 'bt'), exist_ok=True)
    dump(scratch, *(TREES + TABLES))
    montage_pkgs = set()
    for pkg in TREES:
        exports = _load(scratch, pkg)
        with open(os.path.join(OUT, 'bt', pkg.rsplit('/', 1)[1] + '.json'), 'w', encoding='utf-8') as f:
            json.dump(exports, f)
        for path in re.findall(r'"ObjectPath": "(/Game/[^"]+)"', json.dumps(exports)):
            if "AnimMontage" in path or '_mo' in path.rsplit('/', 1)[1]:
                montage_pkgs.add('Archon/Content/' + path[len('/Game/'):].rsplit('.', 1)[0])
    montage_pkgs = sorted(p for p in montage_pkgs if p.rsplit('/', 1)[1].startswith('c_'))
    dump(scratch, *montage_pkgs)
    montages = {}
    for pkg in montage_pkgs:
        try:
            montages[pkg.rsplit('/', 1)[1]] = dict(montage(os.path.join(scratch, pkg.rsplit('/', 1)[1] + '.json')), package='/Game/' + pkg[len('Archon/Content/'):])
        except Exception as exc:                          # reported, not skipped silently
            print('montage %s: %s: %s' % (pkg, type(exc).__name__, exc))
    dmg = _load(scratch, TABLES[0])
    dmg = (dmg[0] if isinstance(dmg, list) else dmg).get('Rows')
    loco_exp = _load(scratch, TABLES[1])
    cdo = next(e for e in loco_exp if e.get('Type', '').endswith('_C') and e['Name'].startswith('Default__'))
    loco = {}
    for row in cdo['Properties']['LocoData']:
        r = {key(k): v for k, v in row.items()}
        loco[int(str(r['LocoType']).rsplit('NewEnumerator', 1)[1])] = r['MovementSpeed']
    for name, data in (('montages', montages), ('damage', dmg), ('loco', loco)):
        with open(os.path.join(OUT, name + '.json'), 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=1)
    print('wrote %s: %d trees, %d montages, %d damage rows, loco %s' % (OUT, len(TREES), len(montages), len(dmg), loco))


if __name__ == '__main__':
    main()
