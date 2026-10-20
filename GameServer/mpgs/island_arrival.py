"""Owner arrival replication required by the exploration controller's loading gate.

The contract comes from this build's cooked controller/arrival Blueprints and
map. Client callbacks, rather than a timer, drive cinematic completion.
"""
import copy

from .repl import model
from .repl.guid import GuidInfo, flatten

ACTOR = 'adventure_island_arrival_trigger_bp_C'
COMPONENT = 'dynamic_cinematic_component_bp_C'
COMPONENT_NAME = 'dynamic_cinematic_component_bp'
PACKAGE = '/Game/Cinematics/support_blueprints/adventure_island_arrival_trigger_bp'


def extract(exports, read):
    """Extract the default arrival assets and map-authored intro/landing data."""
    rows = read(PACKAGE)
    dynamic = read('/Game/Cinematics/support_blueprints/dynamic_cinematic_component_bp')
    root = next(r for r in rows if r['Name'] == 'ExecuteUbergraph_' + PACKAGE.rsplit('/', 1)[1])
    strings = []

    def walk(obj):
        if isinstance(obj, dict):
            if obj.get('Token') == 'EX_StringConst' and isinstance(obj.get('Value'), str):
                strings.append(obj['Value'])
            for value in obj.values():
                walk(value)
        elif isinstance(obj, list):
            for value in obj:
                walk(value)

    walk(root['ScriptBytecode'])
    # The fallback animation is an explicit assignment to LArrivalAnimSequence.
    fallback = next(s['Expression']['Value']['Value'] for s in root['ScriptBytecode']
                    if s.get('Variable', {}).get('Variable', {}).get('Property', {}).get('Name') == 'LArrivalAnimSequence'
                    and s.get('Expression', {}).get('Token') == 'EX_SoftObjectConst')
    intro = [r for r in exports if r['Type'] == 'LevelSequenceActor' and
             'island_intro' in r.get('Properties', {}).get('Tags', [])]
    markers = [r for r in exports if r['Type'] == 'island_player_arrival_marker_bp_C']
    if not intro or len(markers) != 1:
        raise ValueError('Arrival needs authored intro sequences and one landing marker')
    # Preserve the same authored choices; deterministic first choice for now.
    intro = sorted(intro, key=lambda r: r['Name'])[0]
    cdo = next(r for r in dynamic if r['Name'] == 'Default__' + COMPONENT)
    fields = cdo['Properties']['PreloadParameters']
    return {'actorClass': ACTOR, 'componentClass': COMPONENT, 'componentName': COMPONENT_NAME,
            'arrivalAnimation': fallback, 'introActor': intro['Name'],
            'introSequence': intro['Properties']['LevelSequenceAsset']['ObjectPath'],
            'landingTransforms': markers[0]['Properties']['ActorTransforms'],
            'landSequence': next(s for s in strings if s.endswith('/island_into_land.island_into_land')),
            'proxyPlayer': next(s for s in strings if s.endswith('/cine_player_00.cine_player_00_C')),
            'airship': next(s for s in strings if s.endswith('/airship_actor_bp.airship_actor_bp_C')),
            'camera': next(s for s in strings if s.endswith('/camera_actor.camera_actor_C')),
            'preloadFields': list(fields)}


def _info(guid, name=None, outer=None):
    info = GuidInfo()
    info.guid, info.flags, info.name, info.outer_info, info.checksum = guid, 1 if name else 0, name, outer, None
    return info


def _block(cls, values=None, fields=None, guid=None):
    block = model.Block()
    block.actor, block.rep = guid is None, values is not None
    block.guid, block.stable, block.destroy = guid or 0, guid is not None, False
    block.cls = block.outer = block.ref = block.ref_name = None
    block.cls_name, block.values = cls, values
    block.tail = block.raw = None
    block.fields = fields
    return block


def _entry(world, channel, cls, blocks, at=0):
    entry = type(world.entries[0])()
    entry.t, entry.ch, entry.cls = at, channel, cls
    entry.open = entry.close = entry.must_map = False
    entry.reliable, entry.exports, entry.patch_pairs = True, [], ()
    entry.parsed = model.Parsed()
    entry.parsed.blocks = blocks
    return entry


def _array(name, values):
    return {'count': len(values), 'elems': {i: {name + '[]': value} for i, value in enumerate(values)}}


