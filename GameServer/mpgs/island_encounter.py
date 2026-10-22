"""Authored first-encounter selection, spawn, spotting, movement, damage and rewards.

This layer creates the encounter actor after arrival at grounded arena
elevation, keeps it unrevealed on the compass until spotted or damaged, drives
authoritative patrol and combat movement, processes client damage hits, and
grants authored defeat notifications, loot, XP, and quest progress.
"""
import hashlib
import math
import os
import threading
import time

from . import progression, quests, rewards
from .island_arrival import _info, _block, _entry, _array
from .repl import compose, model, remap
from .repl.decode import OrderedFields
from .repl.schema import Prop

ENCOUNTER_CHANNEL = 19
BEHEMOTH_TEMPLATE_GUID = 44000
CDO_GUID = 44001
PKG_GUID = 44003
COMBAT_TEXT_COMP_TEMPLATE_GUID = 44002
DAMAGE_COMP_TEMPLATE_GUID = 44004
NETWORKED_MONTAGE_COMP_TEMPLATE_GUID = 44006
MONTAGE_COMPONENT = ('NetworkedMontage', 'NetworkedMontageComponent')
PLAYER_HITTABLE = ('player_hittable_bpc', 'player_hittable_bpc_C')
HIT_REACTION = ('hit_reaction_bpc', 'hit_reaction_bpc_C')                # replicated; exported in the join (guid 130 template)   # BP_PlayerCharacter's ArchonHittableComponent
ENGINE_FLAG = 'MPGS_BEHEMOTH_ENGINE'   # default on; 0 falls back to the scripted encounter. On: behaviour tree + Brain + Mover drive the encounter (mpgs.behemoth.director)


TRACE_FLAG = 'MPGS_BEHEMOTH_TRACE'     # engine path: 1 logs a director TRACE line every 0.5 s (positions, tree, attack choice)
ASC_FLAG = 'MPGS_BEHEMOTH_ASC'         # engine path only; 0 leaves out the ability system / attribute sets (step 6) for bisecting


def engine_enabled(env=None):
    return (os.environ if env is None else env).get(ENGINE_FLAG, '1') != '0'


def asc_enabled(env=None):
    """The step 6 ASC + attribute-set blocks in the spawn (and their health updates). On unless MPGS_BEHEMOTH_ASC=0."""
    return (os.environ if env is None else env).get(ASC_FLAG, '1') != '0'
DEFAULT_HIT_DAMAGE = 350.0
DEFAULT_KILL_XP = 150
FTUE_HUNT_QUEST_ID = 'CR20_PJM_FTUE_01'
FTUE_HUNT_OBJECTIVE_GUID = 'BB60F69042F1165642414C81ADFC0E5C'


def _name(ref):
    return ref['ObjectName'].split("'", 1)[1].rstrip("'")


def extract(selection, exports, read):
    spec_ref = selection['islandSpec']
    # The catalog retains the resolved exploration row, including its asset.
    if 'IslandSpecAsset' in spec_ref:
        spec_ref = spec_ref['IslandSpecAsset']
    path = spec_ref.get('AssetPathName', spec_ref.get('ObjectPath', ''))
    rows = read(path.rsplit('.', 1)[0])
    spec = next(r['Properties']['IslandSpec'] for r in rows if r['Type'] == 'IslandSpecAsset')
    pool = next(p for p in spec['ContentPools'] if p['ContentPoolName'] == 'StandardBehemoth')
    options = [o for o in pool['ContentOptions'] if o['Weighting'] > 0]
    if len(options) != 1:
        raise ValueError('First encounter requires one unambiguous authored content chooser')
    chooser = rows[int(options[0]['ContentChooser']['ObjectPath'].rsplit('.', 1)[1])]
    choices = chooser['Properties']['Behemoths']
    def field(item, prefix):
        return next(v for k, v in item.items() if k.startswith(prefix + '_'))
    candidates = [(field(c, 'SelectedBehemoth'), field(c, 'Level')) for c in choices
                  if field(c, 'Weighting') > 0]
    if not candidates or len({(r['RowName'], level) for r, level in candidates}) != 1:
        raise ValueError('First encounter has multiple distinct behemoth choices')
    ref, level = candidates[0]
    metadata = next(r['Rows'][ref['RowName']] for r in read(ref['DataTable']['ObjectPath'].rsplit('.', 1)[0])
                    if 'Rows' in r)
    actor_path = metadata['BehemothClass']['AssetPathName']
    package, cls = actor_path.rsplit('.', 1)
    ancestry, defaults, packages = [], {}, []
    while package.startswith('/Game/'):
        bp = read(package)
        packages.append((package, cls, bp))
        generated = next(r for r in bp if r['Name'] == cls and r['Type'] == 'BlueprintGeneratedClass')
        cdo = next(r for r in bp if r['Name'] == 'Default__' + cls)
        for key, value in cdo.get('Properties', {}).items():
            defaults.setdefault(key, value)
        parent = generated.get('Super', generated.get('SuperStruct'))
        props = []
        for p in generated.get('ChildProperties', []):
            if 'Net' not in p.get('PropertyFlags', '').split(' | '):
                continue
            if p['Type'] not in ('BoolProperty', 'ByteProperty', 'DoubleProperty', 'FloatProperty',
                                 'IntProperty', 'NameProperty', 'ObjectProperty', 'StructProperty'):
                raise ValueError('Unsupported encounter net property: ' + p['Name'])
            item = dict(n=p['Name'], ri=p['RepIndex'], off=0, dim=1, fl='0', k=p['Type'])
            if p['Type'] == 'StructProperty':
                item['st'] = _name(p['Struct'])
            elif p['Type'] == 'ByteProperty' and p.get('Enum'):
                en = _name(p['Enum'])
                item['en'] = en
                if en == 'behemoth_states_enum':
                    item['em'] = 29
            props.append(item)
        # Preserve every RPC name in FClassNetCache order.
        functions = [r['Name'] for r in bp if r['Type'] == 'Function' and
                     'FUNC_Net' in r.get('FunctionFlags', '').split(' | ')]
        ancestry.append(dict(name=cls, parent=_name(parent), props=props, netFunctionNames=functions))
        package, cls = parent['ObjectPath'].rsplit('.', 1)[0], _name(parent)
    centers = []
    for actor in exports:
        if actor['Type'] != 'BP_PatrolCenterPoint_C':
            continue
        root = exports[int(actor['Properties']['RootComponent']['ObjectPath'].rsplit('.', 1)[1])]
        p = root['Properties']
        if p.get('AttachParent'):
            raise ValueError('Encounter center needs composed parent transform')
        vent_ref = actor['Properties'].get('ConnectedAetherVent')
        if not vent_ref:
            raise ValueError('Encounter center requires ConnectedAetherVent for grounded arena elevation')
        vent_actor = exports[int(vent_ref['ObjectPath'].rsplit('.', 1)[1])]
        vent_root = exports[int(vent_actor['Properties']['RootComponent']['ObjectPath'].rsplit('.', 1)[1])]
        ground_z = float(vent_root['Properties']['RelativeLocation']['Z'])
        centers.append(dict(name=actor['Name'], location=[p['RelativeLocation'][k] for k in 'XYZ'],
                            groundZ=ground_z,
                            rotation=[p.get('RelativeRotation', {}).get(k, 0.) for k in ('Pitch', 'Yaw', 'Roll')]))
    if not centers or spec['MaxActivePrimaryContents'] != 1:
        raise ValueError('First encounter requires an authored center and one active primary content')
    leaf_bp = packages[0][2]
    mesh = next(r for r in leaf_bp if r['Name'] == 'CharacterMesh0' and 'RelativeLocation' in r.get('Properties', {}))
    capsule_offset = -float(mesh['Properties']['RelativeLocation']['Z'])
    enter_combat_range = None
    walk_speed = None
    for _, _, bp in packages:
        for r in bp:
            if enter_combat_range is None and r['Type'] == 'aggro_bpc_C' and 'EnterCombatRange' in r.get('Properties', {}):
                enter_combat_range = float(r['Properties']['EnterCombatRange'])
            if walk_speed is None and r['Name'] == 'CharMoveComp' and 'MaxWalkSpeed' in r.get('Properties', {}):
                # Prefer the grounded base locomotion speed (400.0 in Bp_Behemoth)
                # when a child overrides MaxWalkSpeed to a charge ceiling (4000.0).
                spd = float(r['Properties']['MaxWalkSpeed'])
                if spd <= 1000.0:
                    walk_speed = spd
    if enter_combat_range is None or walk_speed is None:
        raise ValueError('Encounter blueprint chain missing EnterCombatRange or grounded MaxWalkSpeed')
    kill_reward_item = (metadata.get('Exploration_GenericKillRewardType') or {}).get('RowName') or 'ORB_FLAME'
    hunt_started_rewards = list(metadata.get('HuntStartedItemRewards') or [])
    eom_loot_id = defaults.get('EoM_LootID', 'BUNDLE_KILL_LERAWR_BETA_00')
    return dict(behemoth=ref['RowName'], actorClass=actor_path, level=level,
                baseHealth=defaults['BaseHealth'], montagePlayRate=defaults['CurrentMontagePlayRate'],
                capsuleOffset=capsule_offset, enterCombatRange=enter_combat_range, walkSpeed=walk_speed,
                killRewardItem=kill_reward_item, huntStartedRewards=hunt_started_rewards,
                eomLootId=eom_loot_id, centers=centers, schema=ancestry,
                acceptance='spawn replication, grounded posture, spotting gate, patrol/combat movement, damage and defeat rewards')


