"""Every behemoth in the game: dump its data and write one sheet per behemoth.

The roster is the game's own: behemoth_metadata_table and behemoth_asset_table (Gameplay/hunts) map each
behemoth row (lerawr_normal, beaver_frost, ...) to its class. Following the class's parent chain gives the class
defaults that name its action set table, damage table, behavior tree and physics assets.

  dump    every data package under Archon/Content/Monsters and Archon/Content/Character/Monsters that is not
          render-only (tables, trees, montages, physics assets, blackboards, queries, Blueprint class defaults),
          through the assets mode, plus the two roster tables.
  sheets  data/atlas/behemoths/<row>.txt for each roster row, and data/atlas/behemoths.json / behemoths.txt with
          the roster and what was found or is missing for each row.
"""
import json
import os
import re

from . import assets, blueprints, paths

PREFIXES = ('Archon/Content/Monsters/', 'Archon/Content/Character/Monsters/')
ROSTER_TABLES = ('Archon/Content/Gameplay/hunts/behemoth_metadata_table',
                 'Archon/Content/Gameplay/hunts/behemoth_asset_table')
# Bp_Behemoth.DamageTableLookup falls back to this table when the behemoth's own damage table lacks a row.
GENERIC_DAMAGE_TABLE = 'DamageTable_Generic'
RENDER_ONLY = set(assets.SKIP_TYPES) | {
    'BlendSpace', 'BlendSpace1D', 'AimOffsetBlendSpace1D', 'MaterialFunction', 'ControlRigBlueprintGeneratedClass',
    'PhxPoseMatchingSharedRigBone', 'PhxPoseMatchingDataAsset', 'PhxPoseMatchingRig', 'DestructibleMesh',
    'SkeletalMeshLODSettings'}
# Editor-only hit volume captures (topics/behemoth_design.md section 6): 220 tables, 42 MB; dump one with `assets`.
EDITOR_CAPTURES = ('HitVolumes',)
SHEETS_DIR = os.path.join(paths.OUT, 'behemoths')
OUT_JSON = os.path.join(paths.OUT, 'behemoths.json')
OUT_TXT = os.path.join(paths.OUT, 'behemoths.txt')


def data_packages(refresh=False):
    """{package: type} of every non-render package under PREFIXES."""
    out = {}
    for pre in PREFIXES:
        out.update({p: t for p, t in assets.class_listing(pre, refresh).items()
                    if t not in RENDER_ONLY and not p.rsplit('/', 1)[-1].startswith(EDITOR_CAPTURES)})
    return out


def blueprint_classes(refresh=False):
    """Class names (X_C) of every Blueprint under PREFIXES, plus the referenced classes found by the last dump
    (spawned area effects, projectiles, buffs, ...; listed in EXTRA_LIST), for `build`."""
    out = {p.rsplit('/', 1)[-1] + '_C' for p, t in data_packages(refresh).items() if t in assets.CLASS_TYPES}
    if os.path.exists(EXTRA_LIST):
        out |= {l.strip().rsplit('/', 1)[-1] + '_C' for l in open(EXTRA_LIST, encoding='utf-8') if l.strip()}
    return sorted(out)


# Classes the behemoth data references but that live elsewhere (shared AoEs, projectiles, buffs, minions) are
# followed this many steps, with their Blueprint parents; folders with nothing for the server are not followed.
REF_DEPTH = 3
REF_SKIP = ('/UI/', '/Cinematics/', '/Camera/', '/atmospheres/', '/Audio/')
EXTRA_LIST = os.path.join(paths.CACHE, 'behemoth_referenced_packages.txt')
_BGC = re.compile(r'BlueprintGeneratedClass ([A-Za-z0-9_\-]+_C)\b')


