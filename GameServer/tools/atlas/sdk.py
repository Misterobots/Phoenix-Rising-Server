"""Layer 1 + 3 sources: the Dumper-7 SDK of the 2.1.1 client.

Dumpspace/ClassesInfo.json  parent chain + class size
Dumpspace/FunctionsInfo.json every reflected function: return, params, flags, and the native function address
CppSDK/SDK/*_classes.hpp     every property with its offset and property flags (Net, RepNotify, ...) in the comment
Class names are stored without the C++ prefix (UMonsterPartComponent -> MonsterPartComponent), as the engine names them.
"""
import glob
import json
import os
import pickle
import re

from . import paths

CLASS_RE = re.compile(r'^(?:class|struct) (?:SDK_ALIGN\(\w+\) )?([UAI]\w+)(?: final)?(?: : public (\w+)(?:, public \w+)*)?\s*$')
MEMBER_RE = re.compile(r'^\t(.+?)\s+(\w+)(?:\s*:\s*\d+)?;\s+//\s*0x([0-9A-F]+)\(0x([0-9A-F]+)\)\((.*)\)\s*$')


def bare(cpp_name):
    return cpp_name[1:] if cpp_name[:1] in 'UAIF' else cpp_name


def type_str(t):
    """Dumpspace type tuple [name, kind, ptr, subtypes] -> 'TArray<FName>'."""
    name, _, ptr, sub = t
    if sub:
        name += '<%s>' % ', '.join(type_str(s) for s in sub)
    return name + (ptr or '')


def _load(name):
    with open(os.path.join(paths.DUMPSPACE, name), encoding='utf-8') as f:
        return json.load(f)['data']


def _header_props():
    """{bare class: (parent, [prop dict])} from every *_classes.hpp, cached (100 MB of headers)."""
    cache = os.path.join(paths.CACHE, 'cppsdk_props.pickle')
    files = sorted(glob.glob(os.path.join(paths.CPPSDK, '*_classes.hpp')))
    stamp = max(os.path.getmtime(f) for f in files)
    if os.path.exists(cache) and os.path.getmtime(cache) >= stamp:
        with open(cache, 'rb') as f:
            return pickle.load(f)
    out = {}
    for fn in files:
        cur = None
        with open(fn, encoding='utf-8', errors='replace') as f:
            for line in f:
                m = CLASS_RE.match(line)
                if m:
                    cur = []
                    out[bare(m.group(1))] = (bare(m.group(2)) if m.group(2) else None, cur, os.path.basename(fn))
                    continue
                if cur is None:
                    continue
                if line.startswith('};'):
                    cur = None
                    continue
                m = MEMBER_RE.match(line)
                if m and not m.group(2).startswith('Pad_') and 'Dumper-7' not in m.group(5):
                    flags = [s.strip() for s in m.group(5).split(',')]
                    cur.append({'name': m.group(2), 'type': m.group(1).replace('class ', '').replace('struct ', ''),
                                'offset': int(m.group(3), 16), 'size': int(m.group(4), 16), 'flags': flags})
    os.makedirs(paths.CACHE, exist_ok=True)
    with open(cache, 'wb') as f:
        pickle.dump(out, f)
    return out


class Sdk:
    def __init__(self):
        self.parents = {}   # bare -> [bare parent chain]
        self.size = {}
        for e in _load('ClassesInfo.json'):
            (k, v), = e.items()
            info = {list(x)[0]: list(x.values())[0] for x in v}
            self.parents[bare(k)] = [bare(p) for p in info.get('__InheritInfo', [])]
            self.size[bare(k)] = info.get('__MDKClassSize')
        self.functions = {}  # bare class -> [fn dict]
        for e in _load('FunctionsInfo.json'):
            (k, v), = e.items()
            fl = self.functions.setdefault(bare(k), [])
            for x in v:
                (fname, (ret, params, addr, flags)), = x.items()
                fl.append({'name': fname, 'ret': type_str(ret),
                           'params': [{'name': p[2], 'type': type_str(p[0])} for p in params],
                           'flags': flags.split('|') if flags else [],
                           'rva': '0x%X' % addr if addr else None})
        self.props = _header_props()

    def classes(self):
        return sorted(self.parents)

    def find(self, name):
        """Case-insensitive lookup, also accepting the C++ prefix or a missing _C."""
        for cand in (name, bare(name), name + '_C'):
            if cand in self.parents:
                return cand
        low = {k.lower(): k for k in self.parents}
        for cand in (name.lower(), bare(name).lower(), name.lower() + '_c'):
            if cand in low:
                return low[cand]
        return None
