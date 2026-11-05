"""The weapon meter (swords; the axe's determination, engine/axe.py), held by the server and replicated as the weapon's
Determination attribute.

From the game's Blueprints (AssetDump bytecode, 2026-10-16):
* BP_EB_Weapon.SpecialAttackReady: QReady = (weapon_meter_bpc.GetCurrentMeter == GetMaxMeter). The client only asks to activate
  the special (ServerTryActivateAbility) when the meter is full.
* weapon_meter_bpc.GetCurrentMeter / GetMaxMeter: GetFloatAttribute(GetOwner(), ArchonDamageAttributeSet.Determination /
  DeterminationMax). The component sits on BP_EB_Weapon, and the weapon actor has its own ASC and ArchonDamageAttributeSet
  (channel of eblade_beginner_00_bp_C in the join world), so the meter is that set's Determination.
* BP_EB_Weapon.HandleMeterGain: the gain per landed hit by the attack's damage-row tags (normal / while the special is active).
* weapon_meter_bpc.Auth_IncreaseMeter: gain = amount * WeaponChargeMultiplier (+ WeaponChargePerHitBonus * max when flagged), applied
  as determination_ge with SetByCaller DeterminationGain; clamped to the max.

INFERRED (native defaults the pak data does not hold): DeterminationMax = 100 (the sword row's EBlade_ChargeMax),
WeaponChargeMultiplier = 1, WeaponChargePerHitBonus = 0. Not modelled yet: meter decay (Auth_DecayMeter), sword charges (pips).
"""

TAG_GAINS = {                          # HandleMeterGain: tag -> (gain, gain while the special is active)
    'Combat.Sword.TokenGranter': (6.0, 9.0),
    'Combat.Sword.TokenGranterTwo': (12.0, 18.0),
    'Combat.Sword.XXYDamage': (12.0, 18.0),
    'Combat.Sword.SwordchargeDamageMini': (2.0, 4.0),
    'Combat.Sword.XHoldDamage': (9.0, 12.0),
    'Combat.Sword.YHoldDamage': (9.0, 12.0),
    'Combat.Sword.SwordchargeDelayedAoE': (14.0, 28.0),
}
METER_BASE = 'BP_EB_Weapon_C'          # every sword skin derives from it (data/2.1.1/class_layouts.json)
AXE_BASE = 'BP_Axe_Weapon_C'
# weapon base class -> DeterminationMax: the sword's EBlade_ChargeMax (inferred, see above); the axe's ga_determination_ab
# and BP_Axe_Weapon default 12 (atlas axe_server.md)
METER_MAX = {METER_BASE: 100.0, AXE_BASE: 12.0}
SET_CLS = 'ArchonDamageAttributeSet'


def meter_base(cls_name):
    """The METER_MAX base class among cls_name's ancestors, or None."""
    from ..loadout import class_layout
    cur = cls_name
    while cur:
        if cur in METER_MAX:
            return cur
        info = class_layout(cur)
        cur = info and info['parent']
    return None


def is_meter_weapon(cls_name):
    return meter_base(cls_name) is not None


def increase_params(raw):
    """weapon_meter_bpc.IncreaseMeter raw params -> (AmountToIncrease, ApplyAsScaledValue), the parameters before the
    GameplayCueParameters struct (not decoded). A send bit precedes the double. Checked against 33 live captures
    (2026-10-28, 81 bits: 3.0, true). None when it does not fit."""
    import struct
    from ..bitio import BitReader
    bits, nbits = raw
    try:
        r = BitReader(bits.to_bytes((nbits + 7) // 8, 'little'), nbits)
        amount = struct.unpack('<d', r.read(64).to_bytes(8, 'little'))[0] if r.bit() else 0.0
        return amount, bool(r.bit())
    except Exception:
        return None


def gain_for(tags, special_active=False):
    """HandleMeterGain: the first matching tag in the Blueprint's order decides the amount (0 if none)."""
    for tag, (normal, active) in TAG_GAINS.items():
        if tag in (tags or ()):
            return active if special_active else normal
    return 0.0


class WeaponMeter:
    def __init__(self, session, rows, log):
        from .attributes import find_block
        self.s, self.rows, self.log = session, rows, log
        self.ch = next((e.ch for e in session.entries for b in e.parsed.blocks if b.actor and is_meter_weapon(b.cls_name)), None)
        if self.ch is None:
            raise LookupError('no meter weapon in the sent world')
        self.weapon = session.chan_cls.get(self.ch)
        self.header = find_block(session, self.ch, SET_CLS)
        if self.header is None:
            raise LookupError('weapon %s has no %s' % (self.weapon, SET_CLS))
        self.base = meter_base(self.weapon)
        self.max = METER_MAX[self.base]
        import os
        self.value = min(self.max, float(os.environ.get('MPGS_METER_START', '0')))   # test aid: start part-full
        self.multiplier, self.per_hit_bonus = 1.0, 0.0      # inferred native defaults (see the module doc)
        self.special_active = False

    def _send(self, values):
        from .attributes import property_update
        self.s.entries.insert(self.s.pos, property_update(self.s, self.ch, self.header, values))

    def start_entry(self):
        """The client's initial view, max and current (the join world never set them), as an entry to send."""
        from .attributes import property_update
        self.log('meter on %s (ch%d set guid %s): Determination %.0f / %.0f' % (self.weapon, self.ch, self.header.guid, self.value, self.max))
        return property_update(self.s, self.ch, self.header, {'Determination': self.value, 'DeterminationMax': self.max})

    def update_entry(self):
        """The current value as an entry to send (the special's drain batches its updates)."""
        from .attributes import property_update
        return property_update(self.s, self.ch, self.header, {'Determination': self.value})

    def on_increase(self, amount, scaled):
        """weapon_meter_bpc.IncreaseMeter -> Auth_IncreaseMeter (Blueprint): a scaled amount is multiplied by the weapon's
        WeaponChargeMultiplier, plus WeaponChargePerHitBonus x max for hits tagged Combat.Weapon.WeaponImpact (the client's
        cue parameters carry no tags, so no bonus). The Crowbear exotic asks for 3.0 per hit (live 2026-10-28)."""
        if amount <= 0 or self.value >= self.max:
            return 0.0
        old = self.value
        self.value = min(self.max, self.value + (amount * self.multiplier if scaled else amount))
        self._send({'Determination': self.value})
        self.log('meter IncreaseMeter +%.1f -> %.0f / %.0f%s' % (self.value - old, self.value, self.max,
                                                                 '  FULL: special ready' if self.full else ''))
        return self.value - old

    def on_hit(self, attack):
        tags = (self.rows.get(attack) or {}).get('tags') or []
        amount = gain_for(tags, self.special_active)
        if amount <= 0 or self.full:
            return 0.0
        gain = amount * self.multiplier
        old = self.value
        self.value = min(self.max, self.value + gain)
        if self.value != old:
            self._send({'Determination': self.value})
        self.log('meter %s +%.0f -> %.0f / %.0f%s' % (attack, gain, self.value, self.max, '  FULL: special ready' if self.value >= self.max else ''))
        return self.value - old

    @property
    def full(self):
        return self.value >= self.max

    def spend_all(self, why):
        """The special consumes the meter (INFERRED: the server-side special empties it; its graph is not traced yet)."""
        self.value = 0.0
        self._send({'Determination': self.value})
        self.log('meter emptied (%s)' % why)