def _referenced(names, idx, have):
    """Packages of the Blueprint classes named in these assets' exports, and of their parents, not yet dumped."""
    out = set()
    for n in names:
        rec = _load(n)
        if not rec:
            continue
        found = set(_BGC.findall(json.dumps(rec['exports'])))
        pkg = rec['package']
        d = blueprints.dump([pkg]).get(pkg)
        sup = blueprints.super_of(d, rec.get('class') or n + '_C') if d else None
        if sup and sup.endswith('_C'):
            found.add(sup)
        cf = paths.class_file(n + '_C')
        if os.path.exists(cf):
            for f in json.load(open(cf, encoding='utf-8')).get('functions', []):
                found |= {x.split(' ', 1)[1] for x in f.get('assets', []) if x.startswith('BlueprintGeneratedClass ')}
        for c in found:
            p = blueprints.package_for_class(c, idx)
            if p and c[:-2] not in have and not any(s.lower() in p.lower() for s in REF_SKIP):
                out.add(p)
    return out


def dump(refresh=False, log=print):
    idx = blueprints.package_index()
    pkgs = sorted(data_packages(refresh)) + list(ROSTER_TABLES) + idx.get(GENERIC_DAMAGE_TABLE.lower(), [])
    log('%d packages' % len(pkgs))
    skipped = []
    quiet = lambda m: skipped.append(m) if m.startswith('skip') else None
    assets.build_packages(pkgs, idx, refresh, log=quiet)
    extra, frontier = set(), [p.rsplit('/', 1)[-1] for p in pkgs]
    for step in range(REF_DEPTH):
        have = set(json.load(open(assets.INDEX, encoding='utf-8')))
        new = sorted(_referenced(frontier, idx, have) - extra)
        if not new:
            break
        log('referenced classes, step %d: %d packages' % (step + 1, len(new)))
        assets.build_packages(new, idx, refresh, log=quiet)
        extra |= set(new)
        frontier = [p.rsplit('/', 1)[-1] for p in new]
    with open(EXTRA_LIST, 'w', encoding='utf-8') as f:
        f.write('\n'.join(sorted(extra)) + '\n')
    log('done; %d referenced packages outside the behemoth folders; %d skipped%s' % (
        len(extra), len(skipped), ''.join('\n  ' + s for s in skipped[:40])))


# ---------------------------------------------------------------- sheets

NOISY_COMPONENTS = ('Particle', 'Niagara', 'Audio', 'Ak', 'Light', 'Decal', 'Widget', 'Influence')
LONG_PROPS = ('AttackVectors',)   # long point lists; left in the asset file
RUNTIME_PROPS = ('ReplicatedPredictionKeyMap',)   # replication bookkeeping cooked into a few defaults


def _load(name):
    if not name:
        return None
    p = paths.class_file(name, '.json', assets.ASSETS_DIR)
    return json.load(open(p, encoding='utf-8')) if os.path.exists(p) else None


def _ref_name(v):
    """'DataTable lerawr_damage_table' -> 'lerawr_damage_table'."""
    return v.split(' ', 1)[1] if isinstance(v, str) and ' ' in v else v