def install_schema(schema, contract):
    encounter = (contract or {}).get('encounter')
    if not encounter:
        return
    for row in reversed(encounter['schema']):
        parent, cls = row['parent'], row['name']
        if parent not in schema.inherit:
            raise ValueError('Unknown encounter schema parent: ' + parent)
        if cls in schema.classes or cls in schema.inherit:
            # Repeated installation is expected after a template round trip.
            if schema.inherit.get(cls) != [parent] + schema.inherit[parent]:
                raise ValueError('Encounter schema conflicts with existing class: ' + cls)
            continue
        props = [Prop(p) for p in row['props']]
        for p in props:
            if p.kind == 'StructProperty' and p.st not in schema.structs:
                raise ValueError('Unknown encounter replicated struct: ' + p.st)
            if p.kind == 'ByteProperty' and p.name == 'BehemothMusicState' and not p.en:
                p.en = 'behemoth_states_enum'
                p.em = 29
        schema.classes[cls] = (parent, props)
        schema.inherit[cls] = [parent] + schema.inherit[parent]
        existing_funcs = {fn: (fn, fl, params) for fn, fl, params in schema.funcs.get(cls, [])}
        schema.funcs[cls] = [existing_funcs.get(name, (name, 'Net', None)) for name in row.get('netFunctionNames', [])]
    schema._layouts.clear()


def _grounded_location(config, center):
    """Return [X, Y, grounded_root_Z] so CharacterMesh0 soles sit on the arena floor."""
    loc = list(center['location'])
    if 'groundZ' in center and 'capsuleOffset' in config:
        loc[2] = float(center['groundZ']) + float(config['capsuleOffset'])
    return loc


def start(session):
    """Queue one encounter for this session after confirmed arrival, unrevealed until spotted."""
    config = (getattr(session.world, 'island_contract', None) or {}).get('encounter')
    if not config or not getattr(session, '_arrival_finished', False) or getattr(session, '_encounter_started', False):
        return []
    package, cls = config['actorClass'].rsplit('.', 1)
    center = min(config['centers'], key=lambda c: sum((a-b)**2 for a, b in
                 zip(c['location'], session.world.island_contract['starts'][0]['location'])))
    rot = list(center['rotation'])
    engine = _engine_resources(session.world) if engine_enabled() else None
    if engine is not None:
        # The landscape is the arena floor (ENGINE_PORT_PLAN step 4: live player samples sit a constant capsule height above it;
        # the aether vent Z is a pivot). The director's Mover keeps the root at landscape + capsule offset from here on.
        species, terrain, _ = engine
        ground = terrain.ground_z(center['location'][0], center['location'][1])
        if ground is None:
            raise ValueError('Encounter center %s is off the exported landscape' % center['name'])
        loc = [center['location'][0], center['location'][1], ground + species.half_height]
    else:
        # One fixed authored species at the nearest authored center, placed at the
        # arena floor elevation so full-body IK does not stretch the legs downward.
        loc = _grounded_location(config, center)
    cdo = _info(CDO_GUID, 'Default__' + cls, _info(PKG_GUID, package))
    base_health = float(config['baseHealth'])
    values = {
        'Role': 3, 'RemoteRole': 1, 'bHidden': 0, 'bCanBeDamaged': 1,
        'bReplicateMovement': 1,
        'ReplicatedMovement': {'loc': [round(v, 1) for v in loc], 'rot': [round(v, 2) for v in rot], 'vel': [0., 0., 0.], 'sleep': 0, 'phys': 0},
        # Patrol transforms have no sampled Landscape Z. Keep client
        # CharacterMovement from applying local floor corrections until
        # the encounter is revealed and combat-height tracking begins.
        'ReplicatedMovementMode': 0,
        'BehemothMetaDataName': config['behemoth'],
        'bImageRevealed': 0,
        'HealthDEPRECATED_DO_NOT_USE': base_health,
        'MetadataTableName': (config['behemoth'], 0), 'BehemothLevel': config['level'],
        'CurrentMontagePlayRate': config['montagePlayRate'],
        'ActionRateModifier': 1.0,
        'Curving': 0,
        'DisableIkinema': 1,
        'InCombat': 0, 'IsAlerted': 0, 'IsDead': 0}
    if engine is not None:
        # Server positions now follow the terrain, so the client walks the actor (MOVE_Walking) and keeps its foot IK.
        values['ReplicatedMovementMode'] = 1
        del values['DisableIkinema']
    actor = _entry(session.world, ENCOUNTER_CHANNEL, cls, [_block(cls, values)])
    actor.open, actor.exports = True, [cdo]
    # The behemoth package may not be resident when arrival completes. Match
    # UPackageMapClient::InternalWriteObject's async-load dependency: the client
    # must resolve the archetype before SerializeNewActor consumes this bunch.
    actor.must_map = True
    actor.parsed.must_map = [cdo.guid]
    actor.parsed.open = dict(guid=BEHEMOTH_TEMPLATE_GUID, archetype=cdo.guid, level=5,
                             loc=list(loc), rot=list(rot),
                             vel=[0., 0., 0.], scale=[1., 1., 1.])
    entries, _ = remap.instantiate([actor], session.guid_map, [])
    behemoth_guid = session.guid_map(BEHEMOTH_TEMPLATE_GUID)
    combat_text_guid = session.guid_map(COMBAT_TEXT_COMP_TEMPLATE_GUID)
    damage_comp_guid = session.guid_map(DAMAGE_COMP_TEMPLATE_GUID)
    combat_text_export = _info(combat_text_guid, 'floating_combat_text_bpc', _info(behemoth_guid))
    combat_text_export.flags = 3
    session.chan_cls[ENCOUNTER_CHANNEL] = cls
    session.ctx._names[PKG_GUID] = (0, package)
    session.ctx._names[CDO_GUID] = (PKG_GUID, 'Default__' + cls)
    session.ctx._names[behemoth_guid] = (5, cls + '_0')
    session.ctx._names[combat_text_guid] = (behemoth_guid, 'floating_combat_text_bpc')
    session.ctx._names[damage_comp_guid] = (behemoth_guid, 'DamageComp')
    session._encounter_started = True
    session._encounter = {
        'behemoth': config['behemoth'],
        'cls': cls,
        'guid': behemoth_guid,
        'combat_text_guid': combat_text_guid,
        'combat_text_export': combat_text_export,
        'combat_text_exported': False,
        'damage_comp_guid': damage_comp_guid,
        'center': list(loc),
        'ground_z': loc[2],
        'capsule_offset': float(config.get('capsuleOffset', 149.0)),
        'loc': list(loc),
        'rot': list(rot),
        'vel': [0.0, 0.0, 0.0],
        'health': base_health,
        'max_health': base_health,
        'enter_combat_range': float(config.get('enterCombatRange', 4000.0)),
        'walk_speed': float(config.get('walkSpeed', 400.0)),
        'kill_reward_item': config.get('killRewardItem', 'ORB_FLAME'),
        'hunt_started_rewards': list(config.get('huntStartedRewards', ['TOKEN_HUNT_COUNT_LERAWR_BETA'])),
        'eom_loot_id': config.get('eomLootId', 'BUNDLE_KILL_LERAWR_BETA_00'),
        'kill_xp': DEFAULT_KILL_XP,
        'spotted': False,
        'in_combat': False,
        'dead': False,
        'rewards_granted': False,
        'reward_tx': None,
        'patrol_angle': 0.0,
        'patrol_radius': 0.0,
        'last_step_t': None,
        'spawn_mono': time.monotonic(),
    }
    if engine is not None:
        _attach_director(session, session._encounter, engine, center)
        if asc_enabled():
            _add_ability_system(session, session._encounter, entries[0])
        else:
            print('ENCOUNTER engine: ability system left out (%s=0)' % ASC_FLAG, flush=True)
    print('ENCOUNTER spawn %s level=%d center=%s loc=%s guid=%d (unrevealed)' % (
        config['behemoth'], config['level'], center['name'], [round(v, 1) for v in loc], behemoth_guid), flush=True)
    return entries


def _pawn_guid(session):
    pawn = next((e for e in session.entries if e.open and e.cls == 'BP_PlayerCharacter_C'), None)
    return pawn.parsed.open['guid'] if pawn and pawn.parsed and pawn.parsed.open else 0


def reveal(session):
    """Reveal the Behemoth on the compass and enter combat when spotted or damaged."""
    enc = getattr(session, '_encounter', None)
    if not enc or enc['spotted'] or enc['dead']:
        return []
    enc['spotted'] = True
    enc['in_combat'] = True
    cls = enc['cls']
    pawn_guid = _pawn_guid(session)
    pawn_ref = ('obj', pawn_guid) if pawn_guid else ('obj', 0)
    fi = session.world.schema.field_index(cls)
    gs = next(e for e in session.entries if e.open and e.cls ==
              session.world.island_contract['classes']['GameStateClass'][1])
    changes = [
        _entry(session.world, gs.ch, gs.cls, [_block(gs.cls, {
            'BehemothState': 1,
            'SpawnedBehemoths': _array('SpawnedBehemoths', [('obj', enc['guid'])])})]),
        _entry(session.world, ENCOUNTER_CHANNEL, cls, [
            _block(cls, {
                'bImageRevealed': True,
                'IsAlerted': True,
                'InCombat': True,
                'BehemothMusicState': 1,
                'ChosenTarget': pawn_ref,
                'LockedTarget': pawn_ref,
            }),
            _block(cls, fields=[
                (fi.index(('func', 'MulticastRegisterStartOfCombat')), 'MulticastRegisterStartOfCombat', 'rpc', {})])])]
    session.entries[session.pos:session.pos] = changes
    print('ENCOUNTER spotted guid=%d -> revealed on compass & entered combat' % enc['guid'], flush=True)
    return changes


