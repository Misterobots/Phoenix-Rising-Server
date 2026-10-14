"""LSB-first bit I/O matching UE's FBitReader / FBitWriter."""
import struct


class BitReader:
    def __init__(self, data: bytes, nbits: int):
        self.n = int.from_bytes(data, 'little')
        self.nbits = nbits
        self.pos = 0

    def left(self):
        return self.nbits - self.pos

    def read(self, k):
        if k > self.left():
            raise EOFError('bit underflow')
        v = (self.n >> self.pos) & ((1 << k) - 1)
        self.pos += k
        return v

    def bit(self):
        return self.read(1)

    def read_bytes(self, count):
        return self.read(8 * count).to_bytes(count, 'little')

    def read_double(self):
        return struct.unpack('<d', self.read_bytes(8))[0]

    def read_packed(self):
        """FArchive::SerializeIntPacked: 7 data bits + 1 continuation bit per byte."""
        value = shift = 0
        while True:
            byte = self.read(8)
            value |= (byte >> 1) << shift
            shift += 7
            if not byte & 1:
                return value

    def read_fstring(self):
        n = self.read(32)
        if n >= 1 << 31:
            n -= 1 << 32
        if n < 0:
            return self.read_bytes(-n * 2).decode('utf-16le', 'replace').rstrip('\0')
        return self.read_bytes(n).decode('latin-1').rstrip('\0')


class BitWriter:
    def __init__(self):
        self.n = 0
        self.nbits = 0

    def write(self, value, bits):
        self.n |= (value & ((1 << bits) - 1)) << self.nbits
        self.nbits += bits

    def bit(self, b):
        self.write(1 if b else 0, 1)

    def write_bytes(self, data: bytes):
        self.n |= int.from_bytes(data, 'little') << self.nbits
        self.nbits += 8 * len(data)

    def write_bits_int(self, value, bits):
        """Append `bits` bits of an already-assembled bit string held in an int (LSB first)."""
        self.write(value, bits)

    def write_double(self, d):
        self.write_bytes(struct.pack('<d', d))

    def write_packed(self, value):
        while True:
            chunk = value & 0x7F
            value >>= 7
            self.write((chunk << 1) | (1 if value else 0), 8)
            if not value:
                return

    def write_fstring(self, s: str):
        if s == '':
            self.write(0, 32)
            return
        try:
            raw = s.encode('latin-1') + b'\0'
            self.write(len(raw), 32)
            self.write_bytes(raw)
        except UnicodeEncodeError:
            raw = s.encode('utf-16le') + b'\0\0'
            self.write((-(len(raw) // 2)) & 0xFFFFFFFF, 32)
            self.write_bytes(raw)

    def to_bytes(self, extra_terminator=True):
        """Datagram bytes: bits then a terminating 1 bit (UE convention), zero-padded to a byte."""
        n, nb = self.n, self.nbits
        if extra_terminator:
            n |= 1 << nb
            nb += 1
        return n.to_bytes((nb + 7) // 8, 'little')
