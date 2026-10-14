"""UE5.1 connection-layer packet: notify header + packet info + bunches (verified on 916 captured packets).

Packet bits (LSB first):
  PackedHeader u32: Seq(14) << 18 | AckedSeq(14) << 4 | (HistoryWordCount-1)(4);   then HistoryWordCount x u32
  bHasPacketInfoPayload(1) [JitterClockTimeMS(10) bHasServerFrameTime(1) [FrameTime(8)]]
  bunches...; then the connection terminator bit '1'.
Bunch: bControl [bOpen bClose [CloseReason(4)]] bReplicationPaused bReliable ChIndex(packed) bHasPackageMapExports
       bHasMustBeMappedGUIDs bPartial [ChSequence(10) if reliable] [bPartialInitial bPartialFinal if partial]
       [ChName if reliable||open: bHardcoded + packed EName (255 Control, 102 Actor) | FString + int32] BunchDataBits(13) data
"""
from .bitio import BitReader, BitWriter

SEQ_MOD = 1 << 14
CH_SEQ_MOD = 1 << 10
MAX_BUNCH_BITS = 7488
CHNAME_CONTROL = ('hardcoded', 255)
CHNAME_ACTOR = ('hardcoded', 102)


def seq_diff(a, b):
    """Signed distance a-b on the 14-bit sequence ring."""
    d = (a - b) % SEQ_MOD
    return d - SEQ_MOD if d >= SEQ_MOD // 2 else d


class Bunch:
    __slots__ = ('ch', 'control', 'open', 'close', 'close_reason', 'rep_paused', 'reliable', 'chseq', 'exports',
                 'must_map', 'partial', 'partial_initial', 'partial_final', 'chname', 'nbits', 'data')

    def __init__(self, ch, nbits=0, data=0, reliable=False, open=False, close=False, control=None, chname=None,
                 partial=False, partial_initial=False, partial_final=False, exports=False, must_map=False,
                 chseq=None, close_reason=0, rep_paused=False):
        self.ch, self.nbits, self.data = ch, nbits, data
        self.reliable, self.open, self.close = reliable, open, close
        self.control = bool(open or close) if control is None else control
        self.chname = chname
        self.partial, self.partial_initial, self.partial_final = partial, partial_initial, partial_final
        self.exports, self.must_map, self.chseq = exports, must_map, chseq
        self.close_reason, self.rep_paused = close_reason, rep_paused

    def copy(self):
        b = Bunch.__new__(Bunch)
        for k in self.__slots__:
            setattr(b, k, getattr(self, k))
        return b

    def header_bits(self):
        w = BitWriter()
        self.write_header(w)
        return w.nbits

    def write_header(self, w: BitWriter):
        w.bit(self.control)
        if self.control:
            w.bit(self.open)
            w.bit(self.close)
            if self.close:
                w.write(self.close_reason, 4)
        w.bit(self.rep_paused)
        w.bit(self.reliable)
        w.write_packed(self.ch)
        w.bit(self.exports)
        w.bit(self.must_map)
        w.bit(self.partial)
        if self.reliable:
            w.write(self.chseq, 10)
        if self.partial:
            w.bit(self.partial_initial)
            w.bit(self.partial_final)
        if self.reliable or self.open:
            name = self.chname or CHNAME_ACTOR
            if name[0] == 'hardcoded':
                w.bit(1)
                w.write_packed(name[1])
            else:
                w.bit(0)
                w.write_fstring(name[0])
                w.write(name[1], 32)
        w.write(self.nbits, 13)

    def payload(self):
        return self.data.to_bytes((self.nbits + 7) // 8, 'little')

    def __repr__(self):
        return 'Bunch(ch=%d %db rel=%d open=%d close=%d chseq=%s part=%d%d%d)' % (
            self.ch, self.nbits, self.reliable, self.open, self.close, self.chseq,
            self.partial, self.partial_initial, self.partial_final)


def build_packet(seq, acked, history_words, bunches):
    """-> (bits as int, nbit count INCLUDING the connection terminator)."""
    w = BitWriter()
    w.write((seq << 18) | (acked << 4) | (len(history_words) - 1), 32)
    for h in history_words:
        w.write(h, 32)
    w.bit(0)                       # bHasPacketInfoPayload
    for b in bunches:
        b.write_header(w)
        w.write(b.data, b.nbits)
    w.bit(1)                       # connection terminator
    return w.n, w.nbits


def parse_packet(pt: bytes, nbits: int):
    """pt/nbits as returned by crypto.decrypt / plain_unwrap (nbits includes the connection terminator)."""
    b = BitReader(pt, nbits)
    v = b.read(32)
    pkt = {'seq': v >> 18, 'ack': (v >> 4) & 0x3FFF, 'history': [b.read(32) for _ in range((v & 0xF) + 1)]}
    if b.bit():
        pkt['jitter'] = b.read(10)
        if b.bit():
            pkt['frame_time'] = b.read(8)
    out = []
    while b.left() > 1:
        control = b.bit()
        op = cl = 0
        reason = 0
        if control:
            op, cl = b.bit(), b.bit()
            if cl:
                reason = b.read(4)
        paused, reliable = b.bit(), b.bit()
        ch = b.read_packed()
        exports, must_map, partial = b.bit(), b.bit(), b.bit()
        chseq = b.read(10) if reliable else None
        pi = pf = 0
        if partial:
            pi, pf = b.bit(), b.bit()
        chname = None
        if reliable or op:
            chname = ('hardcoded', b.read_packed()) if b.bit() else (b.read_fstring(), b.read(32))
        nb = b.read(13)
        data = b.read(nb)
        out.append(Bunch(ch, nb, data, bool(reliable), bool(op), bool(cl), bool(control), chname, bool(partial), bool(pi),
                         bool(pf), bool(exports), bool(must_map), chseq, reason, bool(paused)))
    pkt['bunches'] = out
    pkt['ok'] = b.left() == 1 and b.read(1) == 1
    return pkt
