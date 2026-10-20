"""Generic server-side behemoth: behaviour tree + Brain + Mover for one actor, with no wire output (ENGINE_PORT_PLAN.md step 4).

    director = BehemothDirector(species, terrain, spawn_loc, spawn_yaw, player_state=lambda: session.pawn_state)
    for ev in director.tick(now): ...          # every server movement tick; step 5's adapter turns events into bunches

Events, in order, as plain tuples:
    ('move', loc, yaw, vel)                     the actor moved or turned (Mover.pop_changed, capped by send_interval)
    ('montage', name, play_rate, now)           start a montage (attack, reposition, or a tree-requested one)
    ('lock', target)                            Bp_Behemoth.LockTarget / ClearLockedTarget (None)
    ('state', state, added)                     behemoth_states_enum name index entered/left (9 = Combat)
    ('target', target)                          the tree's AttackTarget changed ('player' or None)
    ('brain', kind, payload, t)                 a Brain timeline event (damage windows, AoE spawns, ...)
    ('hit', action, damage_key, row)            the player stood in the attack area while that damage window was open

BehemothContext is Mystic-Paradox's RockContext made species-neutral (RockContext now subclasses it). Species data comes from
Species.load: the all-species action-set export plus the island contract's authored values. Nothing here is Rock-specific.
"""
import json
import math
import os
import random

from . import bt
from .brain import Brain, Target
from .mover import Mover, montage_curve

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.normpath(os.path.join(HERE, '..', '..'))
DEFAULT_MONTAGE_SECONDS = 2.5          # a montage with no root-motion curve to take its length from
TURN_SNAP_DEG = 25.0                   # turns up to this are set directly (no turn montage covers less than 45)
PLAYER_CAPSULE_RADIUS = 50.0           # BP_PlayerCharacter CollisionCylinder.CapsuleRadius (half height 100; matches the live root height)
AGENT_RADIUS_PCT = 1.1                 # UPathFollowingComponent MinAgentRadiusPct (PathFollowingComponent.cpp)
STOP_SPEED = 5.0                       # cm/s; below this the actor counts as standing (a zero-velocity stop is sent once)


def montage_package(name, montage_dir=None):
    """'/Game/Monsters/aq/lerawr/montages/<name>': the AnimMontage export's package in its AssetDump file, or None if not dumped."""
    from .mover import MONTAGE_DIR
    path = os.path.join(montage_dir or MONTAGE_DIR, name + '.json')
    if not os.path.exists(path):
        return None
    with open(path, encoding='utf-8') as f:
        exports = json.load(f)
    return next((e.get('Package') for e in exports if isinstance(e, dict) and e.get('Type') == 'AnimMontage'), None)


class BehemothContext(bt.Context):
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
        st = self.d.player_state()
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
        st = self.d.player_state()
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
        if self.d.mover.montage is None and not self.d.mover.path and self.d.mover.movement is None:
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
        if mv.montage is None and mv.movement is None:      # smooth walking orients rotation to movement instead
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
            self.d.mover.vx = self.d.mover.vy = 0.0          # PathFollowing OnPathFinished -> StopMovementKeepPathing
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

    # ---- hooks the generic tree nodes call (bt.Context) -----------------------------------------------------------
    def reach_radius(self, acceptable, key, include_agent=True, include_goal=True):
        """Live 2026-10-20: with only the 5 cm acceptance radius the Embermane walked into the player's capsule (52-67 cm apart)
        and the client's collision pushed it out against the server position. UE stops at 5 + 50 + 100 x 1.1 = 165 cm."""
        r = acceptable
        if include_goal and key and self.bb.get(key) == 'player':
            r += PLAYER_CAPSULE_RADIUS
        if include_agent:
            r += getattr(getattr(self.d, 'species', None), 'capsule_radius', 0.0) * AGENT_RADIUS_PCT   # RockDirector has no species
        return r

    def action_class(self, name):
        a = self.d.brain.actions.get(name) if name else None
        return a.get('class') if a else None

    def lock_target(self, key):
        lock = getattr(self.d, 'lock_target', None)
        if lock is not None:
            lock(self.bb.get(key))

    def clear_locked_target(self):
        lock = getattr(self.d, 'lock_target', None)
        if lock is not None:
            lock(None)


def _move_props(server, chain):
    """CharMoveComp properties along the Blueprint chain, base first so derived classes override (Bp_Behemoth, then the
    archetype: MaxAcceleration 4500, GroundFriction 8)."""
    props = {}
    for cls_name in reversed(chain):
        path = os.path.join(server, 'world', 'hunt_source', cls_name[:-2] + '.json' if cls_name.endswith('_C') else cls_name + '.json')
        if not os.path.exists(path):
            continue
        with open(path, encoding='utf-8') as f:
            for e in json.load(f):
                if isinstance(e, dict) and e.get('Name') == 'CharMoveComp':
                    props.update(e.get('Properties') or {})
    return props


