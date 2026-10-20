"""The sword's weapon meter, held by the server and replicated as the weapon's Determination attribute.

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
METER_WEAPONS = {'eblade_beginner_00_bp_C': {'max': 100.0}}
SET_CLS = 'ArchonDamageAttributeSet'


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
        self.ch = next((e.ch for e in session.entries for b in e.parsed.blocks if b.actor and b.cls_name in METER_WEAPONS), None)
        if self.ch is None:
            raise LookupError('no meter weapon in the sent world')
        self.weapon = session.chan_cls.get(self.ch)
        self.header = find_block(session, self.ch, SET_CLS)
        if self.header is None:
            raise LookupError('weapon %s has no %s' % (self.weapon, SET_CLS))
        self.max = METER_WEAPONS[self.weapon]['max']
        import os
        self.value = min(self.max, float(os.environ.get('MPGS_METER_START', '0')))   # test aid: start part-full
        self.multiplier, self.per_hit_bonus = 1.0, 0.0      # inferred native defaults (see the module doc)
        self.special_active = False

    def _send(self, values):
        from .attributes import property_update
        self.s.entries.insert(self.s.pos, property_update(self.s, self.ch, self.header, values))

    def start(self):
        """Initialise the client's view: max and current (the join world never set them)."""
        self._send({'Determination': self.value, 'DeterminationMax': self.max})
        self.log('meter on %s (ch%d set guid %s): Determination %.0f / %.0f' % (self.weapon, self.ch, self.header.guid, self.value, self.max))

    def on_hit(self, attack):
        tags = (self.rows.get(attack) or {}).get('tags') or []
        amount = gain_for(tags, self.special_active)
        if amount <= 0:
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
