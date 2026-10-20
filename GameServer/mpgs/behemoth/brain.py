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
from dataclasses import dataclass, field

from . import geometry

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROCK = os.path.join(HERE, 'world', 'behemoths', 'rock.json')

# behemoth_states_enum ids used by the rules (names come from the export's 'states' table)
RAGE, COMBAT, CUSTOM_STATE = 0, 9, 23

FORCE_SCORE = 99999.0
INTENSITY_BOUNDS = (7.0, 15.0)   # threat as % of max health: <7 -> 0, <15 -> 1, else 2 (threat_tracking_bpc.GetIntensityLevel)
THREAT_WEIGHT_PER_PERCENT = 0.1  # threat_tracking_bpc default TargettingWeightingPer1PercentOfMaxHPInThreat


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

    def evaluate(self, name, now, targets, predict_time=0.0):
        """BP_BehemothAction.Evaluate at the behemoth's current transform -> (score, targets hit)."""
        ok, _ = self.available(name, now, self.intensity(targets))
        if not ok:
            return 0.0, []
        hit = self.targets_hit(name, targets, predict_time)
        if not hit:
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