def _capsule_radius(server, chain):
    """CollisionCylinder.CapsuleRadius from the Blueprint chain (most derived first; lerawr_beta_bp sets 100), from the local
    world/hunt_source dumps the island contract was extracted from. 0 when not found (reach tests then ignore the agent radius)."""
    for cls_name in chain:
        path = os.path.join(server, 'world', 'hunt_source', cls_name[:-2] + '.json' if cls_name.endswith('_C') else cls_name + '.json')
        if not os.path.exists(path):
            continue
        with open(path, encoding='utf-8') as f:
            for e in json.load(f):
                p = e.get('Properties') or {} if isinstance(e, dict) else {}
                if e.get('Name') == 'CollisionCylinder' and 'CapsuleRadius' in p:
                    return float(p['CapsuleRadius'])
    return 0.0


class Species:
    """Everything a director needs about one behemoth variant.

    actions        the action-set export (world/behemoths/<variant>.json), Brain's action shape
    tree           behaviour tree asset name (the Blueprint's BehaviorTree default)
    half_height    capsule offset: root height above the floor (contract capsuleOffset; CharacterMesh0 RelativeLocation.Z)
    walk_speed     authored walk speed; enter_combat_range: aggro_bpc EnterCombatRange; base_health: health at this level
    turn_montages  {degrees: (left, right)} turn-in-place montages; empty -> turns are set directly
    """

    def __init__(self, variant, actions, tree, half_height, walk_speed, enter_combat_range, base_health, turn_montages=None,
                 damage=None, capsule_radius=0.0, move_props=None):
        self.variant, self.actions, self.tree = variant, actions, tree
        self.capsule_radius = float(capsule_radius)
        self.move_props = dict(move_props or {})       # authored CharMoveComp values; empty -> the original direct walk     # CollisionCylinder.CapsuleRadius of the most derived class that sets it
        self.half_height, self.walk_speed = float(half_height), float(walk_speed)
        self.enter_combat_range, self.base_health = float(enter_combat_range), float(base_health)
        self.turn_montages = dict(turn_montages or {})
        self.damage = dict(damage or {})

    @classmethod
    def load(cls, hunt, server=SERVER):
        """From the committed island contract + engine manifest and the locally generated action set."""
        islands = os.path.join(server, 'data', '2.1.1', 'islands')
        with open(os.path.join(islands, hunt + '.engine_data.json'), encoding='utf-8') as f:
            manifest = json.load(f)
        with open(os.path.join(islands, hunt + '.contract.json'), encoding='utf-8') as f:
            enc = json.load(f)['encounter']
        behemoths = os.path.join(server, 'world', 'behemoths')
        with open(os.path.join(behemoths, manifest['variant'] + '.json'), encoding='utf-8') as f:
            actions = json.load(f)['actions']
        damage = {}
        damage_path = os.path.join(behemoths, manifest['variant'] + '_damage.json')   # tools.export_behemoth_damage
        if os.path.exists(damage_path):
            with open(damage_path, encoding='utf-8') as f:
                damage = json.load(f)['rows']
        return cls(manifest['variant'], actions, manifest['behaviourTree'], enc['capsuleOffset'], enc['walkSpeed'],
                   enc['enterCombatRange'], enc['baseHealth'], damage=damage,
                   capsule_radius=_capsule_radius(server, [row['name'] for row in enc['schema']]),
                   move_props=_move_props(server, [row['name'] for row in enc['schema']]))

    def brain_data(self):
        """Brain's rock.json shape; damage rows from <variant>_damage.json ({key: {'elements', 'tags'}}, empty if not exported)."""
        return {'health': {'normal': self.base_health},
                'variants': {'normal': {'actions': self.actions, 'damage': self.damage}}}


