"""Build island join data from authored map exports and the supported player bootstrap.

The result is an offline-verified bootstrap, not a claim that encounters work.
"""
import copy
import json
from pathlib import Path

from . import template
from .repl.guid import flatten

CITY_MAP = '/Game/Maps/ramsgate/ramsgate_01_persistent'
CITY_TYPES = {
    'PlayerControllerClass': ('/Game/Character/Player/blueprints/player_controller_city_bp', 'player_controller_city_bp_C'),
    'PlayerStateClass': ('/Game/Character/Player/blueprints/player_state_bp', 'player_state_bp_C'),
    'GameStateClass': ('/Game/Blueprints/BPGS_Archon_GameState', 'BPGS_Archon_GameState_C'),
    'HUDClass': ('/Game/UI/HUD/BPH_ArchonCityHUD', 'BPH_ArchonCityHUD_C'),
}


def class_path(ref):
    package = ref['ObjectPath'].rsplit('.', 1)[0]
    name = ref['ObjectName'].split("'", 1)[1].rstrip("'")
    return package, name


def extract_contract(selection, source_dir):
    source_dir = Path(source_dir)
    sources = {}

    def read(package):
        path = source_dir / (package.rsplit('/', 1)[-1] + '.json')
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(data, list):
            raise ValueError(f'{path}: expected cooked export array')
        sources[path.name] = template.file_sha256(path)
        return data

    package = selection['map']
    exports = read(package)

    def local(ref):
        path, index = ref['ObjectPath'].rsplit('.', 1)
        if path != package:
            raise ValueError('Expected a reference within the selected map')
        return exports[int(index)]

    worlds = [x for x in exports if x['Type'] == 'World' and x.get('Package') == package]
    if len(worlds) != 1:
        raise ValueError('Map export does not contain the selected world')
    world = worlds[0]
    starts = []
    for actor in exports:
        if actor['Type'] != 'ArchonPlayerStart' or actor.get('Properties', {}).get('GroupName') != 'Island':
            continue
        props = actor['Properties']
        component = local(props['RootComponent'])['Properties']
        if component.get('AttachParent'):
            raise ValueError('Attached PlayerStart requires composing its parent transform')
        loc, rot = component['RelativeLocation'], component.get('RelativeRotation', {})
        starts.append({'name': actor['Name'], 'slot': props.get('SlotIndex', 0),
                       'location': [loc[k] for k in ('X', 'Y', 'Z')],
                       'rotation': [rot.get(k, 0.0) for k in ('Pitch', 'Yaw', 'Roll')]})
    if not starts or len({s['slot'] for s in starts}) != len(starts):
        raise ValueError('Map needs uniquely indexed Island PlayerStarts')
    levels = []
    for ref in world.get('StreamingLevels', []):
        obj = local(ref)
        props = obj['Properties']
        levels.append({'package': props['WorldAsset']['AssetPathName'].split('.', 1)[0],
                       'alwaysLoaded': obj['Type'] == 'LevelStreamingAlwaysLoaded',
                       'transform': props.get('LevelTransform')})
    settings = [x for x in exports if x['Type'] == 'WorldSettings']
    if len(settings) != 1:
        raise ValueError('Expected one WorldSettings actor')
    classes = {}
    package_mode, mode = selection['gameMode'].rsplit('.', 1)
    seen = set()
    while set(classes) != set(CITY_TYPES):
        if package_mode in seen:
            raise ValueError('Cyclic game mode inheritance')
        seen.add(package_mode)
        rows = read(package_mode)
        cdo = next(x for x in rows if x['Name'] == 'Default__' + mode)
        for key in CITY_TYPES:
            if key not in classes and cdo.get('Properties', {}).get(key):
                classes[key] = list(class_path(cdo['Properties'][key]))
        if set(classes) != set(CITY_TYPES):
            generated = next(x for x in rows if x['Name'] == mode)
            package_mode, mode = class_path(generated['Super'])
    # Validate the three replicated classes against their actual exported CDOs.
    for key in ('PlayerControllerClass', 'PlayerStateClass', 'GameStateClass'):
        pkg, cls = classes[key]
        if not any(x['Name'] == 'Default__' + cls for x in read(pkg)):
            raise ValueError(f'Missing class default object: {cls}')
    from . import island_arrival, island_atmosphere, island_encounter
    arrival = island_arrival.extract(exports, read)
    atmosphere = island_atmosphere.extract(exports, read)
    encounter = island_encounter.extract(selection, exports, read)
    return {'format': 'phoenix-island-contract', 'version': 1, 'huntId': selection['huntId'],
            'map': selection['map'], 'worldObject': world['Name'], 'gameMode': selection['gameMode'],
            'classes': classes, 'worldSettings': settings[0]['Name'],
            'starts': sorted(starts, key=lambda x: x['slot']), 'streamingLevels': levels,
            'sources': sources, 'arrival': arrival, 'atmosphere': atmosphere, 'encounter': encounter}