class Roster:
    def __init__(self):
        self.idx = blueprints.package_index()
        self.rows = {}
        for t in ('behemoth_metadata_table', 'behemoth_asset_table'):
            rec = _load(t) or {}
            rows = rec.get('exports', {}).get(t, {}).get('props', {}).get('Rows') or {}
            for k, r in rows.items():
                self.rows.setdefault(k, {}).update({'%s.%s' % (t.split('_')[1], f): v for f, v in r.items()})
        wp = os.path.join(paths.OUT, 'damage_windows.json')
        w = json.load(open(wp, encoding='utf-8')) if os.path.exists(wp) else {'behemoths': {}}
        ip = os.path.join(paths.OUT, 'index_assets.json')
        self.assets = json.load(open(ip, encoding='utf-8')) if os.path.exists(ip) else {}
        self.montage_windows = {m: ws for ms in w['behemoths'].values() for m, ws in ms.items()}

    def damage_rows(self):
        """{row name: [damage tables that have it]} over every dumped DamageTableData table (cached)."""
        if not hasattr(self, '_damage_rows'):
            self._damage_rows = {}
            for n in _tables_with('DamageTableData', self):
                for r in _table_rows(n):
                    self._damage_rows.setdefault(r, []).append(n)
        return self._damage_rows

    @staticmethod
    def row_class(r):
        for k in ('metadata.BehemothClass', 'asset.BehemothAsset'):
            v = r.get(k)
            if isinstance(v, str) and v:
                return v.rsplit('.', 1)[-1]
        return None

    def chain(self, cls):
        """The class and its Blueprint parents, nearest first, ending with the first native class."""
        out = [cls]
        while out[-1].endswith('_C') and len(out) < 30:
            pkg = blueprints.package_for_class(out[-1], self.idx)
            d = blueprints.dump([pkg]).get(pkg) if pkg else None
            sup = blueprints.super_of(d, out[-1]) if d else None
            if not sup or sup == 'None':
                break
            out.append(sup)
        return out


def _merged_defaults(chain):
    """{component: {'type', 'props': {prop: (value, class that set it)}}}, nearest class winning; 'actor' = CDO."""
    out = {}
    for cls in reversed(chain):
        rec = _load(cls[:-2]) if cls.endswith('_C') else None
        if not rec or rec.get('type') != 'ClassDefaults':
            continue
        for ename, e in rec['exports'].items():
            comp = 'actor' if ename.startswith('Default__') else ename.replace('_GEN_VARIABLE', '')
            if any(n in e['type'] or n in comp for n in NOISY_COMPONENTS):
                continue
            slot = out.setdefault(comp, {'type': e['type'], 'props': {}})
            slot['type'] = e['type']
            for k, v in e['props'].items():
                if v not in (None, [], {}, 'None') and k not in RUNTIME_PROPS:
                    slot['props'][k] = (v, cls[:-2])
    return out


def _physics_assets(d, chain, ros):
    """PhysicsAsset names referenced anywhere in the merged defaults, plus those in the folder of the first class in
    the chain that lives under Archon/Content/Monsters/<family>/<name>/ (the damage-window bodies are in that
    folder's main *_phat, which the skeletal mesh points to and the dump skips)."""
    found = []
    for comp in d.values():
        for v, _ in comp['props'].values():
            for x in (v if isinstance(v, list) else [v]):
                if isinstance(x, str) and x.startswith('PhysicsAsset ') and x[13:] not in found:
                    found.append(x[13:])
    folder = None
    for c in chain:
        pkg = blueprints.package_for_class(c, ros.idx) if c.endswith('_C') else None
        parts = pkg.split('/') if pkg else []
        if len(parts) > 4 and parts[2] == 'Monsters':
            folder = '/'.join(parts[:5]) + '/'
            break
    if folder:
        found += sorted(n for n, r in ros.assets.items() if r['type'] == 'PhysicsAsset'
                        and r['package'].startswith(folder) and n not in found)
    return found


COSMETIC_NOTIFIES = ('AkEvent', 'akevent', 'Particle', 'particle', 'CameraShake', 'debris', 'vfx', 'material_param',
                     'ikinema')
# Notifies that spawn something that can hit, or deal damage themselves (counted in the coverage table).
DAMAGE_NOTIFY_HINTS = ('spawn', 'projectile', 'beam', 'aoe', 'damage')


