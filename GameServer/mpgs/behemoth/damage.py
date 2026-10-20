"""Player -> behemoth damage for the server-driven Rock: hit decoding, the damage model and the Rock's health.

What is exact: the attack name (a row of Gameplay/DamageTable, found in the client's ServerTryDoDamage report), the row's BaseDamage and the
exploration power curve (BPGS_ArchonExploration_GameState overrides). What is a stand-in until measured: the player's power
(MPGS_PLAYER_POWER, default = the Rock's power so the multiplier is 1.0), the elemental terms and the post-mitigation modifiers, and which
actor a hit landed on (the report's FHitResult is not decoded; a hit counts when the Rock is within MPGS_HIT_RANGE of the player).
"""
import json
import os
import re

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'world', 'behemoths', 'damage_model.json')
PLAYER_CURVE = 'player_to_behemoth_exploration_dmgcurve'


def load_model(path=DATA):
    with open(path, encoding='utf8') as f:
        return json.load(f)


def eval_curve(keys, x):
    """UCurveFloat with linear interpolation, constant outside the keys. Duplicate times (steps) take the later key."""
    if not keys:
        return 1.0
    if x <= keys[0][0]:
        return keys[0][1]
    for (t0, v0), (t1, v1) in zip(keys, keys[1:]):
        if x < t1:
            return v0 if t1 == t0 else v0 + (v1 - v0) * (x - t0) / (t1 - t0)
    return keys[-1][1]


def bit_bytes(bits, nbits, shift):
    """The LSB-first bit stream starting `shift` bits in, regrouped into bytes (strings sit at arbitrary bit offsets)."""
    n = (nbits - shift) // 8
    v = bits >> shift
    return (v & ((1 << (n * 8)) - 1)).to_bytes(n, 'little') if n > 0 else b''


def find_attack_name(raw, known):
    """DamageTypeName of a ServerTryDoDamage report that the codec left raw: raw = (bits, nbits). Returns the longest known
    DamageTable key present as a NUL-terminated string at any bit offset, else None."""
    bits, nbits = raw
    best = None
    for shift in range(8):
        data = bit_bytes(bits, nbits, shift)
        for m in re.finditer(rb'[A-Za-z][A-Za-z0-9_]*\.[A-Za-z0-9_]+(?=\x00)', data):
            name = m.group().decode()
            if name in known and (best is None or len(name) > len(best)):
                best = name
    return best


def tail_prediction_key(raw):
    """The FPredictionKey that ends a ServerTryDoDamage report (the last RPC parameter), as the codec's PredictionKey value, else None.

    Read from live reports (2026-10-15): the last 19 bits are valid=1, has_base=0, server=0 and a 16-bit counter that rises by one per
    swing (23, 24, 25). A key with a base is 35 bits (valid, has_base, server, current, base).
    """
    bits, nbits = raw

    def at(pos, width):
        return (bits >> pos) & ((1 << width) - 1)

    for width, has_base in ((19, 0), (35, 1)):
        if nbits < width:
            continue
        start = nbits - width
        if at(start, 1) == 1 and at(start + 1, 1) == has_base:
            key = {'server': at(start + 2, 1), 'current': at(start + 3, 16)}
            if has_base:
                key['base'] = at(start + 19, 16)
            return key
    return None


def behemoth_to_player(model, element_damage, rock_power, player_power, scale=1.0):
    """Damage the Rock deals to a player for one attack: total element damage of its row (Gameplay damage row, e.g.
    TerraDamage 175 for FrontTackle) x behemoth_to_player_exploration_dmgcurve(rock power - player power).

    element_damage: the row's {element: value} dict. Not checked against a live hit yet.
    """
    total = sum(float(v) for v in element_damage.values()) if element_damage else 0.0
    if total <= 0:
        return None
    mult = eval_curve(model['curves']['behemoth_to_player_exploration_dmgcurve'], rock_power - player_power)
    return total * mult * scale


class RockHealth:
    """Server-side health of one Rock. Not replicated yet (no attribute sets in the spawn bunch)."""

    def __init__(self, model, variant='normal', env=None):
        env = os.environ if env is None else env
        self.model = model
        info = model['rock'].get(variant, model['rock']['normal'])
        self.power = float(info['power'])
        self.max = float(env.get('MPGS_ROCK_HEALTH', '32000' if variant == 'normal' else '36000'))
        self.current = self.max
        self.player_power = float(env.get('MPGS_PLAYER_POWER', self.power))
        self.scale = float(env.get('MPGS_DAMAGE_SCALE', '1'))
        self.dead = False
        self.hits = 0

    def damage_for(self, attack):
        row = self.model['rows'].get(attack)
        if row is None:
            return None
        mult = eval_curve(self.model['curves'][PLAYER_CURVE], self.player_power - self.power)
        return row.get('BaseDamage', 0) * mult * self.scale

    def apply(self, attack):
        """-> (damage dealt, died now). Unknown rows and hits after death deal nothing."""
        if self.dead:
            return 0.0, False
        dmg = self.damage_for(attack)
        if not dmg:
            return 0.0, False
        dmg = min(dmg, self.current)
        self.current -= dmg
        self.hits += 1
        if self.current <= 0:
            self.dead = True
            return dmg, True
        return dmg, False
