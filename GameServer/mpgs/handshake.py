"""UE5.1 stateless connect handshake (StatelessConnectHandlerComponent, HandshakeVersion 3 / NetCLVersion).

Bit layout (LSB first), verified against captured Dauntless traffic and the engine source:
  SessionId(2) ClientId(3) bHandshake(1)=1 bRestart(1) MinVer(8) CurVer(8) Type(8) ClientSentCount(8)
  NetworkVersion(32) SecretId(1) Timestamp(double 64) Cookie(20 bytes) | random padding (64..128 bits) | terminator
Types: 0 Initial (client->server), 1 Challenge (server), 2 Response (client), 3 Ack (server).
Sequence numbers: ServerSeq = int16(cookie[0:2]) & 0x3FFF, ClientSeq = int16(cookie[2:4]) & 0x3FFF.
The cookie is only ever validated by the server, so we mint our own random one and remember it per address.
"""
import os
import random
import struct

from .bitio import BitReader, BitWriter

INITIAL, CHALLENGE, RESPONSE, ACK = 0, 1, 2, 3
MAX_PACKETID = 16384


def parse(raw: bytes):
    """Parse a raw datagram as a handshake packet; None if it is not one."""
    n = int.from_bytes(raw, 'little')
    if n == 0:
        return None
    tot = n.bit_length() - 1
    if tot < 296 + 64 or tot > 296 + 160:
        return None
    b = BitReader(raw, tot)
    try:
        d = {'sid': b.read(2), 'cid': b.read(3), 'hs': b.bit(), 'restart': b.bit(),
             'min': b.read(8), 'cur': b.read(8), 'type': b.read(8), 'count': b.read(8),
             'netcl': b.read(32), 'secret': b.bit(), 'ts': b.read_double(), 'cookie': b.read_bytes(20)}
    except EOFError:
        return None
    return d if d['hs'] == 1 and d['type'] in (INITIAL, CHALLENGE, RESPONSE, ACK) else None


def _build(sid, cid, ptype, count, netcl, secret_bit, ts, cookie, rnd):
    w = BitWriter()
    w.write(sid, 2)
    w.write(cid, 3)
    w.bit(1)          # bHandshakePacket
    w.bit(0)          # bRestartHandshake (ignored client-side)
    w.write(3, 8)     # MinSupportedHandshakeVersion
    w.write(3, 8)     # current handshake version
    w.write(ptype, 8)
    w.write(count, 8)
    w.write(netcl, 32)
    w.bit(secret_bit)
    w.write_double(ts)
    w.write_bytes(cookie)
    pad_bits = rnd.randint(72, 120)
    w.write(rnd.getrandbits(pad_bits), pad_bits)
    return w.to_bytes()


def build_challenge(req, cookie: bytes, elapsed: float, rnd=random) -> bytes:
    return _build(req['sid'], req['cid'], CHALLENGE, req['count'], req['netcl'], 0, elapsed, cookie, rnd)


def build_ack(resp, rnd=random) -> bytes:
    return _build(resp['sid'], resp['cid'], ACK, resp['count'], resp['netcl'], 1, -1.0, resp['cookie'], rnd)


def new_cookie() -> bytes:
    return os.urandom(20)


def sequences(cookie: bytes):
    s, c = struct.unpack('<hh', cookie[:4])
    return s & (MAX_PACKETID - 1), c & (MAX_PACKETID - 1)
