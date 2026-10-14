"""FAESHandlerComponent packet crypto (verified byte-exact against 916 captured packets).

AES-256-ECB + PKCS#7; key = the 32 raw bytes of the matchmaking key. Wire layout of an encrypted datagram:
  7 prefix bits | ciphertext (16*k bytes, starting at bit 7) | 1 outer terminator bit
Prefix bits (LSB first): SessionId(2) ClientId(3) bHandshake(1)=0 bEncrypted(1).
The plaintext is the UE connection packet bits plus the connection terminator bit, then the handler's own
terminator bit (the highest set bit) before PKCS#7 padding.
"""
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

HEADER_BITS = 7


def prefix_bits(client_id: int, encrypted: bool, session_id: int = 0) -> int:
    return (session_id & 3) | ((client_id & 7) << 2) | (0 << 5) | ((1 if encrypted else 0) << 6)


def _shift_right_bits(data: bytes, s: int) -> bytes:
    return (int.from_bytes(data, 'little') >> s).to_bytes(len(data), 'little')


def decrypt(packet: bytes, key: bytes):
    """-> (prefix7, plaintext bytes, bit count before the handler terminator) or None."""
    if len(packet) < 17 or (len(packet) - 1) % 16 != 0:
        return None
    header = packet[0] & 0x7F
    body = _shift_right_bits(packet, HEADER_BITS)[:(len(packet) * 8 - HEADER_BITS) // 128 * 16]
    pt = Cipher(algorithms.AES(key), modes.ECB()).decryptor().update(body)
    pad = pt[-1]
    if not (1 <= pad <= 16 and pt[-pad:] == bytes([pad]) * pad):
        return None
    pt = pt[:-pad]
    if not pt or pt[-1] == 0:
        return None
    return header, pt, (len(pt) - 1) * 8 + pt[-1].bit_length() - 1


def encrypt(bits_int: int, nbits: int, key: bytes, prefix7: int) -> bytes:
    n = (bits_int & ((1 << nbits) - 1)) | (1 << nbits)
    pt = n.to_bytes((nbits + 1 + 7) // 8, 'little')
    pad = 16 - (len(pt) % 16)
    pt += bytes([pad]) * pad
    ct = Cipher(algorithms.AES(key), modes.ECB()).encryptor().update(pt)
    total = (int.from_bytes(ct, 'little') << HEADER_BITS) | (prefix7 & 0x7F)
    total |= 1 << (len(ct) * 8 + HEADER_BITS)
    return total.to_bytes((len(ct) * 8 + HEADER_BITS + 1 + 7) // 8, 'little')


def plain_wrap(bits_int: int, nbits: int, prefix7: int) -> bytes:
    """Unencrypted connection packet: prefix7 | conn bits | outer terminator."""
    total = ((bits_int & ((1 << nbits) - 1)) << HEADER_BITS) | (prefix7 & 0x7F)
    total |= 1 << (nbits + HEADER_BITS)
    return total.to_bytes((nbits + HEADER_BITS + 1 + 7) // 8, 'little')


def plain_unwrap(raw: bytes):
    """-> (prefix7, conn bits as bytes, nbits including the connection terminator)."""
    n = int.from_bytes(raw, 'little')
    tot = n.bit_length() - 1
    return raw[0] & 0x7F, (n >> HEADER_BITS).to_bytes(len(raw), 'little'), tot - HEADER_BITS
