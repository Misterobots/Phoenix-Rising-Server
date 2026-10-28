"""Dauntless 2.1.1 game logic atlas. How to use it to restore content: tools/atlas/GUIDE.md

  python -m tools.atlas build [class ...] [--all-sdk] [--refresh]   generate data/atlas (default: starter.txt)
  python -m tools.atlas show <class> [--inherited] [--fn <name>]    structure, RPCs, Blueprint logic, tables, notes
  python -m tools.atlas who-sets <property>                         functions that write it (+ replication)
  python -m tools.atlas who-calls <function>                        functions that call it
  python -m tools.atlas uses-table <table>                          classes/functions that reference it
  python -m tools.atlas native <Class.Function>                     exe address + decompile notes
  python -m tools.atlas verified [text]                             hand-verified behaviour entries
  python -m tools.atlas assets <package|glob ...> [--refresh]       dump data assets (behavior trees, montages,
                                                                    tables, specs) into data/atlas/assets
  python -m tools.atlas asset <name>                                an asset's text view, refs and users
  python -m tools.atlas damage-windows [glob ...] [--refresh]       every behemoth montage's damage windows
                                                                    -> data/atlas/damage_windows.json/.txt
  python -m tools.atlas behemoths [--dump] [--build] [--refresh]    one data sheet per behemoth in the roster
                                                                    -> data/atlas/behemoths/, behemoths.txt
  python -m tools.atlas weapons [--dump key ...] [--refresh]       one data sheet per weapon type (sword, axe, ...),
                                                                    shared weapon infrastructure and armor
                                                                    -> data/atlas/weapons/, weapons.txt
  python -m tools.atlas areas [--dump area ...] [--refresh]        items, shops/loot/rewards and islands: one sheet
                                                                    per catalog group, reward kind and island spec
                                                                    -> data/atlas/{items,rewards,islands}/ and
                                                                    items.txt, rewards.txt, islands.txt
  python -m tools.atlas area-rules [--dump] [--refresh]            what is allowed in town vs on a hunt (quick
                                                                    items, area checks in code, menu flags)
                                                                    -> data/atlas/area_rules.txt / .json
Names match case-insensitively; who-*/uses-table also accept substrings.
"""
import argparse
import json
import os
import re
import sys

from . import paths


def _j(p, default=None):
    if not os.path.exists(p):
        return default
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def _class_file(name):
    idx = _j(os.path.join(paths.OUT, 'index_classes.json'), {})
    low = {k.lower(): k for k in idx}
    for cand in (name, name + '_C', name[1:]):
        if cand.lower() in low:
            return low[cand.lower()]
    return None


def _verified():
    return _j(paths.VERIFIED, {'entries': []})['entries']


def _notes():
    return _j(paths.NATIVE_NOTES, {'notes': {}})['notes']


def _related_verified(words):
    words = [w.lower() for w in words if w]
    return [e for e in _verified()
            if any(w in json.dumps(e).lower() for w in words)]


def _print_verified(es):
    for e in es:
        print('  * %s  [%s, %s]' % (e['fact'], e['source'], e['date']))


def cmd_show(a):
    c = _class_file(a.cls)
    if not c:
        sys.exit('no atlas entry for %s (python -m tools.atlas build %s)' % (a.cls, a.cls))
    rec = _j(paths.class_file(c))
    if a.fn:
        return show_fn(rec, a.fn)
    print('%s  (%s)  size=%s  header=%s' % (c, rec['kind'], rec['size'], rec['sdk_header']))
    if rec.get('package'):
        print('package: %s' % rec['package'])
    print('parents: %s' % ' -> '.join(rec['parents']))
    recs = [rec]
    if a.inherited:
        for p in rec['parents']:
            pc = _class_file(p)
            if pc:
                recs.append(_j(paths.class_file(pc)))
    if rec['components']:
        print('\ncomponents:')
        for x in rec['components']:
            print('  %-40s %s  (%s)' % (x['name'], x['class'], x['source']))
    for r in recs:
        hdr = '' if r is rec else '  [from %s]' % r['name']
        print('\nproperties%s:  (R = replicated)' % hdr)
        for p in r['properties']:
            rep = 'R' if p.get('net') else ' '
            extra = ''
            if p.get('repnotify'):
                extra = '  OnRep=%s' % (p.get('onrep') or '?')
            if 'schema_rep_index' in p:
                extra += '  schema_ri=%s' % p['schema_rep_index']
            print('  %s %-36s %-40s %s%s' % (rep, p['name'], p['type'][:40], p.get('offset', ''), extra))
        rpcs = [f for f in r['functions'] if f.get('rpc')]
        if rpcs:
            print('\nRPCs%s:' % hdr)
            for f in rpcs:
                print('  %-9s %s(%s)' % (f['rpc'], f['name'], ', '.join('%s %s' % (p['type'], p['name'])
                                                                      for p in f['params'])))
    print('\nfunctions:  (N = native with exe address, B = Blueprint bytecode; auth = server-authority checks)')
    for f in rec['functions']:
        tag = 'N' if f.get('native') else ('B' if f.get('statements') else ' ')
        bits = []
        if f.get('rva'):
            bits.append('rva=' + f['rva'])
        if f.get('authority'):
            bits.append('auth=' + ','.join(f['authority']))
        if f.get('sets'):
            bits.append('sets=' + ','.join(f['sets'][:6]) + ('...' if len(f['sets']) > 6 else ''))
        if f.get('tables'):
            bits.append('tables=%d' % len(f['tables']))
        print('  %s %-44s %s' % (tag, f['name'], '  '.join(bits)))
    if rec['tables']:
        print('\ndata tables / catalogs:')
        for t in rec['tables']:
            print('  ' + t)
    if rec.get('game_data'):
        print('\ngame data mentioning it (data/2.1.1):')
        for f, n in rec['game_data'].items():
            print('  %-50s %d hits' % (f, n))
    notes = {k: v for k, v in _notes().items() if re.split(r'[.:]', k)[0] == c}
    if notes:
        print('\nnative notes:')
        for k, v in notes.items():
            print('  %s: %s' % (k, v.get('summary')))
    vs = _related_verified([c])
    if vs:
        print('\nverified behaviour:')
        _print_verified(vs)
    if rec.get('pseudo_file'):
        print('\nBlueprint code: data/atlas/%s   (or: show %s --fn <name>)' % (rec['pseudo_file'], c))