def append_bootstrap(world, contract, slot):
    arrival = contract['arrival']
    pawn = next(e for e in world.entries if e.cls == 'BP_PlayerCharacter_C' and e.open)
    controller = next(e for e in world.entries if e.cls == contract['classes']['PlayerControllerClass'][1] and e.open)
    state = next(e for e in world.entries if e.cls == contract['classes']['GameStateClass'][1] and e.open)
    actor_guid, component_guid = 40000, 40002
    # Reserve paths outside the captured IDs; dynamic actor/component IDs are
    # subsequently remapped independently for every GenSession.
    package = _info(40001, PACKAGE)
    cdo = _info(40003, 'Default__' + ACTOR, package)
    level = next(copy.deepcopy(i) for e in world.entries for root in e.exports for i in flatten(root)
                 if i.name == 'PersistentLevel' and i.guid == 5)
    sequence = _info(40005, arrival['introActor'], level)
    component = _info(component_guid, COMPONENT_NAME, _info(actor_guid))
    for info in (cdo, sequence, component):
        for item in flatten(info):
            if item.name:
                if item.guid in world.ctx._names and world.ctx._names[item.guid] != (item.outer, item.name):
                    raise ValueError('Arrival NetGUID collides with bootstrap')
                world.ctx._names[item.guid] = (item.outer, item.name)
    world.ctx._names[actor_guid] = (5, ACTOR + '_0')
    prefix = 'PreloadParameters.'
    fields = {key.split('_', 1)[0]: prefix + key for key in arrival['preloadFields']}
    values = {'bReplicates': True,
              'Clients': _array('Clients', [('obj', pawn.parsed.open['guid'])]),
              'ReplacementActors': _array('ReplacementActors', [('obj', pawn.parsed.open['guid'])]),
              'ServerPlaybackState': 1, 'ForceFadeFromBlack': 1,
              fields['SequenceActorInstances']: _array(fields['SequenceActorInstances'], [('obj', sequence.guid)]),
              fields['Sequences']: _array(fields['Sequences'], [('soft', arrival['landSequence'])]),
              fields['ProxyActorsClassIDs']: _array(fields['ProxyActorsClassIDs'], [('soft', arrival['proxyPlayer'])]),
              fields['ActorsToSpawnClassIDs']: _array(fields['ActorsToSpawnClassIDs'], [('soft', arrival['airship'])]),
              fields['ProxyProtagonist']: ('soft', arrival['proxyPlayer']),
              fields['ProxyCameraClassID']: ('soft', arrival['camera'])}
    landing = arrival['landingTransforms'][slot]
    identity = {'Rotation': dict(X=0., Y=0., Z=0., W=1.), 'Translation': dict(X=0., Y=0., Z=0.),
                'Scale3D': dict(X=1., Y=1., Z=1.)}
    transforms = []
    name = fields['StageTransforms']
    for transform in (identity, landing):
        transforms.append({name + '[].Rotation': [transform['Rotation'][k] for k in ('X', 'Y', 'Z', 'W')],
                           name + '[].Translation': [transform['Translation'][k] for k in ('X', 'Y', 'Z')],
                           name + '[].Scale3D': [transform['Scale3D'][k] for k in ('X', 'Y', 'Z')]})
    values[name] = {'count': 2, 'elems': dict(enumerate(transforms))}
    entry = _entry(world, 17, ACTOR, [
        _block(ACTOR, {'Role': 3, 'RemoteRole': 1, 'Owner': ('obj', pawn.parsed.open['guid']),
                       'LArrivalAnimSequence': ('soft', arrival['arrivalAnimation'])}),
        _block(COMPONENT, values, guid=component_guid)], at=state.t + 1)
    entry.open = True
    entry.exports = [cdo, sequence, component]
    entry.parsed.open = {'guid': actor_guid, 'archetype': cdo.guid, 'level': 5, 'loc': [0, 0, 0],
                         'rot': [0, 0, 0], 'vel': [0, 0, 0], 'scale': [1, 1, 1]}
    link = _entry(world, controller.ch, controller.cls, [_block(controller.cls, {
        'ArrivalCinematicTrigger': ('obj', actor_guid),
        'CurrentCinematicPlayerInterface': ('obj', component_guid)})], at=entry.t + 1)
    world.entries.extend([entry, link])
    world.entries.sort(key=lambda e: e.t)