def step_movement(session, now):
    """Advance Behemoth patrol/combat movement and return unreliable ReplicatedMovement bunches."""
    enc = getattr(session, '_encounter', None)
    if not enc or enc['dead']:
        return []
    if enc.get('director') is not None:
        return _engine_step(session, enc, now)
    # Wait until all reliable bunches on the encounter channel (spawn, reveal) have been sent.
    if any(e.ch == ENCOUNTER_CHANNEL for e in session.entries[session.pos:]):
        enc['last_step_t'] = now
        return []
    dt = 0.066 if enc['last_step_t'] is None else max(0.01, min(0.25, now - enc['last_step_t']))
    enc['last_step_t'] = now

    pawn_mv = getattr(session, 'pawn_state', None)
    player_loc = pawn_mv['loc'] if pawn_mv else None
    center_x, center_y, ground_z = enc['center']

    if player_loc is not None and not enc['spotted']:
        dist_3d = math.sqrt(sum((a - b) ** 2 for a, b in zip(player_loc, enc['loc'])))
        if dist_3d <= enc['enter_combat_range']:
            reveal(session)
            return []

    if enc['in_combat'] and player_loc is not None:
        dx = player_loc[0] - enc['loc'][0]
        dy = player_loc[1] - enc['loc'][1]
        dist_xy = math.hypot(dx, dy)
        target_yaw = math.degrees(math.atan2(dy, dx)) % 360.0 if dist_xy > 1.0 else enc['rot'][1]
        # Smooth yaw turn toward the player (up to 180 deg/s).
        diff = ((target_yaw - enc['rot'][1] + 180.0) % 360.0) - 180.0
        max_turn = 180.0 * dt
        if abs(diff) <= max_turn:
            yaw = target_yaw
        else:
            yaw = (enc['rot'][1] + math.copysign(max_turn, diff)) % 360.0
        # Stalk/approach the player until within melee range (~420 units), clamped to arena radius (2500 units).
        melee_stop = 420.0
        speed = enc['walk_speed']
        if dist_xy > melee_stop:
            rad = math.radians(yaw)
            vx = math.cos(rad) * speed
            vy = math.sin(rad) * speed
            nx = enc['loc'][0] + vx * dt
            ny = enc['loc'][1] + vy * dt
            # Clamp inside the patrol arena (radius 2500 around center).
            cdx, cdy = nx - center_x, ny - center_y
            cdist = math.hypot(cdx, cdy)
            if cdist > 2500.0:
                nx = center_x + (cdx / cdist) * 2500.0
                ny = center_y + (cdy / cdist) * 2500.0
                vx = (nx - enc['loc'][0]) / dt
                vy = (ny - enc['loc'][1]) / dt
            enc['loc'] = [nx, ny, enc['ground_z']]
            enc['vel'] = [vx, vy, 0.0]
        else:
            # Position is stationary while facing the target; do not report
            # fictitious horizontal travel as locomotion velocity.
            enc['loc'][2] = enc['ground_z']
            enc['vel'] = [0.0, 0.0, 0.0]
        enc['rot'] = [0.0, yaw, 0.0]
    else:
        # The actor spawns at the patrol center. Expanding the patrol radius
        # gradually avoids the old first-update snap of 450 units while keeping
        # location and replicated velocity consistent during the transition.
        max_radius = 450.0
        patrol_speed = min(220.0, enc['walk_speed'] * 0.55)
        radial_speed = min(60.0, patrol_speed * 0.25)
        omega = patrol_speed / max_radius
        enc['patrol_radius'] = min(max_radius, enc.get('patrol_radius', 0.0) + radial_speed * dt)
        enc['patrol_angle'] = (enc['patrol_angle'] + omega * dt) % (2.0 * math.pi)
        ang = enc['patrol_angle']
        radius = enc['patrol_radius']
        nx = center_x + radius * math.cos(ang)
        ny = center_y + radius * math.sin(ang)
        vx = (nx - enc['loc'][0]) / dt
        vy = (ny - enc['loc'][1]) / dt
        yaw = math.degrees(math.atan2(vy, vx)) % 360.0 if math.hypot(vx, vy) > 1.0 else enc['rot'][1]
        enc['loc'] = [nx, ny, enc['ground_z']]
        enc['rot'] = [0.0, yaw, 0.0]
        enc['vel'] = [vx, vy, 0.0]

    # Before discovery the server drives a sparse authored patrol path, but it
    # has no per-point Landscape collision heights. Leaving the client in
    # MOVE_Walking makes CharacterMovement locally correct the capsule against
    # that terrain between replicated transforms, then snap back to the
    # server's fixed patrol Z. Keep the unrevealed actor out of local walking
    # simulation; switch to walking once combat starts and player-height
    # tracking is available above.
    movement_mode = 1 if enc['in_combat'] else 0
    values = {
        'ReplicatedMovement': {
            'loc': list(enc['loc']),
            'rot': list(enc['rot']),
            'vel': list(enc['vel']),
            'sleep': 0,
            'phys': 0,
        },
        'ReplicatedMovementMode': movement_mode,
    }
    b = model.Block()
    b.actor, b.rep, b.guid, b.stable, b.destroy, b.cls, b.outer = 1, 1, None, None, False, None, None
    b.cls_name, b.values, b.tail, b.raw, b.fields = enc['cls'], values, None, None, None
    p = model.Parsed()
    p.blocks = [b]
    w = model.encode(p, session.ctx, enc['cls'])
    return compose.compose(ENCOUNTER_CHANNEL, w.n, w.nbits, [], open=False, close=False, reliable=False)


# ----------------------------------------------------------------------------- behaviour engine (MPGS_BEHEMOTH_ENGINE=1)
# ENGINE_PORT_PLAN.md step 5. The director (mpgs.behemoth.director) decides and moves; this adapter only turns its events into
# bunches on the encounter channel. Spawn, reveal, damage and rewards stay as above.

def _engine_resources(world):
    """(Species, Terrain, TreeSet) for the world's hunt, loaded once per world from the local engine data (see the manifest)."""
    cached = getattr(world, '_behemoth_engine', None)
    if cached is None:
        import json
        from .behemoth import bt
        from .behemoth.director import Species
        from .behemoth.rock_ai import DEFAULT_BT_DIR
        from .world.terrain import Terrain
        server = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        hunt = world.island_contract['huntId']
        with open(os.path.join(server, 'data', '2.1.1', 'islands', hunt + '.engine_data.json'), encoding='utf-8') as f:
            manifest = json.load(f)
        cached = (Species.load(hunt, server), Terrain.load(os.path.join(server, 'world', 'maps', manifest['terrain'])),
                  bt.TreeSet.load(DEFAULT_BT_DIR))
        world._behemoth_engine = cached
    return cached


def _damage_model(world):
    """world/behemoths/damage_model.json (tools.export_damage: exploration damage curves), loaded once per world."""
    cached = getattr(world, '_damage_model', None)
    if cached is None:
        from .behemoth.damage import load_model
        cached = world._damage_model = load_model()
    return cached


def _attach_director(session, enc, engine, center):
    import random
    from types import SimpleNamespace
    from .behemoth.director import BehemothDirector
    from .behemoth.spawn import GuidSource
    species, terrain, trees = engine
    director = BehemothDirector(species, terrain, enc['loc'], enc['rot'][1], lambda: getattr(session, 'pawn_state', None),
                                trees=trees, rng=random.Random(enc['guid']),
                                log=lambda msg: print('ENCOUNTER AI ' + msg, flush=True))
    director.health = SimpleNamespace(hits=0)
    comp_guid = session.next_live_guid()
    comp_export = _info(comp_guid, MONTAGE_COMPONENT[0], _info(enc['guid']))
    comp_export.flags = 3
    session.ctx._names[comp_guid] = (enc['guid'], MONTAGE_COMPONENT[0])
    from .engine.hits import PendingHits
    enc.update(director=director, montage_comp_guid=comp_guid, montage_comp_export=comp_export, montage_comp_exported=False,
               montage_guids={}, guids=GuidSource(session.next_live_guid), root_motion=None,
               pending_hits=PendingHits(), damage_model=_damage_model(session.world))
    print('ENCOUNTER engine on: %s tree=%s floor z=%.1f center=%s' % (
        species.variant, species.tree, enc['loc'][2] - species.half_height, center['name']), flush=True)


# Static class GUIDs the client already knows from the join (template names table: /Script/Archon classes, outer 33).
HEALTH_SET_CLASS_GUID = 41     # ArchonHealthAttributeSet
DAMAGE_SET_CLASS_GUID = 43     # ArchonDamageAttributeSet
ASC_COMPONENT = ('ArchonAbilitySystem', 'ArchonAbilitySystemComponent')


