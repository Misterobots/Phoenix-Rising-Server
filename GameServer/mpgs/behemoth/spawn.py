"""Compose the wire entries for a behemoth actor: spawn (actor-channel open), movement, montage playback and close.

Entries use the same `Entry` shape as the rest of the world (mpgs.genworld.Entry), so a session can insert them at its send
position the way `_respawn_equipment` does. Nothing here talks to a connection and nothing has been tried against a live client:
the offline tests only prove the bits decode back to what was composed. Assumptions that only a live run can settle are marked.
"""
from ..repl import model
from ..repl.decode import OrderedFields
from ..repl.guid import GuidInfo

LEVEL_GUID = 5                 # persistent level, as in the recorded Ramsgate actors
FLAG_PATH, FLAG_NO_LOAD = 1, 2
ACTOR_OUTER_FLAGS = 6          # the actor as the outer of its components (recorded: no path, no-load + checksum bit)


def _info(guid, name, outer=None, flags=FLAG_PATH):
    i = GuidInfo()
    i.guid, i.flags, i.outer_info, i.name, i.checksum = guid, flags, outer, name, None
    return i


class GuidSource:
    """Fresh GUIDs for one connection. Dynamic objects (actors, spawned sub-objects) are even; the static path GUIDs
    for packages, archetypes and assets are odd. `next_even()` is the session's allocator (genworld `_live_guid`)."""

    def __init__(self, next_even):
        self._next = next_even

    def dynamic(self):
        return self._next()

    def static(self):
        return self._next() + 1


def new_entry(ch, cls, exports, parsed, *, open=False, close=False, reliable=True, must_map=False):
    from ..genworld import Entry
    e = Entry()
    e.t, e.ch, e.cls, e.exports = 0, ch, cls, exports
    e.open, e.close, e.reliable, e.must_map = open, close, reliable, must_map
    e.patch_pairs = ()
    e.parsed = parsed
    return e


def _ordered(values):
    out = OrderedFields()
    for k, v in (values or {}).items():
        out[k] = v
    return out


def _actor_block(cls_name, values):
    b = model.Block()
    b.actor, b.rep, b.guid, b.stable, b.destroy = 1, 1, 0, None, False
    b.cls = b.outer = None
    b.ref = b.ref_name = None
    b.cls_name = cls_name
    b.values = _ordered(values) if values else None
    b.tail = b.raw = b.fields = None
    b.partial_properties = False
    return b


def _component_block(guid, cls_name, values=None, fields=None):
    """A stably named component the client already has (it is in the archetype): referenced by its exported GUID."""
    b = model.Block()
    b.actor, b.rep, b.guid, b.stable, b.destroy = 0, 1 if values else 0, guid, 1, False
    b.cls = b.outer = None
    b.ref = b.ref_name = None
    b.cls_name = cls_name
    b.values = _ordered(values) if values else None
    b.tail = b.raw = None
    b.fields = fields
    b.partial_properties = False
    return b


def spawn_entry(schema, guids, *, ch, cls_name, package, loc, rot=(0.0, 0.0, 0.0), values=None, components=()):
    """The open bunch of one behemoth actor.

    cls_name: Blueprint class (e.g. 'sq_rock_normal_bp_C', registered with bp_schema.extend); package: its package path
    ('/Game/Monsters/sq/rock/sq_rock_normal_bp'). components: [(variable name, class name, {replicated values} | None)] of stably named
    components to introduce with an initial property block. Returns (entry, ids) where ids maps names to the GUIDs used.

    Assumptions needing a live run: (1) the archetype and component exports without network checksums are accepted (the weapon swap
    already sends the archetype that way); (2) a stably named component is exported as {guid, outer = actor, name} with
    flags NO_LOAD|PATH, as the recorded ASC export is; (3) Role/RemoteRole as for the recorded equipment actors.
    """
    if cls_name not in schema.classes:
        raise KeyError('%s is not in the schema (bp_schema.extend first)' % cls_name)
    actor = guids.dynamic()
    pkg = _info(guids.static(), package)
    arch = _info(guids.static(), 'Default__' + cls_name, outer=pkg)
    ids = {'actor': actor, 'archetype': arch.guid, 'package': pkg.guid}
    base = {'RemoteRole': 1, 'Role': 3}
    base.update(values or {})
    blocks = [_actor_block(cls_name, base)]
    exports = [arch]
    actor_info = _info(actor, None, flags=ACTOR_OUTER_FLAGS)
    for name, comp_cls, comp_values in components:
        g = guids.dynamic()
        ids[name] = g
        exports.append(_info(g, name, outer=actor_info, flags=FLAG_PATH | FLAG_NO_LOAD))
        if comp_values:                      # exported either way (later RPCs name it); a block only when it has state to send
            blocks.append(_component_block(g, comp_cls, comp_values))
    parsed = model.Parsed()
    parsed.must_map = [arch.guid]
    parsed.open = {'guid': actor, 'archetype': arch.guid, 'level': LEVEL_GUID, 'loc': list(loc), 'rot': list(rot),
                   'scale': [1, 1, 1], 'vel': [0, 0, 0]}
    parsed.blocks = blocks
    entry = new_entry(ch, cls_name, exports, parsed, open=True, must_map=True)
    return entry, ids


