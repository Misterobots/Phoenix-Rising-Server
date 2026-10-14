"""NetGUID serialization (UPackageMapClient::InternalLoadObject / ReceiveNetGUIDBunch, UE5.1)."""
from ..bitio import BitReader


class GuidInfo:
    __slots__ = ('guid', 'flags', 'outer_info', 'name', 'checksum')

    @property
    def outer(self):
        return self.outer_info.guid if self.outer_info else 0

    def __repr__(self):
        return 'GUID(%d flags=%s outer=%s name=%r)' % (self.guid, self.flags, self.outer, self.name)


def is_dynamic(g):
    return g > 1 and not (g & 1)


def read_load_object(r: BitReader, exporting, out, depth=0):
    """-> guid. When `exporting` (inside an export bunch) the export flags and optional path are present.
    The root info (with its nested outer chain) is appended to `out` at depth 0."""
    info = _read_info(r, exporting, depth)
    if info is None:
        return 0
    if exporting and depth == 0 or (info.flags is not None and depth == 0):
        out.append(info)
    return info.guid


def _read_info(r, exporting, depth):
    g = r.read_packed()
    if g == 0:
        return None
    info = GuidInfo()
    info.guid, info.flags, info.outer_info, info.name, info.checksum = g, None, None, None, None
    if g == 1 or exporting:
        info.flags = r.read(8)
    if info.flags is not None and (info.flags & 1):          # bHasPath
        info.outer_info = _read_info(r, exporting, depth + 1)
        info.name = r.read_fstring()
        if info.flags & 4:
            info.checksum = r.read(32)
    return info


def write_load_object(w, info, exporting=True):
    if info is None:
        w.write_packed(0)
        return
    w.write_packed(info.guid)
    if info.guid == 1 or exporting:
        w.write(info.flags, 8)
    if info.flags is not None and (info.flags & 1):
        write_load_object(w, info.outer_info, exporting)
        w.write_fstring(info.name)
        if info.flags & 4:
            w.write(info.checksum, 32)


def flatten(info):
    """Yield info and its outers (outermost last)."""
    while info is not None:
        yield info
        info = info.outer_info


def read_export_bunch(r: BitReader):
    """Header of a bHasPackageMapExports bunch -> list[GuidInfo] (bHasRepLayoutExport must be 0)."""
    if r.bit():
        raise ValueError('rep-layout export (compat) not supported')
    n = r.read(32)
    out = []
    for _ in range(n):
        read_load_object(r, True, out)
    return out


def write_export_bunch(w, infos):
    w.bit(0)
    w.write(len(infos), 32)
    for i in infos:
        write_load_object(w, i, True)