def build_world(base, contract, slot=0):
    world = copy.deepcopy(base)
    from .island_encounter import install_schema
    install_schema(world.schema, contract)
    spawn = next((s for s in contract['starts'] if s['slot'] == slot), None)
    if spawn is None:
        raise ValueError(f'No authored PlayerStart for slot {slot}')
    rename = {CITY_MAP: contract['map'], 'ramsgate_01_persistent': contract['worldObject'],
              'WorldSettings_1': contract['worldSettings'],
              '/Game/Blueprints/GameMode/BPGM_City': contract['gameMode'].rsplit('.', 1)[0],
              'BPGM_City_C': contract['gameMode'].rsplit('.', 1)[1]}
    classes = {}
    for key, (old_package, old_class) in CITY_TYPES.items():
        new_package, new_class = contract['classes'][key]
        rename.update({old_package: new_package, old_class: new_class,
                       'Default__' + old_class: 'Default__' + new_class})
        classes[old_class] = new_class
    world.ctx._names = {g: (o, rename.get(n, n)) for g, (o, n) in world.ctx._names.items()}
    streams = [x['package'] for x in contract['streamingLevels'] if x['alwaysLoaded']]
    found_streams = found_pawn = False
    for e in world.entries:
        old_cls = e.cls
        e.cls = classes.get(old_cls, old_cls)
        for root in e.exports:
            for info in flatten(root):
                if info.name in rename:
                    info.name = rename[info.name]
                    # A checksum from a different class must never survive a replacement.
                    info.flags, info.checksum = 1, None
        if e.parsed.open and old_cls in ('BP_PlayerCharacter_C', 'player_controller_city_bp_C'):
            e.parsed.open['loc'] = list(spawn['location'])
            e.parsed.open['rot'] = list(spawn['rotation'])
            found_pawn |= old_cls == 'BP_PlayerCharacter_C'
        for block in e.parsed.blocks:
            original = block.cls_name
            block.cls_name = classes.get(original, original)
            if original != block.cls_name:
                if (block.raw and block.raw[1]) or (block.tail and block.tail[1] and block.fields is None):
                    raise ValueError(f'Cannot translate opaque class data: {original}')
                fields = world.schema.field_index(block.cls_name)
                translated = []
                for _, name, kind, payload in block.fields or []:
                    field = ('prop' if kind == 'fast' else 'func', name)
                    if kind == 'raw':
                        raise ValueError(f'Cannot translate opaque field: {original}.{name}')
                    translated.append((fields.index(field), name, kind, payload))
                if block.fields is not None:
                    block.fields = translated
            if block.values is not None:
                if original == 'player_controller_city_bp_C' and 'SpawnLocation' in block.values:
                    block.values['SpawnLocation'] = list(spawn['location'])
                if original == 'player_state_bp_C':
                    block.values['InCity'] = False
                    block.values['PlayerStartSlot'] = slot
                if original == 'BPGS_Archon_GameState_C':
                    # EArchonInProgressGameSubState::OnHunt, verified in this build's enums.json.
                    block.values['InProgressGameSubState'] = 2
            for _, name, kind, payload in block.fields or []:
                if kind != 'rpc':
                    continue
                if name == 'ClientUpdateMultipleLevelsStreamingStatus':
                    key, items = next(iter(payload.items()))
                    if not items:
                        raise ValueError('Missing prototype for streaming status RPC')
                    result = []
                    for level in streams:
                        item = copy.deepcopy(items[0])
                        for k in item:
                            if k.endswith('PackageName'):
                                item[k] = (level, 0)
                            elif k.endswith(('bNewShouldBeLoaded', 'bNewShouldBeVisible')):
                                item[k] = 1
                            elif k.endswith('bNewShouldBlockOnLoad'):
                                item[k] = 0
                        result.append(item)
                    payload[key] = result
                    found_streams = True
                elif name == 'ClientSetRotation':
                    payload['NewRotation'] = list(spawn['rotation'])
    if not (found_streams and found_pawn):
        raise ValueError('Bootstrap lacks pawn spawn or streaming status')
    world.island_contract = contract
    if contract.get('arrival'):
        from .island_arrival import append_bootstrap
        append_bootstrap(world, contract, slot)
    if contract.get('atmosphere'):
        from .island_atmosphere import append_bootstrap
        append_bootstrap(world, contract)
    return world