def show_fn(rec, name):
    f = next((f for f in rec['functions'] if f['name'].lower() == name.lower()), None)
    if not f:
        sys.exit('%s has no function %s' % (rec['name'], name))
    print(json.dumps(f, indent=1))
    if rec.get('pseudo_file'):
        text = open(os.path.join(paths.OUT, rec['pseudo_file']), encoding='utf-8').read()
        head = '\n=== %s\n' % f['name']
        i = text.find(head)
        if i >= 0:
            j = text.find('\n=== ', i + len(head))
            print(text[i:j if j >= 0 else None])


def _lookup(index_name, q, last_segment=False):
    idx = _j(os.path.join(paths.OUT, index_name), {})
    ql = q.lower()
    exact = [k for k in idx if k.lower() == ql or (last_segment and k.split('.')[-1].lower() == ql)]
    keys = exact or [k for k in idx if ql in k.lower()]
    if not keys:
        print('nothing in the atlas matches %r (only built classes are indexed)' % q)
    for k in sorted(keys):
        print('%s:' % k)
        for w in idx[k]:
            print('  ' + w)


def cmd_who_sets(a):
    _lookup('index_who_sets.json', a.prop)
    vs = _related_verified([a.prop])
    if vs:
        print('\nverified behaviour:')
        _print_verified(vs)


def cmd_who_calls(a):
    _lookup('index_who_calls.json', a.fn, last_segment=True)


def cmd_uses_table(a):
    _lookup('index_uses_table.json', a.table)


def cmd_native(a):
    idx = _j(os.path.join(paths.OUT, 'index_native.json'), {})
    q = a.fn.replace('::', '.').lower()
    hits = [k for k in idx if k.lower() == q] or [k for k in idx if q in k.lower()]
    notes = _notes()
    for k in sorted(hits)[:50]:
        print('%-60s rva=%s  (UFunction native pointer; image base 0x140000000)' % (k, idx[k]))
        if k in notes:
            print(json.dumps(notes[k], indent=1))
    # notes for functions without a reflected name are keyed 'Class::Function @0xADDR'
    for k in sorted(k for k in notes if k not in idx and q in k.replace('::', '.').lower()):
        print(k)
        print(json.dumps(notes[k], indent=1))
        hits.append(k)
    if not hits:
        print('no native function matches %r' % a.fn)


def cmd_verified(a):
    es = _related_verified(a.text.split()) if a.text else _verified()
    _print_verified(es)


def cmd_build(a):
    from .build import build
    from .sdk import Sdk
    classes = a.classes
    if a.all_sdk:
        classes = Sdk().classes()
    elif not classes:
        classes = [l.strip() for l in open(paths.STARTER, encoding='utf-8')
                   if l.strip() and not l.startswith('#')]
    build(classes, refresh=a.refresh)


def cmd_assets(a):
    from .assets import build_assets
    build_assets(a.patterns, refresh=a.refresh)


def cmd_behemoths(a):
    from . import behemoths
    if a.dump:
        behemoths.dump(refresh=a.refresh)
    if a.build:
        from .build import build
        build(behemoths.blueprint_classes(), refresh=a.refresh, log=lambda m: None)
    behemoths.sheets()


