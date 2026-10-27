"""Ally raptors: the repeater Q (dp_y_ability_spawn_raptor_ab) pets, run on the server from their own game data.

What the game does (atlas pseudo, AssetDump; read 2026-10-26):
- The ability (server, GameplayTask_SpawnActor with bSpawnOnlyOnAuthority) spawns RaptorPackSpawnQuantity (CDO 2; a talent
  makes it 3) raptors, each a random pick of araptor_00_bp / sraptor_00_bp / praptor_00_bp, at the right hand's
  'weapon_hook_righthSocket' facing the camera yaw, Instigator = the player. KillPrevSpawnedRaptors (CDO true) clears the
  previous pack first. AllyRaptorLifespanDuration (CDO) = 90 s. AbilityCooldown 60 s.
- The raptor runs af_raptor_bt (creature_ai_controller): with the CreatureTags.Ally tag it runs af_raptor_friendly_bt, which
  runs af_raptor_ally_combat_bt against its AttackTarget (ally_try_pick_new_target_bts: the nearest ArchonBehemoth).
- Attacks are perform_action_montage_bttask montages; their damage comes from the montage notifies: archon_damage_window
  (DamageKeys -> af_raptor_damage_table rows), spawn_aoe_class_bpn (aoe_raptor_slam_bp, DamageRadius) and spawn_gameplay_ns
  (BP_RockSpray lobbed at 800 cm forward, row RockSpray).

Stand-ins (labelled, not read from the game):
- CONTACT_REACH: a damage window hits when the behemoth's nearest body surface is this close to the raptor (the raptor's own
  physics bodies were not exported). AoE slams: the AoE centre is the raptor + HEAD_FORWARD. Rock spray: lands 800 cm ahead
  (ManualTargetting) and hits a body within ROCK_RADIUS.
- aoe_raptor_slam_bp's damage row is taken as MediumForwardAOE (the table's only AoE row); its class default was not read.
- Damage = the row's element total x creature curve(power delta 0) = x1.0; creature level 1 (no +3% per level).
- Movement: straight walks on the terrain (no navmesh), no root motion from the attack montages.
"""
import json
import math
import os
import random

from ..behemoth import bt
from ..behemoth.mover import Mover

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, '..', '..', 'world', 'creatures', 'raptor')
RAPTOR_PACKAGES = {
    'araptor_00_bp_C': '/Game/creatures/tp/raptor/araptor/blueprints/araptor_00_bp',
    'sraptor_00_bp_C': '/Game/creatures/tp/raptor/sraptor/blueprints/sraptor_00_bp',
    'praptor_00_bp_C': '/Game/creatures/tp/raptor/praptor/blueprints/praptor_00_bp',
    'uraptor_00_bp_C': '/Game/creatures/tp/raptor/uraptor/uraptor_00_bp',
}
PACK = ['araptor_00_bp_C', 'sraptor_00_bp_C', 'praptor_00_bp_C']   # the ability's MakeArray (the uraptor is the legendary's only)
PACK_SIZE = 2                             # dp_y_ability_spawn_raptor_ab CDO RaptorPackSpawnQuantity
LIFESPAN = 90.0                           # CDO AllyRaptorLifespanDuration
ULTRA_LIFESPAN = 300.0                    # legendary_weapon_part_spawn_raptor_bpc CDO AllyUltraRaptorLifespanDuration
HALF_HEIGHT, CAPSULE_RADIUS = 95.0, 60.0  # af_raptor_archetype_bp CollisionCylinder
CONTACT_REACH = 150.0                     # stand-in
HEAD_FORWARD = 150.0                      # stand-in
ROCK_RADIUS = 300.0                       # stand-in
ROCK_FORWARD = 800.0                      # spawn_gameplay_ns ManualTargetting X
AOE_ROW = 'MediumForwardAOE'              # stand-in (see above)


def load(path=DATA):
    if not os.path.isdir(os.path.join(path, 'bt')):
        return None
    out = {'trees': bt.TreeSet.load(os.path.join(path, 'bt'))}
    for name in ('montages', 'damage', 'loco'):
        with open(os.path.join(path, name + '.json'), encoding='utf-8') as f:
            out[name] = json.load(f)
    out['loco'] = {int(k): float(v) for k, v in out['loco'].items()}
    return out


def row_damage(row):
    return sum(float(row.get(k) or 0.0) for k in ('PhysicalDamage', 'FlameDamage', 'FrostDamage', 'ShockDamage', 'RadiantDamage',
                                                    'UmbralDamage', 'TerraDamage', 'RawElementalDamage', 'BaseDamage'))


