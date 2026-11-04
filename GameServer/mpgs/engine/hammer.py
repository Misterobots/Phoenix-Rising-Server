"""War hammer server state (bp_ihammer_weapon_C). Atlas: topics/hammer_server.md.

Ammo lives on the hammer's ammo_bpc_C SCS component 'AmmoComponent' (MaxAmmoCount 7, StartWithFullAmmo); island_encounter
replicates it. This module applies the server-side ammo changes the 2.1.1 Blueprints make:

* CharCombatComp.ServerStartInputAction(FInputActionData) runs the input action's ExecuteAction with authority:
  - prime_hammer_input_action_bp_C (conditions has_ammo, stamina): PrimeAmmoShell() sets HasPrimedAmmoShell,
    AdjustAmmo(-1), ConsumeStamina(StaminaCost 10).
  - reload_hammer_shell_input_action_bp_C (conditions not_full_ammo, stamina): AdjustAmmo(+1), ConsumeStamina(5).
* ih_blast_base_ab (every ih_blast_<element>_ab_C, the power row's RefSpecialAbilities1): AdjustAmmo(-1) on the server.
* ih_primed_ammo_shell_ab (power row RefAbilities): on Event.OnDamageGiven with Combat.Weapon.WeaponImpact while primed,
  RemovePrimedAmmoShell(). The primed damage and stagger bonus (PrimedShellDamageFlatBonus, PrimedShellExplicitStaggerBonus,
  ih_primed_ammo_shell_blast_ge) is not applied yet: its damage formula is still to be decoded (hammer_server.md).
* Montage notifies (engine.combat_moves): reload_ammo_hammer_bpn fills the clip (active reload), and the end of
  Notify_HammerPrimedShotWindow removes a primed shell the strike did not use.
* Concussive salvo (ih_special_default_bpc, the special part, through engine.combat_moves): the charge level from
  ServerProcessCharge, then the shoot montages' 'Shoot' part event spends that many shells.
"""

PRIME_ACTION = 'prime_hammer_input_action_bp_C'
RELOAD_SHELL_ACTION = 'reload_hammer_shell_input_action_bp_C'
STAMINA_COST = {PRIME_ACTION: 10.0, RELOAD_SHELL_ACTION: 5.0}      # class defaults (atlas assets/*_input_action_bp.txt)
AMMO_COMPONENT = 'AmmoComponent'


def _hammer_channel(session):
    from ..island_encounter import _ammo_weapons
    return next((ch for ch, w in _ammo_weapons(session).items() if w[1] == AMMO_COMPONENT), None)


def _action_class(params):
    """FInputActionData.Action is a class reference: the decoder gives its path or (guid, path); match by class name."""
    action = (params or {}).get('InputAction.Action')
    text = repr(action)
    return next((name for name in (PRIME_ACTION, RELOAD_SHELL_ACTION) if name[:-2] in text), None)


def is_blast_ability(cls):
    """Classes derived from ih_blast_base_ab_C: ih_blast_{blaze,frost,radiant,raw,shock,terra,umbral}_ab_C."""
    return bool(cls) and cls.startswith('ih_blast_') and cls.endswith('_ab_C')


def on_input_action(session, enc, params, now):
    """CharCombatComp.ServerStartInputAction. -> the action applied ('prime' / 'reload_shell'), or None."""
    from ..island_encounter import adjust_ammo, ammo_max
    action = _action_class(params)
    ch = _hammer_channel(session)
    if action is None or ch is None:
        print('HAMMER input action ignored: %s (hammer channel %s)' % ((params or {}).get('InputAction.Action'), ch), flush=True)
        return None
    ammo = enc.setdefault('ammo', {})
    current = ammo.setdefault(ch, ammo_max(session, ch))
    if action == PRIME_ACTION and current <= 0:              # has_ammo_input_action_condition
        print('HAMMER prime refused: no ammo', flush=True)
        return None
    if action == RELOAD_SHELL_ACTION and current >= ammo_max(session, ch):   # not_full_ammo_input_action_condition
        print('HAMMER inline reload refused: clip full', flush=True)
        return None
    stamina = enc.get('stamina')
    if stamina is not None and not stamina.spend(STAMINA_COST[action], now):
        print('HAMMER %s refused: out of stamina' % action, flush=True)
        return None
    if action == PRIME_ACTION:
        enc['hammer_primed'] = True
        left = adjust_ammo(session, ch, -1)
        print('HAMMER shell primed, ammo %d' % left, flush=True)
        return 'prime'
    left = adjust_ammo(session, ch, 1)
    print('HAMMER inline reload, ammo %d' % left, flush=True)
    return 'reload_shell'


def on_blast(session, cls):
    """ih_blast_base_ab activation on the server: AdjustAmmo(-1)."""
    from ..island_encounter import adjust_ammo
    ch = _hammer_channel(session)
    if ch is None or not is_blast_ability(cls):
        return None
    left = adjust_ammo(session, ch, -1)
    print('HAMMER blast %s, ammo %d' % (cls, left), flush=True)
    return left


