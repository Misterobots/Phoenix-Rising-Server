"""Items, rewards and islands: dump their data and write sheets with coverage tables.

Same machinery as weapons.py (folders, name prefixes, referenced classes two steps out, Blueprint code), with
these areas:

  items     every backend catalog (lanterns, quick items, flasks, cells, materials, currencies, cosmetics,
            tokens, keys, bundles, containers, drop tables), lantern and inventory-item Blueprints and abilities,
            consumables, item and aether-core boosts
  rewards   bounties, experience and prestige tracks, mastery, player journey and progression, Hunt Pass seasons,
            achievements, game activities, VIP, vendors
  islands   game modes (exploration island specs and content assets, escalation, gauntlet, portals, events,
            encounters), hunt modifiers, atmosphere and behemoth lists, schedules and matchmaking

  dump    python -m tools.atlas areas --dump items rewards islands
  sheets  data/atlas/items/, rewards/, islands/ and items.txt, rewards.txt, islands.txt (coverage tables)

The sheets render every data table row by row, keep the values that matter (cosmetic fields left out), and mark
every item id, class and table row they name with whether the atlas has it, so the reward tables link to the
items they grant and the island specs to the behemoth sheets.
"""
import json
import os
import re

from . import assets, blueprints, paths, weapons

C = 'Archon/Content/'
# area: (folders, name prefixes, folders left out)
AREAS = {
    'items': (('Gameplay/Backend/', 'blueprints/Lanterns/', 'blueprints/InventoryItems/', '_shared/blueprints/buffs/Lantern/',
               'game/consumables/', 'Gameplay/item_boosts/', 'Gameplay/aether_core_boosts/'),
              ('lantern_', 'ability_quickitem', 'quickitem_', 'flask_', 'currency_', 'dyes_', 'emote', 'banner_',
               'title_', 'grenade_'),
              ('/flares/',)),
    'rewards': (('Gameplay/Bounty/', 'Gameplay/Experience/', 'Gameplay/Mastery/', 'Gameplay/PlayerJourney/',
                 'Gameplay/progression/', 'Gameplay/hunt_pass/', 'Gameplay/Achievements/', 'Gameplay/GameActivity/',
                 'Gameplay/VIP/', 'Gameplay/PlayerDataRepair/'),
                ('mastery_', 'bounty_', 'hunt_pass', 'huntpass', 'season_', 'reward', 'vendor', 'npc_cellvendor',
                 'experience_', 'prestige_', 'player_journey', 'slayers_path', 'crafting_', 'purchase_'),
                ()),
    'islands': (('gamemodes/', 'Gameplay/hunts/', 'blueprints/InteractiveObjects/Gauntlet/',
                 'Character/npc/island_meta_events/'),
                ('island_', 'exploration_', 'escalation_', 'gauntlet_', 'portal_', 'patrol', 'encounter_',
                 'backup_encounter', 'matchmaker', 'schedule'),
                ()),
}
ORDER = ('items', 'rewards', 'islands')
# 418 flare colour variants (QuickItem_Flare_NNN and their abilities) differ only in looks; not dumped.
FLARE_VARIANT = re.compile(r'^(QuickItem|Ability_QuickItem)_Flare_\w+_C$', re.I)
# Class and asset paths that only carry looks (atmospheres, emote and speaker animations, UI); named, never counted.
LOOKS_ONLY = ('/atmospheres/', '/anims/', '/anim/', '/cinematics/', '/emotes/', '/speakers/', '/ui/', '/effects/', '/audio/', '/characterprops/',
              '/maps/', '/world/', '/media/', '/engine/', '/basicshapes/')


def packages(key, idx):
    """{package: why} for one area."""
    folders, prefixes, leave_out = AREAS[key]
    out = {}
    for f in folders:
        for p, t in weapons._listing(f).items():
            if t not in weapons.SKIP_TYPES and not any(x in p.lower() for x in leave_out):
                out[p] = 'folder'
    for p in sorted({p for ps in idx.values() for p in ps}):
        n = p.rsplit('/', 1)[-1].lower()
        if p not in out and n.startswith(prefixes) and weapons._logic(p) and '/anims/' not in p.lower() \
                and not any(x in p.lower() for x in leave_out):
            out[p] = 'prefix'
    return out


def dump(keys, refresh=False, log=print):
    weapons.dump(keys, refresh=refresh, log=log, find=packages)
    idx = blueprints.package_index()
    for key in keys:
        follow_tables(key, idx, refresh, log)


