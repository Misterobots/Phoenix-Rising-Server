"""Replication schema built from the live reflection dump (data/2.1.1/layouts.jsonl; sdk211/repdump.jsonl in the full SDK).

Mirrors FRepLayout::InitFromClass / InitFromProperty_r (UE5.1.1): Parents (one per class-rep, static arrays expanded),
Cmds (leaf properties, structs flattened unless STRUCT_NetSerializeNative, dynamic arrays as DynamicArray..Return),
and the handle numbering used on the wire.
"""
import json
import os

STRUCT_NET_SERIALIZE_NATIVE = 0x400
STRUCT_NET_DELTA_SERIALIZE_NATIVE = 0x100000
CPF_REP_SKIP = 0x80000000
SKIPPED_KINDS = set()   # soft object members occupy a handle; value = [bUsePath][FString | NetGUID] (UE 5.1 CoreNet.cpp)

HERE = os.path.dirname(os.path.abspath(__file__))
SDK = os.path.join(HERE, '..', '..', '..', 'sdk211')
# Game build data (the same for every copy of the build): data/2.1.1/ in the server package; the full SDK otherwise.
BUILD_DATA = os.path.join(HERE, '..', '..', 'data', '2.1.1')
_SDK_NAMES = {'layouts.jsonl': os.path.join(SDK, 'repdump.jsonl'), 'conditions.jsonl': os.path.join(SDK, 'lifetime.jsonl')}
_DUMPSPACE = os.path.join(SDK, '5.1.1-682875+__phx-archon_release_2.1.1-Archon', 'Dumpspace')
_SDK_NAMES.update({'classes.json': os.path.join(_DUMPSPACE, 'ClassesInfo.json'),
                   'functions.json': os.path.join(_DUMPSPACE, 'FunctionsInfo.json'),
                   'enums.json': os.path.join(_DUMPSPACE, 'EnumsInfo.json')})


def build_file(name):
    p = os.path.join(BUILD_DATA, name)
    return p if os.path.exists(p) else _SDK_NAMES[name]


DEFAULT_DUMP = build_file('layouts.jsonl')


class Prop:
    """One reflected property (as dumped)."""
    __slots__ = ('name', 'ri', 'rc', 'off', 'dim', 'flags', 'kind', 'st', 'inner', 'key', 'val', 'en', 'em', 'oc', 'under', 'bsz')

    def __init__(self, d):
        self.name, self.ri, self.rc = d['n'], d['ri'], d.get('rc', 0)
        self.off, self.dim, self.flags, self.kind = d['off'], d['dim'], int(d['fl'], 16), d['k']
        self.st = d.get('st')
        self.inner = Prop({'n': '', 'ri': 0, 'off': 0, 'dim': 1, 'fl': '0', **d['inner']}) if 'inner' in d else None
        self.key = Prop({'n': '', 'ri': 0, 'off': 0, 'dim': 1, 'fl': '0', **d['key']}) if 'key' in d else None
        self.val = Prop({'n': '', 'ri': 0, 'off': 0, 'dim': 1, 'fl': '0', **d['val']}) if 'val' in d else None
        self.en, self.em, self.oc = d.get('en'), d.get('em'), d.get('oc')
        self.under, self.bsz = d.get('under'), d.get('bsz')

    def __repr__(self):
        return 'Prop(%s %s%s)' % (self.name, self.kind, ' ' + self.st if self.st else '')


class Cmd:
    __slots__ = ('type', 'prop', 'parent', 'rel', 'end', 'name', 'offset')

    def __init__(self, type_, prop, parent, rel, name):
        self.type, self.prop, self.parent, self.rel, self.end, self.name = type_, prop, parent, rel, 0, name


class Layout:
    def __init__(self, cls):
        self.cls = cls
        self.parents = []    # (Prop, array_index)
        self.cmds = []       # flattened, final Return appended
        self.parent_cmd_range = []   # (start, end) per parent