def on_damage_given(session, enc):
    """ih_primed_ammo_shell_ab: a hit carrying Combat.Weapon.WeaponImpact while primed removes the primed shell. The tag is
    the hammer's WeaponMeleeImpactTags, which its melee damage states put in the instigator tags, so a melee hit the client
    reports with DamageComponent.ServerTryDoDamage while a hammer is out qualifies. -> True when a shell was spent."""
    if not enc.get('hammer_primed') or _hammer_channel(session) is None:
        return False
    enc['hammer_primed'] = False
    print('HAMMER primed shell spent on a weapon impact', flush=True)
    return True


def on_reload_notify(session, enc, settings):
    """reload_ammo_hammer_bpn (server, in the active reload montages): AuthReloadWeaponWithAmmoScale(NormalAmmoScale), a full
    clip. FastReloadSuccess / FastReloadFailure only pick the effects. -> the new ammo count, or None without a hammer."""
    from ..island_encounter import adjust_ammo, ammo_max
    ch = _hammer_channel(session)
    if ch is None:
        return None
    left = adjust_ammo(session, ch, ammo_max(session, ch))
    kind = 'fast' if settings.get('FastReloadSuccess') else 'failed fast' if settings.get('FastReloadFailure') else 'normal'
    print('HAMMER %s reload, ammo %d' % (kind, left), flush=True)
    return left


def on_primed_window_end(session, enc):
    """Notify_HammerPrimedShotWindow.Received_NotifyEnd: IsAmmoShellPrimed -> RemovePrimedAmmoShell. -> True when removed."""
    if not enc.get('hammer_primed'):
        return False
    enc['hammer_primed'] = False
    print('HAMMER primed shell unused, removed at the end of the strike window', flush=True)
    return True


def on_charge_started(session, enc, now):
    """ih_special_default_bpc.WeaponPartChargeStarted: WeaponCharged = false."""
    enc['hammer_charge'] = None


def on_charge_level(session, enc, now, previous, level):
    """ih_special_default_bpc.WeaponPartChargeLevelChanged: WeaponCharged = true, WeaponChargeLevel = the new level,
    CommitAmmo (only tells the UI how many shells stay). ihammer_all_ammo_commited_ec compares this level with the ammo."""
    enc['hammer_charge'] = level
    print('HAMMER salvo charge level %d -> %d' % (previous, level), flush=True)


def on_attack_state_left(session, enc, now):
    """ih_special_default_bpc.OnDefaultStateChanged with PreviousPlayerState Attack (bound at WeaponPartChargeStarted)
    -> ResetAndReleaseAmmo: WeaponCharged = false, level 0. DeactivateWeaponPartGameplay does not reset it (it only stops
    the aiming and the charge particles), so the level carries from the charge move into the shoot move."""
    if enc.get('hammer_charge') is not None:
        print('HAMMER salvo charge released (left the attack state)', flush=True)
    enc['hammer_charge'] = None


BLAST_ROW = 'IH.Blast.LVL%d'
BASE_BLAST_RADIUS, BLAST_RADIUS_PER_LEVEL = 200.0, 50.0      # ih_special_default_bpc defaults
BLAST_WEDGE = (-30.0, 30.0)                                   # SpawnAoe: WedgeStartAngle / WedgeEndAngle


def on_salvo_shoot(session, enc, now):
    """ih_special_default_bpc.ActivateGameplayEvent('Shoot') with authority: SpawnAoe(), then ConsumeAmmo(-level) when
    charged, else ConsumeAmmo(-1); ConsumeAmmo only adjusts when the clip is not empty.
    SpawnAoe spawns ih_charge_blast_bp (aoe_base_bp) at the hammer's ihammer_blast_socket with OverrideDamageRow
    'IH.Blast.LVL<level or 1>', CollisionRadius BaseBlastRadius 200 + (level-1) x BlastRadiusPerLevel 50, a -30..30 degree
    wedge; its damage scale (GetCustomDamageScale) is AmmoDamageScale, the committed shells' average ammo scale.
    Stand-ins: the blast origin is the player's location (the socket offset is not read), its facing is the control
    yaw (the shoot montages turn the character to the camera), and every shell has NormalAmmoScale 1 (per-shell
    scales are not tracked yet). The damage table has no IH.Blast.LVL7, so a 7-shell salvo deals nothing, as the game's
    row lookup does. -> the ammo left, or None without a hammer."""
    from ..island_encounter import adjust_ammo, apply_aoe
    ch = _hammer_channel(session)
    if ch is None:
        return None
    level = enc.get('hammer_charge')
    shells = level if level else 1
    radius = BASE_BLAST_RADIUS + (shells - 1) * BLAST_RADIUS_PER_LEVEL
    pawn = getattr(session, 'pawn_state', None)
    if pawn is not None:
        apply_aoe(session, BLAST_ROW % shells, pawn['loc'], pawn['ctrl_rot'][1], radius, BLAST_WEDGE, 1.0, 'hammer blast')
    ammo = enc.setdefault('ammo', {})
    left = adjust_ammo(session, ch, -shells) if ammo.get(ch, 1) > 0 else ammo.get(ch, 0)
    print('HAMMER blast %s radius %.0f, %d shell(s), ammo %d' % (BLAST_ROW % shells, radius, shells, left), flush=True)
    return left
