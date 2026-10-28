"""Generate data/atlas/: one JSON per class + cross-reference indexes. Rerunnable; verified.json and
native_notes.json are hand-written and never touched here."""
import glob
import json
import os
import re

from . import blueprints, paths
from .sdk import Sdk

RPC_KINDS = (('NetServer', 'server'), ('NetClient', 'client'), ('NetMulticast', 'multicast'))


def _rpc(flags):
    for f, kind in RPC_KINDS:
        if f in flags:
            return kind
    return None


def _layouts():
    out = {}
    p = os.path.join(paths.BUILD_DATA, 'layouts.jsonl')
    if os.path.exists(p):
        for line in open(p, encoding='utf-8'):
            r = json.loads(line)
            if 'class' in r:
                out[r['class']] = {x['n']: x['ri'] for x in r.get('props', [])}
    return out


def _sdk_props(sdk, cls, onrep_names):
    hdr = sdk.props.get(cls)
    out = []
    for p in (hdr[1] if hdr else []):
        net = 'Net' in p['flags']
        d = {'name': p['name'], 'type': p['type'], 'offset': '0x%X' % p['offset'], 'size': p['size'],
             'flags': p['flags'], 'net': net, 'repnotify': 'RepNotify' in p['flags']}
        if d['repnotify']:
            fn = 'OnRep_' + p['name']
            d['onrep'] = fn if fn in onrep_names else None
            d['onrep_source'] = 'name match OnRep_<prop> (native RepNotify name is not in the SDK)'
        out.append(d)
    return out


def _all_function_names(sdk, cls):
    names = set()
    for c in [cls] + sdk.parents.get(cls, []):
        names.update(f['name'] for f in sdk.functions.get(c, []))
    return names


def parent_chain(sdk, cls, bp, idx):
    """SDK chain, or for a Blueprint missing from the SDK: follow Blueprint parents through the paks until one is."""
    if sdk.parents.get(cls) or not bp:
        return sdk.parents.get(cls) or []
    chain, cur = [], bp['super']
    while cur and cur != 'None' and cur not in chain:
        chain.append(cur)
        if cur in sdk.parents:
            return chain + sdk.parents[cur]
        pkg = blueprints.package_for_class(cur, idx)
        dumped = blueprints.dump([pkg]).get(pkg) if pkg else None
        cur = blueprints.super_of(dumped, cur) if dumped else None
    return chain


def build_class(sdk, cls, bp, layouts, idx):
    parents = parent_chain(sdk, cls, bp, idx)
    fn_names = _all_function_names(sdk, cls)
    if bp:
        fn_names.update(f['name'] for f in bp['functions'])
    rep_idx = layouts.get(cls, {})
    hdr = sdk.props.get(cls)
    rec = {'name': cls, 'kind': 'blueprint' if bp or cls.endswith('_C') else 'native',
           'parents': parents, 'size': sdk.size.get(cls), 'sdk_header': hdr[2] if hdr else None}

    props = _sdk_props(sdk, cls, fn_names)
    if bp:   # the Blueprint's own property list carries RepNotifyFunc and RepIndex verbatim
        byname = {p['name']: p for p in props}
        props = []
        for p in bp['properties']:
            d = dict(byname.get(p['name'], {}))
            d.update({'name': p['name'], 'type': d.get('type') or p['type'], 'bp_type': p['type'],
                      'flags': p['flags'], 'net': 'Net' in p['flags'], 'repnotify': 'RepNotify' in p['flags']})
            if 'RepNotifyFunc' in p:
                d['onrep'], d['onrep_source'] = p['RepNotifyFunc'], 'blueprint RepNotifyFunc'
            if 'RepIndex' in p:
                d['rep_index'] = p['RepIndex']
            props.append(d)
    for p in props:
        if p['name'] in rep_idx:
            p['schema_rep_index'] = rep_idx[p['name']]   # data/2.1.1/layouts.jsonl (the server's repl schema)
    rec['properties'] = props

    comps = [{'name': p['name'], 'class': p['type'].rstrip('*').strip(), 'source': 'property'}
             for p in props if p['type'].rstrip('*').strip().endswith('Component')]
    if bp:
        comps += bp['components']
    rec['components'] = comps

    fns = {}
    for f in sdk.functions.get(cls, []):
        native = 'Native' in f['flags']
        fns[f['name']] = {'name': f['name'], 'flags': f['flags'], 'ret': f['ret'], 'params': f['params'],
                          'rpc': _rpc(f['flags']), 'native': native, 'rva': f['rva'] if native else None}
    if bp:
        for f in bp['functions']:
            d = fns.setdefault(f['name'], {'name': f['name'], 'native': False, 'rva': None,
                                           'params': [{'name': p['name'], 'type': p['type']} for p in f['params']]})
            d['bp_flags'] = f['flags']
            d['rpc'] = d.get('rpc') or _rpc(f['flags'])
            for k in ('statements', 'reads', 'sets', 'calls', 'authority', 'tables', 'assets'):
                d[k] = f[k]
    rec['functions'] = sorted(fns.values(), key=lambda f: f['name'])

    tables = set(bp['default_tables']) if bp else set()
    for f in rec['functions']:
        tables.update(f.get('tables', []))
    rec['tables'] = sorted(tables)
    if bp:
        rec['package'] = bp['package']
        rec['pseudo_file'] = 'pseudo/' + os.path.basename(paths.class_file(cls, '.txt')) if bp['pseudo'] else None
    return rec