def _subobject_block(guid, cls_guid, cls_name, values=None):
    """A dynamic sub-object created in this bunch (not stably named, class GUID, outer = the actor), as the join creates the
    player's attribute sets."""
    b = model.Block()
    b.actor, b.rep, b.guid, b.stable, b.destroy = False, values is not None, guid, False, False
    b.cls, b.outer, b.ref, b.ref_name = cls_guid, None, None, None
    b.cls_name, b.values, b.tail, b.fields, b.partial_properties = cls_name, values, None, None, False
    b.raw = None if values is not None else (0, 0)
    return b


def _add_ability_system(session, enc, actor_entry):
    """Health bar (ENGINE_PORT_PLAN step 6). Bp_Behemoth's ArchonAbilitySystem spawns ArchonHealthAttributeSet and
    ArchonDamageAttributeSet (its DefaultStartingData; tables behemoth_health_as / behemoth_damage_as). Create both in the spawn
    bunch the way the join creates the player's sets (sub-object blocks with values, then the ASC listing them in SpawnedAttributes),
    with CurrentHealth = MaxHealth = the encounter's BaseHealth. INFERRED from the player's proven pattern: no behemoth ASC has been
    seen on the wire yet."""
    health_guid, damage_guid, asc_guid = session.next_live_guid(), session.next_live_guid(), session.next_live_guid()
    behemoth = ('obj', enc['guid'])
    blocks = [
        _subobject_block(health_guid, HEALTH_SET_CLASS_GUID, 'ArchonHealthAttributeSet',
                         {'CurrentHealth': enc['max_health'], 'MaxHealth': enc['max_health']}),
        _subobject_block(damage_guid, DAMAGE_SET_CLASS_GUID, 'ArchonDamageAttributeSet'),
        _block(ASC_COMPONENT[1], {'OwnerActor': behemoth, 'AvatarActor': behemoth,
                                  'SpawnedAttributes': _array('SpawnedAttributes', [('obj', health_guid), ('obj', damage_guid)])},
               guid=asc_guid),
    ]
    actor_entry.parsed.blocks.extend(blocks)
    asc_export = _info(asc_guid, ASC_COMPONENT[0], _info(enc['guid']))
    asc_export.flags = 3
    actor_entry.exports.append(asc_export)
    session.ctx._names[asc_guid] = (enc['guid'], ASC_COMPONENT[0])
    enc.update(health_set=blocks[0], asc_guid=asc_guid)


def _health_update(session, enc):
    """CurrentHealth on the behemoth's health set (engine path), with the same header that created it."""
    from .engine.attributes import property_update
    return property_update(session, ENCOUNTER_CHANNEL, enc['health_set'], {'CurrentHealth': enc['health']})


def _send_hit(session, enc, action, key, row):
    """A damage window caught the player: ClientTryHit to their player_hittable_bpc; damage waits for ServerAcceptHit."""
    from .engine import hits
    from .repl.decode import OrderedFields
    pawn = next((e for e in session.entries if e.open and e.cls == 'BP_PlayerCharacter_C'), None)
    if pawn is None or row is None or enc.get('player_downed'):
        return None
    if enc.get('hittable_guid') is None:
        enc['hittable_guid'] = session.next_live_guid()
        export = _info(enc['hittable_guid'], PLAYER_HITTABLE[0], _info(_pawn_guid(session)))
        export.flags = 3
        enc['hittable_export'] = export
        session.ctx._names[enc['hittable_guid']] = (_pawn_guid(session), PLAYER_HITTABLE[0])
    player = getattr(session, 'pawn_state', None) or {'loc': enc['loc']}
    d = [player['loc'][0] - enc['loc'][0], player['loc'][1] - enc['loc'][1], 0.0]
    n = math.hypot(d[0], d[1]) or 1.0
    hid = hits.hit_id(enc['director'].rng)
    damage, elemental = hits.amount(enc['damage_model'], row.get('elements'))
    enc['pending_hits'].add(hid, {'damage': damage, 'elemental': elemental, 'action': action, 'key': key,
                                  'raw': float(sum((row.get('elements') or {}).values())),
                                  'player': dict(player), 'sent': time.monotonic()})
    fi = session.world.schema.field_index(PLAYER_HITTABLE[1])
    params = hits.client_try_hit_params(hid, enc['guid'], row.get('row'), direction=(d[0] / n, d[1] / n, 0.0))
    entry = _entry(session.world, pawn.ch, pawn.cls, [_block(PLAYER_HITTABLE[1], fields=[
        (fi.index(('func', 'ClientTryHit')), 'ClientTryHit', 'rpc', params)], guid=enc['hittable_guid'])])
    if not enc.get('hittable_exported'):
        entry.exports = [enc['hittable_export']]
        enc['hittable_exported'] = True
    print('ENCOUNTER hit attempt %s/%s -> ClientTryHit (%.0f damage if accepted)' % (action, key, damage), flush=True)
    stagger = _stagger_probe(session, pawn, damage)
    out = [entry] + ([stagger] if stagger is not None else [])
    if os.environ.get(PUSH_PROBE_FLAG, '0') == '1' and 'loc' in player:
        # Bisect: health (a property) lands but no RPC shows. A 250 cm shove via the pawn's TeleportOnOwningClient RPC tells
        # whether any server->client RPC on the pawn channel is executed.
        dest = [player['loc'][0] + d[0] / n * 250.0, player['loc'][1] + d[1] / n * 250.0, player['loc'][2]]
        pfi = session.world.schema.field_index(pawn.cls)
        out.append(_entry(session.world, pawn.ch, pawn.cls, [_block(pawn.cls, fields=[
            (pfi.index(('func', 'TeleportOnOwningClient')), 'TeleportOnOwningClient', 'rpc',
             {'DestLocation': dest, 'DestRotation': list(player.get('rot') or [0.0, 0.0, 0.0])})])]))
        print('ENCOUNTER push probe -> TeleportOnOwningClient %s' % [round(v) for v in dest], flush=True)
    return out


HIT_REPLY_TIMEOUT = 1.0   # seconds without ServerAcceptHit/ServerRefuseHit before the fallback applies the hit anyway


def _hit_feedback(session, enc, info):
    """What an accepted hit does: health and damage summary, the flinch montage and the damage number."""
    if enc.get('player_downed'):
        return []                                   # a hit answered after the player went down changes nothing
    pawn = next(e for e in session.entries if e.open and e.cls == 'BP_PlayerCharacter_C')
    player = info.get('player') or {}
    out = []
    flinch = _flinch_entry(session, enc, pawn, player, info['damage'])
    if flinch is not None:
        out.append(flinch)
    numbers = _player_combat_text(session, enc, pawn, player, info['damage'])
    if numbers is not None:
        out.append(numbers)
    return out + _damage_entries(session, enc, info)


def _expire_hits(session, enc):
    """Hits the client has not answered in time: applied anyway unless MPGS_HIT_DIRECT_PROBE=0 (the client skips a ClientTryHit
    it cannot decode without any error, so a silent client must not mean a free dodge)."""
    pending = enc.get('pending_hits')
    if pending is None:
        return []
    out = []
    for info in pending.expired(time.monotonic(), HIT_REPLY_TIMEOUT):
        if os.environ.get(DIRECT_PROBE_FLAG, '1') == '0':
            print('ENCOUNTER hit unanswered (%s/%s), dropped' % (info['action'], info['key']), flush=True)
            continue
        out += _hit_feedback(session, enc, info)
        print('ENCOUNTER hit unanswered (%s/%s) -> applied anyway, player health %.0f' % (
            info['action'], info['key'], enc['player_health']), flush=True)
    return out


PUSH_PROBE_FLAG = 'MPGS_HIT_PUSH_PROBE'   # engine path: 1 adds a 250 cm TeleportOnOwningClient shove to each hit (bisect probe; no effect live 2026-10-20)


FLINCH_FLAG = 'MPGS_HIT_FLINCH'   # engine path: 0 stops the player flinch montage played with each hit
FLINCH_PACKAGE = '/Game/Character/Player/Anims/Unarmed/'
FLINCH_MEDIUM_DAMAGE = 60.0       # below: the light stagger; at or above: the medium one (heavy launch/land left out for now)


def _flinch_entry(session, enc, pawn, player, damage):
    """A medium stagger montage on the player's NetworkedMontage component, picked by the side the Embermane hit from.

    The client ignores ClientTryHit and ClientStartStaggerAction (live 2026-10-20), but MulticastPlayMontage on a
    NetworkedMontage component is what already plays every Embermane attack, so the hit reaction is played the same way."""
    if os.environ.get(FLINCH_FLAG, '1') == '0' or 'loc' not in player:
        return None
    from .behemoth import spawn
    from .repl.guid import flatten
    comp = next((i.guid for root in pawn.exports for i in flatten(root) if i.name == MONTAGE_COMPONENT[0]), None)
    if comp is None:
        return None
    yaw = math.radians((player.get('rot') or [0.0, 0.0, 0.0])[1])
    to_attacker = math.atan2(enc['loc'][1] - player['loc'][1], enc['loc'][0] - player['loc'][0])
    rel = math.degrees(math.atan2(math.sin(to_attacker - yaw), math.cos(to_attacker - yaw)))
    side = 'fwd' if abs(rel) <= 45 else 'bwd' if abs(rel) >= 135 else 'rgt' if rel > 0 else 'lft'
    if enc.get('player_health') is not None and enc['player_health'] - damage <= 0.0:     # this hit downs the player
        name = 'h_mr_ua_stagger_med_death_fwd_01_mot'
    else:
        tier = 'lgt' if damage < FLINCH_MEDIUM_DAMAGE else 'med'
        name = 'h_mr_ua_stagger_%s_atk_%s_imp_%s_01_mot' % (tier, side, side)
    print('ENCOUNTER flinch -> %s' % name, flush=True)
    return spawn.montage_play_entry(session.world.schema, enc['guids'], ch=pawn.ch, actor_cls=pawn.cls,
                                    montage_guid_cache=enc['montage_guids'], component_guid=comp,
                                    component_cls=MONTAGE_COMPONENT[1], montage_package=FLINCH_PACKAGE + name,
                                    montage_name=name)



