"""What the game allows where (town / lobby vs hunt / island), read from the client's own data.

  python -m tools.atlas area-rules [--dump]   -> data/atlas/area_rules.txt (+ .json)

1. Quick items and curiosities: catalog row -> item class defaults (AbilityClass) -> ability class defaults
   CanUseInCity, inherited through the class parents. Ability_QuickItem_Base.K2_CanActivateAbility returns
   `!IsInCity() || CanUseInCity`, so an item with CanUseInCity false cannot be used in town (or the lobby).
2. Blueprint area checks: every function in data/atlas/pseudo that calls IsInCity / IsInLobby / IsInIsland /
   IsInBountyFriendlyLocation (what else changes by area: cells, HUD, menus ...).
3. Menu and UI rows with area flags (bDisableDuringHunts, bDisableDuringDeparture, bShowDuringTutorial ...).

Everything is read from data/atlas. --dump first adds what the other commands leave out: the main menu panel
tables (UI folders are not dumped otherwise) and the class defaults of every quick item, curiosity and their
abilities, flare variants included.
"""
import json
import os
import re

from . import assets, blueprints, paths

OUT_TXT = os.path.join(paths.OUT, 'area_rules.txt')
OUT_JSON = os.path.join(paths.OUT, 'area_rules.json')
AREA_CALLS = ('IsInCity', 'IsInLobby', 'IsInIsland', 'IsInBountyFriendlyLocation')
AREA_FLAG = re.compile(r'^b?(Disable|Show|Hide|Allow|Enable)[A-Za-z]*During|InCity|InHunt|InLobby')
DUMP_FOLDERS = ('Archon/Content/UI/MainMenu/', 'Archon/Content/blueprints/InventoryItems/')
DUMP_NAMES = re.compile(r'^(mainmenu\w*panel\w*|(Ability_)?QuickItem_\w+|Ability_Curiosity_\w+)$', re.I)


def dump(refresh=False, log=print):
    """Dump the menu panel tables and the quick item, curiosity and ability class defaults."""
    pkgs = sorted(p for f in DUMP_FOLDERS for p, t in assets.class_listing(f, refresh).items()
                  if t not in assets.SKIP_TYPES and DUMP_NAMES.match(p.rsplit('/', 1)[-1]))
    log('%d packages' % len(pkgs))
    assets.build_packages(pkgs, blueprints.package_index(), refresh, log=lambda m: None)


def _load(name):
    path = paths.class_file(name, '.json', assets.ASSETS_DIR)
    return json.load(open(path, encoding='utf-8')) if os.path.exists(path) else None


def _table_rows(name):
    from .areas import table_rows
    return table_rows(name)[1]


def _cdo(cls):
    """Class defaults of a dumped Blueprint class ('X_C'), or None when not dumped."""
    rec = _load(cls[:-2] if cls.endswith('_C') else cls)
    if rec is None:
        return None
    return next((e.get('props') or {} for k, e in rec['exports'].items() if k.startswith('Default__')), {})


def _parents(cls):
    path = paths.class_file(cls)
    return json.load(open(path, encoding='utf-8')).get('parents') or [] if os.path.exists(path) else []


def can_use_in_city(ability_cls):
    """-> (value, where it is set) through the class chain; (False, 'engine default') when no class sets it,
    (None, why) when a class in the chain has not been dumped."""
    for c in [ability_cls] + _parents(ability_cls):
        if not c.endswith('_C'):
            break                                       # native classes: no Blueprint default
        props = _cdo(c)
        if props is None:
            return None, 'class defaults of %s not dumped' % c
        if 'CanUseInCity' in props:
            return bool(props['CanUseInCity']), c
    return False, 'engine default'


def _class_name(ref):
    """'BlueprintGeneratedClass X_C' or '/Game/.../X.X_C' -> 'X_C'."""
    return re.split(r'[ .]', ref or '')[-1] or None if isinstance(ref, str) else None


