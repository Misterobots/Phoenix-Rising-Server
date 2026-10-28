"""Layer 2 + 4 sources: Blueprint packages of the cooked 2.1.1 paks, read with AssetDump.

Each package is dumped once into the cache (AssetDump dump), then every Blueprint function is rendered with
kismet_pseudo.py and tagged with what it reads, sets, calls, which authority checks it makes and which data
tables / catalogs it references. Tags come straight from the bytecode tokens; nothing is inferred.
"""
import json
import os
import re
import subprocess

from . import paths

AUTHORITY_FUNCS = {'HasAuthority', 'IsServer', 'SwitchHasAuthority', 'IsDedicatedServer', 'IsStandalone',
                   'GetLocalRole', 'GetRemoteRole', 'IsLocallyControlled', 'IsNetMode', 'GetNetMode'}
TABLE_CLASSES = ('DataTable', 'CompositeDataTable', 'CurveTable', 'CompositeCurveTable', 'CurveFloat',
                 'StringTable')
LET_TOKENS = ('EX_Let', 'EX_LetBool', 'EX_LetObj', 'EX_LetWeakObjPtr', 'EX_LetDelegate', 'EX_LetMulticastDelegate')


def _pseudo_module():
    """kismet_pseudo.py runs main() on import, so load its definitions without that last call."""
    src = open(paths.KISMET_PSEUDO, encoding='utf-8').read()
    src = src[:src.rindex('\nmain()')]
    ns = {'__name__': 'kismet_pseudo'}
    exec(compile(src, paths.KISMET_PSEUDO, 'exec'), ns)
    ns['var'] = _var_name   # kismet_pseudo's var() prints native field paths as '?'
    base = ns['R']

    def R(x):   # tokens kismet_pseudo prints as raw dicts; anything else goes to it unchanged
        if not isinstance(x, dict) or 'Token' not in x:
            return base(x)
        t, g = x['Token'], x.get
        if t == 'EX_LetValueOnPersistentFrame':
            return 'frame.%s = %s' % (_var_name(g('DestinationProperty')), R(g('AssignmentExpression')))
        if t == 'EX_StructMemberContext':
            return '%s.%s' % (R(g('StructExpression')), re.sub(r'_\d+_[0-9A-F]{32}$', '', _var_name(g('Property'))))
        if t == 'EX_InterfaceContext' and g('InterfaceValue'):
            return R(g('InterfaceValue'))
        if t == 'EX_SwitchValue':
            cases = ', '.join('%s: %s' % (R(c.get('CaseIndexValueTerm')), R(c.get('CaseTerm')))
                              for c in g('Cases') or [])
            return 'switch(%s){%s, default: %s}' % (R(g('IndexTerm')), cases, R(g('DefaultTerm')))
        if t == 'EX_BindDelegate':
            return '%s = bind(%s.%s)' % (R(g('Delegate')), R(g('ObjectTerm')), g('FunctionName'))
        if t in ('EX_AddMulticastDelegate', 'EX_RemoveMulticastDelegate'):
            return '%s %s= %s' % (R(g('Delegate')), '+' if t.startswith('EX_Add') else '-', R(g('DelegateToAdd')))
        if t in ('EX_SetMap', 'EX_SetSet'):
            return '%s = {%s}' % (R(g('MapProperty') or g('SetProperty')), R(g('Elements')))
        return base(x)
    ns['R'] = R
    return ns


_PSEUDO = None


def render(stmts):
    global _PSEUDO
    if _PSEUDO is None:
        _PSEUDO = _pseudo_module()
    out = []
    for s in stmts:
        try:
            out.append('[%s] %s' % (s.get('StatementIndex', '?'), _PSEUDO['R'](s)))
        except Exception as e:   # one odd token must not lose the rest of the function
            out.append('[%s] <render error %s: %s>' % (s.get('StatementIndex', '?'), type(e).__name__, s.get('Token')))
    return out


# ---------------------------------------------------------------- packages

def _run(*args):
    return subprocess.run(['dotnet', paths.ASSETDUMP_DLL] + list(args), capture_output=True, text=True,
                          encoding='utf-8', errors='replace')