class Behemoth:
    """What a raptor sees of the behemoth: () -> (loc, yaw, alive) and its physics bodies in actor space."""

    def __init__(self, state, bodies, capsule_radius=0.0):
        self.state = state
        self.capsule_radius = capsule_radius
        self.bodies = [(b, sh) for b, shapes in (bodies or {}).items() if isinstance(shapes, list) for sh in shapes]
        self.radius = max([math.hypot(sh['pos'][0], sh['pos'][1]) + sh['radius'] + sh.get('half_length', 0.0)
                           for _, sh in self.bodies] or [300.0])

    def world_bodies(self):
        loc, yaw, _ = self.state()
        c, s = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
        for bone, sh in self.bodies:
            x, y, z = sh['pos']
            yield bone, (loc[0] + x * c - y * s, loc[1] + x * s + y * c, loc[2] + z), sh['radius'] + sh.get('half_length', 0.0)

    def nearest(self, point):
        """-> (bone, horizontal surface distance) of the body closest to `point`, or (None, inf). 2D: the raptor's height on the
        terrain and the bodies' reference-pose heights are not compared (a small creature reaches up a behemoth's legs)."""
        best = (None, float('inf'))
        for bone, centre, r in self.world_bodies():
            d = math.dist(point[:2], centre[:2]) - r
            if d < best[1]:
                best = (bone, d)
        return best


class RaptorContext(bt.Context):
    def __init__(self, raptor):
        self.r = raptor
        self.bb = {'SelfActor': 'self', 'TetherActor': 'player', 'HasSpawnedIn': False}
        self.rng = raptor.rng
        self.creature_class = raptor.cls
        self._move = None

    def log(self, msg):
        self.r.log(msg)

    def actor_tags(self, key):
        return ('CreatureTags.Ally',) if self.bb.get(key) == 'self' else ()

    def target_pos(self, key):
        v = self.bb.get(key) if key not in ('SelfPos',) else 'self'
        if v == 'self':
            return tuple(self.r.loc)
        if v == 'player':
            st = self.r.owner_state()
            return tuple(st['loc']) if st else None
        if v == 'behemoth':
            loc, _, alive = self.r.behemoth.state()
            return tuple(loc) if alive else None
        return v if isinstance(v, tuple) else None

    def target_vel(self, key):
        return (0.0, 0.0)

    def behemoth_bounds(self, key, predict):
        if self.bb.get(key) != 'behemoth':
            return None
        loc, _, alive = self.r.behemoth.state()
        return (tuple(loc), self.r.behemoth.radius) if alive else None

    def pick_ally_target(self):
        _, _, alive = self.r.behemoth.state()
        self.bb['AttackTarget'] = 'behemoth' if alive else None

    def set_loco(self, index):
        self.r.mover.walk_speed = self.r.data['loco'].get(index, self.r.mover.walk_speed)

    def run_eqs(self, name, key, cfg):
        """The queries the raptor trees run, by asset name (generators/tests from their AssetDump; see module doc)."""
        me = self.r.loc
        bloc, _, alive = self.r.behemoth.state()
        n = (name or '').lower()
        if 'closest_behemoth' in n:                       # live_behemoths_generator, closest first
            self.bb[key] = 'behemoth' if alive else None
            return alive
        if 'findlocationaroundattacktarget' in n:         # OnCircle r 800 around AttackTarget; score: far from the other raptors
            tgt = self.target_pos('AttackTarget')
            if tgt is None:
                return False
            others = [o.loc for o in self.r.pack() if o is not self.r]
            pts = [(tgt[0] + 800.0 * math.cos(a), tgt[1] + 800.0 * math.sin(a), tgt[2])
                   for a in (i * 2 * math.pi / 12 for i in range(12))]
            self.bb[key] = max(pts, key=lambda p: min([math.dist(p[:2], o[:2]) for o in others] or [0.0])
                               - 0.1 * math.dist(p[:2], me[:2]))
            return True
        if 'combat_circle' in n:                          # donut 1250-1500 around TempActor (the behemoth), nearest the querier
            if not alive:
                return False
            a0 = math.atan2(me[1] - bloc[1], me[0] - bloc[0]) + self.rng.uniform(-0.6, 0.6)
            r = self.rng.uniform(1250.0, 1500.0)
            self.bb[key] = (bloc[0] + r * math.cos(a0), bloc[1] + r * math.sin(a0), bloc[2])
            return True
        if 'passive_location' in n:                       # donut around the tether (the player)
            st = self.r.owner_state()
            centre = tuple(st['loc']) if st else tuple(me)
            inner = float(cfg.get('Donut.InnerRadius') or 200.0)
            outer = float(cfg.get('Donut.OuterRadius') or 600.0)
            a, r = self.rng.uniform(0, 2 * math.pi), self.rng.uniform(inner, outer)
            self.bb[key] = (centre[0] + r * math.cos(a), centre[1] + r * math.sin(a), centre[2])
            return True
        self.log('EQS %s not modelled' % name)
        return False

    def reach_radius(self, acceptable, key, include_agent=True, include_goal=True):
        r = acceptable + (CAPSULE_RADIUS * 1.1 if include_agent else 0.0)
        if include_goal and self.bb.get(key) == 'behemoth':
            r += self.r.behemoth.capsule_radius      # UE's reach test adds the goal actor's collision radius
        return r

    def move_to(self, dest, radius, key=None):
        self._move = {'key': key, 'goal': dest, 'radius': radius}
        self.r.mover.walk_to([dest[:2]])
        return True

    def move_status(self):
        m = self._move
        if m is None:
            return bt.FAILED
        goal = (self.target_pos(m['key']) if m['key'] else None) or m['goal']
        if math.hypot(goal[0] - self.r.loc[0], goal[1] - self.r.loc[1]) <= max(m['radius'], 50.0):
            self.stop_moving()
            return bt.SUCCEEDED
        if not self.r.mover.path:
            self.r.mover.walk_to([goal[:2]])
        return bt.RUNNING

    def stop_moving(self):
        self._move = None
        self.r.mover.path = []

    def face(self, point):
        if point is not None:
            self.r.mover.yaw = math.degrees(math.atan2(point[1] - self.r.loc[1], point[0] - self.r.loc[0]))
        return None

    def play_montage(self, name):
        return self.r.play(name, self.now)