def _other_notifies(montage):
    """(start, duration, class, settings) of a montage's notifies other than damage windows and cosmetic ones."""
    rec = _load(montage)
    if not rec:
        return []
    ex = rec['exports']
    out = []
    for n in ex.get(montage, {}).get('props', {}).get('Notifies') or []:
        ref = n.get('NotifyStateClass') or n.get('Notify')
        e = ex.get(ref[1:]) if isinstance(ref, str) and ref.startswith('@') else None
        cls = e['type'] if e else (ref or n.get('NotifyName') or '?')
        if cls in ('archon_damage_window_C', 'DamageNotifyState') or any(x in cls for x in COSMETIC_NOTIFIES):
            continue
        if n.get('bTriggerOnDedicatedServer') is False:
            continue
        out.append((n.get('LinkValue') or 0.0, n.get('Duration') or 0.0, cls,
                    assets._props_line(e['props']) if e and e['props'] else ''))
    return sorted(out, key=lambda x: x[0])


SPAWN_PROPS = re.compile(r'damage|radius|row|speed|life|duration|interval|effect|buff|tick|projectile|gravity|range|'
                         r'class|spawn|count|delay|height|angle', re.I)


def _leaves(v, key=''):
    """(key, value) for every scalar inside a cleaned property value."""
    if isinstance(v, dict):
        for k, x in v.items():
            yield from _leaves(x, k)
    elif isinstance(v, list):
        for x in v:
            yield from _leaves(x, key)
    else:
        yield key, v


def _rows_named(props):
    """Damage row names in a notify's or class's settings: values of fields named like *Damage*Row* or
    *Damage*Key* (DamageKeys, OverrideDamageRowName, AOEDamageKey, ...), and the row of 'table[Row]' handles into
    a damage table. Other row handles (roster rows, part collision, tag queries) are not damage rows."""
    out = []
    for k, v in _leaves(props):
        if not isinstance(v, str) or v in ('', 'None'):
            continue
        m = re.match(r'^(\S+)\[(.+)\]$', v)
        if m:
            if 'damage' not in m.group(1).lower():
                continue
            v = m.group(2)
        elif not re.search(r'damage.*(row|key)', k, re.I) or k.endswith('Source'):
            continue
        if v not in out:
            out.append(v)
    return out


def _req_check(req, who):
    """BehemothRequirements (raw or as in damage_windows.json) against one behemoth -> (applies, note).

    Archetype and classification are fixed per behemoth and evaluated as Bp_Behemoth does (empty passes, else
    Require passes on a case-insensitive match and Exclude on a mismatch): applies False means the window or spawn
    never fires on this behemoth. Everything else depends on the fight and only goes into the note."""
    if not isinstance(req, dict):
        return True, ''
    val = lambda k, d=None: str(req.get(k, d)).split('::')[-1] if req.get(k) is not None else d
    for field, cond, mine, empty in (('RequireBehemothArchetypeName', 'ArchetypeCondition', who['arch'], ''),
                                     ('RequireBehemothClassificationName', 'ClassificationCondition', who['cls'],
                                      'None')):
        need = val(field, empty) or empty
        if need != empty:
            require = val(cond, 'Require') != 'Exclude'
            if require != (need.lower() == (mine or '').lower()):
                return False, '%s %s %s' % ('needs' if require else 'excludes', field[15:].replace('Name', '').lower(),
                                             need)
    notes = []
    st = val('StateRequirement', 'None')
    if st and st != 'None':
        notes.append('%sstate %s' % ('not in ' if val('StateRequirementType') == 'ExcludedByState' else '', st))
    for k, label in (('RequireIntactPartName', 'intact part'), ('RequireBehemothFunctionSuccess', 'function'),
                     ('ServerBlackboardKey', 'blackboard')):
        if val(k, 'None') not in ('None', ''):
            notes.append('%s %s' % (label, val(k)))
    parts = req.get('PartRequirements') or {}
    if isinstance(parts, dict) and (parts.get('PartNamesToCheck') or parts.get('PartStateRequirements')):
        notes.append('part states')
    if req.get('RequireTagsOnBehemothAbilitySystem') or req.get('ExcludeTagsOnBehemothAbilitySystem') or \
            (req.get('BehemothTagQueries') or {}).get('Queries'):
        notes.append('tags')
    return True, ', '.join(notes)