def _tables(index, catalogs):
    return sorted(n for n, r in index.items() if r['type'] in ('PlayFabCatalogDataTable', 'DataTable')
                  and (('catalog' in n.lower()) == catalogs) and not n.endswith('_composite'))


def quick_items(index):
    out = []
    for table in _tables(index, catalogs=True):
        for cid, row in _table_rows(table).items():
            item_cls = _class_name(row.get('ItemClass')) if isinstance(row, dict) else None
            if not item_cls or not ('QuickItem' in item_cls or 'Curiosit' in item_cls):
                continue
            props = _cdo(item_cls)
            ability = _class_name(props.get('AbilityClass')) if props else None
            if not ability:
                out.append({'item': cid, 'item_class': item_cls, 'ability': None, 'in_town': None,
                            'why': 'item class defaults not dumped' if props is None else 'no AbilityClass'})
                continue
            val, where = can_use_in_city(ability)
            out.append({'item': cid, 'item_class': item_cls, 'ability': ability, 'in_town': val, 'why': where})
    return out


def blueprint_checks():
    out = []
    for fn_name in sorted(os.listdir(paths.PSEUDO_DIR)):
        cls, fn, hits = fn_name[:-4], None, {}
        for line in open(os.path.join(paths.PSEUDO_DIR, fn_name), encoding='utf-8', errors='replace'):
            if line.startswith('=== '):
                fn = line[4:].strip()
                continue
            for call in AREA_CALLS:
                if call + '(' in line:
                    hits.setdefault(fn, set()).add(call)
        out += [{'class': cls, 'function': f, 'checks': sorted(c)} for f, c in sorted(hits.items(), key=lambda x: str(x[0]))]
    return out


def ui_flags(index):
    out = []
    for table in _tables(index, catalogs=False):
        for key, row in _table_rows(table).items():
            if not isinstance(row, dict):
                continue
            flags = {k: v for k, v in row.items() if isinstance(v, bool) and AREA_FLAG.search(k)}
            if flags:
                out.append({'table': table, 'row': key, 'flags': flags})
    return out


def main(log=print):
    index = json.load(open(assets.INDEX, encoding='utf-8'))
    qi, bp, ui = quick_items(index), blueprint_checks(), ui_flags(index)
    with open(OUT_JSON, 'w', encoding='utf-8') as f:
        json.dump({'quick_items': qi, 'blueprint_checks': bp, 'ui_flags': ui}, f, indent=1)
    w = ['# What is allowed where (python -m tools.atlas area-rules; see topics/area_rules.md)\n',
         '## Quick items and curiosities: usable in town? (CanUseInCity; Ability_QuickItem_Base.K2_CanActivateAbility)']
    for r in sorted(qi, key=lambda r: (str(r['in_town']), r['item'])):
        w.append('  %-5s %-42s %-44s %s' % ({True: 'TOWN', False: 'hunt', None: '?'}[r['in_town']], r['item'],
                                             r['ability'] or r['item_class'], r['why']))
    w.append('\n## Blueprint functions that branch on the area (%d)' % len(bp))
    for r in bp:
        w.append('  %-48s %-44s %s' % (r['class'], r['function'], ','.join(r['checks'])))
    w.append('\n## Menu / UI rows with area flags (%d)' % len(ui))
    for r in ui:
        w.append('  %-60s %-28s %s' % (r['table'][-60:], r['row'],
                                       ' '.join('%s=%s' % kv for kv in sorted(r['flags'].items()))))
    with open(OUT_TXT, 'w', encoding='utf-8') as f:
        f.write('\n'.join(w) + '\n')
    n = lambda v: sum(r['in_town'] is v for r in qi)
    log('quick items %d (town %d, hunt only %d, unknown %d); blueprint area checks %d; ui rows %d'
        % (len(qi), n(True), n(False), n(None), len(bp), len(ui)))
