"""One client connection: packet notify (seq/ack/history), reliable bunch delivery, optional AES.

Matches UE5.1 FNetPacketNotify semantics (Engine/Private/Net/NetPacketNotify.cpp):
  * our header carries Seq = next outgoing packet number, AckedSeq = newest peer packet we accepted, and a history
    bitfield where bit i == "peer packet (AckedSeq - i) was delivered";
  * the peer's AckedSeq + history tell us which of OUR packets arrived (delivered) or were lost (retransmit reliable bunches);
  * history length is bounded by what the peer has already confirmed (InAckSeqAck).
Reliable bunch sequence numbers are per channel and start at (ServerSeq & 1023) / (ClientSeq & 1023) as observed on the wire.
"""
import time
from collections import OrderedDict, defaultdict, deque

from . import crypto, handshake, wire
from .wire import SEQ_MOD, CH_SEQ_MOD, seq_diff

MAX_CONN_BITS = 7700          # conn-layer bits per packet (keeps the AES datagram under the 1 KiB net MTU)
RESEND_AFTER = 0.5            # seconds without ack before a packet is considered lost
IDLE_ACK_INTERVAL = 0.02      # minimum gap between ack-only packets
KEEPALIVE_INTERVAL = 0.25     # send something at least this often so the client's 20 s timeout never fires


class SentInfo:
    __slots__ = ('time', 'carried_ack', 'reliable')

    def __init__(self, t, carried_ack, reliable):
        self.time, self.carried_ack, self.reliable = t, carried_ack, reliable


