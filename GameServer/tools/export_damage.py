"""Build world/behemoths/damage_model.json (damage rows + exploration curves) from AssetDump output.

  AssetDump dump <dir> Archon/Content/Gameplay/DamageTable \
      Archon/Content/shared/curves/player_to_behemoth_exploration_dmgcurve ... (see CURVES)
  python -m tools.export_damage <dir> [<dir> ...]

Only generated data is written; it stays local like rock.json. Rows keep the fields the server model uses.
"""
import json
import os
import sys

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'world', 'behemoths', 'damage_model.json')
CURVES = ('player_to_behemoth_exploration_dmgcurve', 'player_to_behemoth_exploration_eleweakness_dmgcurve',
          'player_to_behemoth_exploration_elestrength_dmgcurve', 'behemoth_to_player_exploration_dmgcurve',
          'player_to_behemoth_damage_curve_cr20', 'behemoth_to_player_damage_curve_cr20')
ROW_FIELDS = ('BaseDamage', 'PhysicalTypeOverride', 'ExplicitPartBreak', 'ExplicitStagger', 'ExplicitExpose', 'DamageTrue',
              'PhysicalDamage', 'FlameDamage', 'FrostDamage', 'ShockDamage', 'RadiantDamage', 'UmbralDamage', 'TerraDamage',
              'RawElementalDamage', 'ElementalHealthDamageMod', 'Meter')


def find(dirs, name):
    for d in dirs:
        p = os.path.join(d, name + '.json')
        if os.path.exists(p):
            return json.load(open(p, encoding='utf8'))
    raise FileNotFoundError(name)


def main(dirs):
    out = {'curves': {}, 'rows': {}}
    for name in CURVES:
        for e in find(dirs, name):
            keys = ((e.get('Properties') or {}).get('FloatCurve') or {}).get('Keys')
            if keys:
                out['curves'][name] = [[k['Time'], k['Value']] for k in keys]
    table = find(dirs, 'DamageTable')[0]['Rows']
    for k, row in table.items():
        out['rows'][k] = {f: row[f] for f in ROW_FIELDS if f in row}
        tags = [t.get('TagName') for t in row.get('AssetTags') or [] if t.get('TagName')]
        if tags:
            out['rows'][k]['tags'] = tags          # e.g. Combat.Sword.TokenGranter: weapon meter gain (engine/weapon_meter.py)
    # Rock hunt (matchmaker_hunts_table): threat level and power override; GlobalResistance from behemoth_health_as
    out['rock'] = {'normal': {'power': 120.0, 'resistance': 120.0, 'threat_level': 4},
                   'heroic': {'power': 425.0, 'resistance': 120.0, 'threat_level': 13}}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(out, open(OUT, 'w', encoding='utf8'), indent=1)
    print('wrote %s: %d curves, %d rows' % (OUT, len(out['curves']), len(out['rows'])))


if __name__ == '__main__':
    main(sys.argv[1:])
