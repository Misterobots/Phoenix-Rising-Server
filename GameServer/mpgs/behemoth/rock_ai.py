"""The Rock's real behaviour tree (rock_bt + shared sub-trees) driving a RockDirector. Enabled with MPGS_ROCK_BT=1 (needs MPGS_MOVER=1).

The tree decides; the director does: attacks (brain.py scoring + montage RPC + root motion), plain montages (patrol, rage,
death), walking along terrain paths, facing. Inferred values (no data read yet), all overridable:
  MPGS_ROCK_AGGRO_RANGE=2500     the player becomes the target inside this distance (or once they hit the Rock)
  MPGS_ROCK_DEAGGRO_RANGE=8000   the target is dropped beyond this distance
  MPGS_ROCK_BT_DIR               folder with rock_bt.json and the shared_*_bt.json sub-trees (default <repo>/_dumps/bt)
Montages whose name is not a Rock montage (the tree references one Shrike montage on an alpha-only branch) are skipped.
"""
import os

from . import bt
from .director import BehemothContext

# Local AssetDump output under GameServer/world/dumps (git-ignored game data); Mystic-Paradox kept it in ../_dumps.
DEFAULT_BT_DIR = os.environ.get('MPGS_BT_DIR') or os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'world', 'dumps', 'bt'))
DEFAULT_MONTAGE_SECONDS = 2.5          # used when a montage has no root-motion curve to take its length from


class RockContext(BehemothContext):
    """The Rock's context: the generic behemoth context (director.BehemothContext), unchanged in behaviour."""


class RockAI:
    def __init__(self, director, env):
        folder = env.get('MPGS_ROCK_BT_DIR') or DEFAULT_BT_DIR
        self.trees = bt.TreeSet.load(folder)
        if 'rock_bt' not in self.trees.roots:
            raise FileNotFoundError('rock_bt.json not found in %s' % folder)
        self.ctx = RockContext(director, env)
        self.runner = bt.Runner(self.trees, 'rock_bt', self.ctx)

    def tick(self, now):
        self.runner.tick(now)

    def on_death(self):
        self.ctx.bb['MonsterIsDead'] = True