def follow_tables(key, idx, refresh=False, log=print, steps=3):
    """Dump the tables this area's rows name in row handles ('table[Row]') that are not in the atlas yet, and the
    tables those name, recording them as referenced packages of the area."""
    lst = _area_list(key)
    for step in range(steps):
        index = json.load(open(assets.INDEX, encoding='utf-8'))
        names = set()
        for p in set(lst['packages']) | set(lst['referenced']):
            rec = _rec(lst.get('aliases', {}).get(p) or p.rsplit('/', 1)[-1])
            if not rec:
                continue
            for _, v in weapons._paths(rec['exports']):
                if not isinstance(v, str) or (v.startswith('/Game/') and _looks_only(v)):
                    continue
                m = _HANDLE.match(v)
                if m and m.group(1) not in index and m.group(1) != 'None':
                    names.add(m.group(1))
                m = _CLASS.match(v) or _ASSET.match(v)
                if m:
                    n = (m.group(1) or m.group(2)) if m.re is _CLASS else m.group(2)
                    n = n[:-2] if n.endswith('_C') else n
                    if n not in index and not FLARE_VARIANT.match(n + '_C'):
                        names.add(n)
        new = sorted({(idx.get(n.lower()) or [None])[0] for n in names} - {None} - set(lst['referenced']) -
                     set(lst['packages']))
        new = [p for p in new if not _looks_only(p)]
        if not new:
            break
        log('%s: tables, classes and assets the rows name, step %d: %d packages' % (key, step + 1, len(new)))
        assets.build_packages(new, idx, refresh, log=lambda m: None)
        from .build import build
        build(sorted(p.rsplit('/', 1)[-1] + '_C' for p in weapons._class_types(new, {})), refresh=refresh,
              log=lambda m: None)
        lst['referenced'] = sorted(set(lst['referenced']) | set(new))
        with open(weapons._list_path(key), 'w', encoding='utf-8') as f:
            json.dump(lst, f, indent=1)


# ---------------------------------------------------------------- tables and links

TABLE_TYPES = ('DataTable', 'PlayFabCatalogDataTable', 'CompositeDataTable', 'FeatureFlaggedCompositeDataTable',
               'CurveTable', 'CompositeCurveTable', 'PlayerJourneySeasonsTable')
EMPTY = (None, '', [], {}, 'None', 'None[None]', False, 0, 0.0)
# Row fields that only change looks, sound or store presentation; the full row is in assets/<table>.json.
COSMETIC = re.compile(r'icon|texture|mesh|material|cutmask|thumbnail|image|preview|particle|vfx|sound|audio|akevent|'
                      r'colou?r|tint|brush|widget|decal|socket|animation|montage|camera|^ui|hiding|shrink|swatch',
                      re.I)
# Struct fields every PlayFab catalog row carries with the same default value; left out of the row lines.
CATALOG_NOISE = {'bCanTransmog', 'bCanBeTransmogTarget', 'TransmogCost', 'TickExecFilter', 'NumToCraft', 'MaxQuantity',
                 'bIsConsumable', 'StartingUpgradeLevel', 'MaxUpgradeLevel', 'StartingItemProgressionLevel',
                 'RequiredItemCondition', 'RequireQuestStatusCondition', 'AltRequiredItemCondition',
                 'AltRequireQuestStatusCondition', 'CraftingUnlockCriteriaRequirement',
                 'RequireQuestStatusForPurchaseCondition', 'UsagePeriodGroup', 'MedalType', 'ShinyDropCategory',
                 'bOverrideImpactValue', 'ImpactValueOverride', 'EssenceType', 'PrimaryEssenceType',
                 'SecondaryEssenceType', 'QuestStatusRequired', 'IsStackable'}
ROW_WIDTH = 1200
_CLASS = re.compile(r'(?:^/Game/\S+\.(\w+_C)$)|(?:^(?:BlueprintGeneratedClass|Class) (\w+_C)$)')
_HANDLE = re.compile(r'^(\w+)\[(.+)\]$')
_ASSET = re.compile(r'^(/Game/\S*/)(\w+)\.\2$')


def _rec(name):
    p = paths.class_file(name, '.json', assets.ASSETS_DIR)
    return json.load(open(p, encoding='utf-8')) if os.path.exists(p) else None