# Bp_Behemoth's CDO sets ReplicatedMovement quantization: location and velocity RoundOneDecimal, rotation ShortComponents (the player pawn
# uses x100 / x1 / byte). Sending the pawn's encoding made the stock client fail the bunch (ObjectReplicatorReceivedBunchFail, 2026-10-14).
BEHEMOTH_MOVE_QUANT = {'loc': 10, 'vel': 10, 'rot': 'short'}


def root_motion_values(montage_guid, position, loc, yaw, vel=(0.0, 0.0, 0.0)):
    """Character.RepRootMotion (FRepRootMotionMontage) as ACharacter::PreReplication fills it while a root-motion montage plays:
    the montage, its track position, the actor's location and rotation, no movement base, an empty root-motion source group, and
    the velocity. montage_guid None -> the cleared struct (RepRootMotion.Clear()) that ends client-side root-motion fixups."""
    active = montage_guid is not None
    return {
        'RepRootMotion.bIsActive': 1 if active else 0,
        'RepRootMotion.AnimMontage': ('obj', montage_guid if active else 0),
        'RepRootMotion.Position': float(position) if active else 0.0,
        'RepRootMotion.Location': list(loc) if active else [0.0, 0.0, 0.0],
        'RepRootMotion.Rotation': [0.0, yaw, 0.0] if active else [0.0, 0.0, 0.0],
        'RepRootMotion.MovementBase': ('obj', 0),
        'RepRootMotion.MovementBaseBoneName': ('ename', 0),          # NAME_None
        'RepRootMotion.bRelativePosition': 0,
        'RepRootMotion.bRelativeRotation': 0,
        'RepRootMotion.AuthoritativeRootMotion': {},
        'RepRootMotion.Acceleration': [0.0, 0.0, 0.0],
        'RepRootMotion.LinearVelocity': list(vel) if active else [0.0, 0.0, 0.0],
    }


def movement_entry(ch, cls_name, loc, yaw, vel=(0.0, 0.0, 0.0), mode=1, quant=None, server_time=None, root_motion=None,
                   exports=()):
    """Unreliable property update: ReplicatedMovement + mode, quantized the way the behemoth class receives it.

    server_time: Character.ReplicatedServerLastTransformUpdateTimeStamp, the server time of this transform (the client's movement
    smoothing reads it). root_motion: root_motion_values(...) while a root-motion montage plays, or the cleared form when it ends
    (exports: the montage's path info, so the reference resolves even if this arrives before the montage RPC).
    """
    values = {'ReplicatedMovement': {'loc': list(loc), 'rot': [0.0, yaw, 0.0], 'vel': list(vel), 'sleep': 0, 'phys': 0,
                                     'quant': quant or BEHEMOTH_MOVE_QUANT}}
    if server_time is not None:
        values['ReplicatedServerLastTransformUpdateTimeStamp'] = float(server_time)
    values['ReplicatedMovementMode'] = mode
    if root_motion:
        values.update(root_motion)
    p = model.Parsed()
    p.blocks = [_actor_block(cls_name, values)]
    return new_entry(ch, cls_name, list(exports), p, reliable=False)


def close_entry(ch, cls_name):
    """Close the channel (destroys the actor on the client)."""
    p = model.Parsed()
    p.blocks = []
    return new_entry(ch, cls_name, [], p, close=True)