def _req_label(applies, note):
    return '  [never here: %s]' % note if not applies else ('  [only when: %s]' % note if note else '')


def _row_mark(r, table_rows, generic):
    return '%s%s' % ('+' if r in table_rows else 'g' if r in generic else '?', r)


def _spawn_sources(ros, montage, atk):
    """(source, classes, rows, requirements) per notify of the montage that names a Blueprint class or a damage
    row, and for the attack Blueprint's own defaults."""
    out = []
    rec = _load(montage)
    ex = rec['exports'] if rec else {}
    for n in (ex.get(montage, {}).get('props', {}).get('Notifies') or []):
        ref = n.get('NotifyStateClass') or n.get('Notify')
        e = ex.get(ref[1:]) if isinstance(ref, str) and ref.startswith('@') else None
        if not e or e['type'] in ('archon_damage_window_C', 'DamageNotifyState') or \
                any(x in e['type'] for x in COSMETIC_NOTIFIES):
            continue
        txt = json.dumps(e['props'])
        classes = [c for c in dict.fromkeys(_BGC.findall(txt))]
        rows = _rows_named(e['props'])
        if classes or rows:
            out.append(('%ss %s' % (assets._fmt(n.get('LinkValue') or 0.0), e['type']), classes, rows,
                        e['props'].get('BehemothRequirements')))
    arec = _load(atk[:-2]) if atk and atk.endswith('_C') else None
    if arec:
        props = {k: v for e in arec['exports'].values() for k, v in e['props'].items()}
        classes = list(dict.fromkeys(_BGC.findall(json.dumps(props))))
        rows = _rows_named(props)
        if classes or rows:
            out.append(('%s defaults' % atk[:-2], classes, rows, None))
    return out


def _spawned_class_lines(ros, cls, done):
    """A spawned class once per sheet: chain and the defaults that matter for hitting (damage, radius, rows, ...)."""
    if cls in done:
        return []
    done.add(cls)
    chain = ros.chain(cls)
    d = _merged_defaults(chain)
    lines = ['  %s  (%s)%s' % (cls, ' -> '.join(chain[1:4]), '' if _load(cls[:-2]) else '  [not in the atlas]')]
    for comp in sorted(d, key=lambda c: (c != 'actor', c)):
        props = {k: v for k, v in d[comp]['props'].items() if SPAWN_PROPS.search(k)}
        if props:
            lines.append('    %s: %s' % (comp, '; '.join('%s=%s [%s]' % (k, assets._fmt(v)[:200], src)
                                                       for k, (v, src) in sorted(props.items()))))
    code = paths.class_file(cls)
    if os.path.exists(code):
        rec = json.load(open(code, encoding='utf-8'))
        if rec.get('pseudo_file'):
            lines.append('    code: pseudo/%s' % rec['pseudo_file'].rsplit('/', 1)[-1])
    return lines


def _spawned_section(ros, rows, montages, table_rows, info, who, missing_rows):
    """Per action: what its montage notifies and attack defaults spawn, with damage rows checked against the
    behemoth's damage table; then each spawned class once."""
    generic = set(_table_rows(GENERIC_DAMAGE_TABLE))
    out = ['', '== Spawned by attacks (from action montage notifies and attack defaults; row marks as above)']
    montage_of = dict(montages)
    classes_seen, detail = [], []
    for name, a in rows.items():
        srcs = _spawn_sources(ros, montage_of.get(name), _ref_name(a.get('Class')))
        if not srcs:
            continue
        out.append('  %s' % name)
        for src, classes, rws, req in srcs:
            applies, note = _req_check(req, who)
            if applies:
                missing_rows.update(r for r in rws if r not in table_rows and r not in generic)
                classes_seen += [c for c in classes if c not in classes_seen]
            marks = [_row_mark(r, table_rows, generic) for r in rws]
            out.append('    %s: %s%s%s' % (src, ', '.join(c[:-2] for c in classes) or '-',
                                          ('  rows ' + ', '.join(marks)) if marks else '', _req_label(applies, note)))
    info['spawned_classes'] = classes_seen
    info['spawned_missing'] = [c for c in classes_seen if not _load(c[:-2])]
    out += ['', '== Spawned classes (defaults that matter for hitting, merged along each chain)']
    done = set()
    for c in classes_seen:
        detail += _spawned_class_lines(ros, c, done)
    return out + (detail or ['  none'])