def _player_combat_text(session, enc, pawn, player, damage):
    """Damage number over the player: MulticastSendMultitypeCombatText on their floating_combat_text_bpc (a multicast, like
    the flinch montage; the client ignores client RPCs on the pawn)."""
    if os.environ.get(FLINCH_FLAG, '1') == '0' or 'loc' not in player:
        return None
    from .repl.decode import OrderedFields
    from .repl.guid import flatten
    comp = next((i.guid for root in pawn.exports for i in flatten(root) if i.name == 'floating_combat_text_bpc'), None)
    if comp is None:
        return None
    impact = [player['loc'][0], player['loc'][1], player['loc'][2] + 80.0]
    e = OrderedFields()
    e.update({
        'Entries[].SourceActor': ('obj', enc['guid']),
        'Entries[].CoreDamage': damage, 'Entries[].PartDamage': 0.0, 'Entries[].StaggerDamage': 0.0,
        'Entries[].ExposeDamage': 0.0, 'Entries[].ShieldDamage': 0.0,
        'Entries[].ImpactLocation': impact, 'Entries[].bHasImpactLocation': 1,
        'Entries[].bHitWasAbsorbed': 0, 'Entries[].bIsCrit': 0, 'Entries[].bIsAntiCrit': 0,
        'Entries[].bAlwaysShowDamageNumbers': 1, 'Entries[].bBlockExposeCrit': 0, 'Entries[].bShowWoundedStatus': 0,
    })
    fi = session.world.schema.field_index('floating_combat_text_bpc_C')
    return _entry(session.world, pawn.ch, pawn.cls, [_block('floating_combat_text_bpc_C', fields=[
        (fi.index(('func', 'MulticastSendMultitypeCombatText')), 'MulticastSendMultitypeCombatText', 'rpc', {'Entries': [e]})],
        guid=comp)])

STAGGER_PROBE_FLAG = 'MPGS_HIT_STAGGER_PROBE'   # engine path: 1 adds ClientStartStaggerAction to each hit (bisect probe; ignored live 2026-10-20)


def _stagger_probe(session, pawn, damage):
    """ClientStartStaggerAction on the player's hit_reaction_bpc, alongside ClientTryHit (live 2026-10-20 bisect).

    The client never answered ClientTryHit. hit_reaction_bpc is a replicated pawn component the real server exports in the join
    (flags 7 with checksum), so the client already maps it; player_hittable_bpc is not in the join. A visible stagger with still no
    ServerAcceptHit means pawn-component RPCs arrive and the hittable addressing is at fault; no stagger points at the delivery of
    server->client RPCs on the pawn channel in general."""
    if os.environ.get(STAGGER_PROBE_FLAG, '0') != '1':
        return None
    from .behemoth import spawn
    from .repl.guid import flatten
    guid = next((i.guid for root in pawn.exports for i in flatten(root) if i.name == HIT_REACTION[0]), None)
    if guid is None:
        print('ENCOUNTER stagger probe skipped: %s not in the pawn exports' % HIT_REACTION[0], flush=True)
        return None
    entry = spawn.stagger_entry(session.world.schema, ch=pawn.ch, actor_cls=pawn.cls, component_guid=guid,
                                component_cls=HIT_REACTION[1], damage_magnitude=max(1.0, damage))
    print('ENCOUNTER stagger probe -> %s.ClientStartStaggerAction(%.0f) guid=%d' % (HIT_REACTION[0], damage, guid), flush=True)
    return entry


def _damage_entries(session, enc, info):
    """Lower the player's CurrentHealth (ASC sub-object property) and send ClientHandlePostMitigationDamage (pawn actor RPC)."""
    from .engine import hits
    from .engine.attributes import find_block, property_update
    pawn = next(e for e in session.entries if e.open and e.cls == 'BP_PlayerCharacter_C')
    header = find_block(session, pawn.ch, 'ArchonHealthAttributeSet')
    if enc.get('player_health') is None:
        enc['player_health'] = float((header.values or {}).get('CurrentHealth', 0.0))
    enc['player_max_health'] = float((header.values or {}).get('MaxHealth', enc['player_health']) or enc['player_health'])
    enc['player_health'] = max(0.0, enc['player_health'] - info['damage'])
    fi = session.world.schema.field_index(pawn.cls)
    pm = hits.post_mitigation_params(_pawn_guid(session), info['damage'], info['elemental'], info['raw'])
    out = [
        *_health_entries(session, pawn, header, enc['player_health']),
        _entry(session.world, pawn.ch, pawn.cls, [_block(pawn.cls, fields=[
            (fi.index(('func', 'ClientHandlePostMitigationDamage')), 'ClientHandlePostMitigationDamage', 'rpc', pm)])])]
    if enc['player_health'] <= 0.0 and not enc.get('player_downed'):
        out += _set_bleedout(session, enc, True)
    return out


# Quick items (live capture 2026-10-22): the client sends ServerNotifyQuickitemUsed(slot) on its inventory actor and
# ServerTryActivateAbility(Handle, InputPressed, PredictionKey) on its ASC. The ability is LocalPredicted: the client already
# plays the drink; the server confirms with ClientActivateAbilitySucceed(Handle, the client's key) and applies the effect.
POTION_HEAL = 425.0          # Ability_QuickItem_Healing_Potion_Flask CDO "Heal Amount" (its HoTTime 20 / HoTBonus 1.2 not modelled)
TALISMAN_REVIVE_DELAY = 0.5   # s after the talisman is confirmed
POTION_HEAL_DELAY = 0.8      # the CDO's Audio-ActivateDelay: the burst lands as the drink finishes


def _ability_names(session, pawn):
    """{handle: ability CDO name} from the join's ActivatableAbilities and the pawn's exports."""
    from .repl.guid import flatten
    names = {i.guid: i.name for root in pawn.exports for i in flatten(root) if i.name}
    asc = next((b for b in pawn.parsed.blocks if b.cls_name == 'ArchonAbilitySystemComponent'), None)
    out = {}
    for f in (asc.fields or []) if asc is not None else []:
        if f[1] != 'ActivatableAbilities' or not isinstance(f[3], dict):
            continue
        for _, item in f[3].get('changed', []):
            d = dict(item)
            h, ab = d.get('ActivatableAbilities.Items[].Handle.Handle'), d.get('ActivatableAbilities.Items[].Ability')
            if h is not None and isinstance(ab, tuple):
                out[h] = names.get(ab[1], '')
    return out


def handle_ability(session, ch, req_block, params):
    """ArchonAbilitySystemComponent.ServerTryActivateAbility on the island. -> True when answered."""
    enc = getattr(session, '_encounter', None)
    pawn = next((e for e in session.entries if e.open and e.cls == 'BP_PlayerCharacter_C'), None)
    if pawn is None or ch != pawn.ch or not isinstance(params, dict):
        return False
    h = params.get('AbilityToActivate.Handle')
    name = _ability_names(session, pawn).get(h, '')
    key = params.get('PredictionKey') or {}
    if 'ReviveTalisman' in name:
        # CDO: ActivationRequiredTags State.Bleedout, CanUseInBleedout. Only a downed player can use it.
        if enc is None or not enc.get('player_downed'):
            session._queue_rpc(ch, req_block, 'ClientActivateAbilityFailed',
                               {'AbilityToActivate.Handle': h, 'PredictionKey': key.get('current', 0)})
            print('ENCOUNTER revive talisman (handle %s) while standing -> ClientActivateAbilityFailed' % h, flush=True)
            return True
        session._queue_rpc(ch, req_block, 'ClientActivateAbilitySucceed', {'AbilityToActivate.Handle': h, 'PredictionKey': key})
        enc['revive_due'] = time.monotonic() + TALISMAN_REVIVE_DELAY
        print('ENCOUNTER revive talisman (handle %s) -> ClientActivateAbilitySucceed, back up in %.1f s' % (
            h, TALISMAN_REVIVE_DELAY), flush=True)
        return True
    if 'Healing_Potion' not in name:
        print('ENCOUNTER ability handle %s (%s) not handled; no reply' % (h, name or '?'), flush=True)
        return False
    session._queue_rpc(ch, req_block, 'ClientActivateAbilitySucceed', {'AbilityToActivate.Handle': h, 'PredictionKey': key})
    if enc is not None:
        enc.setdefault('pending_heals', []).append((time.monotonic() + POTION_HEAL_DELAY, POTION_HEAL, name))
    print('ENCOUNTER potion (handle %s, %s) -> ClientActivateAbilitySucceed, +%.0f health in %.1f s' % (
        h, name, POTION_HEAL, POTION_HEAL_DELAY), flush=True)
    return True


