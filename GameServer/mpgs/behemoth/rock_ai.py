"""The Rock's real behaviour tree (rock_bt + shared sub-trees) driving a RockDirector. Enabled with MPGS_ROCK_BT=1 (needs MPGS_MOVER=1).

The tree decides; the director does: attacks (brain.py scoring + montage RPC + root motion), plain montages (patrol, rage,
death), walking along terrain paths, facing. Inferred values (no data read yet), all overridable:
  MPGS_ROCK_AGGRO_RANGE=2500     the player becomes the target inside this distance (or once they hit the Rock)
  MPGS_ROCK_DEAGGRO_RANGE=8000   the target is dropped beyond this distance
  MPGS_ROCK_BT_DIR               folder with rock_bt.json and the shared_*_bt.json sub-trees (default <repo>/_dumps/bt)
Montages whose name is not a Rock montage (the tree references one Shrike montage on an alpha-only branch) are skipped.
"""
import math
import os

from . import bt

# Local AssetDump output under GameServer/world/dumps (git-ignored game data); Mystic-Paradox kept it in ../_dumps.
DEFAULT_BT_DIR = os.environ.get('MPGS_BT_DIR') or os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'world', 'dumps', 'bt'))
DEFAULT_MONTAGE_SECONDS = 2.5          # used when a montage has no root-motion curve to take its length from


