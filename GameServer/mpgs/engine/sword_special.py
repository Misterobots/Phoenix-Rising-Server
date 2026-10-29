"""The sword special (Overdrive; Feast on The Hunger), which runs on the server.

What the game does (atlas topics/sword_server.md, Blueprint code read 2026-10-28):
* The special button with a full meter sends CharCombatComp.ServerStartCombo with InComboInputKey.PrimaryKey 8 (live
  2026-10-28); the client plays the activate montage and its own aura. The special part's ActivateWeaponPartGameplay then
  does, on authority only: special_active_ge (Infinite, Combat.SpecialActive) on the pawn's ASC and the meter decay
  (weapon_meter_bpc.StartMeterDecay(Decay Rate): 5 per second, 4 with talent weapon.talent.d24.sp05; MeterCompoundingDecay
  makes the rate grow by MeterCompoundingDecayRate 0.2 each second). There is no up-front cost.
* When the meter reaches 0, the part's MeterEmpty removes special_active_ge.
* Pressing the special button again while it is active is the dash (Aether Dash / Umbral Flight). Its cooldown effect only
  applies when the part's Cooldown > 0 (parry part 2 s); the default and The Hunger's parts have none.
Sword charges (the pips; BP_EB_Weapon.CurrentSwordcharges, max 6, and SwordchargeFillPercent, server-only):
* gained on a landed hit whose damage row has Combat.Sword.TierAdvancer: +1 with TokenGranter, +2 with TokenGranterTwo, at
  most once per 0.25 s; and during the special: GenerateSwordcharge(1) at the start, then +5% every 0.2 s.
* spent by moves (call_weapon_function_bpn -> CallWeaponSpecificFunction), see SPENDS. The client only allows those moves
  with enough charges (eb_combo_swordcharges_available_ec), so the server takes them when the move starts.
The special part's buffs (weapon_part_catalog Buffs) are buff actors sent at hunt start (engine/buffs.py); the client's copies
switch on with Combat.SpecialActive. What The Hunger's two Feast buffs do on the server is done here:
* ce_crowbear_eb_exotic_drain_lifesteal: while Combat.SpecialActive, ReceiveTick runs Delay(0.2) and then true damage
  FMin(CurrentHealth - 1, DamagePerSecond 7) while health > 1; with weapon.talent.d24.sp02 only while health > 15% of max.
  So 7 every 0.2 s, despite the property's name. Its ApplyDamageBuff (DamageBonus 0.25) has no caller in 2.1.1.
* crowbear_eb_exotic_lifesteal_buff: on Event.OnDamageGiven while Combat.SpecialActive, heals LifestealPercent 0.12 of the
  damage dealt after mitigation. Stand-in: meter_gaining_rules.CanProcOnDamageEffects is taken as true for behemoth hits.
Not yet: the multicasts (OnSpecialActivated, Multi_DisableSuperModeEffects).
"""
SPECIAL_KEY = 8
SPECIAL_GE = 'special_active_ge_C'
DECAY_RATE = 5.0
DECAY_RATE_SP05 = 4.0
COMPOUNDING = 0.2                # MeterCompoundingDecayRate (BP_EB_Weapon's weapon_meter_bpc)
SEND_EVERY = 0.25                # seconds between Determination updates while draining
MAX_CHARGES = 6
FILL_STEP, FILL_EVERY = 0.05, 0.2
GAIN_COOLDOWN = 0.25
DRAIN_BUFF, DRAIN, DRAIN_EVERY, DRAIN_FLOOR_SP02 = 'ce_crowbear_eb_exotic_drain_lifesteal_C', 7.0, 0.2, 0.15
LIFESTEAL_BUFF, LIFESTEAL = 'crowbear_eb_exotic_lifesteal_buff_C', 0.12
# (move set, ComboAnimState index) -> charges the move spends (atlas sword_server.md; states from the animstateset assets)
SPENDS = {
    ('eblade_base_animstateset', 17): 1,          # X6
    ('eblade_base_animstateset', 8): 1,           # RB_dash: The Hunger's dash (h_mr_eb_melee_RB_dash_crowbear_fwd_01_mot)
    ('eblade_base_animstateset', 21): 3,          # Y5
    ('eblade_base_animstateset', 25): 3,          # Y5_02
    ('eblade_base_animstateset', 29): 3,          # Y5_02
    ('eblade_rework_base_animstateset', 16): 3,   # X1_hold
    ('eblade_rework_base_animstateset', 11): 3,   # Y4_aoe
    ('eblade_rework_base_animstateset', 25): 3,   # Y5
    ('eblade_rework_base_animstateset', 1): 3,    # Y1_hold (1 with Combat.Sword.Passive.HoldReduction, not modelled)
}


def _log(msg):
    print('SWORD ' + msg, flush=True)


def _charges(enc):
    return enc.setdefault('swordcharges', {'n': 0, 'fill': 0.0, 'last_gain': -1e9})


def _charges_entry(session, enc):
    """BP_EB_Weapon.CurrentSwordcharges / SwordchargeFillPercent on the sword actor."""
    from ..island_arrival import _block, _entry
    meter, c = enc['meter'], _charges(enc)
    _log('charges %d (+%.0f%%)' % (c['n'], c['fill'] * 100))
    return _entry(session.world, meter.ch, meter.weapon, [_block(meter.weapon, {'CurrentSwordcharges': c['n'],
                                                                                'SwordchargeFillPercent': c['fill']})])