def _apply_heals(session, enc):
    from .engine.attributes import find_block, property_update
    due = [x for x in enc.get('pending_heals', []) if x[0] <= time.monotonic()]
    if not due or enc.get('player_downed'):
        if due:
            enc['pending_heals'] = [x for x in enc['pending_heals'] if x not in due]
        return []
    enc['pending_heals'] = [x for x in enc['pending_heals'] if x not in due]
    pawn = next(e for e in session.entries if e.open and e.cls == 'BP_PlayerCharacter_C')
    header = find_block(session, pawn.ch, 'ArchonHealthAttributeSet')
    values = header.values or {}
    if enc.get('player_health') is None:
        enc['player_health'] = float(values.get('CurrentHealth', 0.0))
    top = float(enc.get('player_max_health') or values.get('MaxHealth') or enc['player_health'])
    enc['player_health'] = min(top, enc['player_health'] + sum(x[1] for x in due))
    print('ENCOUNTER heal -> player health %.0f' % enc['player_health'], flush=True)
    return _health_entries(session, pawn, header, enc['player_health'])


BLEEDOUT = ('player_bleedout_bpc', 'player_bleedout_bpc_C')   # ArchonBleedoutComponent: CurrentBleedoutState (RepNotify)
AUTO_REVIVE_FLAG = 'MPGS_AUTO_REVIVE_SECONDS'   # downed for this long -> back up at REVIVE_HEALTH_FRACTION (0 turns it off)
REVIVE_HEALTH_FRACTION = 0.5
BLEEDOUT_AFTER_FALL = 1.9     # s: the death stagger's fall section ends at 1.93 s


def _set_bleedout(session, enc, down):
    """Enter (down=True) or leave bleedout: EArchonBleedoutState Bleedout(1)/None(0) on the player's player_bleedout_bpc. The
    client's OnRep_CurrentBleedoutState plays the downed loop / the get-up and shows its own UI. While down the behemoth does not
    target the player and no hits are sent. Leaving it restores REVIVE_HEALTH_FRACTION of MaxHealth."""
    from .engine.attributes import find_block, property_update
    pawn = next(e for e in session.entries if e.open and e.cls == 'BP_PlayerCharacter_C')
    comp = find_block(session, pawn.ch, BLEEDOUT[1])
    out = []
    if comp is not None:
        if down:
            # The lethal hit plays h_mr_ua_stagger_med_death_fwd_01_mot (_flinch_entry); its 'Default' section is the fall and
            # 'GetUp' starts at 1.93 s. Setting the state at once cut straight to the downed loop (live 2026-10-22), so it waits.
            enc['bleedout_due'] = (time.monotonic() + BLEEDOUT_AFTER_FALL, comp)
        else:
            enc['bleedout_due'] = None
            out.append(property_update(session, pawn.ch, comp, {'CurrentBleedoutState': 0}))
    enc['player_downed'] = down
    enc['downed_at'] = time.monotonic() if down else None
    if enc.get('director') is not None:
        enc['director'].target_down = down
    if down:
        print('ENCOUNTER player down (bleedout)%s' % ('' if comp else ' - no bleedout component in the join'), flush=True)
    else:
        header = find_block(session, pawn.ch, 'ArchonHealthAttributeSet')
        enc['player_health'] = round(enc.get('player_max_health', 1000.0) * REVIVE_HEALTH_FRACTION)
        out += _health_entries(session, pawn, header, enc['player_health'])
        # BP_PlayerCharacter.InternalReviveFromBleedOut (NetMulticast): Set Bleedout State(false) - hides the downed marker and
        # revive callout (live 2026-10-22: the marker stayed) -, SendGameplayEventToActor(Revive_Tag), interaction
        # RevivedFromBleedOut. Revive_Tag is sent empty (structs.TAG_NONE_INDEX) until the tag net index table exists.
        fi = session.world.schema.field_index(pawn.cls)
        out.append(_entry(session.world, pawn.ch, pawn.cls, [_block(pawn.cls, fields=[
            (fi.index(('func', 'InternalReviveFromBleedOut')), 'InternalReviveFromBleedOut', 'rpc',
             {'RevivingPawn_0': ('obj', _pawn_guid(session)), 'Revive_Tag': None})])]))
        print('ENCOUNTER player revived -> health %.0f' % enc['player_health'], flush=True)
    return out


def _bleedout_now(session, enc):
    """The delayed CurrentBleedoutState=1 once the death stagger's fall has played."""
    from .engine.attributes import property_update
    due = enc.get('bleedout_due')
    if not due or time.monotonic() < due[0] or not enc.get('player_downed'):
        return []
    enc['bleedout_due'] = None
    pawn = next(e for e in session.entries if e.open and e.cls == 'BP_PlayerCharacter_C')
    return [property_update(session, pawn.ch, due[1], {'CurrentBleedoutState': 1})]


def _auto_revive(session, enc):
    """Placeholder until the real revive flow (talisman, party, self-revive charge) is captured live: back up after a delay."""
    if not enc.get('player_downed'):
        return []
    if enc.get('revive_due') and time.monotonic() >= enc['revive_due']:
        enc['revive_due'] = None
        print('ENCOUNTER revive talisman used', flush=True)
        return _set_bleedout(session, enc, False)
    delay = float(os.environ.get(AUTO_REVIVE_FLAG, '10') or 0)
    if delay <= 0 or time.monotonic() - (enc.get('downed_at') or 0.0) < delay:
        return []
    return _set_bleedout(session, enc, False)


def _health_entries(session, pawn, header, health):
    """A player health change as a real server replicates it: the ASC's ArchonHealthAttributeSet.CurrentHealth, the pawn's
    Blueprint 'Current Health' (RepNotify OnRep_Health) and player_state_bp's 'Current Health' (OnRep_Health copies it there on
    the server for the HUD and party). Leaving the two Blueprint copies at the join's value kept the revive callout and the
    downed quick-item bar after a revive (live 2026-10-22)."""
    from .engine.attributes import property_update
    out = [property_update(session, pawn.ch, header, {'CurrentHealth': health}),
           _entry(session.world, pawn.ch, pawn.cls, [_block(pawn.cls, {'Current Health': float(health)})])]
    ps_cls = ((getattr(session.world, 'island_contract', None) or {}).get('classes', {}).get('PlayerStateClass') or
              (None, 'player_state_bp_C'))[1]
    ps = next((e for e in session.entries if e.open and e.cls == ps_cls), None)
    sch = session.world.schema
    if ps is not None and any(p.name == 'Current Health' for c in [ps.cls] + sch.inherit.get(ps.cls, [])
                              for p in sch.classes.get(c, (None, []))[1]):
        out.append(_entry(session.world, ps.ch, ps.cls, [_block(ps.cls, {'Current Health': float(health)})]))
    return out


DIRECT_PROBE_FLAG = 'MPGS_HIT_DIRECT_PROBE'   # engine path: 0 drops hits the client leaves unanswered instead of applying them


def handle_hit_reply(session, func, params):
    """player_hittable_bpc.ServerAcceptHit / ServerRefuseHit for a ClientTryHit this encounter sent."""
    from .engine import hits
    from .engine.attributes import find_block, property_update
    enc = getattr(session, '_encounter', None)
    if not enc or enc.get('pending_hits') is None or not isinstance(params, dict):
        return False
    info = enc['pending_hits'].take(hits.key_of(params))
    if info is None:
        return False
    if func != 'ServerAcceptHit':
        print('ENCOUNTER hit refused by the client (%s/%s)' % (info['action'], info['key']), flush=True)
        return True
    session.entries[session.pos:session.pos] = _hit_feedback(session, enc, info)
    print('ENCOUNTER hit accepted: %.0f damage -> player health %.0f' % (info['damage'], enc['player_health']), flush=True)
    return True


def _montage_info(enc, name, package):
    from .behemoth.spawn import _info as path_info
    mg, pg = enc['montage_guids'][(package, name)]
    return path_info(mg, name, outer=path_info(pg, package))


