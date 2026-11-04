"""Behemoth decision loop for one actor, following docs/BEHEMOTH_SPEC_ROCK_2026-10-14.md (sections 4, 6a-6c).

What is modelled (traced from the cooked Blueprints):
  - evaluate_action_btservice: the ForceAction override (score 99999), else the strictly highest Evaluate() score wins.
  - IsActionAvailable: not on cooldown, BaseValue > 0, threat intensity within the row's range, required states all
    active, excluded states none active, classification / part requirements.
  - ScoreTargetArray: sum(target score * BaseValue * AttackWeighting * RuntimeWeightingPercent), blended with a
    random factor by RandomWeightPercent.
  - Targeting: arc (eval type 1) or box (eval type 0) hit areas tested against each target's predicted position.
  - Cooldowns: UsesLeft, OnCooldown, Cooldown + multi-behemoth delta + uniform(+-variance).
  - Montage timelines: notify times from the montage, damage windows with their DamageKeys and the damage-table row.

What is NOT modelled (see the spec): the 10-degree candidate-position search around a target, the navmesh path test,
the native GetAttackTargetScore (a threat-weighted stand-in is used, flagged below), movement, rage entry/exit
triggers (states are set by the caller), part breaking and stagger, hit detection against player bodies.
"""
import json
import math
import os
import random
from dataclasses import dataclass

from . import geometry
from .helpers import ActionHelper, place

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROCK = os.path.join(HERE, 'world', 'behemoths', 'rock.json')

# behemoth_states_enum ids used by the rules (names come from the export's 'states' table)
RAGE, COMBAT, CUSTOM_STATE = 0, 9, 23

FORCE_SCORE = 99999.0
INTENSITY_BOUNDS = (7.0, 15.0)   # threat as % of max health: <7 -> 0, <15 -> 1, else 2 (threat_tracking_bpc.GetIntensityLevel)
THREAT_WEIGHT_PER_PERCENT = 0.1  # threat_tracking_bpc default TargettingWeightingPer1PercentOfMaxHPInThreat
NAV_PATH_FRACTION = 0.9          # Evaluate: a path hitting the navmesh before 90% of its length rejects the action
SIMPLIFY_EPSILON = 100.0         # BP_BehemothAction init: AttackVectorsN_Simple = SimplifyVectors(AttackVectorsN, 100)


def simplify_vectors(points, epsilon=SIMPLIFY_EPSILON):
    """BP_BehemothAction_C.SimplifyVectors as shipped (native runs matched 538/538 dumped arrays, 2026-11-03).
    ClosestPointToLine is the 2D distance to the infinite line, a zero-length line gives 0 (Divide_DoubleDouble), only
    interior points are candidates, and both recursion halves are points[0..split]: points after a split are dropped."""
    pts = [tuple(p) for p in points]
    start = pts[0] if pts else (0.0, 0.0, 0.0)
    end = pts[-1] if pts else (0.0, 0.0, 0.0)
    best, index = 0.0, 0
    length = math.hypot(end[0] - start[0], end[1] - start[1])
    for i in range(1, len(pts) - 1):
        x, y = pts[i][0], pts[i][1]
        d = abs((end[1] - start[1]) * x - (end[0] - start[0]) * y + end[0] * start[1] - end[1] * start[0]) / length             if length else 0.0
        if d > best:
            best, index = d, i
    if best > epsilon:
        before = simplify_vectors(pts[:index + 1], epsilon)
        return before[:-1] + before
    return [start, end]


def load_rock(path=ROCK):
    with open(path) as f:
        return json.load(f)


@dataclass
class Target:
    id: str
    pos: tuple
    vel: tuple = (0.0, 0.0)
    threat: float = 0.0
    alive: bool = True


@dataclass
class ActionRun:
    name: str
    start: float
    end: float
    events: list            # (absolute time, kind, payload), ascending
    target: str = None
    emitted: int = 0


@dataclass
class _Cooldown:
    uses_left: int
    on_cooldown: bool = False
    ready_at: float = 0.0