def table_rows(name):
    """(row struct, rows) of a dumped table; a composite table's rows are its parents' rows, in order."""
    rec = _rec(name)
    if not rec:
        return None, {}
    props = rec['exports'].get(rec.get('export') or name, {}).get('props', {})
    rows = dict(props.get('Rows') or {})
    for parent in props.get('ParentTables') or []:
        pn = str(parent).split(' ')[-1]
        if pn != name:
            rows = dict(table_rows(pn)[1], **rows)
    return props.get('RowStruct'), rows


def _looks_only(path):
    """True for a path whose asset only carries looks: those folders, animations (_mot), basic shapes."""
    low = path.lower()
    name = low.rsplit('/', 1)[-1].split('.')[0]
    return any(x in low for x in LOOKS_ONLY) or name.endswith(('_mot', '_anim')) or 'arriv' in name         or name.startswith('shape_')


class Links:
    """Indexes for marking what a value names: dumped tables and their rows, item ids, captured classes."""

    def __init__(self, index):
        self.index = index
        self.tables = {n for n, r in index.items() if r['type'] in TABLE_TYPES}
        self._rows = {}
        self.items = {}
        for n in sorted(self.tables):
            st, rows = table_rows(n)
            if 'Catalog' in str(st) or 'catalog' in n.lower():
                for rid in rows:
                    self.items.setdefault(rid, n)
        self.classes = {c[:-5] for c in os.listdir(paths.CLASSES_DIR) if c.endswith('.json')}
        self.stats = {}
        self.missing = {}
        self.behemoths = set()
        self.flares = None

    def rows(self, table):
        if table not in self._rows:
            self._rows[table] = set(table_rows(table)[1]) if table in self.tables else None
        return self._rows[table]

    def count(self, area, kind, ok, what=None):
        s = self.stats.setdefault(area, {})
        a, b = s.get(kind, (0, 0))
        s[kind] = (a + 1, b + (1 if ok else 0))
        if not ok and what:
            self.missing.setdefault(area, {}).setdefault(kind, set()).add(what)

    def mark(self, v, area):
        """None when v names nothing; else v as a link, ending in ? when the atlas does not have it."""
        if not isinstance(v, str) or v in EMPTY:
            return None
        if v.startswith('/Game/') and _looks_only(v):
            return v.rsplit('.', 1)[-1] + '~'
        m = _CLASS.match(v)
        if m:
            c = m.group(1) or m.group(2)
            if self.flares and self.flares.match(c) and c not in self.classes:
                self.count(area, 'flare variants (not dumped)', True)
                return c + '~'
            ok = c in self.classes or os.path.exists(paths.class_file(c[:-2], '.json', assets.ASSETS_DIR))
            self.count(area, 'classes', ok, c)
            return c + ('' if ok else '?')
        m = _HANDLE.match(v)
        if m and m.group(2) != 'None' and m.group(1) != 'None':
            rows = self.rows(m.group(1))
            ok = rows is not None and m.group(2) in rows
            self.count(area, 'row handles', ok, v)
            beh = '<behemoths>' if m.group(2) in self.behemoths else ''
            return '%s[%s]%s%s' % (m.group(1), m.group(2), '' if ok else ('?' if rows is not None else '?table'), beh)
        if v in self.behemoths:
            self.count(area, 'behemoth rows', True)
            return v + '<behemoths>'
        m = _ASSET.match(v)
        if m:
            n = m.group(2)
            ok = n in self.index or os.path.exists(paths.class_file(n, '.json', assets.ASSETS_DIR))
            self.count(area, 'assets', ok, n)
            return n + ('' if ok else '?')
        if v in self.items:
            self.count(area, 'item ids', True)
            return '%s{%s}' % (v, self.items[v])
        return None


def row_vals(row, links, area, noise=()):
    """{field: [values]} with the values that matter; named classes, rows and items marked."""
    vals = {}
    for path, v in weapons._paths(row):
        leaf = path.rsplit('.', 1)[-1]
        if v in EMPTY or leaf in CATALOG_NOISE or leaf in noise or COSMETIC.search(path):
            continue
        m = links.mark(v, area)
        val = m if m is not None else assets._fmt(v)
        val = val if len(val) <= 120 else val[:117] + '...'
        vs = vals.setdefault('.'.join(path.split('.')[-2:]), [])
        if val not in vs:
            vs.append(val)
    return vals


