"""Damage windows of every behemoth montage: which bodies deal damage, when, with which damage rows and settings.

Reads every AnimMontage package under Archon/Content/Monsters (found with AssetDump classes, cached in
.atlas_cache/classes_monsters.tsv by assets.class_listing) from the AssetDump cache, without writing one asset file per montage, and keeps every notify that is a damage window (a DamageNotifyState
subclass, or any notify with PhysicsBodyNameMap / DamageKeys). Writes data/atlas/damage_windows.json and a
text summary that lists the windows whose settings differ from the native defaults first.

The defaults come from the DamageNotifyState constructor (native_notes.json, 0x272BA20); archon_damage_window_C
keeps them (its class defaults only set BehemothRequirements and the debug flags). A cooked notify only holds
the values that differ from its class defaults, so a missing setting means the default.
"""
import fnmatch
import json
import os

from . import assets, blueprints, paths

OUT_JSON = os.path.join(paths.OUT, 'damage_windows.json')
OUT_TXT = os.path.join(paths.OUT, 'damage_windows.txt')
DEFAULT_PATTERN = 'Archon/Content/Monsters/*'

DEFAULTS = {'MultipleHitLockoutPerTarget': -1.0, 'bExcludedHitActorsForDurationOfMontage': True,
            'bUsePreAttackRaycastTest': False, 'DodgeDetectPreDamageWindow': 0.0, 'UseCurrentWeapon': False}
WINDOW_CLASSES = ('DamageNotifyState', 'archon_damage_window_C', 'Notify_WeaponDamageState_C')
SHOWN = ('PhysicsBodyNameMap', 'DamageKeys', 'DamageKeysAfterFirstHitPerTarget', 'BehemothRequirements')


def _is_window(e):
    return e['type'] in WINDOW_CLASSES or 'PhysicsBodyNameMap' in e['props'] or 'DamageKeys' in e['props']


def _owner(pkg):
    """'Archon/Content/Monsters/aq/lerawr/montages/x_mot' -> 'aq/lerawr'."""
    parts = pkg.split('/')
    i = parts.index('Monsters') if 'Monsters' in parts else 1
    return '/'.join(parts[i + 1:i + 3])


def _requirements(req):
    """BehemothRequirements reduced to the fields that are set (the class default is all empty)."""
    if not isinstance(req, dict):
        return None
    out = {}
    for k, v in req.items():
        if v in (None, '', 'None', [], {}) or str(v).endswith('::None'):
            continue
        if k == 'PartRequirements' and not (v.get('PartNamesToCheck') or v.get('PartStateRequirements')
                                            or v.get('PartStateExclusions')):
            continue
        if k in ('MetadataTagQuery', 'BehemothTagQueries', 'ServerBlackboardKeyComparerValue'):
            if not (v.get('QueryTokenStream') or v.get('Queries')) and k != 'ServerBlackboardKeyComparerValue':
                continue
            if k == 'ServerBlackboardKeyComparerValue' and req.get('ServerBlackboardKey') in (None, 'None'):
                continue
        if k.startswith('ServerBlackboardKeyCompare') and req.get('ServerBlackboardKey') in (None, 'None'):
            continue
        if k in ('StateRequirementType', 'ArchetypeCondition', 'ClassificationCondition'):
            continue   # only meaningful next to the field they qualify
        out[k] = v
    for field, cond in (('StateRequirement', 'StateRequirementType'),
                        ('RequireBehemothArchetypeName', 'ArchetypeCondition'),
                        ('RequireBehemothClassificationName', 'ClassificationCondition')):
        if field in out:
            out[cond] = req.get(cond)
    return out or None


def window_records(rec):
    """One converted montage record -> its damage windows, in start order."""
    name = rec.get('export') or rec['name']
    ex = rec['exports']
    out = []
    for n in ex[name]['props'].get('Notifies') or []:
        ref = n.get('NotifyStateClass') or n.get('Notify')
        e = ex.get(ref[1:]) if isinstance(ref, str) and ref.startswith('@') else None
        if not e or not _is_window(e):
            continue
        p = e['props']
        roles = {}
        for kv in p.get('PhysicsBodyNameMap') or []:
            v = kv.get('Value') or {}
            roles[str(kv.get('Key')).split('::')[-1]] = {
                'bodies': v.get('PhysicsBodies') or [], 'skel_bodies': v.get('SkelPhysicsBodies') or [],
                'last_chance': None if v.get('LastChancePhysicsBody') in (None, 'None') else v['LastChancePhysicsBody']}
        settings = {k: p[k] for k in DEFAULTS if k in p and p[k] != DEFAULTS[k]}
        other = {k: v for k, v in p.items() if k not in SHOWN and k not in DEFAULTS
                 and k not in ('DrawDebug', 'DrawCollisionVolumes') and v not in (None, [], {}, 'None')}
        w = {'start': round(n.get('LinkValue') or 0.0, 4), 'duration': round(n.get('Duration') or 0.0, 4),
             'class': e['type'], 'roles': roles, 'keys': p.get('DamageKeys') or [],
             'keys_after_first_hit': p.get('DamageKeysAfterFirstHitPerTarget') or [], 'settings': settings}
        req = _requirements(p.get('BehemothRequirements'))
        if req:
            w['requirements'] = req
        if other:
            w['other'] = other
        if n.get('bTriggerOnDedicatedServer') is False:
            w['not_on_dedicated_server'] = True
        out.append(w)
    return sorted(out, key=lambda w: w['start'])


