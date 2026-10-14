"""ServerMovePacked (ACharacter RPC, UE 5.1.1 FCharacterServerMovePackedBits) -> the client's authoritative movement.

RPC parameter payload = [send bit][packed NumBits][NumBits of FCharacterNetworkMoveDataContainer]:
  NewMove: float TimeStamp, FVector_NetQuantize10 Acceleration, FVector_NetQuantize100 Location, FRotator ControlRotation (per axis
  [nonzero bit][16 bits]), optional uint8 CompressedMoveFlags, optional base object / bone name / uint8 MovementMode;
  then [bHasPendingMove][...], [bHasOldMove][...], [bDisableCombinedScopedMove] (only the new move is used here)."""
from ..bitio import BitReader
from .guid import read_load_object
from .quant import read_quantized_vector, read_rotator
from .values import read_name


def _opt(r, reader, default):
    return reader(r) if r.bit() else default


def parse_server_move_packed(bits, nbits):
    """-> dict(time, accel, loc, ctrl_rot, flags, base, mode) of the NewMove, or None if the payload is not a packed move."""
    r = BitReader(bits.to_bytes((nbits + 7) // 8, 'little'), nbits)
    if not r.bit():
        return None
    n = r.read_packed()
    if n > r.left():
        return None
    data = r.read(n)
    d = BitReader(data.to_bytes((n + 7) // 8, 'little'), n)
    import struct
    t = struct.unpack('<f', d.read_bytes(4))[0]
    accel = read_quantized_vector(d, 10)
    loc = read_quantized_vector(d, 100)
    rot = read_rotator(d)
    flags = _opt(d, lambda x: x.read(8), 0)
    base = _opt(d, lambda x: read_load_object(x, False, []), 0)
    _opt(d, read_name, None)
    mode = _opt(d, lambda x: x.read(8), 1)
    return {'time': t, 'accel': accel, 'loc': loc, 'ctrl_rot': rot, 'flags': flags, 'base': base, 'mode': mode}
