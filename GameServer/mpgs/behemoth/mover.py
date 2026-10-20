"""Authoritative mover for a behemoth: the server owns the position, the client only follows ReplicatedMovement.

  curve = RootCurve.load(path)                       # per-sequence root motion, world-independent (cm, local frame)
  m = Mover(terrain, pos=(x, y), yaw=deg, half_height=250)
  m.walk_to(points)                                  # path from terrain.path(...)
  m.play(curve, now)                                 # an attack montage started at server time `now`
  m.tick(now)                                        # every server tick
  if m.pop_changed(): send spawn.movement_entry(ch, cls, m.loc, m.yaw)

Root motion is applied in the actor's frame at the start of the montage: the displacement is rotated by the yaw the
actor had when the montage began, and the yaw delta is added. The position is clamped to the ground and to walkable
quads; if the next step leaves walkable ground the montage stops at the last good position.

Constants come from the Bp_Behemoth class dump (MaxWalkSpeed 400, RotationRate.Yaw 720). The 30 Hz sample rate is
inferred: b_sq_rok_00_atk_fronttackle_fwd_00 has 116 keys and a 3.833 s montage length (115 / 30 = 3.833).
"""
import bisect
import json
import math

FPS = 30.0
WALK_SPEED = 400.0       # cm/s, Bp_Behemoth MaxWalkSpeed
YAW_RATE = 720.0         # deg/s, Bp_Behemoth RotationRate.Yaw
MOVE_EPS = 0.5           # cm / deg: below this a change is not worth a movement update
WAYPOINT_EPS = 50.0      # cm: a path point this close to the actor is already reached


def _lerp(a, b, t):
    return a + (b - a) * t


def _yaw_of(q):
    x, y, z, w = q
    return math.degrees(math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z)))


def _wrap(deg):
    return (deg + 180.0) % 360.0 - 180.0


class RootCurve:
    """Translation (cm) and yaw (deg) relative to the sequence start, sampled at FPS."""

    def __init__(self, frames, tkeys, rkeys):
        self.frames = frames
        self._tf = [k[0] for k in tkeys]
        self._tv = [k[1:4] for k in tkeys]
        self._rf = [k[0] for k in rkeys]
        self._ry = [_yaw_of(k[1:5]) for k in rkeys]
        self._ry0 = self._ry[0] if self._ry else 0.0

    @classmethod
    def load(cls, path):
        with open(path, encoding='utf-8') as f:
            d = json.load(f)
        if 'error' in d:
            raise ValueError(d['error'])
        return cls(d['frames'], d['keys'], d['rotation_keys'])

    @property
    def duration(self):
        last = max(self._tf[-1] if self._tf else 0, self._rf[-1] if self._rf else 0)
        return last / FPS

    def sample(self, t):
        """(dx, dy, dz, dyaw) relative to the start at time t seconds (clamped to the sequence).

        Relative to the start *transform*, as UE extracts root motion (inverse(start) * current): the first key's translation is
        subtracted and the rest rotated into the start's frame. Live 2026-10-20: b_aq_lerawr_00_atk_short_pounce_fwd_00's root
        track starts 911.8 cm forward; taken as absolute, the second segment of the Embermane's jump-over-turn montage teleported
        it ~900 units back mid-montage (the rubber banding). Rock sequences start at the origin, so they were unaffected."""
        f = min(max(t * FPS, 0.0), self._tf[-1] if self._tf else 0.0)
        x, y, z = self._interp_vec(f)
        x0, y0, z0 = self._interp_vec(0.0)
        r = math.radians(-self._ry0)
        px, py = x - x0, y - y0
        dx, dy, dz = px * math.cos(r) - py * math.sin(r), px * math.sin(r) + py * math.cos(r), z - z0
        dyaw = _wrap(self._interp_yaw(min(max(t * FPS, 0.0), self._rf[-1] if self._rf else 0.0)) - self._ry0)
        return dx, dy, dz, dyaw

    def _interp_vec(self, f):
        if not self._tf:
            return 0.0, 0.0, 0.0
        i = bisect.bisect_right(self._tf, f)
        if i == 0:
            v = self._tv[0]
            return v[0], v[1], v[2]
        if i >= len(self._tf):
            v = self._tv[-1]
            return v[0], v[1], v[2]
        f0, f1 = self._tf[i - 1], self._tf[i]
        t = (f - f0) / (f1 - f0) if f1 > f0 else 0.0
        a, b = self._tv[i - 1], self._tv[i]
        return _lerp(a[0], b[0], t), _lerp(a[1], b[1], t), _lerp(a[2], b[2], t)

    def _interp_yaw(self, f):
        if not self._rf:
            return 0.0
        i = bisect.bisect_right(self._rf, f)
        if i == 0:
            return self._ry[0]
        if i >= len(self._rf):
            return self._ry[-1]
        f0, f1 = self._rf[i - 1], self._rf[i]
        t = (f - f0) / (f1 - f0) if f1 > f0 else 0.0
        return self._ry[i - 1] + _wrap(self._ry[i] - self._ry[i - 1]) * t


