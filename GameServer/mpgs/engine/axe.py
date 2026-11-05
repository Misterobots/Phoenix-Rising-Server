"""War axe server state (BP_Axe_Weapon_C). Atlas: topics/axe_server.md.

* Charge levels: CharCombatComp.ServerProcessCharge (engine.combat_moves.on_charge) is OnChargeLevelChanged(New, Previous).
  With authority BP_Axe_Weapon sets Current Charge Level (rep 21) = New and adds New - Previous to TotalChargeLevel
  (rep 25), except when New is 4. Notify_GAClearChargeLevel (ResetCurrentChargeLevel) zeroes both and
  DeterminationOverride.
* Determination (ga_determination_ab, ServerOnly, the power row's RefAbilities): the meter is the weapon ASC's
  Determination (engine.weapon_meter, max 12). On each dealt hit:
  - with Combat.Determination.ReadyToLevel on: level up (next level up to Axe_MaxDeterminationLevel 3), empty the meter
    (scale_determination_ge x 0), remove the ready effect, then ignore hits for 1 s (Delay 1.0)
  - otherwise, unless the hit's row has Combat.Axe.AttackDoesntGrantMeter: + DeterminationOnHitPerChargeLevel
    [Max(DeterminationOverride, TotalChargeLevel)] x Axe_ChargePerStrikeScale (1 in every axe row) x
    WeaponChargeMultiplier (1, inferred as for the sword); at the max apply determination_ready_to_level_ge.
Not built (atlas gaps): the level's ga_determination_meter_0N_buff goes to the weapon's buffable component, which the
atlas has no name for (ArchonWeapon lists none), so the level is tracked here but not shown; the level's damage bonus
(CustomModifyDamageEventData); MoveSkillCost; special and throw-hit gains; the throw handler buffs.
"""
GAINS = [0.0, 3.0, 6.0, 9.0, 13.0, 17.0, 21.0]       # ga_determination_ab DeterminationOnHitPerChargeLevel
MAX_LEVEL = 3                                        # Axe_MaxDeterminationLevel in every axe power row
READY_GE = 'determination_ready_to_level_ge_C'
NO_METER_TAG = 'Combat.Axe.AttackDoesntGrantMeter'
LEVEL_UP_IGNORE = 1.0


def _log(msg):
    print('AXE ' + msg, flush=True)


def _meter(enc):
    from .weapon_meter import AXE_BASE
    m = enc.get('meter')
    return m if m is not None and m.base == AXE_BASE else None


def _state(enc):
    return enc.setdefault('axe', {'charge': 0, 'total': 0, 'override': 0, 'level': 0, 'ready': False, 'ignore_until': -1e9})


def _charge_entry(session, enc):
    from ..island_arrival import _block, _entry
    m, a = _meter(enc), _state(enc)
    return _entry(session.world, m.ch, m.weapon, [_block(m.weapon, {'Current Charge Level': a['charge'],
                                                                     'TotalChargeLevel': a['total']})])


def on_charge_level(session, enc, previous, level):
    """OnChargeLevelChanged(level, previous) on the server. -> entries."""
    if _meter(enc) is None:
        return []
    a = _state(enc)
    a['charge'] = level
    if level != 4:
        a['total'] += level - previous
    _log('charge level %d (total %d)' % (level, a['total']))
    return [_charge_entry(session, enc)]


def clear_charge(session, enc):
    """Notify_GAClearChargeLevel -> ResetCurrentChargeLevel. -> entries."""
    if _meter(enc) is None:
        return []
    _state(enc).update(charge=0, total=0, override=0)
    _log('charge cleared')
    return [_charge_entry(session, enc)]


def on_hit(session, enc, tags, now):
    """A landed player hit with its damage row's tags. -> entries."""
    from . import effects
    m = _meter(enc)
    if m is None:
        return []
    a = _state(enc)
    if now < a['ignore_until']:
        return []
    if a['ready']:
        a.update(ready=False, ignore_until=now + LEVEL_UP_IGNORE, level=min(MAX_LEVEL, a['level'] + 1))
        m.value = 0.0
        _log('determination level %d (meter emptied; the level buff is not sent, see the module doc)' % a['level'])
        return [m.update_entry()] + effects.for_session(session).remove(READY_GE)
    if NO_METER_TAG in (tags or ()):
        return []
    gain = GAINS[min(len(GAINS) - 1, max(a['override'], a['total']))] * m.multiplier
    if gain <= 0:
        return []
    m.value = min(m.max, m.value + gain)
    _log('determination +%.0f -> %.0f / %.0f' % (gain, m.value, m.max))
    out = [m.update_entry()]
    if m.full:
        a['ready'] = True
        _log('ready to level')
        out += effects.for_session(session).apply(READY_GE, None, now)
    return out
