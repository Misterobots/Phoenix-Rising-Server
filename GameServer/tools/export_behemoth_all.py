"""Generate world/behemoths/<variant>.json for every behemoth action-set table in the 2.1.1 paks.

Generalises tools/export_behemoth.py (rock only). Game data is generated locally, not distributed.

python -m tools.export_behemoth_all <classes.tsv> <work dir> [--out world/behemoths/all]

  classes.tsv  export-class index from `tools/AssetDump classes Archon/Content <classes.tsv>` (~2.5 min)
  work dir     scratch folder for the per-package AssetDump JSON (reused between runs; delete to re-dump)

For each `*action_set*` DataTable under Archon/Content/Monsters it writes one JSON with the same action shape as
rock.json (class, montage, montage_data, cooldown, intensity, attack targeting). Damage tables are not exported here.
Packages that cannot be dumped are reported, not skipped silently.
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETDUMP = os.path.normpath(os.path.join(HERE, '..', '..', 'tools', 'AssetDump', 'bin', 'AssetDump.dll'))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, '..')))

from tools.export_behemoth import attack_bp, montage, table_rows, enum_n  # noqa: E402


def read_index(path):
    """classes.tsv -> (pkg by class name, pkg by montage name, class name set) for Monsters content."""
    cls, mont, tables = {}, {}, {}
    with open(path, encoding='utf-8') as f:
        for line in f:
            parts = line.rstrip('\r\n').split('\t')
            if len(parts) != 3:
                continue
            pkg, name, kind = parts
            if not pkg.startswith('Archon/Content/Monsters/') and not pkg.startswith('Archon/Content/_shared/'):
                continue
            pkg = pkg[:-len('.uasset')] if pkg.endswith('.uasset') else pkg
            if kind == 'BlueprintGeneratedClass' and name.endswith('_C') and not name.startswith('Default__'):
                cls.setdefault(name, pkg)
            elif kind == 'AnimMontage':
                mont.setdefault(name.lower(), pkg)
            elif kind == 'DataTable' and 'action_set' in name:
                tables.setdefault(name, pkg)
    return cls, mont, tables


class Dumper:
    """One AssetDump call per package; packages already dumped are reused."""

    def __init__(self, work):
        self.work = work
        self.failed = []
        os.makedirs(work, exist_ok=True)

    def path(self, pkg):
        return os.path.join(self.work, os.path.basename(pkg) + '.json')

    def get(self, pkg):
        out = self.path(pkg)
        if os.path.exists(out):
            return out
        r = subprocess.run(['dotnet', ASSETDUMP, 'dump', os.path.abspath(self.work), 'Archon/' + pkg.split('Archon/', 1)[-1]],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=600)
        if not os.path.exists(out):
            self.failed.append(pkg)
            return None
        return out


def load(p):
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('classes')
    ap.add_argument('work')
    ap.add_argument('--out', default=os.path.join(HERE, '..', 'world', 'behemoths', 'all'))
    a = ap.parse_args()

    cls, mont, tables = read_index(a.classes)
    dumper = Dumper(os.path.join(a.work, 'pkgs'))
    print('action-set tables:', len(tables))

    # 1. action-set tables -> rows
    rows = {}
    for tname, pkg in sorted(tables.items()):
        j = dumper.get(pkg)
        if not j:
            continue
        variant = re.sub(r'_action_set(_combotable|_table|_set)?$', '', tname.lower())
        rows[variant] = (table_rows(j), pkg)

    # 2. attack BPs and montages referenced by those rows
    need_cls, need_mont = set(), set()
    for variant, (r, _) in rows.items():
        for row in r.values():
            if isinstance(row, dict):
                if row.get('Class'):
                    need_cls.add(str(row['Class']))
                if row.get('Montage'):
                    need_mont.add(str(row['Montage']).lower())
    for c in sorted(need_cls):
        if c in cls:
            dumper.get(cls[c])
    for m in sorted(need_mont):
        if m in mont:
            dumper.get(mont[m])

    # 3. build one JSON per variant
    os.makedirs(a.out, exist_ok=True)
    report = {}
    for variant, (r, _) in sorted(rows.items()):
        actions, missing = {}, []
        for name, row in r.items():
            if not isinstance(row, dict):
                continue
            c = str(row.get('Class', ''))
            m = str(row.get('Montage', ''))
            attack = mdata = None
            try:
                if c in cls and os.path.exists(dumper.path(cls[c])):
                    attack = attack_bp(dumper.path(cls[c]))
                if m.lower() in mont and os.path.exists(dumper.path(mont[m.lower()])):
                    mdata = montage(dumper.path(mont[m.lower()]))
            except (StopIteration, KeyError, TypeError) as e:
                missing.append(f'{name}: parse error {e!r}')
                attack = attack or None
            if not attack or not mdata:
                missing.append(name)
            actions[name] = {
                'class': c, 'montage': m, 'montage_data': mdata,
                'play_rate': row.get('MontagePlayRate'), 'cooldown': row.get('Cooldown'),
                'cooldown_variance': row.get('CooldownVariance'), 'base_value': row.get('BaseValue'),
                'stamina': row.get('StaminaDrainAmount'), 'usage_count': row.get('UsageCount'),
                'multi_behemoth_cooldown_delta': row.get('CooldownDeltaInMultiBehemothFights'),
                'min_intensity': enum_n(row.get('MinIntensity')), 'max_intensity': enum_n(row.get('MaxIntensity')),
                'attack': attack,
            }
        out = {'source': 'Archon/Content/Monsters action-set table (2.1.1 paks) via tools/AssetDump',
               'table_package': 'Archon/' + rows[variant][1].split('Archon/', 1)[-1],
               'actions': actions}
        with open(os.path.join(a.out, variant + '.json'), 'w', encoding='utf-8') as f:
            json.dump(out, f, indent=1)
        report[variant] = {'actions': len(actions), 'missing_attack_or_montage': missing}

    with open(os.path.join(a.out, '_report.json'), 'w', encoding='utf-8') as f:
        json.dump({'variants': report, 'failed_dumps': dumper.failed}, f, indent=1)
    total = sum(v['actions'] for v in report.values())
    gap = sum(len(v['missing_attack_or_montage']) for v in report.values())
    print('variants', len(report), 'actions', total, 'without attack or montage data', gap, 'failed dumps', len(dumper.failed))


if __name__ == '__main__':
    main()