class RockContext(bt.Context):
    def __init__(self, director, env):
        self.d = director
        self.bb = {'EnterCombatRange': 2000.0, 'PatrolCenter': tuple(director.loc), 'StaggerCount': 0}
        self.states = set()
        self.variant = 'Alpha' if director.variant == 'alpha' else 'Normal'
        self.island_mode = 'Hunt'
        self.rng = director.brain.rng
        self.aggro_range = float(env.get('MPGS_ROCK_AGGRO_RANGE', '2500'))
        self.deaggro_range = float(env.get('MPGS_ROCK_DEAGGRO_RANGE', '8000'))
        self._move = None              # (dest, radius)
        self._chosen = None            # (action name, facing it was scored at), from the last choose_action

    def log(self, msg):
        self.d.log_ai('t=%.2f %s' % (self.now, msg))

    # ---- positions -----------------------------------------------------------------------------------------------
    def _player(self):
        st = self.d.s.pawn_state
        return (st['loc'][0], st['loc'][1], st['loc'][2]) if st else None

    def target_pos(self, key):
        if key == 'SelfPos':
            return tuple(self.d.loc)
        if key == 'FrontPoint':
            r = math.radians(self.d.yaw)
            return (self.d.loc[0] + 100.0 * math.cos(r), self.d.loc[1] + 100.0 * math.sin(r), self.d.loc[2])
        v = self.bb.get(key)
        if v == 'player':
            return self._player()
        return v if isinstance(v, tuple) else None

    def target_vel(self, key):
        st = self.d.s.pawn_state
        if self.bb.get(key) == 'player' and st:
            return (st['vel'][0], st['vel'][1])
        return (0.0, 0.0)

    # ---- services ------------------------------------------------------------------------------------------------
    def pick_target(self):
        p = self._player()
        if p is None:
            self.bb['AttackTarget'] = None
            return
        dist = math.hypot(p[0] - self.d.loc[0], p[1] - self.d.loc[1])
        hit = self.d.health is not None and self.d.health.hits > 0
        if self.bb.get('AttackTarget') == 'player':
            if dist > self.deaggro_range:
                self.bb['AttackTarget'] = None
                self.log('target lost (%.0f units away)' % dist)
        elif dist <= self.aggro_range or hit:
            self.bb['AttackTarget'] = 'player'
            self.log('target acquired (%.0f units away%s)' % (dist, ', after a hit' if hit else ''))

    def choose_action(self):
        if self.bb.get('AttackTarget') != 'player' or not self.d.attacks_allowed(self.now):
            return None
        target, _ = self.d._target()
        c = self.d.brain.choose(self.now, [target])
        self._chosen = (c[0], self.d.brain.chosen_yaw) if c else None
        return (c[0], c[1]) if c else None

    # ---- actions -------------------------------------------------------------------------------------------------
    def perform_action(self, name):
        yaw = self._chosen[1] if self._chosen and self._chosen[0] == name else None
        return self.d.start_attack(name, self.now, yaw=yaw)

    def action_running(self):
        return self.d.brain.busy

    def play_montage(self, name):
        return self.d.play_montage(name, self.now)

    def move_to(self, dest, radius, key=None):
        """Walk along a terrain path. With a key (e.g. AttackTarget) the goal follows that key and is re-pathed as it moves."""
        if not self._path_to(dest):
            return False
        self._move = {'key': key, 'goal': dest, 'radius': radius, 'repath_at': self.now + 1.0}
        return True

    def _path_to(self, dest):
        path = self.d.terrain.path((self.d.loc[0], self.d.loc[1]), (dest[0], dest[1]))
        if not path:
            self.log('no path to (%.0f, %.0f)' % (dest[0], dest[1]))
            return False
        if self.d.mover.montage is None and not self.d.mover.path:
            # starting from standing: a big heading change is a stepping turn first (the walk follows when it ends)
            nxt = next((p for p in path if math.hypot(p[0] - self.d.loc[0], p[1] - self.d.loc[1]) > 50.0), None)
            if nxt is not None:
                self.d.face(nxt, self.now, animate=True)
        self.d.mover.walk_to(path)
        return True

    def move_status(self):
        m = self._move
        if m is None:
            return bt.FAILED
        goal = (self.target_pos(m['key']) if m['key'] else None) or m['goal']
        if math.hypot(goal[0] - self.d.loc[0], goal[1] - self.d.loc[1]) <= max(m['radius'], 50.0):
            self.stop_moving()
            return bt.SUCCEEDED
        if m['key'] and self.now >= m['repath_at'] and math.hypot(goal[0] - m['goal'][0], goal[1] - m['goal'][1]) > 200.0:
            m['goal'], m['repath_at'] = goal, self.now + 1.0
            if not self._path_to(goal):
                self._move = None
                return bt.FAILED
        mv = self.d.mover
        if not mv.path:
            self._move = None
            return bt.FAILED
        if mv.montage is None:
            # still facing well away from the next leg (e.g. the target moved during a turn): another stepping turn
            p = mv.path[0]
            want = math.degrees(math.atan2(p[1] - mv.y, p[0] - mv.x))
            if abs(((want - mv.yaw + 180.0) % 360.0) - 180.0) > 45.0:
                path = list(mv.path)
                self.d.face(p, self.now, animate=True)      # a turn montage clears the path; the walk resumes after it
                mv.walk_to(path)
        return bt.RUNNING

    def stop_moving(self):
        if self.d.mover is not None:
            self.d.mover.path = []
        self._move = None

    def patrol_point(self, inner, outer):
        cx, cy = self.bb['PatrolCenter'][0], self.bb['PatrolCenter'][1]
        for _ in range(12):
            a = self.rng.uniform(0.0, 2.0 * math.pi)
            r = self.rng.uniform(inner, outer)
            x, y = cx + r * math.cos(a), cy + r * math.sin(a)
            z = self.d.terrain.ground_z(x, y)
            if z is not None and self.d.terrain.walkable(x, y):
                return (x, y, z)
        return None

    def face(self, point):
        """-> the time the turn montage ends (the pivot task waits for it), or None when the yaw was set directly."""
        return self.d.face(point, self.now, animate=True)

    def set_state(self, s, add=True):
        if (s in self.states) != add:
            self.log('state %s %d' % ('+' if add else '-', s))
        (self.states.add if add else self.states.discard)(s)
        if s == 9 and add:
            self.bb.setdefault('CurrentFightStateStartTime', self.now)


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