class Mover:
    def __init__(self, terrain, pos, yaw, half_height=250.0, walk_speed=WALK_SPEED, yaw_rate=YAW_RATE):
        self.terrain = terrain
        self.x, self.y = float(pos[0]), float(pos[1])
        self.yaw = float(yaw)
        self.half_height = half_height
        self.walk_speed = walk_speed
        self.yaw_rate = yaw_rate
        self.z = self._ground_z(self.x, self.y)
        self.path = []                # remaining waypoints (world x, y)
        self.montage = None           # (curve, start_time, start_x, start_y, start_yaw)
        self.last_tick = None
        self._sent = (self.x, self.y, self.z, self.yaw)
        self._sent_at = None
        self.stopped_reason = None
        # () -> [(x, y, radius)]: capsules the actor may not move deeper into (CharacterMovement's sweep against pawns). Live
        # 2026-10-20: a Bite lunge carried the Embermane 52 cm from the player's centre; the client's collision pushed it back.
        self.blockers = None

    # ---- state ---------------------------------------------------------------------------------------------------
    @property
    def loc(self):
        return [self.x, self.y, self.z]

    @property
    def moving(self):
        return self.montage is not None or bool(self.path)

    def pop_changed(self, now=None, min_interval=0.0):
        """True once per change worth a movement update (position or yaw moved by more than MOVE_EPS).

        With `now` and `min_interval`, at most one update per interval: a change inside the interval stays pending and goes out
        on the first tick after it, so the final resting position is always sent. Live 2026-10-15: without a cap the server
        sent ~120 updates/s during a root-motion attack (one per server tick).
        """
        cur = (self.x, self.y, self.z, self.yaw)
        if max(abs(a - b) for a, b in zip(cur, self._sent)) <= MOVE_EPS:
            return False
        if now is not None and min_interval > 0 and self._sent_at is not None and now - self._sent_at < min_interval:
            return False
        self._sent = cur
        self._sent_at = now
        return True

    # ---- commands ------------------------------------------------------------------------------------------------
    def walk_to(self, points):
        """Leading points within WAYPOINT_EPS of the actor are dropped: the heading to a point the actor stands on is arbitrary.
        Live 2026-10-15: every re-path while chasing began with such a point and the Rock spun toward it and back."""
        pts = [tuple(p) for p in points]
        while pts and math.hypot(pts[0][0] - self.x, pts[0][1] - self.y) <= WAYPOINT_EPS:
            pts.pop(0)
        self.path = pts

    def play(self, curve, now):
        if self.montage is not None:
            return False
        self.path = []
        self.montage = (curve, now, self.x, self.y, self.yaw, self.z)
        self.stopped_reason = None
        return True

    # ---- simulation ----------------------------------------------------------------------------------------------
    def _blocked(self, nx, ny):
        """Moving to (nx, ny) would go deeper into a blocking capsule (moving out of or along one stays allowed)."""
        for bx, by, r in (self.blockers() if self.blockers else ()):
            new = math.hypot(nx - bx, ny - by)
            if new < r and new < math.hypot(self.x - bx, self.y - by):
                return True
        return False

    def tick(self, now):
        dt = 0.0 if self.last_tick is None else max(0.0, now - self.last_tick)
        self.last_tick = now
        if self.montage is not None:
            self._tick_montage(now)
        elif self.path:
            self._tick_walk(dt)
        return self

    def _tick_montage(self, now):
        curve, t0, sx, sy, syaw, sz = self.montage
        t = min(now - t0, curve.duration)
        dx, dy, _, dyaw = curve.sample(t)
        rad = math.radians(syaw)
        nx = sx + dx * math.cos(rad) - dy * math.sin(rad)
        ny = sy + dx * math.sin(rad) + dy * math.cos(rad)
        ground = self._ground_z(nx, ny)
        if ground is None or not self.terrain_walkable(nx, ny):
            self.montage = None
            self.stopped_reason = 'unwalkable'
            return
        if not self._blocked(nx, ny):
            self.x, self.y, self.z = nx, ny, ground
        self.yaw = _wrap(syaw + dyaw)
        if now - t0 >= curve.duration:
            self.montage = None

    def _tick_walk(self, dt):
        budget = self.walk_speed * dt
        turn_budget = self.yaw_rate * dt
        while self.path and budget > 0:
            tx, ty = self.path[0]
            want = math.degrees(math.atan2(ty - self.y, tx - self.x))
            dyaw = _wrap(want - self.yaw)
            if abs(dyaw) > turn_budget:
                self.yaw = _wrap(self.yaw + math.copysign(turn_budget, dyaw))
                return
            self.yaw = _wrap(want)
            d = math.hypot(tx - self.x, ty - self.y)
            if self._blocked(self.x + (tx - self.x) * min(1.0, budget / d) if d else tx,
                             self.y + (ty - self.y) * min(1.0, budget / d) if d else ty):
                break                                   # against a pawn: stay put this tick (the reach test ends the move)
            if d <= budget:
                if not self.terrain_walkable(tx, ty):
                    self.path = []
                    self.stopped_reason = 'unwalkable'
                    return
                self.x, self.y = tx, ty
                budget -= d
                self.path.pop(0)
            else:
                nx = self.x + (tx - self.x) * budget / d
                ny = self.y + (ty - self.y) * budget / d
                if not self.terrain_walkable(nx, ny):
                    self.path = []
                    self.stopped_reason = 'unwalkable'
                    return
                self.x, self.y = nx, ny
                budget = 0
            turn_budget = max(0.0, turn_budget - abs(dyaw))
        g = self._ground_z(self.x, self.y)
        if g is not None:
            self.z = g

    # ---- terrain -------------------------------------------------------------------------------------------------
    def _ground_z(self, x, y):
        if self.terrain is None:
            return 0.0
        g = self.terrain.ground_z(x, y)
        return None if g is None else g + self.half_height

    def terrain_walkable(self, x, y):
        return self.terrain is None or self.terrain.walkable(x, y)


