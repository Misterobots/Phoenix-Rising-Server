"""Authored hunt lookup. Selection never invents a map or a default game mode."""
import json
from pathlib import Path

CATALOG = Path(__file__).resolve().parents[1] / 'data/2.1.1/hunts.json'


def load(path=CATALOG):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if (data.get('format'), data.get('version'), data.get('build')) != ('phoenix-hunts', 1, '2.1.1'):
        raise ValueError('Unsupported hunt catalog format or build')
    return data['hunts']


def select(hunt_id, map_name=None, catalog=None):
    catalog = load() if catalog is None else catalog
    if hunt_id not in catalog:
        raise ValueError(f'Unknown hunt: {hunt_id}')
    hunt = catalog[hunt_id]
    # This is explicit selection, not a weighted random draw. Exploration rows
    # contain a single authored map with Weighting=0; zero is not an unlock flag.
    maps = [m for m in hunt['maps'] if
            map_name is None or m['name'] == map_name or m['package'] == map_name]
    if not maps:
        raise ValueError(f'{hunt_id}: no eligible authored map for {map_name!r}')
    if len(maps) != 1:
        raise ValueError(f'{hunt_id}: choose one map explicitly: ' + ', '.join(m['package'] for m in maps))
    if not hunt['gameModeOverride']:
        raise ValueError(f'{hunt_id}: default game mode is unresolved; no override in the authored table')
    return {'huntId': hunt_id, 'map': maps[0]['package'], 'gameMode': hunt['gameModeOverride'],
            'mapDefinition': maps[0]['definition'], 'islandSpec': hunt['islandSpec'],
            'definition': hunt['definition']}


def validate_world(selection, world):
    """Reject a city recording passed off as an island world before opening UDP."""
    contract = getattr(world, 'island_contract', None)
    if contract and any(contract.get(k) != selection[k] for k in ('huntId', 'map', 'gameMode')):
        raise ValueError('World contract does not match selected hunt')
    names = {name for _, name in world.ctx._names.values()}
    package, cls = selection['gameMode'].rsplit('.', 1)
    required = {selection['map'], package, cls}
    missing = sorted(required - names)
    if missing:
        raise ValueError('World template does not describe the selected hunt; missing references: ' + ', '.join(missing))
    if '/Game/Maps/ramsgate/ramsgate_01_persistent' in names:
        raise ValueError('Hunt template still contains the Ramsgate persistent world')
    contract = getattr(world, 'island_contract', None)
    if isinstance(contract, dict):
        if (contract.get('huntId'), contract.get('map'), contract.get('gameMode')) != (
                selection['huntId'], selection['map'], selection['gameMode']):
            raise ValueError('World template islandContract does not match the selected hunt')
