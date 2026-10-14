"""Encode a replicated-property block (FRepLayout::SendProperties wire format) from python values."""
from ..bitio import BitWriter
from .decode import handle_table
from .values import write_prop, Unsupported
from .structs import STRUCT_WRITERS


class Encoder:
    def __init__(self, layout):
        self.L = layout
        self._t = {}

    def table(self, lo, hi):
        t = self._t.get((lo, hi))
        if t is None:
            t = self._t[(lo, hi)] = handle_table(self.L, lo, hi)
        return t

    def write_cmd(self, w, cmd, v):
        if cmd.type.startswith('NetSerialize:'):
            fn = STRUCT_WRITERS.get(cmd.type[len('NetSerialize:'):])
            if fn is None:
                raise Unsupported(cmd.type)
            fn(w, v)
        else:
            write_prop(w, cmd.prop, v)

    def write_level(self, w, lo, hi, values, in_array=False, terminate=True):
        """values: {cmd name: value} (top level) or {elem index: {cmd name: value}} (array)."""
        tbl = self.table(lo, hi)
        n = len(tbl)
        items = []
        if in_array:
            for elem, fields in values.items():
                order = getattr(fields, 'order', None)
                if order is None:
                    order = [self.L.cmds[ci].name for ci in tbl if self.L.cmds[ci].name in fields]
                positions = {self.L.cmds[ci].name: (idx, ci) for idx, ci in enumerate(tbl)}
                for name in order:
                    if name in fields and name in positions:
                        idx, ci = positions[name]
                        items.append((elem * n + idx + 1, ci, fields[name]))
        else:
            order = getattr(values, 'order', None)
            if order is None:
                order = [self.L.cmds[ci].name for ci in tbl if self.L.cmds[ci].name in values]
            positions = {self.L.cmds[ci].name: (idx, ci) for idx, ci in enumerate(tbl)}
            for name in order:
                if name in values and name in positions:
                    idx, ci = positions[name]
                    items.append((idx + 1, ci, values[name]))
        # UE ReceiveProperties_r advances through layout commands once. A
        # decoded OrderedFields map preserves capture order, but appending a
        # newly replicated lower-handle field must not send it after a higher
        # handle. Array elements likewise follow handle/index order.
        # A partial captured prefix has an incompletely known layout and an
        # opaque continuation; preserve its captured order rather than sorting
        # it against schema handles that are known to be incomplete.
        if terminate:
            items.sort(key=lambda item: item[0])
        for h, ci, v in items:
            cmd = self.L.cmds[ci]
            w.write_packed(h)
            if cmd.type == 'DynamicArray':
                w.write(v['count'], 16)
                self.write_level(w, ci + 1, cmd.end - 1, v['elems'], True)
            else:
                self.write_cmd(w, cmd, v)
        if terminate:
            w.write_packed(0)

    def write_block(self, values, terminate=True):
        """-> BitWriter holding the property block ([checksum bit][handles...][0]); empty values -> None (nothing to send)."""
        if not values:
            return None
        w = BitWriter()
        w.bit(0)
        self.write_level(w, 0, len(self.L.cmds) - 1, values, terminate=terminate)
        return w