def _bt_closure(name, seen=None):
    """A behavior tree and every tree it references, root first."""
    seen = [] if seen is None else seen
    rec = _load(name)
    if not rec or name in seen:
        return seen
    seen.append(name)
    for r in rec.get('refs', []):
        sub = _load(r)
        if sub and sub.get('type') == 'BehaviorTree':
            _bt_closure(r, seen)
    return seen


def _table_rows(name):
    rec = _load(name)
    return (rec['exports'].get(name, {}).get('props', {}).get('Rows') or {}) if rec else {}


def sheet(ros, row, r):
    cls = ros.row_class(r)
    info = {'row': row, 'class': cls, 'display_name': r.get('asset.DisplayName') or r.get('metadata.DisplayName'),
            'threat_level': r.get('metadata.ThreatLevel'), 'star_level': r.get('asset.StarLevel'),
            'resists': r.get('metadata.Resists'), 'missing': []}
    out = ['%s  "%s"  (%s)' % (row, info['display_name'] or '-', cls or 'no class')]
    for k in ('metadata.ThreatLevel', 'metadata.Resists', 'metadata.Exploration_BehemothStarLevel',
              'asset.StarLevel', 'asset.ComplexityLevel', 'metadata.BuiltInModifiers'):
        if r.get(k) not in (None, [], ''):
            out.append('  %s = %s' % (k.split('.', 1)[1], assets._fmt(r[k])))
    if not cls:
        info['missing'].append('class')
        return info, out
    pkg = blueprints.package_for_class(cls, ros.idx)
    chain = ros.chain(cls)
    info['package'], info['chain'] = pkg, chain
    if not pkg:
        info['missing'].append('class package')
    out.append('  class chain: %s' % ' -> '.join(chain))
    d = _merged_defaults(chain)
    actor = {k: v for k, (v, _) in d.get('actor', {}).get('props', {}).items()}
    dmg = d.get('DamageComp', {}).get('props', {})
    links = {'ActionSetTable': _ref_name(actor.get('ActionSetTable')),
             'DamageTable': _ref_name(dmg.get('DamageTable', (None,))[0]),
             'BehaviorTree': _ref_name(actor.get('BehaviorTree'))}
    info['links'] = links
    phats = _physics_assets(d, chain, ros)
    info['physics_assets'] = phats
    if not phats:
        info['missing'].append('physics assets')
    for k, v in links.items():
        if not v:
            info['missing'].append(k)
        elif not _load(v):
            info['missing'].append('%s asset %s' % (k, v))

    out += ['', '== Class defaults, merged along the chain (nearest class wins; [class that set it])']
    for comp in sorted(d, key=lambda c: (c != 'actor', c)):
        props = d[comp]['props']
        if props:
            out.append('  %s  (%s)' % (comp, d[comp]['type']))
            out += ['    %s = %s  [%s]' % (k, assets._fmt(v), src) for k, (v, src) in sorted(props.items())]

    out += ['', '== Action set: %s' % (links['ActionSetTable'] or 'none')]
    rows = _table_rows(links['ActionSetTable'])
    montages = []
    for name, a in rows.items():
        out.append('  %s: %s' % (name, assets._props_line(a)))
        m = _ref_name(a.get('Montage'))
        if m:
            montages.append((name, m))
        atk = _ref_name(a.get('Class'))
        rec = _load(atk[:-2]) if isinstance(atk, str) and atk.endswith('_C') else None
        cdo = next((e for n, e in (rec or {}).get('exports', {}).items() if n.startswith('Default__')), None)
        if cdo:
            short = {k: v for k, v in cdo['props'].items() if not k.startswith(LONG_PROPS)}
            if short:
                out.append('      %s defaults: %s' % (atk[:-2], assets._props_line(short)))
            if len(short) < len(cdo['props']):
                out.append('      (attack vectors: assets/%s.txt)' % atk[:-2])
    info['actions'] = len(rows)

    who = {'arch': _ref_name(actor.get('ArchetypeName')), 'cls': _ref_name(actor.get('ClassificationName'))}
    table_rows, generic = set(_table_rows(links['DamageTable'])), set(_table_rows(GENERIC_DAMAGE_TABLE))
    missing_rows = set()
    out += ['', '== Action montages: damage windows (start s, duration s, keys, bodies per role), then the other',
            '   gameplay notifies (sound, particles and camera shakes left out; full list in assets/<montage>.txt).',
            '   This behemoth: archetype %s, classification %s. Requirements on archetype/classification are' %
            (who['arch'], who['cls']),
            '   evaluated: [never here: ...] does not fire on it; [only when: ...] depends on the fight.',
            '   Damage rows (looked up in DamageComp.DamageTable, then %s): + own table, g fallback only,' %
            GENERIC_DAMAGE_TABLE, '   ? neither (no damage from that hit; read from the code, not checked live)']
    nwin = nother = 0
    for name, m in montages:
        ws = ros.montage_windows.get(m)
        rate = ' rate %s' % assets._fmt(rows[name].get('MontagePlayRate')) if rows[name].get('MontagePlayRate') else ''
        out.append('  %s  %s%s' % (name, m, rate))
        for w in ws or []:
            applies, note = _req_check(w.get('requirements'), who)
            keys = w['keys'] + w['keys_after_first_hit']
            if applies:
                nwin += 1
                missing_rows.update(k for k in keys if k not in table_rows and k not in generic)
            roles = '; '.join('%s: %s' % (k.replace('WeaponRole_', ''), ','.join(x['bodies'] + x['skel_bodies']))
                              for k, x in w['roles'].items())
            extra = ''.join('  %s=%s' % (k, assets._fmt(v)) for k, v in w['settings'].items())
            marks = ','.join(_row_mark(k, table_rows, generic) for k in w['keys']) or '-'
            out.append('    %7s %7s  window %-16s %s%s%s' % (assets._fmt(w['start']), assets._fmt(w['duration']),
                                                            marks, roles, extra, _req_label(applies, note)))
        for start, dur, cls, settings in _other_notifies(m):
            nother += any(x in cls.lower() for x in DAMAGE_NOTIFY_HINTS)
            out.append('    %7s %7s  %s%s' % (assets._fmt(start), assets._fmt(dur), cls,
                                             ('  ' + settings[:400] + (' ...' if len(settings) > 400 else ''))
                                             if settings else ''))
    info['action_windows'] = nwin
    info['action_damage_notifies'] = nother
    if rows and not nwin and not nother:
        info['missing'].append('damage source in the action montages')

    out += _spawned_section(ros, rows, montages, table_rows, info, who, missing_rows)
    info['rows_not_found'] = sorted('%s (%s)' % (r, ', '.join(ros.damage_rows().get(r, [])) or 'in no damage table')
                                    for r in missing_rows)
    if info['spawned_missing']:
        info['missing'].append('spawned classes not in the atlas: %s' % ', '.join(info['spawned_missing']))

    out += ['', '== Damage table: %s' % (links['DamageTable'] or 'none')]
    for name, a in _table_rows(links['DamageTable']).items():
        out.append('  %s: %s' % (name, assets._props_line({k: v for k, v in a.items() if v not in (0, 0.0)})))

    bts = _bt_closure(links['BehaviorTree']) if links['BehaviorTree'] else []
    info['behavior_trees'] = bts
    out += ['', '== Behavior trees (root first; text in assets/<name>.txt)', '  ' + (', '.join(bts) or 'none')]
    out += ['', "== Physics assets (referenced by the defaults, or in the behemoth's folder; bodies in assets/<name>.txt)"]
    for n in phats:
        rec = _load(n)
        bodies = len(rec['exports'][n]['props'].get('SkeletalBodySetups') or []) if rec else 0
        out.append('  %s  (%d bodies)' % (n, bodies))
    return info, out