def _line(rid, vals, skip=None):
    line = '  %s  %s' % (rid, '  '.join('%s=%s' % (k, ','.join(v)) for k, v in vals.items()
                                       if not skip or skip.get(k) != v))
    return line if len(line) <= ROW_WIDTH else line[:ROW_WIDTH - 3] + '...'


def row_line(rid, row, links, area, noise=()):
    return _line(rid, row_vals(row, links, area, noise))


def table_block(name, links, area, noise=()):
    st, rows = table_rows(name)
    rec = _rec(name)
    out = ['%s  (%s, %d rows; full rows: asset %s)' % (name, str(st).split(' ')[-1], len(rows), name)]
    if rec:
        props = rec['exports'].get(rec.get('export') or name, {}).get('props', {})
        parents = [str(p).split(' ')[-1] for p in props.get('ParentTables') or []]
        if parents:
            out.append('  composite of: %s' % ', '.join(parents))
    vals = {rid: row_vals(r, links, area, noise) for rid, r in rows.items()}
    common = {}
    if len(vals) >= 10:   # values (nearly) every row shares are printed once
        seen = {}
        for v in vals.values():
            for k, x in v.items():
                seen.setdefault(k, {}).setdefault(tuple(x), 0)
                seen[k][tuple(x)] += 1
        for k, c in seen.items():
            x, n = max(c.items(), key=lambda kv: kv[1])
            if n >= 0.9 * len(vals):
                common[k] = list(x)
    if common:
        out.append(_line('most rows:', common) + '   (rows list only what differs)')
    out += [_line(rid, v, common) for rid, v in vals.items()]
    return out


# ---------------------------------------------------------------- sheets

# Every dumped table goes to the first sheet whose pattern matches its name (searched in this order).
SHEETS = (
    ('rewards', 'drops', r'^dt_|droptable|^bundles_|^containers_|reward|promotion|^behemoth_breakitems|^generated_.*break|'
                         r'break_data|breakitem|conditional_.*break|_breaks$|breakparts|loot'),
    ('rewards', 'crafting', r'recipe|crafting|unlock_criteria|unlock_pattern|exchange|^purchase_|dust|transmog'),
    ('rewards', 'bounties', r'bounty|bounties|challenge'),
    ('rewards', 'hunt_pass', r'hunt_?pass|season'),
    ('rewards', 'progression', r'experience|prestige|mastery|journey|progression|achievement|activity|vip|slayer|'
                               r'player_level|rank|repair'),
    ('islands', 'hunts', r'matchmaker|island_spec|hunt_|schedule|exploration|^hunts?$|map_list'),
    ('islands', 'escalation', r'escalation|backup|relic|gauntlet|trial'),
    ('islands', 'modes', r'portal|event|encounter|content|arena|patrol|danger|spawn|gatherable_distribution'),
    ('items', 'lanterns', r'lantern'),
    ('items', 'cells', r'cell'),
    ('items', 'quick_items', r'quick_item|flask|grenade|consumable|flare|boost'),
    ('items', 'materials', r'material|archonite|gatherable|bait|chum|kite|skyfish|creature_items|orb_gem|glider'),
    ('items', 'currencies_tokens', r'currency|token|portal_keys|grant_progress|quest_item|key'),
    ('items', 'cosmetics', r'dye|emote|emoji|banner|title|character_creator|cosmetic|accessory|toys|pet_|player_role|lore|'
                           r'quickchat'),
    ('items', 'weapons_armor', r'weapon|armou?r|part_catalog|talent|gear_'),
)
SHEET_TEXT = {
    'drops': 'Loot: drop tables, bundles and containers, behemoth break rewards, promotions',
    'crafting': 'Crafting and unlocks: recipes and recipe patterns, crafting and purchase unlock criteria, exchanges',
    'bounties': 'Bounties', 'hunt_pass': 'Hunt Pass seasons and their reward tracks',
    'progression': 'Progression rewards: experience and prestige tracks, mastery, player journey, achievements',
    'hunts': "Hunts and islands: the matchmaker's hunts, island spec rows, schedules",
    'escalation': 'Escalation, gauntlet and trials: rounds, backup encounters, talents, buffs, relics',
    'modes': 'Other game-mode data: portals, events, encounters, content and spawns',
    'lanterns': 'Lanterns', 'cells': 'Cells as items (what each does: weapons/armor.txt)',
    'quick_items': 'Quick items, flasks, grenades, consumables and boosts',
    'materials': 'Materials, gatherables, skyfishing, orbs and gems',
    'currencies_tokens': 'Currencies, tokens, keys and quest items',
    'cosmetics': 'Cosmetics: dyes, emotes, banners, titles, character creator, accessories, pets',
    'weapons_armor': 'Weapon and armor catalogs and tables (per-type detail: weapons/<key>.txt, weapons/armor.txt)',
}
AREA_TEXT = {'items': 'Items (everything a player can own)', 'rewards': 'Shops, loot and rewards',
             'islands': 'Islands, hunts and game modes'}