class BehemothDirector:
    """One behemoth. Owns its position (Mover on the terrain), decisions (behaviour tree + Brain) and lock/target state."""

    def __init__(self, species, terrain, loc, yaw, player_state, trees=None, rng=None, log=None, send_interval=1.0 / 30.0,
                 env=None):
        self.species = species
        self.terrain = terrain
        self.variant = 'normal'
        self.player_state = player_state          # () -> {'loc': [x, y, z], 'vel': [...]} | None (GenSession.pawn_state)
        self.rng = rng or random.Random()
        self._log = log or (lambda msg: None)
        self.send_interval = send_interval
        self.brain = Brain(species.brain_data(), 'normal', rng=self.rng)
        from .mover import Movement
        movement = Movement.from_props(species.move_props) if species.move_props else None
        # MaxWalkSpeed stays the species walk speed (contract: 400); the archetype's 4000 is the charge ceiling loco modes cap.
        self.mover = Mover(terrain, loc[:2], yaw, half_height=species.half_height, walk_speed=species.walk_speed,
                           movement=movement)
        self.mover.blockers = self._blockers
        self._ignore_pawn = False                  # an attack's ModifyPartCollisionChannelNotify window is open
        self.health = None                         # set by the damage layer (needs .hits); unused until step 5/6
        self.locked = None
        self.dead = False
        self._events = []
        self._tick_vel = [0.0, 0.0, 0.0]           # measured over the last tick (_measure)
        self._prev_tick = None
        self._sent_moving = False
        self._windows = {}                         # (action, damage key) -> damage row, while that damage window is open
        self._hit_in_window = set()
        self._target_seen = None
        self._states_seen = set()
        trees = trees or bt.TreeSet.load(os.environ.get('MPGS_BT_DIR') or os.path.join(SERVER, 'world', 'dumps', 'bt'))
        env = dict(env or {})
        env.setdefault('MPGS_ROCK_AGGRO_RANGE', str(species.enter_combat_range))
        self.ctx = BehemothContext(self, env)
        self.ctx.bb['EnterCombatRange'] = species.enter_combat_range
        self.runner = bt.Runner(trees, species.tree, self.ctx)

    # ---- what BehemothContext reads -----------------------------------------------------------------------------
    @property
    def loc(self):
        return self.mover.loc

    @property
    def yaw(self):
        return self.mover.yaw

    @yaw.setter
    def yaw(self, value):
        self.mover.yaw = value

    def log_ai(self, msg):
        self._log(msg)

    def attacks_allowed(self, now):
        return not self.dead

    def _target(self):
        st = self.player_state()
        if not st:
            return Target('player', (0.0, 0.0), alive=False), None
        return Target('player', tuple(st['loc'][:2]), tuple(st['vel'][:2])), None

    def lock_target(self, target):
        if target != self.locked:
            self.locked = target
            self._events.append(('lock', target))

    # ---- actions the tree asks for -----------------------------------------------------------------------------
    def _play(self, name, rate, now):
        curve = montage_curve(name)
        self._events.append(('montage', name, rate, now))
        if curve is not None and not self.mover.play(curve, now):
            self._log('t=%.2f %s: a montage is already moving the actor' % (now, name))
        return curve

    def start_attack(self, name, now, score=1.0, yaw=None):
        b = self.brain
        if name not in b.actions or b.busy or self.dead:
            return False
        if yaw is not None:
            self.mover.yaw = ((yaw + 180.0) % 360.0) - 180.0      # face the yaw the attack was scored at; root motion starts from it
        self._sync_brain()
        b.start(name, now, self._target()[0])
        self.mover.path = []
        self._flush_move(now, force=True)                       # the turned facing goes out before the montage starts from it
        self._play(b.actions[name]['montage'], b.actions[name].get('play_rate') or 1.0, now)
        self._log('t=%.2f attack %s' % (now, name))
        return True

    def play_montage(self, name, now):
        if not name:
            return None
        curve = self._play(name, 1.0, now)
        return now + (curve.duration if curve is not None else DEFAULT_MONTAGE_SECONDS)

    def face(self, point, now=None, animate=False):
        want = math.degrees(math.atan2(point[1] - self.loc[1], point[0] - self.loc[0]))
        delta = ((want - self.yaw + 180.0) % 360.0) - 180.0
        turns = self.species.turn_montages
        if animate and turns and now is not None and self.mover.montage is None and not self.brain.busy \
                and abs(delta) > TURN_SNAP_DEG:
            mag = min(turns, key=lambda m: abs(abs(delta) - m))
            end = self.play_montage(turns[mag][0 if delta < 0 else 1], now)
            if self.mover.montage is not None:
                return end
        self.mover.yaw = want
        return None

    # ---- simulation ----------------------------------------------------------------------------------------------
    def _sync_brain(self):
        self.brain.pos, self.brain.yaw = tuple(self.loc[:2]), self.mover.yaw

    def _flush_move(self, now, force=False):
        """Live 2026-10-20 idle rubber banding: the old velocity was the nominal walk speed whenever a path existed, also on the
        tick a patrol leg ended (16 u/s actual). Then no update followed while the actor stood at the point (up to 1.7 s), so the
        client extrapolated it forward at 400 u/s and snapped it back. The velocity is now the measured one, and a stop is always
        sent once, with zero velocity."""
        vel = self._velocity()
        moving = math.hypot(vel[0], vel[1]) > STOP_SPEED
        if self.mover.pop_changed(now, 0.0 if force else self.send_interval):
            self._events.append(('move', list(self.loc), self.mover.yaw, vel))
            self._sent_moving = moving
        elif self._sent_moving and not moving:
            self._events.append(('move', list(self.loc), self.mover.yaw, [0.0, 0.0, 0.0]))
            self._sent_moving = False

    def _velocity(self):
        """Measured over the last tick (cm/s); zero before the first one."""
        return list(self._tick_vel)

    def _measure(self, now):
        x, y = self.mover.x, self.mover.y
        if self._prev_tick is not None and now > self._prev_tick[0]:
            dt = now - self._prev_tick[0]
            self._tick_vel = [(x - self._prev_tick[1]) / dt, (y - self._prev_tick[2]) / dt, 0.0]
        self._prev_tick = (now, x, y)

    def tick(self, now):
        """Advance to `now` (seconds) and return the events since the last tick."""
        if not self.dead:
            self._sync_brain()
            self.runner.tick(now)
            self.mover.tick(now)
            self._measure(now)
            self._sync_brain()
            if self.brain.busy:
                action = self.brain.run.name
                for t, kind, payload in self.brain.tick(now) or ():
                    self._events.append(('brain', kind, payload, t))
                    if kind == 'damage_window_open':
                        for key in payload['keys']:
                            self._windows[(action, key)] = payload['rows'].get(key)
                    elif kind == 'damage_window_close':
                        for key in payload['keys']:
                            self._windows.pop((action, key), None)
                            self._hit_in_window.discard((action, key))
                    elif kind == 'part_collision_ignore_pawn':
                        self._ignore_pawn = True
                    elif kind == 'part_collision_restore':
                        self._ignore_pawn = False
                    elif kind == 'action_end':
                        self._ignore_pawn = False
                        self._windows.clear()
                        self._hit_in_window.clear()
            self._check_hits()
            tgt = self.ctx.bb.get('AttackTarget')
            if tgt != self._target_seen:
                self._target_seen = tgt
                self._events.append(('target', tgt))
            for s in self.ctx.states ^ self._states_seen:
                self._events.append(('state', s, s in self.ctx.states))
            self._states_seen = set(self.ctx.states)
            self._flush_move(now)
        out, self._events = self._events, []
        return out

    def _blockers(self):
        """The player's capsule (radius + this actor's) unless the current attack lets pawns through."""
        st = self.player_state()
        if not st or self._ignore_pawn or self.species.capsule_radius <= 0:
            return []
        return [(st['loc'][0], st['loc'][1], PLAYER_CAPSULE_RADIUS + self.species.capsule_radius)]

    def _check_hits(self):
        """While a damage window is open, the first tick the player stands in the action's attack area (Brain.in_area at the
        current, root-motion-moved transform) is a hit: ('hit', action, damage key, damage row). One hit per window and key.
        A stand-in for the native per-frame hit-volume sweep; the attack areas are the authored HitAreaBoxes/arcs."""
        if not self._windows:
            return
        target, _ = self._target()
        if not target.alive:
            return
        for (action, key), row in list(self._windows.items()):
            if (action, key) in self._hit_in_window or action not in self.brain.actions:
                continue
            if self.brain.in_area(action, target.pos):
                self._hit_in_window.add((action, key))
                self._events.append(('hit', action, key, row))

    def trace_line(self, now):
        """One diagnostic line (MPGS_BEHEMOTH_TRACE=1): where the actor is, what the tree runs, what it would attack and why not."""
        st = self.player_state()
        dist = math.dist(self.loc[:2], st['loc'][:2]) if st else -1.0
        bb = self.ctx.bb
        why = ''
        if bb.get('ChosenAttack') is None and st:
            target, _ = self._target()
            self._sync_brain()
            fails = {}
            for name in self.brain.actions:
                ok, reason = self.brain.available(name, now, self.brain.intensity([target]))
                fails[name] = reason if not ok else ('in area' if self.brain.in_area(name, target.pos) else 'out of area')
            why = ' why=' + ','.join('%s:%s' % (k, v) for k, v in fails.items())
        return ('TRACE t=%.2f pos=(%.0f,%.0f) yaw=%.0f dist=%.0f vel=%.0f rm=%s path=%d bt=%s target=%s chosen=%s score=%.1f '
                'busy=%s states=%s%s' % (now, self.loc[0], self.loc[1], self.yaw, dist, math.hypot(*self._tick_vel[:2]),
                                         self.mover.montage is not None, len(self.mover.path), '/'.join(self.runner.path()[-2:]),
                                         bb.get('AttackTarget'), bb.get('ChosenAttack'), bb.get('TopScore') or 0.0,
                                         self.brain.busy, sorted(self.ctx.states), why))

    def kill(self):
        self.dead = True
        self._windows.clear()
        self.ctx.bb['MonsterIsDead'] = True
