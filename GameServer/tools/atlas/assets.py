"""Data assets of the cooked 2.1.1 paks: behavior trees, montages, data tables, island specs, content assets,
hunt modifiers, blackboards and anything else that is not a Blueprint class.

Each package is dumped with AssetDump (same cache as blueprints.py) and written to data/atlas/assets/ as
<name>.json (every export, cleaned up) plus <name>.txt for the kinds that read better as text (behavior trees,
montages, data tables). index_assets.json maps each asset to its type, package, the assets it references and
the assets and classes that reference it.

Clean-up only renames and shortens; no values are changed:
- struct field names lose their Blueprint GUID suffix (Cooldown_18_33D3... -> Cooldown)
- object references become 'Class name' (another package) or '@ExportName' (same package)
- soft references become the asset path, localized text becomes its source string
- user-defined enum values become their display name (behemoth_states_enum::NewEnumerator9 -> ...::Combat)
"""
import fnmatch
import glob
import json
import os
import re

from . import blueprints, paths

ASSETS_DIR = os.path.join(paths.OUT, 'assets')
INDEX = os.path.join(paths.OUT, 'index_assets.json')

# Rendered/binary content with nothing for the server to read; skipped unless named exactly.
SKIP_FOLDERS = ('/anims/', '/materials/', '/textures/', '/meshes/', '/PoseMatching/', '/fx/', '/vfx/', '/audio/',
                '_ctrlrig')
SKIP_TYPES = ('Material', 'MaterialInstanceConstant', 'Texture2D', 'SkeletalMesh', 'StaticMesh', 'AnimSequence',
              'Skeleton', 'ControlRigBlueprint', 'ParticleSystem', 'NiagaraSystem', 'SoundWave',
              'PoseSearchDatabase', 'PoseSearchSchema')
CLASS_TYPES = ('BlueprintGeneratedClass', 'WidgetBlueprintGeneratedClass', 'AnimBlueprintGeneratedClass')
BOILERPLATE = {'TreeAsset', 'ParentNode', 'Children', 'Services', 'NodeName', 'CachedDescription',
               'bIsObservingBB', 'LinkedMontage', 'LinkedSequence', 'EndLink', 'CachedLinkMethod', 'LinkMethod',
               'SlotIndex', 'SegmentIndex', 'SegmentBeginTime', 'SegmentLength', 'TriggerWeightThreshold',
               'NotifyFilterType', 'NotifyFilterLOD', 'bConvertedFromBranchingPoint', 'MontageTickType',
               'TrackIndex', 'NotifyTriggerChance', 'bTriggerOnFollower'}
GUID_SUFFIX = re.compile(r'_\d+_[0-9A-F]{32}$')
ENUM_VALUE = re.compile(r'^(\w+)::(NewEnumerator\d+)$')

_enums = {}


def _enum_names(enum, idx):
    """{NewEnumeratorN: display name} for a user-defined enum, read from its package (cached)."""
    if enum not in _enums:
        _enums[enum] = {}
        hits = idx.get(enum.lower(), [])
        dumped = blueprints.dump(hits[:1]) if hits else {}
        for p in dumped.values():
            for e in json.load(open(p, encoding='utf-8')):
                if e.get('Type') == 'UserDefinedEnum':
                    for kv in e.get('Properties', {}).get('DisplayNameMap', []):
                        v = kv['Value']
                        _enums[enum][kv['Key']] = v.get('CultureInvariantString') or v.get('SourceString') or kv['Key']
    return _enums[enum]


def _short(ref, pkg_name):
    """Object reference -> '@Export' inside this package, else 'Class name'."""
    s = ref.get('ObjectName') or ''
    if "'" not in s:
        return s
    cls, inner = s.split("'")[0], s.split("'")[1]
    if inner.split(':')[0].split('.')[0] == pkg_name and (':' in inner or '.' in inner):
        return '@' + re.split(r'[:.]', inner)[-1]
    return '%s %s' % (cls, inner.split(':')[-1])


def clean(v, pkg_name, idx, refs):
    if isinstance(v, list):
        return [clean(x, pkg_name, idx, refs) for x in v]
    if isinstance(v, str):
        m = ENUM_VALUE.match(v)
        if m:
            return '%s::%s' % (m.group(1), _enum_names(m.group(1), idx).get(m.group(2), m.group(2)))
        return v
    if not isinstance(v, dict):
        return v
    if set(v) == {'ObjectName', 'ObjectPath'}:
        s = _short(v, pkg_name)
        if not s.startswith('@') and ' ' in s:
            refs.add(s.split(' ', 1)[1])
        return s
    if 'AssetPathName' in v and set(v) <= {'AssetPathName', 'SubPathString'}:
        p = v['AssetPathName']
        if not p or p == 'None':
            return None
        refs.add(p.rsplit('.', 1)[-1].removesuffix('_C'))
        return p
    if 'SourceString' in v and ('Key' in v or 'Namespace' in v):
        return v.get('SourceString')
    if set(v) == {'CultureInvariantString'}:
        return v['CultureInvariantString']
    if set(v) <= {'SelectedKeyName', 'bNoneIsAllowedValue'} and 'SelectedKeyName' in v:   # blackboard key
        return 'bb.' + v['SelectedKeyName']
    if set(v) == {'DataTable', 'RowName'}:   # row handle
        t = clean(v['DataTable'], pkg_name, idx, refs)
        return '%s[%s]' % (t.split(' ')[-1] if t else None, v['RowName'])
    return {GUID_SUFFIX.sub('', k): clean(x, pkg_name, idx, refs) for k, x in v.items()}