def package_index(refresh=False):
    """{package basename lower: [package path]} for every .uasset in the paks."""
    p = os.path.join(paths.CACHE, 'packages.txt')
    if refresh or not os.path.exists(p):
        os.makedirs(paths.CACHE, exist_ok=True)
        r = _run('find', '')
        with open(p, 'w', encoding='utf-8') as f:
            f.write(r.stdout)
    idx = {}
    for line in open(p, encoding='utf-8'):
        line = line.strip()
        if line:
            idx.setdefault(line.rsplit('/', 1)[-1].lower(), []).append(line)
    return idx


def package_for_class(cls, idx):
    """BP class 'monsterpart_bpc_C' -> its package path, or None when not a (unique) Blueprint package."""
    if not cls.endswith('_C'):
        return None
    hits = idx.get(cls[:-2].lower(), [])
    return hits[0] if len(hits) == 1 else (sorted(hits, key=len)[0] if hits else None)


def dump(pkgs, refresh=False):
    """Dump packages (AssetDump dump) into the cache; returns {package: json path}."""
    out_dir = os.path.join(paths.CACHE, 'dumps')
    os.makedirs(out_dir, exist_ok=True)
    def target(p):
        return os.path.join(out_dir, p.replace('/', '__') + '.json')
    todo = [p for p in pkgs if refresh or not os.path.exists(target(p))]
    tmp = os.path.join(paths.CACHE, 'dump_tmp')
    for i in range(0, len(todo), 40):
        os.makedirs(tmp, exist_ok=True)
        _run('dump', tmp, *todo[i:i + 40])
        for p in todo[i:i + 40]:
            src = os.path.join(tmp, p.rsplit('/', 1)[-1] + '.json')
            if not os.path.exists(src):   # a batch can lose packages that dump fine on their own
                _run('dump', tmp, p)
            if os.path.exists(src):
                os.replace(src, target(p))
    return {p: target(p) for p in pkgs if os.path.exists(target(p))}


# ---------------------------------------------------------------- analysis

def _name(ref):
    s = ref.get('ObjectName', '') if isinstance(ref, dict) else str(ref)
    return s.split("'")[-2] if "'" in s else s


def _obj_class(ref):
    s = ref.get('ObjectName', '') if isinstance(ref, dict) else ''
    return s.split("'")[0] if "'" in s else ''


def _is_table(cls, n):
    """Data tables, curve tables, curves and catalog assets; not a class's own curve subobjects (Owner:Name)."""
    if ':' in n:
        return False
    return 'DataTable' in cls or 'CurveTable' in cls or cls in TABLE_CLASSES or 'catalog' in (cls + n).lower()


def _func_ref(x):
    """'KismetMathLibrary.Greater_IntInt' / 'self.Foo' style name of a call token."""
    if 'VirtualFunctionName' in x:
        return x['VirtualFunctionName']
    n = _name(x.get('Function'))
    return n.replace(':', '.')


def _var_name(v):
    """Name of a variable reference ({'Property': {...}} or the property itself)."""
    if not isinstance(v, dict):
        return str(v)
    if v.get('Path'):   # native property, written as a field path ({'Path': ['AttackTargetScore'], ...})
        return '.'.join(v['Path'])
    return (v.get('Property') or v).get('Name', '?')


class FnTags:
    def __init__(self):
        self.reads, self.sets, self.calls, self.authority, self.tables, self.assets = (set() for _ in range(6))

    def as_dict(self):
        return {k: sorted(getattr(self, k)) for k in ('reads', 'sets', 'calls', 'authority', 'tables', 'assets')}


