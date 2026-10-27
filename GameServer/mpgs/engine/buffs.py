"""Archon buffs on the player: a buff is its own replicated actor (AArchonBuff) that names the pawn's ArchonBuffable component.

What the game does (atlas ArchonBuff / ArchonBuffableComponent; read 2026-10-27): the server's TryAddBuff spawns the buff actor with
Owner, OwnerAbilitySystemComponent, OwnerBuffableComponent and bActive replicated; the client's OnRep_Active registers it with that
component, which is what the client's FindBuff / HasBuff then see.

Why the repeaters need it: dp_y_ability_spawn_raptor_ab (Q) runs FindBuff(alluring_raptor_manager_buff) on the client before
StartYCooldown and K2_EndAbility; without the buff the client's ability stops there, stays active, and Q never activates again.
Here the buff is sent once at hunt start (stand-in timing: the game adds it on the first Q, server side). Built from the class
layouts, no captured buff to compare against - the most likely piece to need a live fix.
"""
from ..behemoth import spawn

BUFFABLE_COMPONENT = 'ArchonBuffable'          # BP_PlayerCharacter's SCS component (ArchonCharacter.ArchonBuffableComponent)
PACKAGES = {
    'alluring_raptor_manager_buff_C': '/Game/_shared/blueprints/cells/Buffs/alluring_raptor_manager_buff',
}


def _log(msg):
    print('BUFF ' + msg, flush=True)


def _buffable_guid(session, enc, pawn_guid):
    """-> (guid, export) for the pawn's buffable component, introduced by name once per session."""
    g = enc.get('buffable_guid')
    first = g is None
    if first:
        g = enc['buffable_guid'] = enc['guids'].dynamic()
        session.ctx._names[g] = (pawn_guid, BUFFABLE_COMPONENT)
    exp = spawn._info(g, BUFFABLE_COMPONENT, outer=spawn._info(pawn_guid, None, flags=spawn.ACTOR_OUTER_FLAGS),
                      flags=spawn.FLAG_PATH | spawn.FLAG_NO_LOAD)
    return g, exp


def add(session, enc, cls):
    """Spawn buff `cls` on the player. -> entries ([] when it cannot)."""
    from ..loadout import register_atlas_class_full
    from ..island_encounter import _pawn_guid
    from .attributes import find_block
    pawn = next((e for e in session.entries if e.open and e.cls == 'BP_PlayerCharacter_C'), None)
    asc = find_block(session, pawn.ch, 'ArchonAbilitySystemComponent') if pawn is not None else None
    schema = session.world.schema
    if asc is None or cls not in PACKAGES or not register_atlas_class_full(schema, cls):
        _log('%s not sent (%s)' % (cls, 'no pawn ASC' if asc is None else 'no layout'))
        return []
    pawn_guid = _pawn_guid(session)
    comp, comp_export = _buffable_guid(session, enc, pawn_guid)
    session._live_ch = getattr(session, '_live_ch', 900)
    ch = session._live_ch
    session._live_ch += 1
    entry, ids = spawn.spawn_entry(schema, enc['guids'], ch=ch, cls_name=cls, package=PACKAGES[cls], loc=[0.0, 0.0, 0.0],
                                   values={'Owner': ('obj', pawn_guid), 'Instigator': ('obj', pawn_guid),
                                           'OwnerAbilitySystemComponent': ('obj', asc.guid),
                                           'InstigatorAbilitySystemComponent': ('obj', asc.guid),
                                           'OwnerBuffableComponent': ('obj', comp), 'bActive': True})
    entry.exports.insert(0, comp_export)
    session.chan_cls[ch] = cls
    session.ctx._names[ids['package']] = (0, PACKAGES[cls])
    session.ctx._names[ids['archetype']] = (ids['package'], 'Default__' + cls)
    enc.setdefault('buffs', {})[cls] = ch
    _log('%s ch%d on the player (buffable component guid %d)' % (cls, ch, comp))
    return [entry]
