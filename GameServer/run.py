"""Run the game server: python run.py [--port 8790] [--template data/2.1.1/ramsgate_world.json]"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# Player data from the backend and the Ramsgate atmosphere are on by default.
os.environ.setdefault('MPGS_INITIAL_DATA', '1')
os.environ.setdefault('MPGS_RAMSGATE_ATMOSPHERE', '1')
os.environ.setdefault('MPGS_PLAYER_DATA', '1')
from mpgs.server import GameServer, log  # noqa: E402
from mpgs.genworld import GenWorld  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser()
ap.add_argument('--port', type=int, default=8790)
ap.add_argument('--template', default=os.path.join(HERE, 'data', '2.1.1', 'ramsgate_world.json'))
a = ap.parse_args()
world = GenWorld.from_template(a.template)
log('world: %d entries from %s' % (len(world.events), a.template))
GameServer(a.port, world).run()
