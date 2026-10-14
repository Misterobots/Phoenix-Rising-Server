"""Turn a complete actor-channel bunch (exports + body bits) into wire Bunch objects, splitting into partial bunches.

Rules (UChannel::ReceivedRawBunch / UPackageMapClient): export headers are consumed per raw bunch and may appear in any
partial; non-final partial bunches that carry no export header must be byte aligned; open belongs to the initial
partial, close to the final one; the must-be-mapped flag rides on the final partial."""
from ..bitio import BitWriter
from ..wire import Bunch, CHNAME_ACTOR
from .guid import write_export_bunch

MAX_BITS = 7400            # data bits per raw bunch (UE limit is 7480 incl. nothing else; keep headroom)


def _export_chunks(exports):
    """Greedy: lists of root infos whose export header fits in MAX_BITS."""
    chunks, cur, size = [], [], 33
    for e in exports:
        w = BitWriter()
        write_export_bunch(w, [e])
        eb = w.nbits - 33
        if cur and size + eb > MAX_BITS - 64:
            chunks.append(cur)
            cur, size = [], 33
        cur.append(e)
        size += eb
    if cur:
        chunks.append(cur)
    return chunks


def compose(ch, body_bits, body_n, exports, *, open=False, close=False, reliable=True, must_map=False, chname=CHNAME_ACTOR):
    """Export-carrying bunches hold ONLY the export header (the engine ignores any other bits in them); the body follows in
    export-free partial bunches (all but the last byte aligned)."""
    pending = []                                   # (has_exports, bits, n)
    for infos in _export_chunks(exports):
        w = BitWriter()
        write_export_bunch(w, infos)
        pending.append((True, w.n, w.nbits))
    pos = 0
    while pos < body_n or not pending:
        take = min(body_n - pos, MAX_BITS)
        if pos + take < body_n and take % 8:
            take -= take % 8
        pending.append((False, (body_bits >> pos) & ((1 << take) - 1), take))
        pos += take
        if take == 0:
            break
    partial = len(pending) > 1
    out = []
    for i, (has_ex, bits, n) in enumerate(pending):
        last = i == len(pending) - 1
        out.append(Bunch(ch, n, bits, reliable=reliable, open=open and i == 0, close=close and last, chname=chname,
                         partial=partial, partial_initial=partial and i == 0, partial_final=partial and last,
                         exports=has_ex, must_map=must_map and last))
    return out