GAME_DATA_SKIP = {'classes.json', 'functions.json', 'enums.json', 'layouts.jsonl', 'conditions.jsonl'}


def _game_data_texts():
    """Server-side game data exported from the client (data/2.1.1, minus the SDK copies)."""
    out = {}
    for fn in glob.glob(os.path.join(paths.BUILD_DATA, '**', '*.json*'), recursive=True):
        if os.path.basename(fn) not in GAME_DATA_SKIP:
            out[os.path.relpath(fn, paths.GAMESERVER).replace(os.sep, '/')] = open(fn, encoding='utf-8').read()
    return out


def game_data_links(cls, texts):
    """Files under data/2.1.1 naming this class or its package (word match), with the hit count."""
    stem = cls[:-2] if cls.endswith('_C') else cls
    rx = re.compile(r'(?<![A-Za-z0-9_])%s(?:_C)?(?![A-Za-z0-9_])' % re.escape(stem))
    return {f: n for f, t in texts.items() for n in [len(rx.findall(t))] if n}


def write_pseudo(cls, pseudo):
    os.makedirs(paths.PSEUDO_DIR, exist_ok=True)
    with open(paths.class_file(cls, '.txt', paths.PSEUDO_DIR), 'w', encoding='utf-8') as f:
        f.write('# %s: Blueprint bytecode rendered by kismet_pseudo.py ([n] = byte offset, @n = jump target)\n' % cls)
        for name in sorted(pseudo):
            f.write('\n=== %s\n' % name)
            for line in pseudo[name]:
                f.write('  %s\n' % line)


def build(classes, refresh=False, log=print):
    sdk = Sdk()
    layouts = _layouts()
    idx = blueprints.package_index(refresh=refresh)
    resolved = []
    for c in classes:
        r = sdk.find(c) or (c if blueprints.package_for_class(c, idx) else None)
        if not r and not c.endswith('_C') and blueprints.package_for_class(c + '_C', idx):
            r = c + '_C'
        if r:
            resolved.append(r)
        else:
            log('skip %s: not in the SDK and no Blueprint package' % c)
    pkgs = {c: blueprints.package_for_class(c, idx) for c in resolved}
    dumps = blueprints.dump(sorted({p for p in pkgs.values() if p}), refresh=refresh)
    os.makedirs(paths.CLASSES_DIR, exist_ok=True)
    analyzed = {}
    texts = _game_data_texts()
    for c in resolved:
        bp = None
        if pkgs[c] in dumps:
            if pkgs[c] not in analyzed:
                analyzed[pkgs[c]] = blueprints.analyze(dumps[pkgs[c]])
            bp = analyzed[pkgs[c]].get(c)
        rec = build_class(sdk, c, bp, layouts, idx)
        rec['game_data'] = game_data_links(c, texts)
        if bp and bp['pseudo']:
            write_pseudo(c, bp['pseudo'])
        with open(paths.class_file(c), 'w', encoding='utf-8') as f:
            json.dump(rec, f, indent=1)
        log('%-45s %s props=%d fns=%d' % (c, rec['kind'], len(rec['properties']), len(rec['functions'])))
    write_indexes()


def write_indexes():
    """Cross-reference indexes over every class file present (so partial builds accumulate)."""
    sets, calls, tables, classes, native = {}, {}, {}, {}, {}
    for fn in sorted(glob.glob(os.path.join(paths.CLASSES_DIR, '*.json'))):
        rec = json.load(open(fn, encoding='utf-8'))
        c = rec['name']
        classes[c] = {'kind': rec['kind'], 'parent': (rec['parents'] or [None])[0], 'package': rec.get('package')}
        for t in rec['tables']:
            tables.setdefault(t, set()).add(c)
        for p in rec['properties']:
            if p.get('repnotify') or p.get('net'):
                sets.setdefault(p['name'], set()).add('%s (replicated%s)' % (
                    c, ', OnRep ' + p['onrep'] if p.get('onrep') else ''))
        for f in rec['functions']:
            where = '%s.%s' % (c, f['name'])
            if f.get('rva'):
                native[where] = f['rva']
            for p in f.get('sets', []):
                sets.setdefault(p, set()).add(where)
            for k in f.get('calls', []):
                calls.setdefault(k, set()).add(where)
            for t in f.get('tables', []):
                tables.setdefault(t, set()).add(where)
    def dump(name, d):
        with open(os.path.join(paths.OUT, name), 'w', encoding='utf-8') as f:
            json.dump({k: sorted(v) if isinstance(v, set) else v for k, v in sorted(d.items())}, f, indent=1)
    dump('index_classes.json', classes)
    dump('index_who_sets.json', sets)
    dump('index_who_calls.json', calls)
    dump('index_uses_table.json', tables)
    dump('index_native.json', native)