def cmd_weapons(a):
    from . import weapons
    if a.dump:
        weapons.dump(a.dump, refresh=a.refresh)
    weapons.sheets()


def cmd_areas(a):
    from . import areas
    if a.dump:
        areas.dump(a.dump, refresh=a.refresh)
    areas.sheets()


def cmd_area_rules(a):
    from . import area_rules
    if a.dump:
        area_rules.dump(refresh=a.refresh)
    area_rules.main()


def cmd_damage_windows(a):
    from .windows import build as build_windows
    build_windows(a.patterns or None, refresh=a.refresh)


def cmd_asset(a):
    from .assets import ASSETS_DIR, INDEX
    idx = _j(INDEX, {})
    low = {k.lower(): k for k in idx}
    n = low.get(a.name.lower())
    if not n:
        hits = sorted(k for k in idx if a.name.lower() in k.lower())
        if len(hits) != 1:
            print('\n'.join(hits) if hits else 'no asset matches %r (python -m tools.atlas assets <package>)' % a.name)
            return
        n = hits[0]
    r = idx[n]
    print('%s  (%s)\npackage: %s' % (n, r['type'], r['package']))
    txt = paths.class_file(n, '.txt', ASSETS_DIR)
    if os.path.exists(txt):
        print(open(txt, encoding='utf-8').read())
    else:
        print('exports: data/atlas/assets/%s' % os.path.basename(paths.class_file(n, '.json', ASSETS_DIR)))
    for k in ('refs', 'used_by'):
        if r[k]:
            print('%s:\n  %s' % (k, '\n  '.join(r[k])))


def main():
    ap = argparse.ArgumentParser(prog='python -m tools.atlas', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    b = sub.add_parser('build')
    b.add_argument('classes', nargs='*')
    b.add_argument('--all-sdk', action='store_true', help='every class in the SDK dump (Blueprints included)')
    b.add_argument('--refresh', action='store_true', help='re-list and re-dump Blueprint packages')
    b.set_defaults(f=cmd_build)
    s = sub.add_parser('show')
    s.add_argument('cls')
    s.add_argument('--inherited', action='store_true')
    s.add_argument('--fn')
    s.set_defaults(f=cmd_show)
    for name, arg, f in (('who-sets', 'prop', cmd_who_sets), ('who-calls', 'fn', cmd_who_calls),
                         ('uses-table', 'table', cmd_uses_table), ('native', 'fn', cmd_native)):
        p = sub.add_parser(name)
        p.add_argument(arg)
        p.set_defaults(f=f)
    v = sub.add_parser('verified')
    v.add_argument('text', nargs='?')
    v.set_defaults(f=cmd_verified)
    x = sub.add_parser('assets')
    x.add_argument('patterns', nargs='+')
    x.add_argument('--refresh', action='store_true', help='re-dump packages already in the cache')
    x.set_defaults(f=cmd_assets)
    y = sub.add_parser('asset')
    y.add_argument('name')
    y.set_defaults(f=cmd_asset)
    w = sub.add_parser('damage-windows')
    w.add_argument('patterns', nargs='*', help='montage packages or globs (default: every behemoth montage)')
    w.add_argument('--refresh', action='store_true', help='re-dump packages already in the cache')
    w.set_defaults(f=cmd_damage_windows)
    m = sub.add_parser('behemoths')
    m.add_argument('--dump', action='store_true', help='dump every behemoth data package into data/atlas/assets')
    m.add_argument('--build', action='store_true', help='build the Blueprint code of every behemoth class')
    m.add_argument('--refresh', action='store_true', help='re-list and re-dump packages already in the cache')
    m.set_defaults(f=cmd_behemoths)
    wp = sub.add_parser('weapons')
    wp.add_argument('--dump', nargs='+', metavar='KEY', help='dump these first: shared, sword, axe, hammer, chainblades, warpike, repeaters, '
                    'strikers, bow, armor')
    wp.add_argument('--refresh', action='store_true', help='re-dump packages already in the cache')
    wp.set_defaults(f=cmd_weapons)
    ar = sub.add_parser('areas')
    ar.add_argument('--dump', nargs='+', metavar='AREA', choices=('items', 'rewards', 'islands'),
                    help='dump these first: items, rewards, islands')
    ar.add_argument('--refresh', action='store_true', help='re-dump packages already in the cache')
    ar.set_defaults(f=cmd_areas)
    rr = sub.add_parser('area-rules')
    rr.add_argument('--dump', action='store_true', help='dump the menu tables and quick item classes first')
    rr.add_argument('--refresh', action='store_true', help='re-dump packages already in the cache')
    rr.set_defaults(f=cmd_area_rules)
    a = ap.parse_args()
    a.f(a)


if __name__ == '__main__':
    main()