def handle_rpc(session, channel, cls, name, params):
    if not (getattr(session.world, 'island_contract', None) or {}).get('arrival'):
        return False
    controller = next(e for e in session.entries if e.open and e.cls ==
                      session.world.island_contract['classes']['PlayerControllerClass'][1])
    player_state = next(e for e in session.entries if e.open and e.cls ==
                        session.world.island_contract['classes']['PlayerStateClass'][1])
    if channel == player_state.ch and cls == player_state.cls and name in (
            'ServerSetUserWantsToLeaveMatch', 'ServerSetUserWantsToRetryMatch'):
        prop = 'bUserWantsToLeaveMatch' if name == 'ServerSetUserWantsToLeaveMatch' else 'bUserWantsToRetryMatch'
        param_key = 'bInUserWantsToLeaveMatch' if name == 'ServerSetUserWantsToLeaveMatch' else 'bInUserWantsToRetryMatch'
        val = bool((params or {}).get(param_key, True))
        session.entries[session.pos:session.pos] = [
            _entry(session.world, player_state.ch, player_state.cls, [_block(player_state.cls, {prop: val})])]
        print('ARRIVAL %s=%s' % (prop, val), flush=True)
        return True
    if channel != controller.ch or cls != controller.cls:
        return False
    if name == 'Server_MovePlayersBackToArrivalArea':
        start_info = session.world.island_contract['starts'][0]
        loc = list(start_info['location'])
        rot = list(start_info['rotation'])
        fi = session.world.schema.field_index(controller.cls)
        changes = [
            _entry(session.world, controller.ch, controller.cls, [_block(controller.cls, fields=[
                (fi.index(('func', 'ClientSetLocation')), 'ClientSetLocation', 'rpc',
                 {'NewLocation': loc, 'NewRotation': rot})])])]
        pawn = next((e for e in session.entries if e.open and e.cls == 'BP_PlayerCharacter_C'), None)
        if pawn is not None:
            pfi = session.world.schema.field_index(pawn.cls)
            changes.append(_entry(session.world, pawn.ch, pawn.cls, [_block(pawn.cls, fields=[
                (pfi.index(('func', 'TeleportOnOwningClient')), 'TeleportOnOwningClient', 'rpc',
                 {'DestLocation': loc, 'DestRotation': rot})])]))
        session._safe_loc = (list(loc), list(rot))
        if isinstance(getattr(session, 'pawn_state', None), dict):
            session.pawn_state['loc'] = list(loc)
        session.entries[session.pos:session.pos] = changes
        print('ARRIVAL return to arrival area -> %s' % [round(v, 1) for v in loc], flush=True)
        return True
    if name not in ('SetCinematicStateRPC', 'Server_Gameplay_Start'):
        return False
    if name == 'SetCinematicStateRPC':
        state = params.get('CinematicState', 0)
        print('ARRIVAL client cinematic state=%s' % state, flush=True)
        if state != 2 or getattr(session, '_arrival_finished', False):
            return True
        session._arrival_finished = True
        actor = next(e for e in session.entries if e.cls == ACTOR and e.open)
        component = next(b for b in actor.parsed.blocks if b.cls_name == COMPONENT)
        fields = session.world.schema.field_index(COMPONENT)
        changes = [
            # The completion switch treats 0 as fade back to gameplay. Mode 1
            # blends the camera but deliberately skips the fade-from-black
            # sequence; leaving the startup override there retains the black
            # frame from the landing sequence. Send the reset before the RPC.
            _entry(session.world, actor.ch, ACTOR, [_block(COMPONENT, {
                'ServerPlaybackState': 2, 'ForceFadeFromBlack': 0}, guid=component.guid)]),
            _entry(session.world, actor.ch, ACTOR, [_block(COMPONENT, fields=[
                (fields.index(('func', 'AllClientsCompleteOwnerOnly')), 'AllClientsCompleteOwnerOnly', 'rpc', {})], guid=component.guid)])]
        session.entries[session.pos:session.pos] = changes
    if not getattr(session, '_island_gameplay_started', False):
        session._island_gameplay_started = True
        fi = session.world.schema.field_index(controller.cls)
        changes = [
            _entry(session.world, player_state.ch, player_state.cls, [_block(player_state.cls, {'bIsActiveGameplay': True})]),
            _entry(session.world, controller.ch, controller.cls, [_block(controller.cls, fields=[
                (fi.index(('func', 'ClientIslandGameplayStarted')), 'ClientIslandGameplayStarted', 'rpc', {})])])]
        from .island_encounter import start
        changes.extend(start(session))
        session.entries[session.pos:session.pos] = changes
        print('ARRIVAL gameplay start acknowledged', flush=True)
    elif getattr(session, '_arrival_finished', False):
        # Gameplay-start can be acknowledged before the cinematic callback.
        # Arrival must still release the encounter once that callback arrives.
        from .island_encounter import start
        session.entries[session.pos:session.pos] = start(session)
    return True

