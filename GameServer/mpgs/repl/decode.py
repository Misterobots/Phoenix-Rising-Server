"""Decode a replicated-property block (FRepLayout::ReceiveProperties) with a Layout from schema.py."""
import copy

from .values import read_prop, Unsupported
from .structs import STRUCT_READERS


class OrderedFields(dict):
    """Decoded property map retaining wire order for exact re-encoding."""
    def __init__(self):
        super().__init__()
        self.order = []

    def __setitem__(self, key, value):
        if key not in self:
            self.order.append(key)
        super().__setitem__(key, value)

    def __deepcopy__(self, memo):
        out = type(self)()
        memo[id(self)] = out
        for key, value in self.items():
            out[copy.deepcopy(key, memo)] = copy.deepcopy(value, memo)
        out.order = copy.deepcopy(self.order, memo)
        return out


def handle_table(layout, lo, hi):
    """Relative-handle -> cmd index for the cmd range [lo, hi), nested arrays count as one handle."""
    out, i = [], lo
    while i < hi:
        c = layout.cmds[i]
        out.append(i)
        i = c.end if c.type == 'DynamicArray' else i + 1
    return out


class Decoder:
    def __init__(self, layout):
        self.L = layout
        self._tables = {}
        self.trace = []
        self.allow_bad_root_handle = False
        self.stop_pos = None

    def table(self, lo, hi):
        t = self._tables.get((lo, hi))
        if t is None:
            t = self._tables[(lo, hi)] = handle_table(self.L, lo, hi)
        return t

    def read_cmd(self, r, cmd, out, key):
        t = cmd.type
        self.trace.append((cmd.name, r.pos))
        if t.startswith('NetSerialize:'):
            name = t[len('NetSerialize:'):]
            fn = STRUCT_READERS.get(name)
            if fn is None:
                raise Unsupported('struct ' + name)
            out[key] = fn(r)
        else:
            out[key] = read_prop(r, cmd.prop)

    def read_level(self, r, lo, hi, out, in_array=False):
        """Read handle/value pairs for the cmd range until handle 0 (top level or one array)."""
        tbl = self.table(lo, hi)
        n = len(tbl)
        is_root = lo == 0 and hi == len(self.L.cmds) - 1 and not in_array
        while True:
            handle_pos = r.pos
            h = r.read_packed()
            if h == 0:
                return
            elem, idx = ((h - 1) // n, (h - 1) % n) if in_array else (None, h - 1)
            if idx >= n:
                # Some captured PlayerState blocks continue with handles that
                # are outside the reflected property layout. Preserve that
                # suffix as opaque bits while keeping the already-decoded
                # property prefix usable. Never soften nested-array failures.
                if is_root and self.allow_bad_root_handle:
                    self.stop_pos = handle_pos
                    return
                raise ValueError('bad handle %d (n=%d)' % (h, n))
            ci = tbl[idx]
            cmd = self.L.cmds[ci]
            dest = out if elem is None else out.setdefault(elem, OrderedFields())
            if cmd.type == 'DynamicArray':
                self.trace.append((cmd.name + '#array', r.pos))
                cnt = r.read(16)
                self.trace.append(('count=%d' % cnt, r.pos))
                sub = OrderedFields()
                self.read_level(r, ci + 1, cmd.end - 1, sub, True)
                dest[cmd.name] = {'count': cnt, 'elems': sub}
            else:
                self.read_cmd(r, cmd, dest, cmd.name)

    def read_block(self, r, allow_bad_root_handle=False):
        out = OrderedFields()
        self.allow_bad_root_handle = allow_bad_root_handle
        self.stop_pos = None
        if r.bit():
            raise Unsupported('property checksums')
        self.read_level(r, 0, len(self.L.cmds) - 1, out)
        return out
