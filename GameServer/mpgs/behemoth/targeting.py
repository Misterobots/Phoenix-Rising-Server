"""Behemoth target selection: UArchonAggroComponent TargetData and CurrentTargetIndex (2.1.1 binary, decoded 2026-11-03).

Timers (aggro_bpc ubergraph): RefreshAndScoreTargets every TargetRefreshAndScoreInterval, RefreshTargetVisibility every
VisibilityCheckInterval, RefreshTargetAggro every AggroCheckInterval, CalculatePathCosts every PathCostCheckInterval; each
ends in UpdateAfterTargetDataChange. Stickiness decays on ReceiveTick.

- RefreshAndScoreTargets: eqs_score_behemoth_targets generates players with health above 0 (EQGenerator_LivePlayers)
  and filters them at a 2D distance of 8000 (EnvQueryTest_Distance_2). OnTargetRefreshedAndScored sets LastAggroTimestamp
  and TargetScore (score x threat weighting) on a known entry, and adds an entry when CanAggroNewTarget (always true
  without a tether). FEnvQueryInstance::NormalizeScores gives a lone item 1 whenever its summed test score is not 0.
- GetOrAddTargetData (new entry): score 0, LastAggro now, LastSeen 0, LastAccessible 0, DeaggroState 1, accessibility
  calculated at once; LastAccessible becomes now when Accessible or Obstructed. CalculatePathCosts refreshes each entry
  the same way.
- RefreshTargetAggro (impl 0x264FA10): inside the tether (always, untethered) DeaggroState 0 and LastAggro now, else
  DeaggroState 1.
- RefreshTargetVisibility (impl 0x264FB40): within SightRange (3D), a line trace from the owner that hits nothing or the
  target sets LastSeenTimestamp. Phoenix has no world collision, so the trace counts as clear.
- IsTargetValid (impl 0x2648600): accessible = accessibility below CloseButLongPath, or CloseButLongPath / Inaccessible
  within TimeForTargetToExitAccessibility of LastAccessible; seen = score above 0, target alive, LastSeen within
  TimeForTargetToExitLOS. Requirement 0 both, 1 accessible, 2 seen, 3 seen and not accessible, 4 any.
- UpdateAfterTargetDataChange (impl 0x265AD00): drops gone targets and non-current ones past TimeForTargetToExitAggro
  since LastAggro; keeps a current target that passes requirement 0, else picks as TryPickNewTarget.
- TryPickNewTarget (impl 0x265A3B0): highest score (current target x CurrentTargetStickiness), a requirement-0 target over
  a requirement-2 one, a deaggroing target never over a chosen non-deaggroing one. A change resets stickiness to
  TargetStickinessMax and stamps TargetChangedTimestamp.
"""
from dataclasses import dataclass

from .aggro import CLOSE_BUT_LONG_PATH, INACCESSIBLE, OBSTRUCTED

DEFAULTS = {     # native host capture of the aggro_bpc_C defaults (2026-11-03) and the aggro_bpc overrides
    'TimeForTargetToExitAggro': 10.0, 'TimeForTargetToExitLOS': 5.0, 'TimeForTargetToExitAccessibility': 6.0,
    'TargetStickinessMax': 1.25, 'TargetStickinessMin': 0.75, 'TargetStickinessDecayTime': 20.0, 'SightRange': 10000.0,
    'TargetRefreshAndScoreInterval': 1.0, 'VisibilityCheckInterval': 0.25, 'AggroCheckInterval': 0.25,
    'PathCostCheckInterval': 2.0,
}
QUERY_RANGE = 8000.0             # eqs_score_behemoth_targets EnvQueryTest_Distance_2 FloatValueMax (2D filter)
REQ_ACCESSIBLE_SEEN, REQ_ACCESSIBLE, REQ_SEEN, REQ_SEEN_NOT_ACCESSIBLE, REQ_ANY = range(5)


@dataclass
class TargetData:
    key: object
    score: float = 0.0
    last_aggro: float = 0.0
    last_seen: float = 0.0
    last_accessible: float = 0.0
    accessibility: int = INACCESSIBLE
    deaggro_state: int = 1