def _fmt(v):
    if isinstance(v, float):
        return ('%.4f' % v).rstrip('0').rstrip('.')
    if isinstance(v, str):
        return re.sub(r'^\w+::', '', v) if '::' in v and ' ' not in v else v
    return json.dumps(v, ensure_ascii=False)


def _props_line(p, skip=()):
    return ', '.join('%s=%s' % (k, _fmt(x)) for k, x in p.items()
                     if k not in BOILERPLATE and k not in skip and x not in (None, [], {}, 'None'))


# ---------------------------------------------------------------- text renderers

def _node_label(e):
    t = re.sub(r'^(BTComposite_|BTTask_|BTDecorator_|BTService_)', '', e['type']).removesuffix('_C')
    p = e['props']
    name = p.get('NodeName')
    extra = p.get('CachedDescription') or _props_line(p)   # the editor's own summary when it has one
    return '%s%s%s' % (t, ' "%s"' % name if name else '', '  (%s)' % extra if extra else '')


def render_bt(name, ex):
    tree = ex[name]['props']
    out = ['%s  blackboard=%s' % (name, tree.get('BlackboardAsset')),
           '  (children run in order; a Selector stops at the first that succeeds, a Sequence at the first that fails;',
           '   [if ...] lines are the decorators on the node below them)']
    for d in tree.get('RootDecorators') or []:
        out.append('  [root if %s]' % _node_label(ex[d[1:]]))

    def walk(ref, depth, decos, num):
        e = ex.get(ref[1:]) if ref and ref.startswith('@') else None
        pad = '  ' * depth
        for d in decos or []:
            de = ex.get(d[1:])
            if de:
                inv = 'not ' if de['props'].get('bInverseCondition') else ''
                out.append('%s[if %s%s]' % (pad, inv, _node_label(de)))
        if e is None:
            out.append('%s%s%s' % (pad, num, ref))
            return
        out.append(pad + num + _node_label(e))
        for s in e['props'].get('Services') or []:
            se = ex.get(s[1:])
            if se:
                out.append('%s  {service %s}' % (pad, _node_label(se)))
        for i, c in enumerate(e['props'].get('Children') or [], 1):
            walk(c.get('ChildComposite') or c.get('ChildTask'), depth + 1, c.get('Decorators'), '%d) ' % i)
    walk(tree.get('RootNode'), 1, [], '')
    return out


def render_montage(name, ex):
    m = ex[name]['props']
    out = ['%s  length=%ss  blend_out=%s' % (name, _fmt(m.get('SequenceLength')),
                                             _fmt((m.get('BlendOut') or {}).get('BlendTime')))]
    for s in m.get('CompositeSections') or []:
        out.append('  section %-16s at %ss  next=%s' % (s.get('SectionName'), _fmt(s.get('LinkValue')),
                                                         s.get('NextSectionName')))
    for tr in m.get('SlotAnimTracks') or []:
        for seg in (tr.get('AnimTrack') or {}).get('AnimSegments') or []:
            out.append('  slot %s: %s  [%s-%s]s  rate=%s' % (tr.get('SlotName'), seg.get('AnimReference'),
                                                           _fmt(seg.get('AnimStartTime')), _fmt(seg.get('AnimEndTime')),
                                                           _fmt(seg.get('AnimPlayRate'))))
    out.append('  notifies (start s, duration s, class, settings):')
    rows = []
    for n in m.get('Notifies') or []:
        ref = n.get('NotifyStateClass') or n.get('Notify')
        e = ex.get(ref[1:]) if isinstance(ref, str) and ref.startswith('@') else None
        cls = e['type'] if e else (ref or n.get('NotifyName'))
        settings = _props_line(e['props']) if e and e['props'] else ''
        dedicated = '' if n.get('bTriggerOnDedicatedServer', True) else '  [not on dedicated server]'
        rows.append((n.get('LinkValue') or 0.0, '    %7s %7s  %s%s%s' % (
            _fmt(n.get('LinkValue') or 0.0), _fmt(n.get('Duration') or 0.0), cls,
            '  ' + settings if settings else '', dedicated)))
    out += [r for _, r in sorted(rows, key=lambda r: r[0])]
    return out