def _engine_step(session, enc, now):
    """Tick the director and return its movement as unreliable bunches; queue montage RPCs and target state as reliable entries."""
    from .behemoth import spawn
    from .behemoth.director import montage_package
    # Spawn, reveal and montage RPCs go first: movement that refers to the actor or a montage must not overtake them.
    t = now - enc['spawn_mono']
    held = sum(1 for e in session.entries[session.pos:] if e.ch == ENCOUNTER_CHANNEL and e.reliable)
    if held:
        if os.environ.get(TRACE_FLAG) == '1' and t - enc.get('trace_t', -1.0) >= 0.5:
            enc['trace_t'] = t
            print('ENCOUNTER TRACE t=%.2f held: %d reliable encounter entries not yet sent' % (t, held), flush=True)
        return []
    director, cls = enc['director'], enc['cls']
    if os.environ.get(TRACE_FLAG) == '1' and t - enc.get('trace_t', -1.0) >= 0.5:
        enc['trace_t'] = t
        print('ENCOUNTER ' + director.trace_line(t), flush=True)
    reliable, bunches, revealed = _expire_hits(session, enc) + _bleedout_now(session, enc) + _auto_revive(session, enc) +         _apply_heals(session, enc), [], 0
    for ev in director.tick(t):
        kind = ev[0]
        if kind == 'target':
            if ev[1] is not None and not enc['spotted']:
                revealed += len(reveal(session))    # the tree acquired the player: compass reveal + combat start
        elif kind == 'hit':
            reliable.extend(_send_hit(session, enc, ev[1], ev[2], ev[3]) or [])
        elif kind == 'lock':
            pawn_guid = _pawn_guid(session) if ev[1] is not None else 0
            reliable.append(_entry(session.world, ENCOUNTER_CHANNEL, cls, [_block(cls, {'LockedTarget': ('obj', pawn_guid)})]))
        elif kind == 'montage':
            _, name, rate, t0 = ev
            package = montage_package(name)
            if package is None:
                print('ENCOUNTER montage %s skipped: no montage dump' % name, flush=True)
                continue
            entry = spawn.montage_play_entry(session.world.schema, enc['guids'], ch=ENCOUNTER_CHANNEL, actor_cls=cls,
                                             montage_guid_cache=enc['montage_guids'], component_guid=enc['montage_comp_guid'],
                                             component_cls=MONTAGE_COMPONENT[1], montage_package=package, montage_name=name,
                                             play_rate=rate)
            if not enc['montage_comp_exported']:
                entry.exports.insert(0, enc['montage_comp_export'])
                enc['montage_comp_exported'] = True
            reliable.append(entry)
            if director.mover.montage is not None:
                enc['root_motion'] = (name, package, t0, rate)
            print('ENCOUNTER montage %s t=%.2f' % (name, t0), flush=True)
        elif kind == 'move':
            _, loc, yaw, vel = ev
            enc['loc'], enc['rot'], enc['vel'] = list(loc), [0.0, yaw, 0.0], list(vel)
            rm, exports = None, []
            if enc['root_motion'] is not None:
                name, package, t0, rate = enc['root_motion']
                if director.mover.montage is not None:
                    mg = enc['montage_guids'][(package, name)][0]
                    rm = spawn.root_motion_values(mg, (t - t0) * rate, loc, yaw, vel)
                    exports = [_montage_info(enc, name, package)]
                else:
                    rm = spawn.root_motion_values(None, 0.0, (0.0, 0.0, 0.0), 0.0)   # RepRootMotion.Clear() when it ends
                    enc['root_motion'] = None
            upd = spawn.movement_entry(ENCOUNTER_CHANNEL, cls, loc, yaw, vel=vel, mode=1, server_time=t, root_motion=rm,
                                       exports=exports)
            w = model.encode(upd.parsed, session.ctx, cls)
            bunches.extend(compose.compose(ENCOUNTER_CHANNEL, w.n, w.nbits, upd.exports, open=False, close=False,
                                           reliable=False))
    if reliable:
        at = session.pos + revealed                 # after the reveal entries reveal() queued this tick
        session.entries[at:at] = reliable
    return bunches


def grant_defeat_rewards(session):
    """Build and replicate Behemoth defeat notifications, loot, XP, and quest progress once per kill."""
    enc = getattr(session, '_encounter', None)
    if not enc or enc.get('rewards_granted'):
        return []
    enc['rewards_granted'] = True
    behemoth_id = enc.get('behemoth', 'lerawr_beta')
    kill_item = enc.get('kill_reward_item', 'ORB_FLAME')
    reward_items = [
        (kill_item, 10),
        ('CURRENCY_NOTES', 250),
        ('CURRENCY_WEAPONXP_SMALL', 1),
    ]
    for tok in enc.get('hunt_started_rewards', []):
        if tok:
            reward_items.append((tok, 1))
    ids = getattr(session, 'live_ids', None) or {}
    acct = ids.get('acct') or 'local'
    char = ids.get('char') or 'local'
    seed = '%s:%s:encounter:%s:%d' % (acct, char, behemoth_id, enc['guid'])
    tx_body = rewards.build_transaction(acct, char, seed, reward_items)
    enc['reward_tx'] = tx_body
    enc['reward_items'] = list(reward_items)

    curves = getattr(session, 'prog_curves', None) or progression.load_curves()
    session.prog_curves = curves
    prog = getattr(session, 'prog_progress', None)
    if prog is None:
        prog = {}
        session.prog_progress = prog
    xp_gain = int(enc.get('kill_xp', DEFAULT_KILL_XP))
    pl_track = 'ExperienceTrack_PlayerLevel'
    pl_before = prog.get(pl_track, 0)
    pl_after = pl_before + xp_gain
    prog[pl_track] = pl_after

    weapon = ((((getattr(session, 'player_records', None) or {}).get('active_loadout') or {}).get('weapon') or {}).get('item_id')
              or 'WP_EB_Recruit_00')
    w_track = progression.weapon_track(weapon)
    w_before = w_after = 0
    if w_track and w_track[0] in curves:
        w_before = prog.get(w_track[0], 0)
        w_after = w_before + xp_gain
        prog[w_track[0]] = w_after

    out = []
    controller = next((e for e in session.entries if e.open and e.cls ==
                       session.world.island_contract['classes']['PlayerControllerClass'][1]), None)
    if controller is not None:
        ctrl_fi = session.world.schema.field_index(controller.cls)
        rpc_fields = [
            (ctrl_fi.index(('func', 'BehemothSlain_Notification')), 'BehemothSlain_Notification', 'rpc',
             {'Behemoth': ('obj', enc['guid'])}),
            (ctrl_fi.index(('func', 'Client_ShowBehemothSlainChatMessage')), 'Client_ShowBehemothSlainChatMessage', 'rpc',
             {'Behemoth': ('obj', enc['guid'])}),
            (ctrl_fi.index(('func', 'ClientSetEncounteredState')), 'ClientSetEncounteredState', 'rpc',
             {'ContentId': (behemoth_id, 0), 'ContentType': 0, 'bAddBreadcrumb': 1}),
        ]
        for item_id, qty in reward_items:
            if not item_id.startswith('TOKEN_'):
                rpc_fields.append((
                    ctrl_fi.index(('func', 'NotifyOwningClientAboutConditionalLoot')),
                    'NotifyOwningClientAboutConditionalLoot', 'rpc',
                    {'ItemId': item_id, 'Amount': qty, 'ConditionType': 0, 'OverrideShouldShowChecks': 1}))
        ctrl_blocks = [_block(controller.cls, fields=rpc_fields)]
        if pl_track in curves:
            lb, pb, nb = progression.level(curves[pl_track], pl_before)
            la, pa, na = progression.level(curves[pl_track], pl_after)
            exp_vals = OrderedFields()
            for k, v in (
                ('LastExperienceGranted.ExperienceType', 1),
                ('LastExperienceGranted.LevelAmount', pa),
                ('LastExperienceGranted.MaxLevelAmount', na),
                ('LastExperienceGranted.PreviousLevelAmount', pb),
                ('LastExperienceGranted.PreviousMaxLevelAmount', nb),
                ('LastExperienceGranted.IncreaseAmount', xp_gain),
                ('LastExperienceGranted.BoostAmount', 0),
                ('LastExperienceGranted.Level', la),
                ('LastExperienceGranted.PreviousLevel', lb),
                ('LastExperienceGranted.ItemId', behemoth_id),
            ):
                exp_vals[k] = v
            ctrl_blocks.append(_block(controller.cls, exp_vals))
        _, prog_hdr = session._server_header('progression_bpc_C') if hasattr(session, '_server_header') else (None, None)
        if prog_hdr is not None:
            pp_vals = OrderedFields()
            pp_vals['PlayerProgress'] = progression.player_progress_value(curves, prog)
            pb_blk = model.Block()
            pb_blk.actor, pb_blk.rep, pb_blk.guid, pb_blk.stable, pb_blk.destroy = False, True, prog_hdr.guid, prog_hdr.stable, False
            pb_blk.cls, pb_blk.outer, pb_blk.ref, pb_blk.ref_name = prog_hdr.cls, prog_hdr.outer, None, None
            pb_blk.cls_name = 'progression_bpc_C'
            pb_blk.values, pb_blk.tail, pb_blk.raw, pb_blk.fields = pp_vals, None, None, None
            pb_blk.partial_properties = False
            ctrl_blocks.append(pb_blk)
        out.append(_entry(session.world, controller.ch, controller.cls, ctrl_blocks))

    ps = next((e for e in session.entries if e.open and e.cls ==
               session.world.island_contract['classes']['PlayerStateClass'][1]), None)
    wd = progression.weapon_details(curves, prog, weapon)
    if ps is not None and wd:
        wd_vals = OrderedFields()
        for k, v in wd.items():
            wd_vals[k] = v
        out.append(_entry(session.world, ps.ch, ps.cls, [_block(ps.cls, wd_vals)]))

    quest_meta = None
    quest_obj_payload = None
    qg = getattr(session, 'quest_by_id', {}).get(FTUE_HUNT_QUEST_ID)
    qb = getattr(session, 'quest_blocks', {}).get(qg) if qg else None
    if qb is not None and qb.values.get('Status') == 1:
        q_blocks = []
        objs_val = qb.values.get('Objectives') or {}
        elems = objs_val.get('elems') or {}
        first_elem = elems.get(0) or {}
        obj_ref = first_elem.get('Objectives[]')
        og = obj_ref[1] if isinstance(obj_ref, tuple) and obj_ref[0] == 'obj' else None
        ob = getattr(session, 'objective_blocks', {}).get(og) if og else None
        if ob is not None:
            ob.values['Status'] = 2
            ob.values['CurrentAmount'] = 1
            ov = OrderedFields()
            ov['Status'] = 2
            ov['CurrentAmount'] = 1
            ob_blk = model.Block()
            ob_blk.actor, ob_blk.rep, ob_blk.guid, ob_blk.stable, ob_blk.destroy = False, True, og, False, False
            ob_blk.cls, ob_blk.outer, ob_blk.ref, ob_blk.ref_name = ob.cls, ob.outer, None, None
            ob_blk.cls_name = 'Objective'
            ob_blk.values, ob_blk.tail, ob_blk.raw, ob_blk.fields = ov, None, None, None
            ob_blk.partial_properties = False
            q_blocks.append(ob_blk)
        qb.values['Status'] = 2
        qv = OrderedFields()
        qv['Status'] = 2
        qb_blk = model.Block()
        qb_blk.actor, qb_blk.rep, qb_blk.guid, qb_blk.stable, qb_blk.destroy = False, True, qg, False, False
        qb_blk.cls, qb_blk.outer, qb_blk.ref, qb_blk.ref_name = qb.cls, qb.outer, None, None
        qb_blk.cls_name = 'Quest'
        qb_blk.values, qb_blk.tail, qb_blk.raw, qb_blk.fields = qv, None, None, None
        qb_blk.partial_properties = False
        q_blocks.append(qb_blk)
        qch = getattr(session, 'quest_channel', controller.ch if controller else 2)
        qcls = session.chan_cls.get(qch, controller.cls if controller else 'BPPC_Adventure_PlayerController_C')
        out.append(_entry(session.world, qch, qcls, q_blocks))
        quest_meta = getattr(session, 'quest_meta', {}).get(qg)
        quest_obj_payload = {FTUE_HUNT_OBJECTIVE_GUID: {'Status': 2, 'CurrentAmount': 1}}

    if ids.get('acct') and ids.get('char') and os.environ.get('MPGS_ENCOUNTER_REWARDS', '1') == '1':
        from .initialization import _backend_headers
        hdrs = _backend_headers(ids.get('auth_token'))
        pl_tx = hashlib.sha256(('encounter-xp:%s:%s' % (seed, pl_track)).encode()).hexdigest()[:32].upper()
        w_tx = hashlib.sha256(('encounter-xp:%s:%s' % (seed, w_track[0] if w_track else '')).encode()).hexdigest()[:32].upper()

        def persist():
            try:
                ok, detail = rewards.post_transaction(tx_body, hdrs)
                print('ENCOUNTER loot grant %s -> %s' % (reward_items, detail), flush=True)
                if ok:
                    session._main_queue.append(('v2', tx_body))
            except Exception as exc:
                print('ENCOUNTER loot grant failed (%s: %s)' % (type(exc).__name__, str(exc)[:120]), flush=True)
            try:
                progression.grant_xp(ids['acct'], pl_track, xp_gain, hdrs, tx_id=pl_tx)
                if w_track and w_track[0] in curves:
                    progression.grant_xp(ids['acct'], w_track[0], xp_gain, hdrs, tx_id=w_tx)
                print('ENCOUNTER XP saved +%d (%s, %s)' % (xp_gain, pl_track, w_track[0] if w_track else 'no-weapon'), flush=True)
            except Exception as exc:
                print('ENCOUNTER XP save failed (%s: %s)' % (type(exc).__name__, str(exc)[:120]), flush=True)
            if quest_meta and os.environ.get('MPGS_QUEST_PERSIST', '1') == '1':
                try:
                    code = quests.persist_status(ids['acct'], ids['char'], quest_meta[0], quest_meta[1], 2,
                                                 hdrs, objectives=quest_obj_payload)
                    print('ENCOUNTER quest %s -> Redeemable (HTTP %s)' % (FTUE_HUNT_QUEST_ID, code), flush=True)
                except Exception as exc:
                    print('ENCOUNTER quest save failed (%s: %s)' % (type(exc).__name__, str(exc)[:120]), flush=True)

        threading.Thread(target=persist, daemon=True).start()
    return out