def _walk(x, t, target=False):
    if isinstance(x, list):
        for i in x:
            _walk(i, t)
        return
    if not isinstance(x, dict):
        return
    tok = x.get('Token')
    if tok in LET_TOKENS:
        _walk(x.get('Variable'), t, target=True)
        _walk(x.get('Expression'), t)
        return
    if tok == 'EX_InstanceVariable':
        (t.sets if target else t.reads).add(_var_name(x.get('Variable')))
        return
    if tok in ('EX_Context', 'EX_Context_FailSilent') and target:
        # other.Prop = ...  -> the property on the context object is the one written
        _walk(x.get('ObjectExpression'), t)
        inner = x.get('ContextExpression') or {}
        if inner.get('Token') == 'EX_InstanceVariable':
            t.sets.add(_var_name(inner.get('Variable')))
        else:
            _walk(inner, t, target=True)
        return
    if tok in ('EX_CallMath', 'EX_FinalFunction', 'EX_LocalFinalFunction', 'EX_LocalVirtualFunction',
               'EX_VirtualFunction', 'EX_CallMulticastDelegate'):
        f = _func_ref(x)
        t.calls.add(f)
        if f.split('.')[-1] in AUTHORITY_FUNCS:
            t.authority.add(f.split('.')[-1])
    if tok == 'EX_ObjectConst':
        v = x.get('Value') or {}
        cls, n = _obj_class(v), _name(v)
        if _is_table(cls, n):
            t.tables.add('%s %s' % (cls, n))
        elif cls and not cls.startswith('Class') and not n.startswith('Default__'):
            t.assets.add('%s %s' % (cls, n))
    for k, v in x.items():
        if isinstance(v, (dict, list)):
            _walk(v, t)


def _prop(p):
    d = {'name': p['Name'], 'type': p.get('Type'),
         'flags': [s.strip() for s in p.get('PropertyFlags', '').split('|') if s.strip()]}
    for k in ('RepIndex', 'RepNotifyFunc'):
        if k in p:
            d[k] = p[k]
    for k in ('Struct', 'Enum', 'PropertyClass'):
        if isinstance(p.get(k), dict):
            d['type'] += '<%s>' % _name(p[k])
    return d


def _scan_defaults(obj, t):
    """Table / catalog references in class defaults and component templates."""
    if isinstance(obj, dict):
        if 'ObjectName' in obj:
            cls, n = _obj_class(obj), _name(obj)
            if _is_table(cls, n):
                t.add('%s %s' % (cls, n))
        for v in obj.values():
            _scan_defaults(v, t)
    elif isinstance(obj, list):
        for v in obj:
            _scan_defaults(v, t)


def super_of(dump_path, cls):
    """Parent class name straight from a dumped package, without analysing its functions."""
    with open(dump_path, encoding='utf-8') as f:
        for e in json.load(f):
            if e.get('Name') == cls and 'GeneratedClass' in e.get('Type', ''):
                return _name(e.get('SuperStruct') or e.get('Super'))
    return None


def analyze(dump_path):
    """One package -> {class: {...}} for each BlueprintGeneratedClass in it."""
    with open(dump_path, encoding='utf-8') as f:
        exports = json.load(f)
    by_name = {}
    for e in exports:
        if e.get('Type') == 'Function':
            owner = _name(e.get('Outer'))
            by_name.setdefault(owner, []).append(e)
    result = {}
    for c in exports:
        if c.get('Type') not in ('BlueprintGeneratedClass', 'WidgetBlueprintGeneratedClass',
                                 'AnimBlueprintGeneratedClass'):
            continue
        name = c['Name']
        fns, pseudo = [], {}
        for fe in by_name.get(name, []):
            t = FnTags()
            code = fe.get('ScriptBytecode') or []
            _walk(code, t)
            fns.append({'name': fe['Name'],
                        'flags': [s.strip().replace('FUNC_', '') for s in (fe.get('FunctionFlags') or '').split('|')
                                  if s.strip()],
                        'params': [_prop(p) for p in fe.get('ChildProperties', [])
                                   if 'Parm' in p.get('PropertyFlags', '')],
                        'statements': len(code), **t.as_dict()})
            if code:
                pseudo[fe['Name']] = render(code)
        comps = []
        for e in exports:
            if e.get('Type') == 'SCS_Node':
                pr = e.get('Properties', {})
                comps.append({'name': pr.get('InternalVariableName'), 'class': _name(pr.get('ComponentClass')),
                              'source': 'SCS'})
        tables = set()
        for e in exports:
            if e.get('Type') not in ('Function',):
                _scan_defaults(e.get('Properties'), tables)
        result[name] = {
            'package': c.get('Package'),
            'super': _name(c.get('SuperStruct') or c.get('Super')),   # CUE4Parse uses either key
            'properties': [_prop(p) for p in c.get('ChildProperties', [])],
            'functions': sorted(fns, key=lambda f: f['name']),
            'components': comps,
            'default_tables': sorted(tables),
            'pseudo': pseudo,
        }
    return result