def render_table(name, ex):
    t = ex[name]['props']
    out = ['%s  row struct=%s  rows=%d' % (name, t.get('RowStruct'), len(t.get('Rows') or {}))]
    for k, row in (t.get('Rows') or {}).items():
        out.append('  %s: %s' % (k, _props_line(row) if isinstance(row, dict) else _fmt(row)))
    return out


def _vec(v, keys='XYZ'):
    return '(%s)' % ', '.join(_fmt(v.get(k, 0.0)) for k in keys) if v else '(0, 0, 0)'


def render_physics(name, ex):
    """Collision bodies per bone; sizes in cm, centre and rotation relative to the bone."""
    out = ['%s  (cm, relative to the bone; rot = pitch, yaw, roll)' % name]
    for ref in ex[name]['props'].get('SkeletalBodySetups') or []:
        b = ex.get(ref[1:], {}).get('props', {})
        geo = b.get('AggGeom') or {}
        out.append('  %s  %s  %s' % (b.get('BoneName'), _fmt(b.get('PhysicsType') or 'Default'),
                                     _fmt(b.get('CollisionResponse') or '')))
        for s in geo.get('SphylElems') or []:
            out.append('    capsule r=%s len=%s at %s rot %s' % (_fmt(s.get('Radius')), _fmt(s.get('Length')),
                                                               _vec(s.get('Center')),
                                                               _vec(s.get('Rotation'), ('Pitch', 'Yaw', 'Roll'))))
        for s in geo.get('SphereElems') or []:
            out.append('    sphere r=%s at %s' % (_fmt(s.get('Radius')), _vec(s.get('Center'))))
        for s in geo.get('BoxElems') or []:
            out.append('    box %sx%sx%s at %s rot %s' % (_fmt(s.get('X')), _fmt(s.get('Y')), _fmt(s.get('Z')),
                                                       _vec(s.get('Center')),
                                                       _vec(s.get('Rotation'), ('Pitch', 'Yaw', 'Roll'))))
    return out


RENDERERS = {'BehaviorTree': render_bt, 'AnimMontage': render_montage, 'DataTable': render_table,
             'CompositeDataTable': render_table, 'PhysicsAsset': render_physics}


# ---------------------------------------------------------------- build

def resolve(patterns, idx):
    """Package paths for names or globs ('Archon/Content/Monsters/aq/lerawr/*'); globs skip render-only folders."""
    every = sorted({p for ps in idx.values() for p in ps})
    out = []
    for pat in patterns:
        if any(ch in pat for ch in '*?['):
            out += [p for p in every if fnmatch.fnmatch(p.lower(), pat.lower())
                    and not any(f.lower() in p.lower() for f in SKIP_FOLDERS)]
        else:
            out += idx.get(pat.rsplit('/', 1)[-1].lower(), [])
    return sorted(set(out))


def convert(pkg, dump_path, idx):
    """One dumped package -> (record, text lines) or (None, reason)."""
    exports = json.load(open(dump_path, encoding='utf-8'))
    name = pkg.rsplit('/', 1)[-1]
    # A Blueprint package holds <name>_C; a montage with a custom blend-in Blueprint holds a class too, but its main
    # export is the montage itself.
    classes = [e.get('Name', '').lower() for e in exports if e.get('Type') in CLASS_TYPES]
    if name.lower() + '_c' in classes or (classes and not any(e.get('Name') == name for e in exports)):
        return convert_defaults(pkg, name, exports, idx)
    main = next((e for e in exports if e.get('Name') == name), exports[0] if exports else None)
    if not main:
        return None, 'empty package'
    if main.get('Type') in SKIP_TYPES:
        return None, 'render-only type %s' % main.get('Type')
    refs = set()
    ex = {}
    for e in exports:
        props = dict(e.get('Properties') or {})
        if 'Rows' in e:                 # data tables keep rows beside Properties
            props['Rows'] = e['Rows']
        ex[e['Name']] = {'type': e.get('Type'), 'props': clean(props, name, idx, refs) or {}}
    refs.discard(name)
    rec = {'name': name, 'package': pkg, 'type': main.get('Type'), 'refs': sorted(refs), 'exports': ex}
    r = RENDERERS.get(main.get('Type'))
    return rec, (r(name, ex) if r else None)


CLASS_STRUCTURE = CLASS_TYPES + ('Function', 'SCS_Node', 'SimpleConstructionScript', 'InheritableComponentHandler')