def handle_damage(session, ch, req_block, params=None):
    """Process a client DamageComponent.ServerTryDoDamage hit against the active encounter."""
    enc = getattr(session, '_encounter', None)
    if not enc or enc['dead']:
        return False
    if not enc['spotted']:
        reveal(session)
    # Reply with ClientDoDamageSucceed on the player's DamageComponent if a decoded PredictionKey is present.
    if isinstance(params, dict) and 'PredictionKey' in params and req_block is not None:
        session._queue_rpc(ch, req_block, 'ClientDoDamageSucceed', {'PredictionKey': params['PredictionKey']})
    damage = float(params.get('Damage', DEFAULT_HIT_DAMAGE)) if isinstance(params, dict) else DEFAULT_HIT_DAMAGE
    enc['health'] = max(0.0, enc['health'] - damage)
    director = enc.get('director')
    if director is not None:
        director.health.hits += 1                  # BehemothContext.pick_target: a hit acquires the attacker as target
        if enc['health'] <= 0.0:
            director.kill()
    cls = enc['cls']
    fi = session.world.schema.field_index(cls)
    ct_fi = session.world.schema.field_index('floating_combat_text_bpc_C')
    pawn_guid = _pawn_guid(session)
    impact = list(enc['loc'])
    if isinstance(params, dict) and isinstance(params.get('Payload.Hit'), dict):
        hit = params['Payload.Hit']
        impact = list(hit.get('ImpactPoint') or hit.get('Location') or impact)
    else:
        impact[2] += 80.0
    ct_elem = OrderedFields()
    ct_elem.update({
        'Entries[].SourceActor': ('obj', pawn_guid) if pawn_guid else ('obj', 0),
        'Entries[].CoreDamage': damage,
        'Entries[].PartDamage': damage,
        'Entries[].StaggerDamage': round(damage * 0.75, 1),
        'Entries[].ExposeDamage': 0.0,
        'Entries[].ShieldDamage': 0.0,
        'Entries[].ImpactLocation': impact,
        'Entries[].bHasImpactLocation': 1,
        'Entries[].bHitWasAbsorbed': 0,
        'Entries[].bIsCrit': 0,
        'Entries[].bIsAntiCrit': 0,
        'Entries[].bAlwaysShowDamageNumbers': 1,
        'Entries[].bBlockExposeCrit': 0,
        'Entries[].bShowWoundedStatus': 0,
    })
    blocks = [
        _block(cls, {'HealthDEPRECATED_DO_NOT_USE': enc['health']}),
        _block(cls, fields=[(fi.index(('func', 'UpdateHealth')), 'UpdateHealth', 'rpc', {})]),
        _block('floating_combat_text_bpc_C', fields=[
            (ct_fi.index(('func', 'MulticastSendMultitypeCombatText')),
             'MulticastSendMultitypeCombatText', 'rpc', {'Entries': [ct_elem]})],
            guid=enc['combat_text_guid']),
    ]
    dmg_entry = _entry(session.world, ENCOUNTER_CHANNEL, cls, blocks)
    if not enc.get('combat_text_exported') and enc.get('combat_text_export') is not None:
        dmg_entry.exports = [enc['combat_text_export']]
        enc['combat_text_exported'] = True
    changes = [dmg_entry]
    if enc.get('health_set') is not None:
        changes.append(_health_update(session, enc))   # the health bar reads the attribute set (engine path)
    if enc['health'] <= 0.0:
        enc['dead'] = True
        enc['in_combat'] = False
        enc['vel'] = [0.0, 0.0, 0.0]
        gs = next(e for e in session.entries if e.open and e.cls ==
                  session.world.island_contract['classes']['GameStateClass'][1])
        changes.append(_entry(session.world, ENCOUNTER_CHANNEL, cls, [
            _block(cls, {
                'IsDead': True,
                'InCombat': False,
                'IsAlerted': False,
                'bCanBeDamaged': False,
                'BehemothMusicState': 0,
                'ReplicatedMovement': {
                    'loc': list(enc['loc']),
                    'rot': list(enc['rot']),
                    'vel': [0.0, 0.0, 0.0],
                    'sleep': 0,
                    'phys': 0,
                },
            }),
            _block(cls, fields=[
                (fi.index(('func', 'MulticastSetBehemothCollision')), 'MulticastSetBehemothCollision', 'rpc', {'CollisionOn': 0}),
                (fi.index(('func', 'MultiDissolve')), 'MultiDissolve', 'rpc', {})])]))
        changes.append(_entry(session.world, gs.ch, gs.cls, [_block(gs.cls, {
            'BehemothState': 0,
            'SpawnedBehemoths': _array('SpawnedBehemoths', [])})]))
        changes.extend(grant_defeat_rewards(session))
        print('ENCOUNTER defeated guid=%d -> MultiDissolve + rewards' % enc['guid'], flush=True)
    else:
        print('ENCOUNTER hit guid=%d damage=%.0f health=%.0f/%.0f' % (
            enc['guid'], damage, enc['health'], enc['max_health']), flush=True)
    session.entries[session.pos:session.pos] = changes
    return True


