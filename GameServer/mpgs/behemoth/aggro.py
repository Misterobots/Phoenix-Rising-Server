"""Behemoth frustration: aggro_bpc_C over the native UArchonAggroComponent (2.1.1).

A target the behemoth cannot reach builds frustration; at MaxFrustration the ReadyToDeaggro key opens the deaggro branch.
Values are the effective Default__aggro_bpc_C defaults captured in the native host (2026-11-03).

- CalculateAccessibility (native, impl RVA 0x263E500): project the owner (extent 50, 50, 1000) and the target
  (AccessibleTargetTolerance, else AccessibleTargetInAirCheck); no projection -> Inaccessible. Path cost on the agent's
  navigation data (partial paths count); not found -> Inaccessible without a path; cost above MaxPathCostForAccessibility
  -> Inaccessible with a path; owner-target distance under 1500 with cost above 3000 -> CloseButLongPath; else Accessible.
- aggro_bpc CalculatePathCosts every PathCostCheckInterval refreshes each target's accessibility (targeting.py).
- UpdateCurrentTargetFrustration (ReceiveTick): CloseButLongPath or Inaccessible adds FrustrationPerSecInaccessibleTarget * dt;
  otherwise a target in a deaggro state adds FrustrationPerSecDeaggroingTarget * dt.
- IncreaseFrustration (aggro_bpc), TryDegenFrustration / DecreaseFrustration / ResetFrustration (native).
"""
ACCESSIBLE, OBSTRUCTED, CLOSE_BUT_LONG_PATH, INACCESSIBLE, ATTACHED, DEAD = range(6)

OWNER_PROJECT_EXTENT = (50.0, 50.0, 1000.0)    # CalculateAccessibility constant
CLOSE_DISTANCE_SQ = 2250000.0                  # 1500 squared
CLOSE_LONG_PATH_COST = 3000.0
WARNING_THRESHOLD = 50.0                       # IncreaseFrustration / DecreaseFrustration warning keys

DEFAULTS = {
    'MaxFrustration': 100.0, 'AccessibleTargetTolerance': (750.0, 750.0, 25.0),
    'AccessibleTargetInAirCheck': (100.0, 100.0, 800.0), 'MaxPathCostForAccessibility': 11000.0,
    'DegenDelayAfterFrustrationAdded': 3.0, 'FrustrationDegenPerSecond': 5.0, 'FrustrationDegenInterval': 0.25,
    'PathCostCheckInterval': 2.0, 'CanDeaggro': True,
    'FrustrationPerSecInaccessibleTarget': 1.0, 'FrustrationPerSecDeaggroingTarget': 15.0,
}


def calculate_accessibility(navmesh, owner, target, alive=True, tuning=DEFAULTS):
    """-> (accessibility, path exists, path cost or -1)."""
    if not alive:
        return DEAD, False, -1.0
    owner_on = navmesh.project_point(owner, OWNER_PROJECT_EXTENT)
    target_on = navmesh.project_point(target, tuning['AccessibleTargetTolerance']) \
        or navmesh.project_point(target, tuning['AccessibleTargetInAirCheck'])
    if owner_on is None or target_on is None:
        return INACCESSIBLE, False, -1.0
    path = navmesh.find_path(owner_on[1], target_on[1])
    if path is None:
        return INACCESSIBLE, False, -1.0
    cost = path['cost']
    if cost > tuning['MaxPathCostForAccessibility']:
        return INACCESSIBLE, True, cost
    close = sum((owner[i] - target[i]) ** 2 for i in range(3)) < CLOSE_DISTANCE_SQ
    return (CLOSE_BUT_LONG_PATH if close and cost > CLOSE_LONG_PATH_COST else ACCESSIBLE), True, cost


class Aggro:
    """Frustration state of one behemoth. bb is its blackboard dict."""

    def __init__(self, bb, tuning=DEFAULTS):
        self.bb = bb
        self.tuning = dict(tuning)
        self.current = 0.0
        self.paused = False
        self.next_degen = 0.0
        self.degen_after = 0.0

    def able(self, states, tethered=False):
        """Bp_Behemoth_C.IsAbleToBuildFrustration and aggro_bpc's own gate."""
        # IsInState(16) / IsInState(5) compare behemoth_states_enum values; Phoenix states use the NewEnumerator name
        # numbers, and value 16 is NewEnumerator14, value 5 is NewEnumerator4.
        owner = (bool(self.bb.get('HasEnteredCombatSinceLastDisengage')) or (tethered and 14 in states)) and 4 not in states
        return owner and not self.bb.get('ReadyToDeaggro') and not self.paused and self.tuning['CanDeaggro']

    def increase(self, amount, now, states):
        if amount <= 0.0 or not self.able(states):
            return
        self.degen_after = now + self.tuning['DegenDelayAfterFrustrationAdded']   # TryDelayDegen
        if self.current + amount >= WARNING_THRESHOLD and self.bb.get('CanDeaggroWarning'):
            self.bb['ReadyToDeaggroWarning'] = True
        self.current += amount
        if self.current >= self.tuning['MaxFrustration']:
            self.current = self.tuning['MaxFrustration']
            self.bb['ReadyToDeaggro'] = True

    def decrease(self, amount, force=False):
        if amount < 0.0 or not self.tuning['CanDeaggro'] or (self.paused and not force):
            return
        self.current = max(0.0, self.current - amount)
        if self.current <= 0.0:
            self.bb['CanDeaggroWarning'] = True
        if self.current < WARNING_THRESHOLD:
            self.bb['ReadyToDeaggroWarning'] = False

    def reset(self):
        """Native ResetFrustration. The cleared key's FName is inferred as ReadyToDeaggro (it sits between
        CanDeaggroWarning and ReadyToDeaggroWarning in the binary's name table)."""
        if not self.tuning['CanDeaggro']:
            return
        self.decrease(self.current, force=True)
        if self.bb.get('ReadyToDeaggro'):
            self.bb['ReadyToDeaggro'] = False

    def update(self, now, dt, has_navmesh, target, states):
        """target: the current target's TargetData (targeting.py), or None."""
        if target is not None:
            # Without navigation data accessibility is never calculated, and the initial Inaccessible is not evidence.
            if has_navmesh and target.accessibility in (CLOSE_BUT_LONG_PATH, INACCESSIBLE):
                self.increase(self.tuning['FrustrationPerSecInaccessibleTarget'] * dt, now, states)
            elif target.deaggro_state != 0:
                self.increase(self.tuning['FrustrationPerSecDeaggroingTarget'] * dt, now, states)
        interval = self.tuning['FrustrationDegenInterval']       # looping TryDegenFrustration timer
        if now >= self.next_degen:
            self.next_degen = now + interval
            if now >= self.degen_after:
                self.decrease(interval * self.tuning['FrustrationDegenPerSecond'])