def _unusual(w):
    """Why a window is worth a look, or [] when it uses the defaults throughout."""
    why = ['%s=%s' % (k, assets._fmt(v)) for k, v in w['settings'].items()]
    if w['keys_after_first_hit']:
        why.append('after-first-hit keys %s' % ','.join(w['keys_after_first_hit']))
    for role, r in w['roles'].items():
        if role != 'WeaponRole_PRIMARY':
            why.append('role %s' % role)
        if r['last_chance']:
            why.append('LastChancePhysicsBody=%s' % r['last_chance'])
        if r['skel_bodies']:
            why.append('SkelPhysicsBodies=%s' % ','.join(r['skel_bodies']))
    if w.get('not_on_dedicated_server'):
        why.append('not on dedicated server')
    return why


def montage_packages(refresh=False):
    """Every package under Archon/Content/Monsters whose main export is an AnimMontage (names vary: _mot,
    _Montage, _fastblend, ...), from the cached AssetDump class listing."""
    return sorted(p for p, t in assets.class_listing('Archon/Content/Monsters/', refresh).items() if t == 'AnimMontage')


def build(patterns=None, refresh=False, log=print):
    idx = blueprints.package_index()
    pkgs = [p for p in montage_packages(refresh)
            if any(fnmatch.fnmatch(p.lower(), pat.lower()) for pat in patterns or [DEFAULT_PATTERN])]
    dumps = blueprints.dump(pkgs, refresh=refresh)
    by_owner, montages, skipped = {}, 0, []
    for pkg in pkgs:
        if pkg not in dumps:
            skipped.append(pkg)
            continue
        rec, why = assets.convert(pkg, dumps[pkg], idx)
        if rec is None or rec['type'] != 'AnimMontage':
            skipped.append(pkg)
            continue
        montages += 1
        ws = window_records(rec)
        if ws:
            by_owner.setdefault(_owner(pkg), {})[rec['name']] = ws
    data = {'source': 'python -m tools.atlas damage-windows', 'defaults': DEFAULTS,
            'montages_read': montages, 'skipped': skipped, 'behemoths': by_owner}
    with open(OUT_JSON, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    with open(OUT_TXT, 'w', encoding='utf-8') as f:
        f.write('\n'.join(render(data)) + '\n')
    n = sum(len(ws) for m in by_owner.values() for ws in m.values())
    log('%d montages read, %d with damage windows, %d windows, %d skipped -> %s' % (
        montages, sum(len(m) for m in by_owner.values()), n, len(skipped), os.path.relpath(OUT_JSON, paths.OUT)))


def render(data):
    beh = data['behemoths']
    total = sum(len(ws) for m in beh.values() for ws in m.values())
    out = ['Damage windows of behemoth montages (python -m tools.atlas damage-windows)',
           '%d montages read; %d windows in %d montages of %d folders.' % (
               data['montages_read'], total, sum(len(m) for m in beh.values()), len(beh)),
           'Defaults (DamageNotifyState constructor): %s' % ', '.join(
               '%s=%s' % (k, assets._fmt(v)) for k, v in data['defaults'].items()),
           '', '== Windows that differ from the defaults']
    counts = {}
    for owner, ms in sorted(beh.items()):
        for m, ws in sorted(ms.items()):
            for w in ws:
                why = _unusual(w)
                for y in why:
                    counts[y.split('=')[0].split(' ')[0]] = counts.get(y.split('=')[0].split(' ')[0], 0) + 1
                if why:
                    out.append('  %-18s %-52s %6ss  %s  [%s]' % (owner, m, assets._fmt(w['start']),
                                                                ','.join(w['keys']), '; '.join(why)))
    out.insert(5, 'Counts: %s' % (', '.join('%s %d' % kv for kv in sorted(counts.items())) or 'none'))
    out += ['', '== Every window (start s, duration s, damage keys, bodies per role, requirements)']
    for owner, ms in sorted(beh.items()):
        out.append('%s  (%d montages)' % (owner, len(ms)))
        for m, ws in sorted(ms.items()):
            out.append('  %s' % m)
            for w in ws:
                roles = '; '.join('%s: %s' % (r.replace('WeaponRole_', ''), ','.join(x['bodies'] + x['skel_bodies']))
                                  for r, x in w['roles'].items())
                req = ('  req=%s' % json.dumps(w['requirements'], ensure_ascii=False)) if w.get('requirements') else ''
                out.append('    %7s %7s  %-24s %s%s' % (assets._fmt(w['start']), assets._fmt(w['duration']),
                                                       ','.join(w['keys']) or '-', roles, req))
    return out
