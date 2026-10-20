"""Write world/behemoths/<variant>_damage.json from an AssetDump of the variant's damage DataTable (game data, generated locally).

  dotnet <AssetDump.dll> dump <abs dir> Archon/Content/Monsters/aq/lerawr/data_tables/lerawr_beta_damage_table
  python -m tools.export_behemoth_damage <dump json> --variant lerawr_beta

Rows keep the shape rock.json uses for its damage tables ({key: {'elements': {XDamage: n}, 'tags': [...]}}), plus the full
FDamageTableData fields under 'row' so ClientTryHit can carry the authored row. Keys are the montage damage windows' DamageKeys.
"""
import argparse
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROW_FIELDS = ('BaseDamage', 'ExplicitPartBreak', 'ExplicitStagger', 'ExplicitExpose', 'DamageTrue', 'PhysicalDamage', 'FlameDamage',
              'FrostDamage', 'ShockDamage', 'RadiantDamage', 'UmbralDamage', 'TerraDamage', 'RawElementalDamage',
              'ElementalHealthDamageMod', 'Meter')


def rows_of(dump):
    return next(e['Rows'] for e in dump if isinstance(e, dict) and 'Rows' in e)


def convert(rows):
    out = {}
    for key, r in rows.items():
        out[key] = {'elements': {e: v for e, v in r.items() if e.endswith('Damage') and e != 'BaseDamage' and v},
                    'tags': [t['TagName'] if isinstance(t, dict) else t for t in r.get('AssetTags') or []],
                    'row': {f: r[f] for f in ROW_FIELDS if f in r}}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('dump')
    ap.add_argument('--variant', required=True)
    ap.add_argument('--out')
    a = ap.parse_args()
    with open(a.dump, encoding='utf-8') as f:
        rows = convert(rows_of(json.load(f)))
    out = a.out or os.path.join(HERE, '..', 'world', 'behemoths', a.variant + '_damage.json')
    with open(out, 'w', encoding='utf-8') as f:
        json.dump({'source': os.path.basename(a.dump) + ' via tools/AssetDump', 'rows': rows}, f, indent=1)
    print('%s: %d rows' % (out, len(rows)))


if __name__ == '__main__':
    main()