class Schema:
    def __init__(self, path=DEFAULT_DUMP):
        self.classes, self.structs = {}, {}
        with open(path) as f:
            for line in f:
                r = json.loads(line)
                props = [Prop(p) for p in r['props']]
                if 'class' in r:
                    self.classes[r['class']] = (r['super'], props)
                else:
                    self.structs[r['struct']] = (r['super'], int(r['sflags'], 16), props)
        self._layouts = {}
        # full inheritance (the live dump only lists classes that have net properties of their own)
        self.inherit = {}
        with open(build_file('classes.json')) as f:
            for e in json.load(f)['data']:
                for k, v in e.items():
                    inh = [x for x in v if '__InheritInfo' in x][0]['__InheritInfo']
                    self.inherit[k[1:]] = [i[1:] for i in inh]
        self.members = {}        # class name (no prefix) -> {member name: type name (no prefix/pointer)}
        with open(build_file('classes.json')) as f:
            for e in json.load(f)['data']:
                for k, v in e.items():
                    mm = {}
                    for m in v:
                        for mk, mv in m.items():
                            if not mk.startswith('__') and isinstance(mv, list) and mv and isinstance(mv[0], list) and mv[0]:
                                mm[mk] = mv[0][0][1:] if mv[0][0][:1] in 'UAF' else mv[0][0]
                    self.members[k[1:]] = mm
        # net RPCs per class: [(name, flags string, params)] from FunctionsInfo
        self.funcs = {}
        with open(build_file('functions.json')) as f:
            for e in json.load(f)['data']:
                for k, v in e.items():
                    lst = []
                    for m in v:
                        for fn, fv in m.items():
                            if 'Net' in fv[3].split('|'):
                                lst.append((fn, fv[3], fv[1]))
                    self.funcs[k[1:]] = lst
        # real replication conditions (live GetLifetimeReplicatedProps on every CDO): class -> {RepIndex: (cond, raw2, raw3)}
        self.life = {}
        lp = build_file('conditions.jsonl')
        if os.path.exists(lp):
            with open(lp) as f:
                for line in f:
                    r = json.loads(line)
                    self.life[r['class']] = {e[0]: (e[3], e[1], e[2]) for e in r['life']}
        self.enums = {}
        with open(build_file('enums.json')) as f:
            for e in json.load(f)['data']:
                for k, v in e.items():
                    self.enums[k] = v

    # -- class chain -------------------------------------------------------
    def class_chain(self, name):
        """Root-first list of dumped (net-carrying) classes in name's inheritance chain, including name."""
        full = [name] + self.inherit.get(name, [])
        return [c for c in full[::-1] if c in self.classes]

    def net_props(self, cls):
        """ClassReps order: parent chain first, then this class' props by RepIndex; static arrays expanded."""
        reps = []
        for c in self.class_chain(cls):
            for p in sorted(self.classes[c][1], key=lambda p: p.ri):
                for j in range(p.dim):
                    reps.append((p, j))
        return reps

    # -- layout --------------------------------------------------------------
    def struct_members(self, name):
        sup, flags, props = self.structs[name]
        members = [p for p in props if not (p.flags & CPF_REP_SKIP)]
        members.sort(key=lambda p: (p.off, p.name))
        return members

    def _init_prop(self, lay, parent, prop, rel, name, rec_ns=False):
        """InitFromProperty_r; returns new relative handle."""
        if prop.kind == 'ArrayProperty':
            rel += 1
            start = len(lay.cmds)
            c = Cmd('DynamicArray', prop, parent, rel, name)
            lay.cmds.append(c)
            self._init_prop(lay, parent, prop.inner, 0, name + '[]')
            lay.cmds.append(Cmd('Return', None, parent, 0, ''))
            c.end = len(lay.cmds)
            return rel
        if prop.kind == 'StructProperty':
            sflags = self.structs[prop.st][1] if prop.st in self.structs else 0
            if prop.st not in self.structs:
                raise KeyError('struct %s missing from dump' % prop.st)
            if sflags & STRUCT_NET_SERIALIZE_NATIVE:
                rel += 1
                lay.cmds.append(Cmd('NetSerialize:' + prop.st, prop, parent, rel, name))
                return rel
            for m in self.struct_members(prop.st):
                for j in range(m.dim):
                    rel = self._init_prop(lay, parent, m, rel, '%s.%s' % (name, m.name) + ('[%d]' % j if m.dim > 1 else ''))
            return rel
        if prop.kind in SKIPPED_KINDS:
            return rel
        rel += 1
        lay.cmds.append(Cmd(prop.kind, prop, parent, rel, name))
        return rel

    def layout(self, cls):
        if cls in self._layouts:
            return self._layouts[cls]
        lay = Layout(cls)
        rel = 0
        for i, (p, j) in enumerate(self.net_props(cls)):
            lay.parents.append((p, j))
            start = len(lay.cmds)
            rel = self._init_prop(lay, i, p, rel, p.name + ('[%d]' % j if p.dim > 1 else ''))
            lay.parent_cmd_range.append((start, len(lay.cmds)))
        lay.cmds.append(Cmd('Return', None, -1, 0, ''))
        self._layouts[cls] = lay
        return lay

    def member_class(self, cls, name):
        """Type of the member `name` on cls or its ancestors (components are looked up this way)."""
        for c in [cls] + self.inherit.get(cls, []):
            t = self.members.get(c, {}).get(name)
            if t:
                return t
        return None

    def own_net_functions(self, cls):
        """UClass::NetFields: Net functions declared in cls that do not override a super function, sorted by name."""
        sup_names = set()
        for a in self.inherit.get(cls, []):
            for fn, _, _ in self.funcs.get(a, []):
                sup_names.add(fn)
        # a function overriding one in an ancestor (any flags) is excluded; ancestors' non-net functions are unknown -> approximate
        return sorted(fn for fn, _, _ in self.funcs.get(cls, []) if fn not in sup_names)

    def field_index(self, cls):
        """FClassNetCache field list for cls: [('prop'|'func', name)], index = position (properties then RPCs, per class from the root)."""
        out = []
        chain = [c for c in ([cls] + self.inherit.get(cls, []))[::-1]]
        for c in chain:
            if c in self.classes:
                seen = set()
                for p in sorted(self.classes[c][1], key=lambda p: p.ri):
                    out.append(('prop', p.name))
            for fn in self.own_net_functions(c):
                out.append(('func', fn))
        return out

    # ELifetimeCondition
    COND = ['None', 'InitialOnly', 'OwnerOnly', 'SkipOwner', 'SimulatedOnly', 'AutonomousOnly', 'SimulatedOrPhysics', 'InitialOrOwner',
            'Custom', 'ReplayOrOwner', 'ReplayOnly', 'SimulatedOnlyNoReplay', 'SimulatedOrPhysicsNoReplay', 'SkipReplay', 'Dynamic', 'Never']

    def condition(self, cls, rep_index):
        """Real ELifetimeCondition name of property `rep_index` of `cls` (None if the class has no lifetime list / entry)."""
        for c in [cls] + self.inherit.get(cls, []):
            tab = self.life.get(c)
            if tab is not None:
                e = tab.get(rep_index)
                return None if e is None else self.COND[e[0]] if e[0] < len(self.COND) else e[0]
        return None