class Connection:
    def __init__(self, addr, sid, cid, cookie, net_cl, now=None):
        now = now if now is not None else time.monotonic()
        self.addr, self.sid, self.cid, self.net_cl = addr, sid, cid, net_cl
        server_seq, client_seq = handshake.sequences(cookie)
        self.out_seq = server_seq
        self.out_ack_seq = (server_seq - 1) % SEQ_MOD
        self.in_seq = (client_seq - 1) % SEQ_MOD
        self.in_ack_seq = self.in_seq
        self.in_ack_seq_ack = self.in_seq
        self.received = set()
        self.sent = OrderedDict()
        self.out_reliable = defaultdict(lambda: server_seq & (CH_SEQ_MOD - 1))
        self.in_reliable = defaultdict(lambda: client_seq & (CH_SEQ_MOD - 1))
        self.in_pending = {}                     # (ch, chseq) -> Bunch received ahead of order
        self.queue = deque()
        self.key = None
        self.encrypted = False
        self.last_rx = now
        self.last_tx = 0.0
        self.ack_pending = False
        self.stats = {'rx': 0, 'tx': 0, 'dup': 0, 'lost': 0, 'resent': 0, 'undecodable': 0}

    # ---------------------------------------------------------------- crypto
    def enable_encryption(self, key: bytes):
        self.key = key
        self.encrypted = True

    # ---------------------------------------------------------------- sending
    def queue_bunch(self, b: wire.Bunch):
        if b.reliable and b.chseq is None:
            self.out_reliable[b.ch] = (self.out_reliable[b.ch] + 1) % CH_SEQ_MOD
            b.chseq = self.out_reliable[b.ch]
        self.queue.append(b)

    def queue_bunches(self, bunches):
        for b in bunches:
            self.queue_bunch(b)

    def _history_words(self):
        length = max(seq_diff(self.in_ack_seq, self.in_ack_seq_ack), 1)
        words = min(max((length + 31) // 32, 1), 8)
        out = []
        for w in range(words):
            v = 0
            for i in range(32):
                if ((self.in_ack_seq - (w * 32 + i)) % SEQ_MOD) in self.received:
                    v |= 1 << i
            out.append(v)
        return out

    def _emit(self, bunches, now):
        seq = self.out_seq
        n, nbits = wire.build_packet(seq, self.in_ack_seq, self._history_words(), bunches)
        if self.encrypted:
            raw = crypto.encrypt(n, nbits, self.key, crypto.prefix_bits(self.cid, True, self.sid))
        else:
            raw = crypto.plain_wrap(n, nbits, crypto.prefix_bits(self.cid, False, self.sid))
        self.sent[seq] = SentInfo(now, self.in_ack_seq, [b for b in bunches if b.reliable])
        self.out_seq = (seq + 1) % SEQ_MOD
        self.last_tx = now
        self.ack_pending = False
        self.stats['tx'] += 1
        return raw

    def pump(self, now=None, max_packets=64):
        """Build the datagrams to send now (data first, then at most one ack-only packet)."""
        now = now if now is not None else time.monotonic()
        # time-based loss detection
        for seq in [s for s, i in self.sent.items() if now - i.time > RESEND_AFTER]:
            info = self.sent.pop(seq)
            if info.reliable:
                self.stats['resent'] += len(info.reliable)
                for b in reversed(info.reliable):
                    self.queue.appendleft(b)
        out = []
        while self.queue and len(out) < max_packets:
            head = 32 + 32 * 8 + 1 + 1
            bits, chosen = head, []
            while self.queue:
                b = self.queue[0]
                need = b.header_bits() + b.nbits
                if chosen and bits + need > MAX_CONN_BITS:
                    break
                chosen.append(self.queue.popleft())
                bits += need
            out.append(self._emit(chosen, now))
        if not out and ((self.ack_pending and now - self.last_tx >= IDLE_ACK_INTERVAL) or now - self.last_tx >= KEEPALIVE_INTERVAL):
            out.append(self._emit([], now))      # ack-only / keepalive packet
        return out

    # ---------------------------------------------------------------- receiving
    def _process_acks(self, acked, history):
        count = seq_diff(acked, self.out_ack_seq)
        if count <= 0:
            return
        for i in range(count - 1, -1, -1):
            seq = (acked - i) % SEQ_MOD
            delivered = i < 32 * len(history) and bool((history[i // 32] >> (i % 32)) & 1)
            info = self.sent.pop(seq, None)
            if info is None:
                continue
            if delivered:
                if seq_diff(info.carried_ack, self.in_ack_seq_ack) > 0:
                    self.in_ack_seq_ack = info.carried_ack
            else:
                self.stats['lost'] += 1
                for b in reversed(info.reliable):
                    self.queue.appendleft(b)
        self.out_ack_seq = acked

    def on_datagram(self, raw: bytes, now=None):
        """-> list of in-order bunches received (reliable ones in channel order), or [] for duplicates/garbage."""
        now = now if now is not None else time.monotonic()
        prefix = raw[0] & 0x7F
        if prefix & 0x40:
            if self.key is None:
                self.stats['undecodable'] += 1
                return []
            res = crypto.decrypt(raw, self.key)
            if not res:
                self.stats['undecodable'] += 1
                return []
            _, pt, nb = res
        else:
            _, pt, nb = crypto.plain_unwrap(raw)
        try:
            pkt = wire.parse_packet(pt, nb)
        except (EOFError, ValueError):
            self.stats['undecodable'] += 1
            return []
        if not pkt['ok']:
            self.stats['undecodable'] += 1
            return []
        if seq_diff(pkt['seq'], self.in_seq) <= 0:
            self.stats['dup'] += 1
            return []
        self._process_acks(pkt['ack'], pkt['history'])
        self.in_seq = self.in_ack_seq = pkt['seq']
        self.received.add(pkt['seq'])
        if len(self.received) > 600:
            self.received = {s for s in self.received if seq_diff(self.in_seq, s) < 400}
        self.ack_pending = True
        self.last_rx = now
        self.stats['rx'] += 1
        return self._order(pkt['bunches'])

    def _order(self, bunches):
        out = []
        for b in bunches:
            if not b.reliable:
                out.append(b)
                continue
            expected = (self.in_reliable[b.ch] + 1) % CH_SEQ_MOD
            if b.chseq == expected:
                out.append(b)
                self.in_reliable[b.ch] = expected
                while True:               # release buffered successors
                    nxt = (self.in_reliable[b.ch] + 1) % CH_SEQ_MOD
                    held = self.in_pending.pop((b.ch, nxt), None)
                    if held is None:
                        break
                    out.append(held)
                    self.in_reliable[b.ch] = nxt
            elif ((b.chseq - expected) % CH_SEQ_MOD) < CH_SEQ_MOD // 2:
                self.in_pending[(b.ch, b.chseq)] = b         # ahead of order: hold
        return out