def _area_list(key):
    p = weapons._list_path(key)
    return json.load(open(p, encoding='utf-8')) if os.path.exists(p) else None


def _sheet_for(table, dump_area):
    for area, sheet, pat in SHEETS:
        if re.search(pat, table, re.I):
            return area, sheet
    return dump_area, 'other'


def tree(rec, name, links, area, depth=0, seen=None):
    """An asset export and the same-package exports it names ('@x'), as an indented tree."""
    seen = set() if seen is None else seen
    e = rec['exports'].get(name)
    if not e or name in seen or depth > 12:
        return []
    seen.add(name)
    pad = '  ' * (depth + 1)
    line = row_line('%s%s (%s)' % (pad[2:], name, e['type']), {k: v for k, v in e['props'].items()}, links, area)
    out = [line]
    refs = []
    for _, v in weapons._paths(e['props']):
        if isinstance(v, str) and v.startswith('@') and v[1:] not in refs:
            refs.append(v[1:])
    for r in refs:
        out += tree(rec, r, links, area, depth + 1, seen)
    return out


def class_lines(info):
    """The area's Blueprint classes by kind, with their own non-cosmetic defaults (as in the weapon sheets)."""
    out = []
    cls = info['classes']
    for kind, _ in weapons.KINDS + (('other', ()),):
        group = sorted(c for c, v in cls.items() if v['kind'] == kind)
        if not group or kind == 'widget':
            continue
        out += ['', '== %s classes (%d)' % (kind.capitalize(), len(group))]
        for c in group:
            v = cls[c]
            out.append('%s  < %s%s' % (c, ' < '.join(v['parents'][:3]), '' if v['code'] else '  [no code built]'))
            out += ['    %s: %s' % (obj, weapons._fmt_props(props)) for obj, props in v['defaults']]
            users = [u for u in v['used_by'] if u.split('.')[0] not in (c, c[:-2])]
            if users:
                out.append('    used by: %s%s' % (', '.join(users[:12]), ' ...' if len(users) > 12 else ''))
    return out


def _behemoth_rows():
    p = os.path.join(paths.OUT, 'behemoths.json')
    return set(json.load(open(p, encoding='utf-8'))) if os.path.exists(p) else set()