# ---- montage -> root curve --------------------------------------------------------------------------------------

import os
import re

_ANIM_REF = re.compile(r'/anims/([A-Za-z0-9_]+)\.\d+')
# Local AssetDump output under GameServer/world/dumps (git-ignored game data); Mystic-Paradox kept it in ../_dumps.
ROOTMOTION_DIR = os.environ.get('MPGS_ROOTMOTION_DIR') or os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'world', 'dumps', 'rootmotion'))
MONTAGE_DIR = os.environ.get('MPGS_MONTAGE_DIR') or os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'world', 'dumps', 'montages'))


def sequences_for_montage(montage_json):
    """Animation sequence names a montage plays, in order of first reference in its dump (inferred = segment order)."""
    with open(montage_json, encoding='utf-8') as f:
        text = f.read()
    seen = []
    for name in _ANIM_REF.findall(text):
        if name not in seen:
            seen.append(name)
    return seen


def compose(curves):
    """One curve from consecutive segments: each segment's root motion starts where the previous one ended.

    Each segment is sampled at FPS; its displacement is rotated by the yaw accumulated before it.
    """
    keys_t, keys_r = [], []
    x = y = yaw = 0.0
    idx = 0
    for c in curves:
        n = max(1, int(round(c.duration * FPS)))
        for f in range(n):
            dx, dy, dz, dyaw = c.sample(f / FPS)
            rad = math.radians(yaw)
            keys_t.append([idx, x + dx * math.cos(rad) - dy * math.sin(rad), y + dx * math.sin(rad) + dy * math.cos(rad), dz])
            a = math.radians(_wrap(yaw + dyaw)) / 2
            keys_r.append([idx, 0.0, 0.0, math.sin(a), math.cos(a)])
            idx += 1
        dx, dy, dz, dyaw = c.sample(c.duration)
        rad = math.radians(yaw)
        x, y = x + dx * math.cos(rad) - dy * math.sin(rad), y + dx * math.sin(rad) + dy * math.cos(rad)
        yaw = _wrap(yaw + dyaw)
    keys_t.append([idx, x, y, 0.0])
    a = math.radians(yaw) / 2
    keys_r.append([idx, 0.0, 0.0, math.sin(a), math.cos(a)])
    return RootCurve(idx, keys_t, keys_r)


def montage_curve(montage_name, montage_dir=None, rootmotion_dir=None):
    """RootCurve for a montage, or None when any segment has no usable root motion (the montage then stays in place)."""
    montage_dir = montage_dir or MONTAGE_DIR
    rootmotion_dir = rootmotion_dir or ROOTMOTION_DIR
    mpath = os.path.join(montage_dir, montage_name + '.json')
    if not os.path.exists(mpath):
        return None
    curves = []
    for seq in sequences_for_montage(mpath):
        p = os.path.join(rootmotion_dir, seq + '.rootmotion.json')
        if not os.path.exists(p):
            return None
        try:
            curves.append(RootCurve.load(p))
        except ValueError:
            return None
    if not curves:
        return None
    return curves[0] if len(curves) == 1 else compose(curves)