def _add(session, enc, n):
    c = _charges(enc)
    if c['n'] >= MAX_CHARGES:
        return []
    c['n'] = min(MAX_CHARGES, c['n'] + n)
    if c['n'] >= MAX_CHARGES:
        c['fill'] = 0.0
    return [_charges_entry(session, enc)]


def on_hit(session, enc, tags, now):
    """A landed hit with its damage row's tags. -> reliable entries."""
    if enc.get('meter') is None or 'Combat.Sword.TierAdvancer' not in (tags or ()):
        return []
    c = _charges(enc)
    gain = 2 if 'Combat.Sword.TokenGranterTwo' in tags else 1 if 'Combat.Sword.TokenGranter' in tags else 0
    if not gain or now - c['last_gain'] < GAIN_COOLDOWN:
        return []
    c['last_gain'] = now
    return _add(session, enc, gain)


def _active(enc, buff):
    return (enc.get('sword_special') or {}).get('active') and buff in (enc.get('buffs') or {})


def lifesteal(session, enc, damage):
    """A landed player hit of `damage`. -> entries (the heal) while Feast is on."""
    if not _active(enc, LIFESTEAL_BUFF) or damage <= 0:
        return []
    from ..island_encounter import change_player_health
    return change_player_health(session, enc, lambda h, top: min(top, h + damage * LIFESTEAL), 'Feast lifesteal')


def _drain(session, enc, now):
    sp = enc['sword_special']
    if not _active(enc, DRAIN_BUFF):
        return []
    from ..island_encounter import change_player_health
    sp02 = 'weapon.talent.d24.sp02' in (getattr(session, 'talent_tags', None) or set())

    def change(h, top):
        return h if h <= (top * DRAIN_FLOOR_SP02 if sp02 else 1.0) else h - min(h - 1.0, DRAIN)
    out = []
    while now - sp['drained'] >= DRAIN_EVERY - 1e-9:
        sp['drained'] += DRAIN_EVERY
        out = change_player_health(session, enc, change, 'Feast drain')     # absolute health: the last entry is enough
    return out


def on_move(session, enc, dest_state):
    """CharCombatComp.ServerStartNextMove's ComboInputBuffer.DestState (soft path '...animstateset:ComboAnimState_N').
    -> reliable entries."""
    if enc.get('meter') is None or not isinstance(dest_state, str) or ':ComboAnimState_' not in dest_state:
        return []
    path, state = dest_state.rsplit(':ComboAnimState_', 1)
    cost = SPENDS.get((path.rsplit('.', 1)[-1], int(state))) if state.isdigit() else None
    c = _charges(enc)
    if not cost or c['n'] < cost:
        return []
    c['n'] -= cost
    _log('move %s spends %d' % (dest_state.rsplit('.', 1)[-1], cost))
    return [_charges_entry(session, enc)]


def on_combo(session, enc, key, now):
    """CharCombatComp.ServerStartCombo. -> reliable entries to send."""
    meter = enc.get('meter')
    if key != SPECIAL_KEY or meter is None:
        return []
    sp = enc.setdefault('sword_special', {'active': False})
    if sp['active']:
        _log('dash during the special (no cooldown on this part)')
        return []
    if not meter.full:
        _log('special refused: meter %.0f / %.0f' % (meter.value, meter.max))
        return []
    from . import effects
    tags = getattr(session, 'talent_tags', None) or set()
    sp.update(active=True, start=now, last=now, sent=now, filled=now, drained=now,
              rate=DECAY_RATE_SP05 if 'weapon.talent.d24.sp05' in tags else DECAY_RATE)
    meter.special_active = True
    _log('special on: meter drains from %.0f at %.1f/s' % (meter.value, sp['rate']))
    out = _add(session, enc, 1)                         # GenerateSwordcharge(1)
    try:
        return effects.for_session(session, 'pawn').apply(SPECIAL_GE, None, now) + out
    except LookupError as exc:
        _log('%s not applied (%s)' % (SPECIAL_GE, exc))
        return out


def step(session, enc, now):
    """While the special is active: drain the meter; at 0 end the special. -> reliable entries to send."""
    sp = enc.get('sword_special')
    meter = enc.get('meter')
    if not sp or not sp['active'] or meter is None:
        return []
    dt, sp['last'] = now - sp['last'], now
    rate = sp['rate'] + COMPOUNDING * (now - sp['start'])
    meter.value = max(0.0, meter.value - rate * dt)
    c, changed = _charges(enc), False
    while now - sp['filled'] >= FILL_EVERY - 1e-9:             # AddPercentPipFill(0.05) every 0.2 s
        sp['filled'] += FILL_EVERY
        if c['n'] < MAX_CHARGES:
            c['fill'] += FILL_STEP
            if c['fill'] >= 1.0 - 1e-9:
                c['fill'] = 0.0
                c['n'] += 1
            changed = True
    out = ([_charges_entry(session, enc)] if changed else []) + _drain(session, enc, now)
    if meter.value <= 0.0 or now - sp['sent'] >= SEND_EVERY:
        sp['sent'] = now
        out.append(meter.update_entry())
    if meter.value <= 0.0:
        from . import effects
        sp['active'] = False
        meter.special_active = False
        _log('special off: meter empty after %.1f s' % (now - sp['start']))
        out += effects.for_session(session, 'pawn').remove(SPECIAL_GE)
    return out