def sheets(log=print):
    index = json.load(open(assets.INDEX, encoding='utf-8'))
    links = Links(index)
    links.behemoths = _behemoth_rows()
    lists = {k: _area_list(k) for k in ORDER}
    lists = {k: v for k, v in lists.items() if v}
    if not lists:
        log('nothing dumped yet: python -m tools.atlas areas --dump items rewards islands')
        return
    infos = {k: weapons.collect(k, index) for k in lists}
    for info in infos.values():   # catalogs and composite tables are tables too
        info['tables'] += [n for n, t in info['other_assets'] if t in TABLE_TYPES]
        info['other_assets'] = [(n, t) for n, t in info['other_assets'] if t not in TABLE_TYPES]
    # flare colour variants are not dumped (FLARE_VARIANT); counted apart from real gaps
    links.flares = FLARE_VARIANT
    # tables -> sheet; assets of other kinds per area
    placed = {}
    for k, info in infos.items():
        for t in info['tables']:
            placed.setdefault(_sheet_for(t, k), []).append(t)
    for area in ORDER:
        os.makedirs(os.path.join(paths.OUT, area), exist_ok=True)
    written, summary = [], {}
    for (area, sheet), tables in sorted(placed.items()):
        title = SHEET_TEXT.get(sheet, 'Other %s tables' % area)
        out = ['%s  (%s/%s.txt)' % (title, area, sheet),
               'Generated by python -m tools.atlas areas. Marks: Class_C? / table[Row]? / asset? = not in the atlas; '
               'ITEM{catalog} = an item id and the catalog that has it; ROW<behemoths> = a behemoth sheet exists; '
               'Name~ = looks only (flare colour variants, atmospheres, animations, UI), not dumped.']
        for t in sorted(set(tables)):
            out += [''] + table_block(t, links, area)
        _write(area, sheet, out)
        written.append('%s/%s.txt' % (area, sheet))
        s = summary.setdefault(area, {'tables': 0, 'rows': 0, 'sheets': 0})
        s['tables'] += len(set(tables))
        s['rows'] += sum(len(table_rows(t)[1]) for t in set(tables))
        s['sheets'] += 1
    for k, info in infos.items():
        extra = AREA_EXTRAS.get(k)
        if extra:
            for sheet, lines in extra(info, links, k):
                _write(k, sheet, lines)
                written.append('%s/%s.txt' % (k, sheet))
                summary.setdefault(k, {'tables': 0, 'rows': 0, 'sheets': 0})['sheets'] += 1
        _write(k, 'classes', ['%s: Blueprint classes  (%s/classes.txt)' % (AREA_TEXT[k], k)] + class_lines(info))
        written.append('%s/classes.txt' % k)
        s = summary.setdefault(k, {'tables': 0, 'rows': 0, 'sheets': 0})
        s['sheets'] += 1
        s['classes'] = len(info['classes'])
        s['assets'] = len(info['other_assets'])
        s['packages'] = len(set(_area_list(k)['packages']) | set(_area_list(k)['referenced']))
        s['not_captured'] = info['not_captured']
    sources = item_sources(placed, links)
    _write('rewards', 'item_sources', sources)
    written.append('rewards/item_sources.txt')
    with open(os.path.join(paths.OUT, 'areas.json'), 'w', encoding='utf-8') as f:
        json.dump({k: dict(v, links=links.stats.get(k, {}),
                           unresolved={kind: sorted(x) for kind, x in links.missing.get(k, {}).items()})
                   for k, v in summary.items()}, f, indent=1, ensure_ascii=False)
    for k in summary:
        with open(os.path.join(paths.OUT, k + '.txt'), 'w', encoding='utf-8') as f:
            f.write('\n'.join(coverage(k, summary[k], links, written)) + '\n')
    log('%d sheets -> data/atlas/{items,rewards,islands}/' % len(written))


def _write(area, sheet, lines):
    p = os.path.join(paths.OUT, area, sheet + '.txt')
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')


def coverage(area, s, links, written):
    st = links.stats.get(area, {})
    miss = links.missing.get(area, {})
    out = ['%s  (python -m tools.atlas areas; sheets in data/atlas/%s/)' % (AREA_TEXT[area], area), '',
           'Coverage: %d packages dumped (%d Blueprint classes, %d tables with %d rows, %d other data assets); %d '
           'sheets.' % (s.get('packages', 0), s.get('classes', 0), s['tables'], s['rows'], s.get('assets', 0),
                        s['sheets']),
           'Links named by the rows (found / named):']
    for kind, (n, ok) in sorted(st.items()):
        out.append('  %-12s %6d / %-6d %s' % (kind, ok, n, '' if ok == n else '(%d not in the atlas)' % (n - ok)))
    out += ['', 'Sheets:'] + ['  ' + w for w in written if w.startswith(area + '/')]
    if s.get('not_captured'):
        out += ['', 'Packages not captured (render-only or empty): %d' % len(s['not_captured'])]
        out += ['  ' + p for p in s['not_captured']]
    for kind, xs in sorted(miss.items()):
        out += ['', '%s named but not in the atlas (%d):' % (kind.capitalize(), len(xs))]
        out += ['  ' + x for x in sorted(xs)]
    return out


def item_sources(placed, links):
    """Every item id the reward-side tables name -> the table rows that name it."""
    src = {}
    for (area, sheet), tables in placed.items():
        if area == 'items' and sheet != 'weapons_armor':
            continue
        for t in set(tables):
            for rid, row in table_rows(t)[1].items():
                for _, v in weapons._paths(row):
                    if isinstance(v, str) and v in links.items and v != rid:
                        src.setdefault(v, set()).add('%s[%s]' % (t, rid))
    out = ['Item sources  (rewards/item_sources.txt): each item id named by a loot, crafting, unlock, bounty, Hunt Pass, '
           'progression or island table, with its catalog and every row that names it.',
           '%d items named.' % len(src), '']
    for item in sorted(src):
        by = {}
        for r in sorted(src[item]):
            t, rid = r.split('[', 1)
            by.setdefault(t, []).append(rid[:-1])
        parts = ['%s[%s%s]' % (t, ', '.join(rs[:6]), ' +%d more' % (len(rs) - 6) if len(rs) > 6 else '')
                 for t, rs in sorted(by.items())]
        out.append('%s{%s}  %d rows: %s' % (item, links.items[item], len(src[item]), '; '.join(parts)))
    return out