def convert_defaults(pkg, name, exports, idx):
    """A Blueprint package -> its class defaults and component templates (the code goes through `build`).
    Only values the class sets itself are in the cook; anything inherited unchanged is absent."""
    refs = set()
    ex = {}
    for e in exports:
        if e.get('Type') in CLASS_STRUCTURE or not e.get('Properties'):
            continue
        ex[e['Name']] = {'type': e.get('Type'), 'props': clean(e['Properties'], name, idx, refs)}
    refs.discard(name)
    cls = next((e['Name'] for e in exports if e.get('Type') in CLASS_TYPES), name + '_C')
    rec = {'name': name, 'package': pkg, 'type': 'ClassDefaults', 'class': cls, 'refs': sorted(refs), 'exports': ex}
    out = ['%s  class defaults of %s (only values set in this class; code: show %s)' % (name, cls, cls)]
    for n, e in ex.items():
        out.append('  %s  (%s)' % (n, e['type']))
        for k, v in e['props'].items():
            if v not in (None, [], {}, 'None'):
                out.append('    %s = %s' % (k, _fmt(v)))
    return rec, out


def class_listing(prefix, refresh=False):
    """{package: main export class} for every package under a pak path prefix ('Archon/Content/Monsters/'),
    from AssetDump classes (loads each package once; cached in .atlas_cache/classes_<prefix>.tsv). The main export
    is the one named like the package; a Blueprint package reports its generated class type."""
    tag = prefix.strip('/').replace('Archon/Content/', '').replace('/', '_').lower()
    tsv = os.path.join(paths.CACHE, 'classes_%s.tsv' % tag)
    if refresh or not os.path.exists(tsv):
        os.makedirs(paths.CACHE, exist_ok=True)
        blueprints._run('classes', prefix, tsv)
    out = {}
    for line in open(tsv, encoding='utf-8'):
        f = line.rstrip('\n').split('\t')
        if len(f) != 3:
            continue
        pkg = f[0].rsplit('.', 1)[0]
        name = pkg.rsplit('/', 1)[-1]
        if f[2] in CLASS_TYPES and f[1] == name + '_C':
            out[pkg] = f[2]
        elif f[1] == name and pkg not in out:
            out[pkg] = f[2]
    return out


def build_assets(patterns, refresh=False, log=print):
    idx = blueprints.package_index()
    pkgs = resolve(patterns, idx)
    if not pkgs:
        log('no package matches %s' % ' '.join(patterns))
        return
    build_packages(pkgs, idx, refresh, log)


def build_packages(pkgs, idx, refresh=False, log=print, aliases=None):
    """Dump and convert exact package paths, then rewrite index_assets.json. aliases ({package: name}) files a
    package under another name when two packages share a basename; its main export name is kept in 'export'."""
    dumps = blueprints.dump(pkgs, refresh=refresh)
    os.makedirs(ASSETS_DIR, exist_ok=True)
    for pkg in pkgs:
        if pkg not in dumps:
            log('skip %s: AssetDump wrote nothing' % pkg)
            continue
        rec, text = convert(pkg, dumps[pkg], idx)
        if rec is None:
            log('skip %s: %s' % (pkg, text))
            continue
        if aliases and pkg in aliases:
            rec = dict(rec, name=aliases[pkg], export=rec['name'])
        base = paths.class_file(rec['name'], '', ASSETS_DIR)
        with open(base + '.json', 'w', encoding='utf-8') as f:
            json.dump(rec, f, indent=1, ensure_ascii=False)
        if text:
            with open(base + '.txt', 'w', encoding='utf-8') as f:
                f.write('\n'.join(text) + '\n')
        log('%-50s %s exports=%d%s' % (rec['name'], rec['type'], len(rec['exports']), '  +txt' if text else ''))
    write_index()


def write_index():
    """Every asset file present (partial builds accumulate) + which classes and assets reference each one."""
    out = {}
    for fn in sorted(glob.glob(os.path.join(ASSETS_DIR, '*.json'))):
        rec = json.load(open(fn, encoding='utf-8'))
        out[rec['name']] = {'type': rec['type'], 'package': rec['package'], 'refs': rec['refs'], 'used_by': set()}
    for n, r in out.items():
        for x in r['refs']:
            if x in out:
                out[x]['used_by'].add(n)
    for fn in glob.glob(os.path.join(paths.CLASSES_DIR, '*.json')):
        rec = json.load(open(fn, encoding='utf-8'))
        for f in rec.get('functions', []):
            for a in f.get('assets', []) + f.get('tables', []):
                n = a.split(' ', 1)[-1]
                if n in out:
                    out[n]['used_by'].add('%s.%s' % (rec['name'], f['name']))
        for t in rec.get('tables', []):
            n = t.split(' ', 1)[-1]
            if n in out:
                out[n]['used_by'].add(rec['name'])
    with open(INDEX, 'w', encoding='utf-8') as f:
        json.dump({k: dict(v, used_by=sorted(v['used_by'])) for k, v in sorted(out.items())}, f, indent=1)
