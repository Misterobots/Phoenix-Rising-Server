"""Run the game server: python run.py [--port 8790] [--template data/2.1.1/ramsgate_world.json]"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# Player data from the backend and the Ramsgate atmosphere are on by default.
os.environ.setdefault('MPGS_INITIAL_DATA', '1')
os.environ.setdefault('MPGS_RAMSGATE_ATMOSPHERE', '1')
os.environ.setdefault('MPGS_PLAYER_DATA', '1')
from mpgs.server import GameServer, log, MAP_PATH, GAME_MODE_PATH  # noqa: E402
from mpgs.genworld import GenWorld  # noqa: E402
from mpgs import hunts  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser()
ap.add_argument('--port', type=int, default=8790)
ap.add_argument('--template', default=os.path.join(HERE, 'data', '2.1.1', 'ramsgate_world.json'))
ap.add_argument('--list-hunts', action='store_true', help='list authored hunt IDs and map packages; does not start services')
ap.add_argument('--hunt', help='authored hunt ID; requires a matching island --template')
ap.add_argument('--hunt-map', help='map name or package for a hunt with multiple maps')
ap.add_argument('--inspect-hunt', action='store_true', help='print the selected authored hunt without starting services')
a = ap.parse_args()
if a.list_hunts:
    for hunt_id, row in sorted(hunts.load().items()):
        print(hunt_id + '\t' + ', '.join(m['package'] for m in row['maps']))
    sys.exit(0)
if (a.inspect_hunt or a.hunt_map) and not a.hunt:
    ap.error('--inspect-hunt and --hunt-map require --hunt')
selection = None
if a.hunt:
    try:
        selection = hunts.select(a.hunt, a.hunt_map)
    except ValueError as exc:
        ap.error(str(exc))
    if a.inspect_hunt:
        print(json.dumps(selection, indent=2))
        sys.exit(0)
world = GenWorld.from_template(a.template)
if selection:
    try:
        hunts.validate_world(selection, world)
    except ValueError as exc:
        ap.error(str(exc))
    os.environ['MPGS_RAMSGATE_ATMOSPHERE'] = '0'
elif getattr(world, 'island_contract', None):
    ap.error('Island world template requires --hunt')
log('world: %d entries from %s' % (len(world.events), a.template))
try:
    GameServer(a.port, world, selection['map'] if selection else MAP_PATH,
               selection['gameMode'] if selection else GAME_MODE_PATH,
               selection['huntId'] if selection else '').run()
except BaseException as exc:       # a log that ends without this line or a traceback means the process was killed
    import traceback
    log('game server stopping: %s' % type(exc).__name__)
    traceback.print_exc()
    raise