class Raptor:
    def __init__(self, data, cls, terrain, loc, yaw, owner_state, behemoth, pack, now, rng=None, log=None, lifespan=LIFESPAN):
        self.data, self.cls = data, cls
        self.owner_state, self.behemoth, self.pack = owner_state, behemoth, pack
        self.rng = rng or random.Random()
        self.log = log or (lambda m: None)
        self.mover = Mover(terrain, loc[:2], yaw, half_height=HALF_HEIGHT, walk_speed=data['loco'].get(0, 400.0))
        self.expires = now + lifespan
        self.events = []                  # ('montage', name, t) | ('hit', row name, bone, damage)
        self._notifies = []               # (time, event) of the playing montage
        self.ctx = RaptorContext(self)
        self.runner = bt.Runner(data['trees'], 'af_raptor_bt', self.ctx)
        self.ch = None                    # set by the encounter when spawned
        self.dead = False

    @property
    def loc(self):
        return self.mover.loc

    @property
    def yaw(self):
        return self.mover.yaw

    def play(self, name, now):
        m = self.data['montages'].get(name)
        self.events.append(('montage', name, now))
        from ..behemoth.mover import montage_curve
        curve = montage_curve(name)                      # the attack's root motion moves the raptor (world/dumps/rootmotion)
        if curve is not None:
            self.mover.path = []
            self.mover.montage = None
            self.mover.play(curve, now)
            self.root_motion = (name, (m or {}).get('package'), now)
        self._notifies = [(now + e['t'], e) for e in (m or {}).get('events', ())
                          if e['type'] in ('archon_damage_window_C', 'spawn_aoe_class_bpn_C', 'spawn_gameplay_ns_C')]
        return now + (m['length'] if m else 1.0)

    def _forward(self, dist):
        r = math.radians(self.yaw)
        return (self.loc[0] + dist * math.cos(r), self.loc[1] + dist * math.sin(r), self.loc[2])

    def _notify(self, ev):
        if ev['type'] == 'archon_damage_window_C':
            rows, centre, reach = ev.get('damage_keys') or [], self.loc, CONTACT_REACH
        elif ev['type'] == 'spawn_aoe_class_bpn_C':
            rows, centre, reach = [AOE_ROW], self._forward(HEAD_FORWARD), float(ev.get('DamageRadius') or 225.0)
        else:
            rows, centre, reach = ['RockSpray'], self._forward(ROCK_FORWARD), ROCK_RADIUS
        bone, d = self.behemoth.nearest(centre)
        if bone is None or d > reach:
            return
        for name in rows:
            row = self.data['damage'].get(name)
            if row:
                self.events.append(('hit', name, bone, row_damage(row)))

    def tick(self, now):
        if self.dead:
            return []
        self.runner.tick(now)
        self.mover.tick(now)
        due = [e for t, e in self._notifies if t <= now]
        self._notifies = [(t, e) for t, e in self._notifies if t > now]
        for ev in due:
            self._notify(ev)
        out, self.events = self.events, []
        return out