def montage_play_entry(schema, guids, *, ch, actor_cls, montage_guid_cache, component_guid, component_cls, montage_package,
                       montage_name, play_rate=1.0, position=0.0, blend_time=0.2, section_id=0, server_initiated=True):
    """NetworkedMontageComponent.MulticastPlayMontage on the actor's NetworkedMontage component (reliable RPC).

    The montage is a path-referenced asset: its GUID (odd) is allocated once per name in `montage_guid_cache` and exported with
    each use (re-exporting an already known path is harmless). Parameter order follows FunctionsInfo: NewAnimMontage, PlayRate,
    Position, BlendTime, Tags, InPlayRate, NextSectionID, bPlayBit, bServerInitiated, bIsFlying. Which value the client needs in
    which field (PlayRate vs InPlayRate, bPlayBit) is a guess from the CharacterMovement montage convention - confirm live.
    """
    key = (montage_package, montage_name)
    if key not in montage_guid_cache:
        montage_guid_cache[key] = (guids.static(), guids.static())      # (montage guid, its package guid)
    mg, pg = montage_guid_cache[key]
    mont = _info(mg, montage_name, outer=_info(pg, montage_package))
    idx = schema.field_index(component_cls).index(('func', 'MulticastPlayMontage'))
    params = OrderedFields()
    params['NewAnimMontage'] = ('obj', mg)
    params['PlayRate'] = play_rate
    if position:
        params['Position'] = position
    params['BlendTime'] = blend_time
    params['InPlayRate'] = play_rate
    if section_id:
        params['NextSectionID'] = section_id
    params['bPlayBit'] = 1
    params['bServerInitiated'] = 1 if server_initiated else 0
    params['bIsFlying'] = 0
    blk = _component_block(component_guid, component_cls, fields=[(idx, 'MulticastPlayMontage', 'rpc', params)])
    p = model.Parsed()
    p.blocks = [blk]
    return new_entry(ch, actor_cls, [mont], p)


def montage_prop_entry(schema, guids, *, ch, actor_cls, montage_guid_cache, component_guid, component_cls, montage_package,
                       montage_name, play_rate=1.0, position=0.0, blend_time=0.2, force_play_bit=1, server_initiated=True):
    """Play a montage through the component's replicated property RepAnimMontageInfo (CombatRepAnimMontage) instead of the multicast.

    This is the path a late joiner takes, so it is ordinary property replication. The client plays when the struct changes; ForcePlayBit
    should be flipped between plays of the same montage so the change is always seen. Reliable, like the RPC variant.
    """
    key = (montage_package, montage_name)
    if key not in montage_guid_cache:
        montage_guid_cache[key] = (guids.static(), guids.static())
    mg, pg = montage_guid_cache[key]
    mont = _info(mg, montage_name, outer=_info(pg, montage_package))
    values = {'RepAnimMontageInfo.AnimMontage': ('obj', mg), 'RepAnimMontageInfo.PlayRate': play_rate,
              'RepAnimMontageInfo.Position': position, 'RepAnimMontageInfo.BlendTime': blend_time,
              'RepAnimMontageInfo.ForcePlayBit': 1 if force_play_bit else 0,
              'RepAnimMontageInfo.IsStopped': 0, 'RepAnimMontageInfo.ServerInitiatedBit': 1 if server_initiated else 0}
    p = model.Parsed()
    p.blocks = [_component_block(component_guid, component_cls, values=values)]
    return new_entry(ch, actor_cls, [mont], p)


def player_pawn(template_path, name):
    """(channel, GUID) of a component export on the player pawn in the join template. The client already knows these from the join."""
    import json
    with open(template_path, encoding='utf-8') as f:
        t = json.load(f)
    pawn = next(e for e in t['entries'] if e.get('cls') == 'BP_PlayerCharacter_C')
    for ex in pawn['exports']:
        if ex.get('name') == name:
            return pawn['ch'], ex['guid']
    raise KeyError('component %r not in the player pawn exports' % name)


def stagger_entry(schema, *, ch, actor_cls, component_guid, component_cls, damage_magnitude):
    """ClientStartStaggerAction(DamageMagnitude) on a player's hit reaction component (client RPC, reliable)."""
    idx = schema.field_index(component_cls).index(('func', 'ClientStartStaggerAction'))
    params = OrderedFields()
    params['DamageMagnitude'] = float(damage_magnitude)
    blk = _component_block(component_guid, component_cls, fields=[(idx, 'ClientStartStaggerAction', 'rpc', params)])
    p = model.Parsed()
    p.blocks = [blk]
    return new_entry(ch, actor_cls, [], p)