class Targeting:
    """Target list of one untethered behemoth. Candidates are (key, location, alive) tuples."""

    def __init__(self, tuning=DEFAULTS):
        self.t = dict(tuning)
        self.data = []
        self.current = -1
        self.stickiness = self.t['TargetStickinessMax']
        self.changed_at = 0.0
        self.next = {'refresh': 0.0, 'visibility': 0.0, 'aggro': 0.0, 'path': 0.0}
        self.alive = lambda key: True                # health above 0 (set by update)
        self.gone = lambda key: False                # the target actor no longer exists (set by update)

    # ---- queries -------------------------------------------------------------------------------------------------
    def current_key(self):
        return self.data[self.current].key if 0 <= self.current < len(self.data) else None

    def entry(self, key):
        return next((d for d in self.data if d.key == key), None)

    def is_valid(self, i, req, now):
        d = self.data[i]
        if d.score > 0.0:
            if d.accessibility <= OBSTRUCTED:
                acc = True
            elif d.accessibility in (CLOSE_BUT_LONG_PATH, INACCESSIBLE):
                acc = now - d.last_accessible < self.t['TimeForTargetToExitAccessibility']
            else:
                acc = False
            seen = self.alive(d.key) and now - d.last_seen < self.t['TimeForTargetToExitLOS']
        else:
            acc = seen = False
        return {REQ_ACCESSIBLE_SEEN: acc and seen, REQ_ACCESSIBLE: acc, REQ_SEEN: seen,
                REQ_SEEN_NOT_ACCESSIBLE: seen and not acc, REQ_ANY: True}.get(req, False)

    # ---- data passes ---------------------------------------------------------------------------------------------
    def get_or_add(self, key, now, accessibility):
        d = self.entry(key)
        if d is None:
            d = TargetData(key, last_aggro=now)
            self.data.append(d)
        d.accessibility = accessibility
        if accessibility <= OBSTRUCTED:
            d.last_accessible = now
        return d

    def refresh_and_score(self, owner, candidates, now, accessibility_of):
        for key, loc, alive in candidates:
            if not alive or (owner[0] - loc[0]) ** 2 + (owner[1] - loc[1]) ** 2 > QUERY_RANGE ** 2:
                continue
            d = self.entry(key) or self.get_or_add(key, now, accessibility_of(loc))
            d.last_aggro = now
            d.score = 1.0
        self.update_after_change(now)

    def refresh_visibility(self, owner, locations, now):
        sight_sq = self.t['SightRange'] ** 2
        for d in self.data:
            loc = locations.get(d.key)
            if loc is not None and sum((owner[i] - loc[i]) ** 2 for i in range(3)) <= sight_sq:
                d.last_seen = now
        self.update_after_change(now)

    def refresh_aggro(self, now):
        for d in self.data:
            d.deaggro_state = 0
            d.last_aggro = now
        self.update_after_change(now)

    def calculate_path_costs(self, locations, now, accessibility_of):
        for d in list(self.data):
            loc = locations.get(d.key)
            if loc is not None:
                self.get_or_add(d.key, now, accessibility_of(loc))
        self.update_after_change(now)

    def remove_all(self, now):
        """Native RemoveAllTargets: empty TargetData, CurrentTargetIndex -1, TargetChangedTimestamp."""
        self.data = []
        if self.current != -1:
            self.current = -1
            self.changed_at = now

    # ---- selection -----------------------------------------------------------------------------------------------
    def update_after_change(self, now):
        cur = self.current_key()
        self.data = [d for d in self.data
                     if not self.gone(d.key) and (d.key == cur or now - d.last_aggro <= self.t['TimeForTargetToExitAggro'])]
        self.current = next((i for i, d in enumerate(self.data) if d.key == cur), -1)
        if self.current >= 0 and self.is_valid(self.current, REQ_ACCESSIBLE_SEEN, now):
            return False
        return self.try_pick_new_target(now)

    def try_pick_new_target(self, now):
        best, best_score, best_full, best_not_deaggro = -1, 0.0, False, False
        for i, d in enumerate(self.data):
            full = self.is_valid(i, REQ_ACCESSIBLE_SEEN, now)
            if not full and (best_full or not self.is_valid(i, REQ_SEEN, now)):
                continue
            if d.deaggro_state != 0 and best_not_deaggro:
                continue
            score = d.score * (self.stickiness if i == self.current else 1.0)
            if (full and not best_full) or score > best_score:
                best, best_score, best_full, best_not_deaggro = i, score, full, d.deaggro_state == 0
        if best == self.current:
            return False
        self.stickiness = self.t['TargetStickinessMax']
        self.current = best
        self.changed_at = now
        return True

    def tick(self, now):
        """aggro_bpc ReceiveTick: CurrentTargetStickiness = Lerp(Min, Max, 1 - clamp((now - TargetChangedTimestamp) / DecayTime))."""
        a = min(max((now - self.changed_at) / self.t['TargetStickinessDecayTime'], 0.0), 1.0)
        self.stickiness = self.t['TargetStickinessMin'] + (self.t['TargetStickinessMax'] - self.t['TargetStickinessMin']) * (1.0 - a)

    def update(self, now, owner, candidates, navmesh, accessibility_of):
        """Run the due timer passes. candidates: [(key, location, alive)]; accessibility_of(location) is
        CalculateAccessibility from the owner. Without navigation data there are no path cost passes."""
        locations = {k: loc for k, loc, _ in candidates}
        alive = {k: a for k, _, a in candidates}
        self.alive = lambda k: alive.get(k, False)
        self.gone = lambda k: k not in locations
        self.tick(now)
        before = self.current_key()
        if now >= self.next['refresh']:
            self.next['refresh'] = now + self.t['TargetRefreshAndScoreInterval']
            self.refresh_and_score(owner, candidates, now, accessibility_of)
        if now >= self.next['visibility']:
            self.next['visibility'] = now + self.t['VisibilityCheckInterval']
            self.refresh_visibility(owner, locations, now)
        if now >= self.next['aggro']:
            self.next['aggro'] = now + self.t['AggroCheckInterval']
            self.refresh_aggro(now)
        if navmesh is not None and now >= self.next['path']:
            self.next['path'] = now + self.t['PathCostCheckInterval']
            self.calculate_path_costs(locations, now, accessibility_of)
        self.update_after_change(now)
        return self.current_key() != before
