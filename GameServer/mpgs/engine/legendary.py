"""Legendary weapon parts on the server: the Alluring Moons repeaters' ultra raptor (legendary_weapon_part_spawn_raptor_bpc).

What the game does (atlas pseudo legendary_weapon_part_base_bpc / _spawn_raptor_bpc; AssetDump; read 2026-10-26):
- On equip (BP_HandleEquipForGameplay -> Auth_RefreshLegendaryAbility, server) the part applies weapon_legendary_ability_available_ge,
  which grants Combat.LegendaryAbilityAvailable. The part's anim-state extension (active_weapon_part_DP_umbral_1_animstateset)
  adds the 'Legendary' combo state (h_mr_dp_melee_alluringmoons_atk_fwd_00_mot) on the Legendary Attack input, entered only
  while legendary_ability_available_ec passes (the tag on the player's ASC).
- That montage's activate_weapon_part_gameplay_event_bpns (0.845 s, Weapon_Part_DP_Legendary, EventName 'Activate') calls the
  part's ActivateGameplayEvent -> OnAbilityActivated: ClearRaptorsLegend, then spawn one uraptor_00_bp at the right hand socket
  with the character's rotation (authority only; talents add more - none modelled). CommitAbility removes the available effect
  and starts the CooldownTime timer (CDO 120 s; CooldownModes 3); when it ends Auth_RefreshLegendaryAbility grants it again.

Here: the tag goes to the client as a replicated loose tag on the pawn's ASC (ReplicatedLooseTags) instead of the gameplay
effect (this server does not replicate ActiveGameplayEffects yet) - the client's tag check (HasAnyMatchingGameplayTags) sees
both. The move is detected from the client's ServerStartNextMove into the extension's state.
"""
from ..repl.structs import tag_index

AVAILABLE_TAG = 'Combat.LegendaryAbilityAvailable'
# Weapon talents (legendary_weapon_part_spawn_raptor_bpc, GetTalentStatusAct; talents.py): Act01 sets CooldownTime 100;
# Act02 applies legendary_allure_raptordamage_ge (GlobalTargetPostMigationMultiplier Additive, magnitude 0.2, for the ultra
# raptor's lifespan) to the ultra raptor - modelled as x1.2 on its hits (stand-in for the attribute's place in the damage
# formula); Act03 / Act04 also spawn one random araptor / sraptor / praptor near the right hand. Act05 (something over every
# af_raptor_archetype_bp actor) is not modelled.
ACT01_COOLDOWN = 100.0
ACT02_DAMAGE = 0.2
PARTS = {
    'legendary_weapon_part_spawn_raptor_bpc_C': {
        'state': 'active_weapon_part_DP_umbral_1_animstateset',   # the extension holding the Legendary combo state
        'activate_at': 0.8449,                                    # the montage's Activate notify
        'cooldown': 120.0,                                        # legendary_weapon_part_base_bpc CDO CooldownTime
    },
}


def _log(msg):
    print('LEGENDARY ' + msg, flush=True)


def _tags_entry(session, on):
    from .attributes import find_block, property_update
    pawn = next((e for e in session.entries if e.open and e.cls == 'BP_PlayerCharacter_C'), None)
    header = find_block(session, pawn.ch, 'ArchonAbilitySystemComponent') if pawn is not None else None
    idx = tag_index(AVAILABLE_TAG)
    if header is None or idx is None:
        _log('tag not sent (%s)' % ('no pawn ASC' if header is None else 'no gameplay tag table'))
        return None
    return property_update(session, pawn.ch, header, {'ReplicatedLooseTags': [idx] if on else []})


def _refreshed(session, cls):
    """Auth_RefreshLegendaryAbility's Multicast_OnLegendaryAbilityRefreshed on the part: the client clears the cooldown timer its
    own Activate notify started (CommitAbility runs on both sides; the HUD's legendary icon reads that timer) and refreshes the
    part's VFX/audio."""
    from ..loadout import register_atlas_class_full
    from .attributes import find_block
    hdr = next((b for e in session.entries for b in e.parsed.blocks if b.cls_name == cls and not b.actor), None)
    ch = next((e.ch for e in session.entries for b in e.parsed.blocks if b is hdr), None)
    if hdr is None or not register_atlas_class_full(session.world.schema, cls):
        _log('Multicast_OnLegendaryAbilityRefreshed not sent (%s)' % ('no part in the world' if hdr is None else 'no layout'))
        return
    session._queue_rpc(ch, find_block(session, ch, cls) or hdr, 'Multicast_OnLegendaryAbilityRefreshed', {})
    _log('Multicast_OnLegendaryAbilityRefreshed on ch%d' % ch)


def setup(session, enc, part_classes):
    """At hunt start: the equipped legendary part (if this server runs it) becomes available. -> entries."""
    cls = next((c for c in part_classes if c in PARTS), None)
    if cls is None:
        return []
    enc['legendary'] = {'cls': cls, 'ready': True, 'spawn_at': None, 'ready_at': None, **PARTS[cls]}
    if 'weapon.talent.d24.act01' in (getattr(session, 'talent_tags', None) or set()):
        enc['legendary']['cooldown'] = ACT01_COOLDOWN
    e = _tags_entry(session, True)
    _log('%s available (tag %s)' % (cls, AVAILABLE_TAG))
    return [e] if e is not None else []


def on_move(session, enc, params, now):
    """CharCombatComp.ServerStartNextMove: the Legendary state starting -> schedule the part's Activate."""
    leg = (enc or {}).get('legendary')
    if not leg or not isinstance(params, dict):
        return
    dest = params.get('ComboInputBuffer.DestState')
    path = dest[1] if isinstance(dest, tuple) else str(dest or '')
    if leg['state'] not in path:
        return
    if not leg['ready']:
        _log('Legendary move while on cooldown (%.0f s left); nothing spawned' % max(0.0, leg['ready_at'] - now))
        return
    leg['ready'], leg['spawn_at'], leg['ready_at'] = False, now + leg['activate_at'], now + leg['cooldown']
    e = _tags_entry(session, False)
    if e is not None:
        session.entries.append(e)
    _log('Legendary move: Activate at +%.2f s, cooldown %.0f s' % (leg['activate_at'], leg['cooldown']))


def step(session, enc, now):
    """-> reliable entries: the ultra raptor when the Activate notify is due, the tag again when the cooldown ends."""
    leg = enc.get('legendary')
    if not leg:
        return []
    out = []
    if leg['spawn_at'] is not None and now >= leg['spawn_at']:
        leg['spawn_at'] = None
        from . import allies
        from ..creatures import raptors
        tags = getattr(session, 'talent_tags', None) or set()
        classes = ['uraptor_00_bp_C']
        if tags & {'weapon.talent.d24.act03', 'weapon.talent.d24.act04'}:
            classes.append(enc.setdefault('ally_rng', __import__('random').Random(enc['guid'] + 7)).choice(raptors.PACK))
        out += allies.spawn_raptors(session, enc, now, group='legend', classes=classes,
                                    lifespan=raptors.ULTRA_LIFESPAN, yaw_key='rot')
        if 'weapon.talent.d24.act02' in tags:
            for r in enc.get('allies') or ():
                if r.group == 'legend' and r.cls == 'uraptor_00_bp_C':
                    r.damage_mult = 1.0 + ACT02_DAMAGE
    if not leg['ready'] and leg['ready_at'] is not None and now >= leg['ready_at']:
        leg['ready'], leg['ready_at'] = True, None
        e = _tags_entry(session, True)
        if e is not None:
            out.append(e)
        _refreshed(session, leg['cls'])
        _log('available again')
    return out
