"""Action helpers: where a behemoth repositions to when an attack cannot be used from where it stands.

Native chain (2.1.1 bytecode, Atlas pseudo):
- BP_BehemothAction_C.Evaluate, after a navmesh rejection or a miss at the current transform, places the action actor beside
  the target, takes the closest point of its OptimArea box to the behemoth, snaps it to the ground and puts the action's
  BP_ActionHelper_C there; PredictivePathTestPassed is the attack-path navmesh test run from the helper.
- BP_ActionHelper_C.ReceiveTick scores the players in the action's hit areas at the helper transform.
- get_action_helper_btservice_C runs EQ_FindBestActionHelper and stores the best helper in the CurrentHelper key.
"""
import math

from . import geometry

PATH_LENGTH_MAX = 10000.0        # EQ_FindBestActionHelper EnvQueryTest_Pathfinding FloatValueMax (Range filter)
HELPER_SCORE_MIN = 0.001         # EnvQueryTest_ActionHelperScore FloatValueMin (Minimum filter)
CURRENT_HELPER_BONUS = 0.1       # BP_ActionHelper_C tick: SelectFloat(0.1, 0, CurrentHelper == self)


class ActionHelper:
    """One action's BP_ActionHelper_C: stand location, yaw, predictive test result and tick score."""

    def __init__(self, action):
        self.action = action
        self.pos = None
        self.yaw = 0.0
        self.passed = False
        self.score = 0.0


def _normal(v):
    length = math.sqrt(sum(c * c for c in v))
    return tuple(c / length for c in v) if length > 1e-4 else (0.0, 0.0, 0.0)


def closest_point_on_box(center, yaw, extent, point):
    """UPrimitiveComponent::GetClosestPointOnCollision for a box rotated by yaw: clamp the point in box space."""
    c, s = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    dx, dy, dz = point[0] - center[0], point[1] - center[1], point[2] - center[2]
    lx, ly = dx * c + dy * s, -dx * s + dy * c
    lx = max(-extent[0], min(extent[0], lx))
    ly = max(-extent[1], min(extent[1], ly))
    lz = max(-extent[2], min(extent[2], dz))
    return (center[0] + lx * c - ly * s, center[1] + lx * s + ly * c, center[2] + lz)


def place(brain, name, target, behemoth, ground_z):
    """Evaluate's helper branch for action `name`. target/behemoth are actor locations; ground_z(x, y) stands in for the
    native 10000-unit vertical LineTraceSingle (trace channel 4) against world collision, which is not acquired."""
    atk = brain.actions[name]['attack']
    optimal = atk.get('optimal')
    helper = brain.helpers.get(name)
    if helper is None or not optimal:
        return
    sx, sy, _ = brain.scaling_factor(name)
    offset = (optimal['offset'][0] * sx, optimal['offset'][1] * sy, optimal['offset'][2])
    direction = _normal((behemoth[0] - target[0], behemoth[1] - target[1], behemoth[2] - target[2]))
    reach = math.sqrt(sum(c * c for c in offset))
    actor = tuple(target[i] + direction[i] * reach for i in range(3))
    yaw = math.degrees(math.atan2(target[1] - behemoth[1], target[0] - behemoth[0])) + optimal['yaw']
    flat = _normal(offset)
    yaw -= math.degrees(math.atan2(flat[1], flat[0]))
    extent = (optimal['extent'][0] * sx, optimal['extent'][1] * sy, optimal['extent'][2])
    stand = closest_point_on_box(actor, yaw, extent, behemoth)
    ground = ground_z(stand[0], stand[1])
    if ground is not None:
        stand = (stand[0], stand[1], ground)
    helper.pos, helper.yaw = stand, yaw - optimal['yaw']
    helper.passed = not brain.paths_rejected(name, (stand[0], stand[1]), stand[2], helper.yaw)


def tick_score(brain, helper, targets, current):
    """BP_ActionHelper_C tick: 0 unless the predictive test passed, the action is off cooldown and ActionSpecifcTests pass;
    else the attack-target score times BaseValue for each player in the hit areas at the helper, plus the current bonus."""
    helper.score = 0.0
    name = helper.action
    if helper.pos is None or not helper.passed or brain._cd[name].on_cooldown or not brain.action_tests(name, targets):
        return
    atk = brain.actions[name]['attack']
    scale = brain.scaling_factor(name)[0]
    origin = (helper.pos[0], helper.pos[1])
    total = 0.0
    for t in targets:
        if not t.alive:
            continue
        if atk['eval_type'] == 1:
            inside = any(geometry.in_arc(origin, helper.yaw, t.pos, a, scale) for a in atk['arcs'])
        else:
            inside = any(geometry.in_box(origin, helper.yaw, t.pos, b, scale) for b in atk['boxes'])
        if inside:
            total += brain._target_score(t) * brain.actions[name]['base_value'] + (CURRENT_HELPER_BONUS if current is helper else 0.0)
    helper.score = total


def _normalize(values, weight, filtered):
    """UEnvQueryTest::NormalizeItemScores, Absolute normalization, no clamping, Linear equation."""
    live = [v for v, ok in zip(values, filtered) if ok]
    if not live:
        return [0.0] * len(values)
    low, high = min([0.0] + live), max(live)
    if low == high:
        return [0.0] * len(values)
    span = max(abs(high - low), 0.0)
    out = []
    for v, ok in zip(values, filtered):
        if not ok:
            out.append(0.0)
            continue
        clamped = max(low, min(high, v))
        normalized = 1.0 - abs(high - clamped) / span if weight >= 0 else abs(high - clamped) / span
        out.append(normalized * abs(weight))
    return out


def best_helper(helpers, querier, querier_yaw, find_path):
    """EQ_FindBestActionHelper: eqs_generator_valid_action_helpers (PredictivePathTestPassed) then Pathfinding PathLength
    from the querier (NoDynamic, 0..10000, weight -1), ActionHelperScore (>= 0.001, weight 2), Dot2D of the querier and
    item rotations (weight 1) and Dot2D of the querier rotation with item->querier (weight 1). -> best helper or None.
    ActionHelperScore is native; its value is taken as the helper's Score (inferred from the class name)."""
    items = [h for h in helpers if h.passed and h.pos is not None]
    if not items:
        return None
    forward = (math.cos(math.radians(querier_yaw)), math.sin(math.radians(querier_yaw)))
    lengths, scores, dot_a, dot_b, keep = [], [], [], [], []
    for h in items:
        path = find_path(querier, h.pos)
        length = path['length'] if path is not None else None
        ok = length is not None and 0.0 <= length <= PATH_LENGTH_MAX and h.score >= HELPER_SCORE_MIN
        keep.append(ok)
        lengths.append(length if length is not None else 0.0)
        scores.append(h.score)
        item_forward = (math.cos(math.radians(h.yaw)), math.sin(math.radians(h.yaw)))
        dot_a.append(forward[0] * item_forward[0] + forward[1] * item_forward[1])
        to_querier = _normal((querier[0] - h.pos[0], querier[1] - h.pos[1], 0.0))
        dot_b.append(forward[0] * to_querier[0] + forward[1] * to_querier[1])
    total = [0.0] * len(items)
    for values, weight in ((lengths, -1.0), (scores, 2.0), (dot_a, 1.0), (dot_b, 1.0)):
        for i, s in enumerate(_normalize(values, weight, keep)):
            total[i] += s
    best = None
    for i, h in enumerate(items):
        if keep[i] and (best is None or total[i] > total[best]):
            best = i
    return None if best is None else items[best]
