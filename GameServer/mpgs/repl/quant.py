"""UE::Net::Read/WriteQuantizedVector (Net/Core QuantizedVectorSerialization.cpp) and rotator/fixed helpers. Bit-exact ports."""
import struct


def read_int(r, vmax):
    """FBitReader::SerializeInt(Value, ValueMax)."""
    v, mask = 0, 1
    while v + mask < vmax and mask:
        if r.bit():
            v |= mask
        mask <<= 1
    return v


def write_int(w, value, vmax):
    """FBitWriter::SerializeInt: writes bits while (mask < vmax)."""
    mask = 1
    # mirrors FBitWriter::SerializeInt: stops once Value+Mask would reach ValueMax relative to the bits already sent
    v = 0
    while v + mask < vmax and mask:
        bit = 1 if value & mask else 0
        w.bit(bit)
        if bit:
            v |= mask
        mask <<= 1


def _bits_needed(v):
    m = v ^ (v >> 63)           # python ints are unbounded; arithmetic shift gives the sign mask
    return m.bit_length() + 1


def read_quantized_vector(r, scale):
    info = read_int(r, 1 << 7)
    count, extra = info & 63, info >> 6
    if count:
        vals = []
        sign = 1 << (count - 1)
        for _ in range(3):
            x = r.read(count)
            vals.append(((x ^ sign) - sign) / (scale if extra else 1))
        return vals
    if extra:
        return [r.read_double() for _ in range(3)]
    return [struct.unpack('<f', r.read_bytes(4))[0] for _ in range(3)]


def write_quantized_vector(w, scale, vec):
    scaled = [c * scale for c in vec]
    if max(abs(c) for c in scaled) < float(1 << 62):
        use_scaled = min(abs(c) for c in vec) < float(1 << 52)
        src = scaled if use_scaled else vec
        ints = [int(c + (0.5 if c >= 0 else -0.5)) for c in src]       # RoundFloatToInt
        count = max(_bits_needed(i) for i in ints)
        write_int(w, (64 if use_scaled else 0) | count, 1 << 7)
        for i in ints:
            w.write(i & ((1 << count) - 1), count)
    else:
        write_int(w, 64, 1 << 7)
        for c in vec:
            w.write_double(c)


def read_rotator(r):
    """FRotator::NetSerialize (SerializeCompressedShort): per axis [nonzero][uint16]."""
    return [r.read(16) * 360.0 / 65536.0 if r.bit() else 0.0 for _ in range(3)]


def write_rotator(w, rot):
    for a in rot:
        s = int(round(a * 65536.0 / 360.0)) & 0xFFFF
        if s:
            w.bit(1)
            w.write(s, 16)
        else:
            w.bit(0)