def _tables_with(row_struct, ros):
    """Names of dumped data tables whose row struct name contains row_struct."""
    out = []
    for n, r in ros.assets.items():
        if r['type'] == 'DataTable':
            rec = _load(n)
            if row_struct in str(rec['exports'].get(n, {}).get('props', {}).get('RowStruct')):
                out.append(n)
    return sorted(out)


def sheets(log=print):
    ros = Roster()
    if not ros.rows:
        log('roster tables not dumped yet: run python -m tools.atlas behemoths --dump')
        return
    os.makedirs(SHEETS_DIR, exist_ok=True)
    summary = {}
    for row in sorted(ros.rows):
        info, lines = sheet(ros, row, ros.rows[row])
        summary[row] = info
        with open(os.path.join(SHEETS_DIR, row + '.txt'), 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')
    with open(OUT_JSON, 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=1, ensure_ascii=False)
    out = ['Behemoth roster and data coverage (python -m tools.atlas behemoths)',
           'One sheet per row in behemoths/<row>.txt. Rows come from behemoth_metadata_table and behemoth_asset_table.',
           "thr = threat level, acts = action set rows; in those actions' montages: wins = damage windows,",
           'spawn = notifies that spawn something that can hit or deal damage (AoEs, projectiles, beams, grabs).',
           '', '%-26s %-34s %3s %4s %4s %5s  %s' % ('row', 'name', 'thr', 'acts', 'wins', 'spawn', 'missing')]
    for row, i in summary.items():
        out.append('%-26s %-34s %3s %4s %4s %5s  %s' % (
            row, (i['display_name'] or '-')[:34], i['threat_level'] or '', i.get('actions', ''),
            i.get('action_windows', ''), i.get('action_damage_notifies', ''), ', '.join(i['missing'])))
    out += ['', "Damage rows named by windows or spawns that can fire on the behemoth (archetype/classification",
            "requirements evaluated) but in neither its damage table nor %s. Those hits deal no damage;" %
            GENERIC_DAMAGE_TABLE, 'a window still uses up its target. Read from the data and code; not checked live:']
    out += ['  %-26s %s' % (row, ', '.join(i['rows_not_found'])) for row, i in summary.items()
            if i.get('rows_not_found')] or ['  none']
    out += ['', 'Dumped but not reached from any roster row (clones, twins, combo tables, unused trees):']
    reached = {'action set': {i.get('links', {}).get('ActionSetTable') for i in summary.values()},
               'damage': {i.get('links', {}).get('DamageTable') for i in summary.values()},
               'tree': {x for i in summary.values() for x in i.get('behavior_trees', [])}}
    for kind, names in (('action set', _tables_with('ActionSet', ros)), ('damage', _tables_with('DamageTableData', ros)),
                        ('tree', sorted(n for n, r in ros.assets.items() if r['type'] == 'BehaviorTree'
                                        and '/Monsters/' in r['package']))):
        left = [n for n in names if n not in reached[kind]]
        out.append('  %s (%d of %d): %s' % (kind, len(left), len(names), ', '.join(left) or 'none'))
    with open(OUT_TXT, 'w', encoding='utf-8') as f:
        f.write('\n'.join(out) + '\n')
    log('%d sheets; %d rows with something missing -> %s' % (
        len(summary), sum(1 for i in summary.values() if i['missing']), os.path.relpath(OUT_TXT, paths.OUT)))
