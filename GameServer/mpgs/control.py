"""Control-channel (channel 0) messages. Types per UE5.1 NetworkCommon + Dauntless GameSpecific."""
import random
import re

from .bitio import BitWriter
from .wire import Bunch, CHNAME_CONTROL

NMT_HELLO, NMT_WELCOME, NMT_UPGRADE, NMT_CHALLENGE, NMT_NETSPEED, NMT_LOGIN, NMT_FAILURE = 0, 1, 2, 3, 4, 5, 6
NMT_JOIN, NMT_GAMESPECIFIC, NMT_ENCRYPTION_ACK, NMT_CLOSEREASON = 9, 20, 21, 23

MAX_PARTIAL_BITS = 7480


def _bunch(w: BitWriter, reliable=True):
    return Bunch(0, w.nbits, w.n, reliable=reliable, chname=CHNAME_CONTROL)


def encryption_ack():
    w = BitWriter()
    w.write(NMT_ENCRYPTION_ACK, 8)
    return [_bunch(w)]


def challenge(text=None):
    w = BitWriter()
    w.write(NMT_CHALLENGE, 8)
    w.write_fstring(text or '%08X' % random.getrandbits(32))
    return [_bunch(w)]


def game_specific(index: int, blob_len: int = 936):
    """Dauntless sends two of these (index 0 and 1) after Challenge: u8 id, FString of `blob_len` zero bytes.
    Wire size 7536 bits => split into two partial reliable bunches (7480 + 56)."""
    w = BitWriter()
    w.write(NMT_GAMESPECIFIC, 8)
    w.write(index, 8)
    w.write(blob_len, 32)
    w.write_bytes(bytes(blob_len))
    return split_partial(w.nbits, w.n)


def welcome(map_path: str, game_mode_path: str, redirect: str = ''):
    w = BitWriter()
    w.write(NMT_WELCOME, 8)
    w.write_fstring(map_path)
    w.write_fstring(game_mode_path)
    w.write_fstring(redirect)
    w.write(0, 32)   # extra trailing int32 present in the captured Dauntless Welcome (108 bytes total)
    return [_bunch(w)]


def split_partial(nbits, data, reliable=True, ch=0, chname=CHNAME_CONTROL):
    """Split an oversize message into partial bunches (bPartialInitial ... bPartialFinal)."""
    if nbits <= MAX_PARTIAL_BITS:
        return [Bunch(ch, nbits, data, reliable=reliable, chname=chname)]
    out, pos = [], 0
    while pos < nbits:
        take = min(MAX_PARTIAL_BITS, nbits - pos)
        out.append(Bunch(ch, take, (data >> pos) & ((1 << take) - 1), reliable=reliable, chname=chname, partial=True,
                         partial_initial=(pos == 0), partial_final=(pos + take >= nbits)))
        pos += take
    return out


# ---- parsing client messages -------------------------------------------------------------------------------------
def message_type(b: Bunch):
    return b.data & 0xFF if b.nbits >= 8 else None


def hello_token(b: Bunch):
    """NMT_Hello: u8 type, u8 IsLittleEndian, u32 NetworkVersion, FString EncryptionToken, ... -> 'token' (before ':nonce')."""
    raw = b.payload()
    ln = int.from_bytes(raw[6:10], 'little')
    return raw[10:10 + ln].rstrip(b'\0').decode('latin-1').split(':', 1)[0]


def _login_fields(b: Bunch):
    """Return parsed NMT_Login identity fields and its opaque auth token."""
    import base64
    import json
    t = b.payload().decode('latin-1')
    name = re.search(r'\?Name=([^?\x00]*)', t).group(1)
    char = re.search(r'\?CharacterId=([^?\x00]*)', t).group(1)
    jwt = re.search(r'\?AuthToken=([^?\x00]*)', t).group(1)
    body = jwt.split('.')[1]
    acct = json.loads(base64.urlsafe_b64decode(body + '=' * (-len(body) % 4)))['sub']
    ids = {'name': name, 'char': char, 'acct': acct,
           'session': (re.search(r'gameSessionId=([^?\x00]*)', t) or [None, None])[1]}
    return ids, jwt


def login_options(b: Bunch):
    """Name / CharacterId / account subject / session from NMT_Login."""
    return _login_fields(b)[0]


def login_options_with_auth(b: Bunch):
    """Return identity fields and the bearer token, separately, for trusted backend calls."""
    return _login_fields(b)
