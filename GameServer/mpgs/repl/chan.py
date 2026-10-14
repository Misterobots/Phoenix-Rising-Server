"""Actor-channel bunch reading: partial reassembly, SerializeNewActor header, content blocks (UE5.1)."""
from ..bitio import BitReader
from .guid import read_export_bunch, read_load_object, is_dynamic, flatten, write_export_bunch


class Full:
    """One complete (reassembled) bunch: export infos consumed, bits after the export headers, flags."""
    __slots__ = ('exports', 'open', 'close', 'must_map', 'bits', 'nbits', 'reliable')


class Reassembler:
    """Collects partial bunches of one channel into complete bunches. Export headers are consumed per raw bunch
    (UChannel::ReceivedRawBunch), the remainder is concatenated."""

    def __init__(self):
        self.parts = None

    @staticmethod
    def _strip(b):
        bits, n, infos = b.data, b.nbits, []
        if b.exports:
            r = BitReader(bits.to_bytes((n + 7) // 8, 'little'), n)
            infos = read_export_bunch(r)
            # UChannel::ReceivedRawBunch: a partial bunch that carries exports contributes ONLY its exports
            # (the rest of its data is not appended to the merged bunch)
            if b.partial:
                bits, n = 0, 0
            else:
                bits, n = bits >> r.pos, n - r.pos
        return bits, n, infos

    def feed(self, b):
        bits, n, infos = self._strip(b)
        if not b.partial:
            f = Full()
            f.exports, f.open, f.close, f.must_map, f.bits, f.nbits, f.reliable = infos, b.open, b.close, b.must_map, bits, n, b.reliable
            return f
        if b.partial_initial:
            self.parts = {'bits': 0, 'n': 0, 'infos': [], 'open': b.open}
        if self.parts is None:
            return None
        self.parts['bits'] |= bits << self.parts['n']
        self.parts['n'] += n
        self.parts['infos'] += infos
        if not b.partial_final:
            return None
        f = Full()
        f.exports, f.open, f.close, f.must_map = self.parts['infos'], self.parts['open'], b.close, b.must_map
        f.bits, f.nbits, f.reliable = self.parts['bits'], self.parts['n'], b.reliable
        self.parts = None
        return f


from .quant import read_int, read_quantized_vector, read_rotator


def read_cond_vec(r, default, scale=10):
    """SerializeNewActor ConditionallySerializeQuantizedVector."""
    if not r.bit():
        return default
    if r.bit():                                   # bShouldQuantize -> FVector_NetQuantize10 (scale 10)
        return read_quantized_vector(r, scale)
    return [r.read_double() for _ in range(3)]


def read_new_actor(r, info):
    """UPackageMapClient::SerializeNewActor, reading side (after the export/must-map prefix)."""
    g = read_load_object(r, False, [])
    res = {'guid': g}
    if is_dynamic(g):
        res['archetype'] = read_load_object(r, False, [])
        res['level'] = read_load_object(r, False, [])
        res['loc'] = read_cond_vec(r, [0, 0, 0])
        res['rot'] = read_rotator(r) if r.bit() else [0, 0, 0]
        res['scale'] = read_cond_vec(r, [1, 1, 1])
        res['vel'] = read_cond_vec(r, [0, 0, 0])
    return res


def read_content_block(r, from_client=False):
    """UActorChannel::ReadContentBlockHeader + payload length. -> dict(rep, actor, guid, stable, destroy, cls, outer, bits, nbits).
    from_client: the client writes only the sub-object reference (stable/class info is server-only)."""
    b = {'rep': r.bit(), 'actor': r.bit(), 'guid': None, 'stable': None, 'destroy': False, 'cls': None, 'outer': None}
    if not b['actor']:
        start, infos = r.pos, []
        b['guid'] = read_load_object(r, False, infos)
        # the reference exactly as written (a client names unreplicated sub-objects by path: guid 1 + outer + name)
        b['ref'] = ((r.n >> start) & ((1 << (r.pos - start)) - 1), r.pos - start)
        b['ref_name'] = infos[0].name if infos else None
        b['stable'] = True if from_client else r.bit()
        if not b['stable']:
            if r.bit():
                b['destroy'] = True
                r.read(8)
            else:
                b['cls'] = read_load_object(r, False, [])
            if not r.bit():                     # bActorIsOuter == 0
                b['outer'] = read_load_object(r, False, [])
    if b['destroy']:
        b['nbits'], b['bits'] = 0, 0
        return b
    b['nbits'] = r.read_packed()
    b['bits'] = r.read(b['nbits'])
    return b


def iter_content_blocks(r):
    while r.left() > 0:
        yield read_content_block(r)


def read_field_entries(bits, nbits, nfields):
    """Tail of a content block after the property section: [SerializeInt(idx, nfields+1)][packed nbits][payload]*.
    -> list of (field_index, payload_bits, payload_nbits)."""
    r = BitReader(bits.to_bytes((nbits + 7) // 8, 'little'), nbits)
    out = []
    while r.left() > 0:
        idx = read_int(r, nfields + 1)
        n = r.read_packed()
        out.append((idx, r.read(n), n))
    return out