# ---------------------------------------------------------------- asset sheets (non-table data assets)

TYPE_SHEETS = {'IslandSpecAsset': 'island_specs', 'GameContentDataAsset': 'content_assets',
               'Hunt_Modifier': 'hunt_modifiers', 'Hunt_AtmosphereList': 'hunt_lists', 'Hunt_BehemothList': 'hunt_lists',
               'Hunt_MapList': 'hunt_lists', 'HuntPassAssetReferences': 'hunt_pass_assets'}
TYPE_TEXT = {'island_specs': 'Island spec assets: limits, kill rewards, content pools and their chooser trees',
             'content_assets': 'Game content assets: public events, portals, meta events, call to action (chooser trees)',
             'hunt_modifiers': 'Hunt modifiers: the buffs each applies', 'hunt_lists': 'Hunt atmosphere, behemoth and map lists',
             'hunt_pass_assets': 'Hunt Pass season assets (city event level, gatherables, screens)',
             'other_assets': 'Other data assets'}


def _spec_users(name, links):
    """(island spec table rows, matchmaker hunts) that lead to an island spec asset."""
    spec_rows = []
    for t in sorted(links.tables):
        if 'island_spec' not in t:
            continue
        for rid, row in table_rows(t)[1].items():
            if any(isinstance(v, str) and v.rsplit('.', 1)[-1] == name for _, v in weapons._paths(row)):
                spec_rows.append('%s[%s]' % (t, rid))
    hunts = []
    spec_ids = {r.split('[', 1)[1][:-1] for r in spec_rows}
    for t in sorted(links.tables):
        if 'matchmaker' not in t:
            continue
        for rid, row in table_rows(t)[1].items():
            # GameModeSpecificData is a {DataTable, RowName} pair, not a table[Row] handle
            if any(isinstance(v, str) and (v in spec_rows or (p.endswith('RowName') and v in spec_ids))
                   for p, v in weapons._paths(row)):
                hunts.append('%s[%s]' % (t, rid))
    return spec_rows, hunts


def _contract_files():
    """{hunt id: data/2.1.1/islands file names} from the island contracts the server already uses."""
    out = {}
    d = os.path.join(paths.BUILD_DATA, 'islands')
    for fn in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if fn.endswith('.contract.json'):
            hid = json.load(open(os.path.join(d, fn), encoding='utf-8')).get('huntId')
            out.setdefault(str(hid), []).append('data/2.1.1/islands/' + fn)
    return out


def asset_sheets(info, links, area):
    groups = {}
    for n, t in info['other_assets']:
        groups.setdefault(TYPE_SHEETS.get(t, 'other_assets'), []).append((n, t))
    contracts = _contract_files() if area == 'islands' else {}
    for sheet, items in sorted(groups.items()):
        out = ['%s  (%s/%s.txt)' % (TYPE_TEXT[sheet], area, sheet)]
        for n, t in sorted(items):
            rec = _rec(n)
            if not rec:
                continue
            out += ['', '%s  (%s)' % (n, t)]
            if t == 'IslandSpecAsset':
                spec_rows, hunts = _spec_users(n, links)
                out.append('  spec rows: %s' % (', '.join(spec_rows) or 'none'))
                out.append('  hunts: %s' % (', '.join(hunts) or 'none'))
                files = [f for h in hunts for f in contracts.get(h.split('[')[-1][:-1], [])]
                if files:
                    out.append('  server island data: %s' % ', '.join(files))
                out.append('  sheet: islands/specs/%s.txt' % n)
                yield 'specs/' + n, ['Island spec %s  (islands/specs/%s.txt)' % (n, n)] + out[-4 - bool(files):] + \
                    [''] + tree(rec, rec.get('export') or n, links, area)
                continue
            out += tree(rec, rec.get('export') or n, links, area)
        yield sheet, out


AREA_EXTRAS = {k: asset_sheets for k in ORDER}