class Brain:
    def __init__(self, data, variant='normal', classification=None, rng=None, scale=1.0,
                 random_weight_percent=0.0, random_weight_max=2.0, attack_weighting=1.0, runtime_weighting=1.0):
        self.data = data
        self.variant = variant
        self.actions = data['variants'][variant]['actions']
        self.damage = data['variants'][variant]['damage']
        self.classification = classification or {'normal': 'Normal', 'alpha': 'Alpha'}.get(variant, variant)
        self.max_health = data['health']['normal']
        self.rng = rng or random.Random()
        self.scale = scale
        self.random_weight_percent = random_weight_percent    # not set in the base defaults: 0 unless the BP sets it
        self.random_weight_max = random_weight_max            # base default 2.0
        self.attack_weighting = attack_weighting              # base default 1.0, no Rock attack overrides it
        self.runtime_weighting = runtime_weighting            # base default 1.0
        self.states = {COMBAT}
        self.part_state = {}
        self.pos, self.yaw = (0.0, 0.0), 0.0
        self.yaw_sweep = 0.0          # degrees; see choose()
        self.chosen_yaw = 0.0
        self.multiple_behemoths = False
        self.force_action = None
        self.run = None
        self._cd = {n: _Cooldown(a['usage_count']) for n, a in self.actions.items()}
        # Navigation: path_query(world points) -> {hit, found, percentage} (DetourNavMesh.path_query); None without navmesh
        # data. z is the root (capsule centre) height; behemoth_scale is GetBehemothScale_interface (mesh component scale).
        self.path_query = None
        self.z = 0.0
        self.behemoth_scale = (1.0, 1.0, 1.0)
        self._simple = {n: [simplify_vectors(a['attack'].get(k) or [])
                            for k in ('attack_vectors1', 'attack_vectors2')]
                        for n, a in self.actions.items() if a.get('attack')}
        # Helpers (helpers.py): one per action with an OptimalHitArea and CreateHelper (BP_BehemothAction_C default true).
        # ground_z(x, y) stands in for the helper ground trace; target_z is the attack target's actor height.
        self.helpers = {n: ActionHelper(n) for n, a in self.actions.items()
                        if a.get('attack') and a['attack'].get('optimal') and a['attack'].get('create_helper', True) is not False}
        self.ground_z = lambda x, y: None
        self.target_z = 0.0
        self.severed = set()          # severed part names; part severing is not modelled, so this stays empty
        self.last_targets = []

    # --- state ---------------------------------------------------------------------------------------------------
    def set_state(self, state, on=True):
        (self.states.add if on else self.states.discard)(state)

    def intensity(self, targets):
        pct = sum(t.threat for t in targets) / self.max_health * 100.0
        return 0 if pct < INTENSITY_BOUNDS[0] else 1 if pct < INTENSITY_BOUNDS[1] else 2

    def _update_cooldowns(self, now):
        """EndCooldown timer: clears OnCooldown and resets UsesLeft to UsageCount_Base."""
        for n, c in self._cd.items():
            if c.on_cooldown and now >= c.ready_at:
                c.on_cooldown, c.uses_left = False, self.actions[n]['usage_count']

    def on_cooldown(self, name, now):
        self._update_cooldowns(now)
        return self._cd[name].on_cooldown

    # --- availability and scoring ----------------------------------------------------------------------------------
    def available(self, name, now, intensity):
        a, atk = self.actions[name], self.actions[name]['attack']
        self._update_cooldowns(now)
        if self._cd[name].on_cooldown:
            return False, 'on cooldown'
        if not a['base_value'] > 0:
            return False, 'base value'
        if not a['min_intensity'] <= intensity <= a['max_intensity']:
            return False, 'intensity'
        if any(s not in self.states for s in atk['require_states']):
            return False, 'required state'
        if any(s in self.states for s in atk['excluded_states']):
            return False, 'excluded state'
        if atk.get('classification') and atk['classification'] != self.classification:
            return False, 'classification'
        pr = atk.get('part_requirements') or {}
        for part in pr.get('PartNamesToCheck') or []:
            want = [int(str(s).rsplit('NewEnumerator', 1)[-1]) for s in pr.get('PartStateRequirements') or []]
            if want and self.part_state.get(part) not in want:
                return False, 'part ' + part
        return True, 'ok'

    def _target_score(self, t):
        """STAND-IN for the native ArchonCharacter.GetAttackTargetScore: 1.0 scaled by the threat weighting
        (1 + threat% of max health * 0.1) that threat_tracking_bpc.GetWeightingForActor computes."""
        return 1.0 + (t.threat / self.max_health * 100.0) * THREAT_WEIGHT_PER_PERCENT

    def in_area(self, name, point):
        atk = self.actions[name]['attack']
        if atk['eval_type'] == 1:
            return any(geometry.in_arc(self.pos, self.yaw, point, a, self.scale) for a in atk['arcs'])
        return any(geometry.in_box(self.pos, self.yaw, point, b, self.scale) for b in atk['boxes'])

    def targets_hit(self, name, targets, predict_time=0.0):
        hit = []
        for t in targets:
            if not t.alive:
                continue
            p = (t.pos[0] + t.vel[0] * predict_time, t.pos[1] + t.vel[1] * predict_time)
            if self.in_area(name, p):
                hit.append(t)
        return hit

    def scaling_factor(self, name):
        """GetScalingFactor: DebugScale when set, else the behemoth scale when ScaleHitAreaToBehemothSize, else 1.
        Unset properties take the BP_BehemothAction_C defaults (ScaleHitAreaToBehemothSize true, DebugScale 0)."""
        atk = self.actions[name]['attack']
        debug = atk.get('debug_scale') or 0.0
        if abs(debug) > 1e-6:
            return (debug, debug, debug)
        return self.behemoth_scale if atk.get('scale_hit_area', True) is not False else (1.0, 1.0, 1.0)

    def nav_rejected(self, name):
        """Evaluate's PathHitsNavMesh test from the behemoth root."""
        return self.paths_rejected(name, self.pos, self.z, self.yaw)

    def paths_rejected(self, name, pos, z, yaw):
        """Either scaled simplified attack path with more than one point that hits the navmesh before NAV_PATH_FRACTION of
        its length rejects the action. Paths are local to the parent transform (behemoth, or helper for the predictive test)."""
        if self.path_query is None or name not in self._simple:
            return False
        sx, sy, sz = self.scaling_factor(name)
        c, s = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
        x, y = pos
        for local in self._simple[name]:
            if len(local) < 2:
                continue
            world = [(x + px * sx * c - py * sy * s, y + px * sx * s + py * sy * c, z + pz * sz)
                     for px, py, pz in local]
            q = self.path_query(world)
            if q['hit'] and q['percentage'] < NAV_PATH_FRACTION:
                return True
        return False

    def _place_helper(self, name, targets):
        """Evaluate's helper branch (CreateHelper, a valid helper and a valid current target); its score is always 0."""
        target = next((t for t in targets if t.alive), None)
        if target is not None and name in self.helpers:
            place(self, name, (target.pos[0], target.pos[1], self.target_z), (self.pos[0], self.pos[1], self.z), self.ground_z)

    def action_tests(self, name, targets=()):
        """ActionSpecifcTests. BP_BehemothAction_C: the action hits a target from the behemoth's current transform.
        sally_rocklaunch1_atk_C replaces that result with 'Tail not severed'."""
        if self.actions[name].get('class') == 'sally_rocklaunch1_atk_C':
            return 'Tail' not in self.severed
        return bool(self.targets_hit(name, targets or self.last_targets))

    def evaluate(self, name, now, targets, predict_time=0.0):
        """BP_BehemothAction.Evaluate at the behemoth's current transform -> (score, targets hit)."""
        self.last_targets = targets
        ok, _ = self.available(name, now, self.intensity(targets))
        if not ok:
            return 0.0, []
        if self.nav_rejected(name):
            self._place_helper(name, targets)
            return 0.0, []
        hit = self.targets_hit(name, targets, predict_time)
        if not hit:
            self._place_helper(name, targets)
            return 0.0, []
        base = sum(self._target_score(t) * self.actions[name]['base_value'] * self.attack_weighting *
                   self.runtime_weighting for t in hit)
        rnd = base * self.rng.uniform(0.0, self.random_weight_max)
        return base * (1.0 - self.random_weight_percent) + self.random_weight_percent * rnd, hit

    def choose(self, now, targets, predict_time=0.0):
        """evaluate_action_btservice: -> (action name, score, targets hit) or None.

        With `yaw_sweep` > 0, an action that misses at the current facing is also tried with the behemoth turned by
        +-10, +-20 ... degrees up to the sweep (smallest turn first), a reduced form of the 10-degree candidate sweep in
        GetBestPositionForEffectAreaForTarget (rotation only, the position stays). The facing that made the chosen action
        hit is left in `chosen_yaw`; the caller turns the behemoth to it when the action starts. Live 2026-10-15: the
        player stood 21 degrees off the Rock's nose, inside the reposition tree's 22-degree cone (so no pivot), and no
        attack covered them at the exact facing, so the Rock never attacked.
        """
        self.chosen_yaw = self.yaw
        if self.force_action in self.actions:
            return self.force_action, FORCE_SCORE, []
        offsets = [0.0]
        step = 10.0
        while step <= self.yaw_sweep + 1e-6:
            offsets += [step, -step]
            step += 10.0
        base_yaw, best = self.yaw, None
        try:
            for name in self.actions:
                for off in offsets:
                    self.yaw = base_yaw + off
                    score, hit = self.evaluate(name, now, targets, predict_time)
                    if score > 0.0:
                        if score > (best[1] if best else 0.0):
                            best = (name, score, hit)
                            self.chosen_yaw = self.yaw
                        break
        finally:
            self.yaw = base_yaw
        return best

    # --- execution -----------------------------------------------------------------------------------------------
    def _start_cooldown(self, name, now):
        a, c = self.actions[name], self._cd[name]
        c.uses_left -= 1
        if c.uses_left <= 0:
            var = a['cooldown_variance']
            t = a['cooldown'] + (a['multi_behemoth_cooldown_delta'] if self.multiple_behemoths else 0.0) \
                + self.rng.uniform(-var, var)
            c.on_cooldown, c.ready_at = True, now + max(t, 0.001)

    def start(self, name, now, target=None):
        """Begin an action: starts its cooldown (assumption: at start) and resolves the montage timeline."""
        a = self.actions[name]
        m, rate = a['montage_data'], a['play_rate'] or 1.0
        events = []
        for ev in m['events']:
            t0 = now + ev['t'] / rate
            if ev['type'] == 'archon_damage_window_C':
                rows = {k: self.damage.get(k) for k in ev['damage_keys']}
                events.append((t0, 'damage_window_open', {'keys': ev['damage_keys'], 'rows': rows, 'bodies': ev['bodies']}))
                events.append((t0 + ev['duration'] / rate, 'damage_window_close', {'keys': ev['damage_keys']}))
            elif ev['type'] == 'spawn_aoe_class_bpn_C':
                events.append((t0, 'spawn_aoe', {k: ev.get(k) for k in ('AoEClassToSpawn', 'DamageRadius', 'SpawnOffset')}))
            elif ev['type'] == 'spawn_actors_ns_C':
                events.append((t0, 'spawn_actors', {k: ev.get(k) for k in ('actor', 'damage_row', 'per_target')}))
            elif ev['type'] == 'Notify_StaggerProneBehemoth_C':
                events.append((t0, 'stagger_prone_open', {'threshold': ev.get('threshold'), 'parts': ev.get('parts')}))
                events.append((t0 + ev['duration'] / rate, 'stagger_prone_close', {}))
            elif ev['type'] == 'ModifyPartCollisionChannelNotify':
                events.append((t0, 'part_collision_ignore_pawn', {'bones': ev.get('bones')}))
                events.append((t0 + ev['duration'] / rate, 'part_collision_restore', {}))
            elif ev['type'] in ('behemoth_custom_notify_event_bpn_C', 'call_behemoth_function_bpns_C'):
                events.append((t0, 'behemoth_call', {k: ev.get(k) for k in ('event', 'begin', 'end')}))
        events.sort(key=lambda e: e[0])
        self._start_cooldown(name, now)
        self.run = ActionRun(name, now, now + m['length'] / rate, events, target.id if target else None)
        return self.run

    @property
    def busy(self):
        return self.run is not None

    def tick(self, now):
        """Events due by `now`; the action finishes at its montage end."""
        out = []
        r = self.run
        if r is None:
            return out
        while r.emitted < len(r.events) and r.events[r.emitted][0] <= now:
            out.append(r.events[r.emitted])
            r.emitted += 1
        if now >= r.end:
            out.append((r.end, 'action_end', {'action': r.name}))
            self.run = None
        return out
