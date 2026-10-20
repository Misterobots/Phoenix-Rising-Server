"""Behaviour-tree interpreter for cooked UE behaviour trees (AssetDump JSON of *_bt packages). Step D of
docs/BEHEMOTH_RUNTIME_PLAN_2026-10-15.md.

    trees = TreeSet.load(dump_dir)                 # rock_bt.json + the shared_*_bt.json sub-trees it runs
    runner = Runner(trees, 'rock_bt', ctx)         # ctx: see Context below
    runner.tick(now)                               # every server tick

Semantics (UE BehaviorTree, simplified):
- Selector runs children in order until one succeeds; Sequence until one fails. A child runs only when all its
  decorators pass. `bInverseCondition` inverts a decorator. `BTDecorator_ForceSuccess` turns the child's result into
  success. `BTDecorator_TimeLimit` fails the child after TimeLimit seconds.
- Observer aborts: while a task runs, a decorator on the running path with FlowAbortMode Self/Both that now fails aborts
  it; a higher-priority sibling branch whose decorators have FlowAbortMode LowerPriority/Both and now all pass aborts
  it and the tree restarts from the root.
- Services on a composite or task on the running path tick at their Interval.
- BTTask_RunBehavior runs the named sub-tree in place (same blackboard).
- Node classes without a handler: decorators pass and tasks succeed at once. Each is logged once (`unknown`), so the
  list of gaps is visible in the server log. That is a deliberate simplification: most unhandled nodes are
  coordination, audio or cosmetic tasks.

Decorator semantics were read from the cooked Blueprint bytecode where noted (kismet_pseudo.py):
- behemoth_in_state_btdeco: StateRequirement 0 = must be in State, 1 = must not be in State.
- btdeco_variablecooldown: passes when now > CurrentFightStateStartTime + StartTime and now >= NextAllowedTime; on pass
  NextAllowedTime = now + random(Cooldown_Min, Cooldown_Max).
"""
import json
import math
import os
import random

SUCCEEDED, FAILED, RUNNING = 'succeeded', 'failed', 'running'


def _short_name(ref):
    """{'ObjectName': "Type'tree:Name'"} -> 'Name' (or None)."""
    if not ref:
        return None
    n = ref['ObjectName']
    inner = n.split("'", 1)[1].rstrip("'") if "'" in n else n
    return inner.split(':')[-1]


def _asset_name(ref):
    """{'ObjectName': "BehaviorTree'shared_x_bt'"} or a plain string -> 'shared_x_bt'."""
    if isinstance(ref, str):
        return ref
    if not ref:
        return None
    n = ref['ObjectName']
    return n.split("'")[1] if "'" in n else n


def _enum_index(value, default=0):
    """'behemoth_states_enum::NewEnumerator23' -> 23."""
    if value is None:
        return default
    if isinstance(value, int):
        return value
    tail = str(value).rsplit('NewEnumerator', 1)
    return int(tail[1]) if len(tail) == 2 and tail[1].isdigit() else default


def _key(props, name):
    k = props.get(name)
    return k.get('SelectedKeyName') if isinstance(k, dict) else None


# ---------------------------------------------------------------------------------------------------------------
class Node:
    def __init__(self, tree, export):
        self.tree = tree
        self.name = export['Name']
        self.type = export['Type']
        self.props = export.get('Properties') or {}
        self.children = []              # composites: [(child Node, [decorator Nodes])]
        self.services = []              # [Node]

    @property
    def is_composite(self):
        return self.type in ('BTComposite_Selector', 'BTComposite_Sequence')

    def __repr__(self):
        return '%s:%s' % (self.tree, self.name)


class TreeSet:
    """All behaviour trees loaded from a folder of AssetDump JSON files, by asset name."""

    def __init__(self):
        self.roots = {}

    @classmethod
    def load(cls, folder):
        ts = cls()
        # Not every tree asset is named *_bt (e.g. _shared/ai/sub_flee_arrive): load any dump that holds a BehaviorTree export.
        for fn in sorted(os.listdir(folder)):
            if not fn.endswith('.json'):
                continue
            with open(os.path.join(folder, fn), encoding='utf-8') as f:
                exports = json.load(f)
            if isinstance(exports, list) and any(isinstance(e, dict) and e.get('Type') == 'BehaviorTree' for e in exports):
                ts.add(exports)
        return ts

    def add(self, exports):
        tree = next(e for e in exports if e.get('Type') == 'BehaviorTree')
        tname = tree['Name']
        nodes = {e['Name']: Node(tname, e) for e in exports if e.get('Type') != 'BehaviorTree'}

        def get(ref):
            n = _short_name(ref)
            return nodes.get(n)

        for n in nodes.values():
            for c in n.props.get('Children') or []:
                child = get(c.get('ChildComposite')) or get(c.get('ChildTask'))
                if child is not None:
                    n.children.append((child, [d for d in (get(r) for r in c.get('Decorators') or []) if d is not None]))
            n.services = [s for s in (get(r) for r in n.props.get('Services') or []) if s is not None]
        root = get(tree['Properties']['RootNode'])
        root.root_decorators = [d for d in (get(r) for r in tree['Properties'].get('RootDecorators') or []) if d is not None]
        self.roots[tname] = root
        return root


# ---------------------------------------------------------------------------------------------------------------
class Context:
    """What the tree needs from the game. The session adapter implements these; tests use a fake.

    bb: dict blackboard. Attributes: now, states (set of behemoth_states_enum *name* indices, e.g. 9 for NewEnumerator9 = Combat;
    a behemoth is in several states at once), variant ('Normal'/'Alpha'), island_mode ('Hunt'), rng.
    The enum's stored values differ from the name indices (NewEnumerator23 is value 28): everything here uses the name index,
    as the cooked tree properties do.
    pos() -> (x, y, z); yaw() -> deg; target_pos(key) -> (x, y, z) | None (actor or vector key); target_vel(key) -> (vx, vy).
    """
    bb = None
    now = 0.0
    states = frozenset()
    variant = 'Normal'
    island_mode = 'Hunt'
    rng = random

    def log(self, msg):
        pass


class Frame:
    def __init__(self, node, decorators=(), started=0.0):
        self.node = node
        self.decorators = list(decorators)      # the decorators that guarded entry into this node
        self.idx = 0                            # next child to try (composites)
        self.started = started
        self.forced = any(d.type == 'BTDecorator_ForceSuccess' for d in self.decorators)
        self.time_limit = next((float(d.props.get('TimeLimit', 5.0)) for d in self.decorators
                                if d.type == 'BTDecorator_TimeLimit'), None)


class Runner:
    MAX_STEPS = 500     # guard against a tree that never reaches a task in one tick

    def __init__(self, trees, root_name, ctx):
        self.trees = trees
        self.root = trees.roots[root_name]
        self.ctx = ctx
        self.stack = []
        self.task = None                         # (Frame, handler state) of the running task
        self.unknown = set()
        self._service_due = {}
        self._deco_state = {}                    # per-decorator memory (cooldowns)
        self.last_leaf = None

    # ---- public --------------------------------------------------------------------------------------------------
    def tick(self, now):
        self.ctx.now = now
        self._run_services(now)
        if self.task is not None:
            if self._should_abort(now):
                self._abort_task()
                self.stack = []
            else:
                frame, h = self.task
                r = h.update(self.ctx)
                if r == RUNNING and frame.time_limit is not None and now - frame.started >= frame.time_limit:
                    h.abort(self.ctx)
                    r = FAILED
                if r == RUNNING:
                    return
                self.task = None
                self.stack.pop()
                self._finish(frame, r)
        self._advance(now)

    def path(self):
        return [f.node.name for f in self.stack]

    # ---- traversal -----------------------------------------------------------------------------------------------
    def _advance(self, now):
        self._started_root = False
        for _ in range(self.MAX_STEPS):
            if not self.stack:
                if self._started_root:
                    self._started_root = False
                    return                         # one full pass per tick at most
                self.stack.append(Frame(self.root, getattr(self.root, 'root_decorators', ()), now))
                self._started_root = True
            frame = self.stack[-1]
            node = frame.node
            if node.is_composite:
                if frame.idx >= len(node.children):
                    self.stack.pop()
                    self._finish(frame, FAILED if node.type == 'BTComposite_Selector' else SUCCEEDED)
                    continue
                child, decos = node.children[frame.idx]
                frame.idx += 1
                if not all(self._check(d) for d in decos):
                    # UE notifies the decorators of a blocked child too, so ForceSuccess still turns it into a success
                    forced = any(d.type == 'BTDecorator_ForceSuccess' for d in decos)
                    self._apply(frame, SUCCEEDED if forced else FAILED)
                    continue
                self._on_enter(decos)
                if child.type == 'BTTask_RunBehavior':
                    sub = _asset_name(child.props.get('BehaviorAsset'))
                    if sub in self.trees.roots:
                        sroot = self.trees.roots[sub]
                        rdecos = getattr(sroot, 'root_decorators', [])
                        if not all(self._check(d) for d in rdecos):      # the sub-tree's own entry conditions
                            forced = any(d.type == 'BTDecorator_ForceSuccess' for d in decos)
                            self._apply(frame, SUCCEEDED if forced else FAILED)
                            continue
                        self.stack.append(Frame(sroot, list(decos) + list(rdecos), now))
                        continue
                    self._note_unknown('subtree ' + str(sub))
                    self._finish_child(frame, Frame(child, decos, now), SUCCEEDED)
                    continue
                if child.is_composite:
                    self.stack.append(Frame(child, decos, now))
                    continue
                # a task
                f = Frame(child, decos, now)
                h = self._task_handler(child)
                r = h.start(self.ctx)
                self.last_leaf = child
                if r == RUNNING:
                    self.stack.append(f)
                    self.task = (f, h)
                    return
                self._finish_child(frame, f, r)
            else:                                   # a bare task frame left on the stack (should not happen)
                self.stack.pop()
        self.ctx.log('BT step limit reached at %s' % self.path())
        self.stack = []

    def _finish_child(self, parent, child_frame, result):
        if child_frame.forced:
            result = SUCCEEDED
        self._apply(parent, result)

    def _finish(self, frame, result):
        """`frame` has been popped with `result`; apply it to its parent (the new top of stack)."""
        if frame.forced:
            result = SUCCEEDED
        if not self.stack:
            return
        self._apply(self.stack[-1], result)

    def _apply(self, parent, result):
        node = parent.node
        if node.type == 'BTComposite_Selector' and result == SUCCEEDED:
            parent.idx = len(node.children) + 1     # done: success
            self.stack.pop()
            self._finish(parent, SUCCEEDED)
        elif node.type == 'BTComposite_Sequence' and result == FAILED:
            parent.idx = len(node.children) + 1
            self.stack.pop()
            self._finish(parent, FAILED)
        # otherwise: continue with the next child on the next loop iteration

    # ---- aborts --------------------------------------------------------------------------------------------------
    def _should_abort(self, now):
        # Self aborts: a guarding decorator on the running path now fails
        for f in self.stack:
            for d in f.decorators:
                mode = str(d.props.get('FlowAbortMode', ''))
                if mode.endswith('Self') or mode.endswith('Both'):
                    if d.type != 'BTDecorator_ForceSuccess' and not self._check(d, peek=True):
                        return True
        # LowerPriority aborts: an earlier sibling (in a selector on the path) would now run
        for i, f in enumerate(self.stack[:-1]):
            node = f.node
            if node.type != 'BTComposite_Selector':
                continue
            active = f.idx - 1
            for child, decos in node.children[:max(active, 0)]:
                aborting = [d for d in decos if str(d.props.get('FlowAbortMode', '')).endswith(('LowerPriority', 'Both'))]
                if aborting and all(self._check(d, peek=True) for d in decos):
                    return True
        return False

    def _abort_task(self):
        frame, h = self.task
        h.abort(self.ctx)
        self.task = None
        self.ctx.log('BT abort %s' % frame.node.name)

    # ---- services ------------------------------------------------------------------------------------------------
    def _run_services(self, now):
        nodes = [f.node for f in self.stack]
        if not nodes:
            nodes = [self.root]
        for n in nodes:
            for s in n.services:
                due = self._service_due.get(id(s), 0.0)
                if now < due:
                    continue
                self._service_due[id(s)] = now + float(s.props.get('Interval', 0.5))
                fn = SERVICES.get(s.type)
                if fn is None:
                    self._note_unknown(s.type)
                else:
                    fn(self.ctx, s.props)

    def _on_enter(self, decos):
        for d in decos:
            if d.type == 'btdeco_variablecooldown_C':
                pass                                # state already advanced in the check (as the Blueprint does)

    # ---- decorators ----------------------------------------------------------------------------------------------
    def _check(self, d, peek=False):
        fn = DECORATORS.get(d.type)
        if fn is None:
            self._note_unknown(d.type)
            ok = True
        else:
            mem = self._deco_state.setdefault(id(d), {})
            ok = bool(fn(self.ctx, d.props, mem, peek))
        if d.props.get('bInverseCondition'):
            ok = not ok
        return ok

    def _task_handler(self, node):
        cls = TASKS.get(node.type)
        if cls is None:
            self._note_unknown(node.type)
            return Instant(node.props)
        return cls(node.props)

    def _note_unknown(self, what):
        if what not in self.unknown:
            self.unknown.add(what)
            self.ctx.log('BT unknown node %s (treated as pass/success)' % what)

    _started_root = False


# ---------------------------------------------------------------------------------------------------------------
# decorators: fn(ctx, props, memory, peek) -> bool

def _d_blackboard(ctx, p, mem, peek):
    key = _key(p, 'BlackboardKey')
    v = ctx.bb.get(key)
    op = int(p.get('OperationType', 0))
    if v is None and op >= 2:
        v = 0.0                                   # arithmetic test on an unset (numeric) key: UE's default value is 0
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        ref = float(p.get('FloatValue', p.get('IntValue', 0)))
        return [v == ref, v != ref, v < ref, v <= ref, v > ref, v >= ref][op] if op < 6 else False
    is_set = bool(v) if isinstance(v, bool) else v is not None
    return is_set if op == 0 else not is_set


def _d_in_state(ctx, p, mem, peek):
    in_state = _enum_index(p.get('State')) in ctx.states
    return in_state if _enum_index(p.get('StateRequirement'), 0) == 0 else not in_state


def _d_requirements(ctx, p, mem, peek):
    req = p.get('BehemothRequirements') or {}
    for k, v in req.items():
        if k.startswith('RequireBehemothClassificationName') and v not in (None, '', 'None'):
            if str(v).lower() != ctx.variant.lower():
                return False
    return True


def _d_variable_cooldown(ctx, p, mem, peek):
    start = float(ctx.bb.get('CurrentFightStateStartTime') or 0.0) + float(p.get('StartTime', 0.0))
    if ctx.now <= start or ctx.now < mem.get('next', 0.0):
        return False
    if not peek:
        lo, hi = float(p.get('Cooldown_Min', 0.0)), float(p.get('Cooldown_Max', 0.0))
        mem['next'] = ctx.now + ctx.rng.uniform(min(lo, hi), max(lo, hi))
    return True


def _d_cooldown(ctx, p, mem, peek):
    # BTDecorator_Cooldown: blocks for CoolDownTime after it last let its child run
    if ctx.now < mem.get('next', 0.0):
        return False
    if not peek:
        mem['next'] = ctx.now + float(p.get('CoolDownTime', 5.0))
    return True


def _d_island_mode(ctx, p, mem, peek):
    want = str(p.get('IslandGameMode', '')).rsplit('::', 1)[-1]
    return want.lower() == ctx.island_mode.lower()


def _d_member_present(ctx, p, mem, peek):
    cls = _asset_name(p.get('ClassToTest')) or str(p.get('ClassToTest'))
    return 'PatrolCenterPoint' in cls and ctx.bb.get('PatrolCenter') is not None


def _d_target_valid(ctx, p, mem, peek):
    valid = ctx.bb.get('AttackTarget') is not None
    return valid == bool(p.get('ShouldBeValid', True))


def _dist2d(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _d_enter_combat(ctx, p, mem, peek):
    if ctx.bb.get('ForceEnterCombat'):
        return True
    self_pos, tgt = ctx.target_pos(_key(p, 'Origin') or 'SelfPos'), ctx.target_pos(_key(p, 'Target') or 'AttackTarget')
    if self_pos is None or tgt is None:
        return False
    t = float(p.get('PredictTargetHorizFuturePosTime', 0.0))
    vx, vy = ctx.target_vel(_key(p, 'Target') or 'AttackTarget')
    rng = ctx.bb.get('EnterCombatRange') or 2000.0
    return _dist2d(self_pos, (tgt[0] + vx * t, tgt[1] + vy * t)) <= rng


def _d_distance(ctx, p, mem, peek):
    a, b = ctx.target_pos(_key(p, 'Origin')), ctx.target_pos(_key(p, 'Target'))
    if a is None or b is None:
        return False
    d = _dist2d(a, b)
    return float(p.get('MinDistance', 0.0)) <= d <= float(p.get('MaxDistance', 1e12))


def _d_cone(ctx, p, mem, peek):
    o, fwd, obs = ctx.target_pos(_key(p, 'ConeOrigin')), ctx.target_pos(_key(p, 'ConeDirection')), ctx.target_pos(_key(p, 'Observed'))
    if o is None or fwd is None or obs is None:
        return False
    a1 = math.atan2(fwd[1] - o[1], fwd[0] - o[0])
    a2 = math.atan2(obs[1] - o[1], obs[0] - o[0])
    diff = abs((math.degrees(a2 - a1) + 180.0) % 360.0 - 180.0)
    return diff <= float(p.get('ConeHalfAngle', 45.0))


DECORATORS = {
    'BTDecorator_Blackboard': _d_blackboard,
    'BTDecorator_ForceSuccess': lambda ctx, p, m, peek: True,
    'BTDecorator_TimeLimit': lambda ctx, p, m, peek: True,
    'BTDecorator_Cooldown': _d_cooldown,
    'behemoth_in_state_btdeco_C': _d_in_state,
    'passes_behemoth_requirements_btdeco_C': _d_requirements,
    'btdeco_variablecooldown_C': _d_variable_cooldown,
    'is_island_gamemode_btdeco_C': _d_island_mode,
    'BTDeco_MemberOfClassIsPresent_C': _d_member_present,
    'current_target_is_valid_btdeco_C': _d_target_valid,
    'enter_combat_check_btdeco_C': _d_enter_combat,
    'BTDecorator_DistanceCheck_C': _d_distance,
    'BTDecorator_ConeCheck': _d_cone,
    # no allies / no messages / no flee despawn in a one-behemoth session
    'check_next_message_type_btdeco_C': lambda ctx, p, m, peek: False,
    'should_despawn_on_flee_btdeco_C': lambda ctx, p, m, peek: False,
    # cosmetic or always-true in this server
    'set_behemoth_loco_mode_btdeco_C': lambda ctx, p, m, peek: True,
    'set_state_btdeco_C': lambda ctx, p, m, peek: True,
    'navtrace_btdeco_C': lambda ctx, p, m, peek: True,
}


# ---------------------------------------------------------------------------------------------------------------
# tasks: start(ctx) -> result, update(ctx) -> result, abort(ctx)

class Task:
    def __init__(self, props):
        self.p = props

    def start(self, ctx):
        return SUCCEEDED

    def update(self, ctx):
        return SUCCEEDED

    def abort(self, ctx):
        pass


class Instant(Task):
    pass


class Wait(Task):
    def start(self, ctx):
        dev = float(self.p.get('RandomDeviation', 0.0))
        self.until = ctx.now + float(self.p.get('WaitTime', 5.0)) + (ctx.rng.uniform(-dev, dev) if dev else 0.0)
        return RUNNING

    def update(self, ctx):
        return SUCCEEDED if ctx.now >= self.until else RUNNING


class MoveTo(Task):
    def start(self, ctx):
        dest = ctx.target_pos(_key(self.p, 'BlackboardKey'))
        if dest is None:
            return FAILED
        self.radius = float(self.p.get('AcceptableRadius', 5.0))
        if _dist2d(ctx.target_pos('SelfPos'), dest) <= self.radius:
            return SUCCEEDED
        return RUNNING if ctx.move_to(dest, self.radius, _key(self.p, 'BlackboardKey')) else FAILED

    def update(self, ctx):
        return ctx.move_status()

    def abort(self, ctx):
        ctx.stop_moving()


class EQSQuery(Task):
    """Only the passive-patrol query: a random walkable point in a donut around the patrol centre."""

    def start(self, ctx):
        req = self.p.get('EQSRequest') or {}
        name = _asset_name(req.get('QueryTemplate')) or _asset_name(self.p.get('QueryTemplate')) or ''
        if 'patrol' not in name.lower() and 'NextPointAroundTargetPoint' not in name:
            return FAILED
        cfg = {c.get('ParamName'): c.get('Value') for c in req.get('QueryConfig') or []}
        outer = float(cfg.get('DonutOuterRadius') or cfg.get('Radius') or 500.0)
        inner = float(cfg.get('DonutInnerRadius') or 0.0)
        p = ctx.patrol_point(inner, outer)
        if p is None:
            return FAILED
        ctx.bb[_key(self.p, 'BlackboardKey') or 'MoveDestination'] = p
        return SUCCEEDED


class BehemothAction(Task):
    def start(self, ctx):
        mont = self.p.get('Montage')
        name = _asset_name(mont) if isinstance(mont, dict) else mont
        if not name:
            return SUCCEEDED
        self.until = ctx.play_montage(name)
        return RUNNING if self.until is not None else SUCCEEDED

    def update(self, ctx):
        return SUCCEEDED if ctx.now >= self.until else RUNNING


class PerformAction(Task):
    def start(self, ctx):
        key = self.p.get('ActionBlackboardKeyName')
        name = ctx.bb.get(key) if key else _asset_name(self.p.get('ActionClass'))
        if not name:
            return FAILED
        ok = ctx.perform_action(name)
        if key:
            ctx.bb['TopScore'] = 0.0                # consumed; the service fills it again
        return RUNNING if ok else FAILED

    def update(self, ctx):
        return RUNNING if ctx.action_running() else SUCCEEDED


class SetState(Task):
    """try_set_behemoth_state_bttask: StateChangeType 0 (default) adds the state, 1 removes it."""

    def start(self, ctx):
        ctx.set_state(_enum_index(self.p.get('State')), add=_enum_index(self.p.get('StateChangeType'), 0) == 0)
        return SUCCEEDED


class SetBool(Task):
    def start(self, ctx):
        key = _key(self.p, 'Key') or _key(self.p, 'ResultKey')
        val = self.p.get('Value')
        if val is None:
            src = self.p.get('SourceData') or {}
            val = next((v for k, v in src.items() if k.startswith('ConstantBool')), True) if self.type_is_set_key else False
        ctx.bb[key] = bool(val)
        return SUCCEEDED

    type_is_set_key = False


class SetBlackboardKey(SetBool):
    type_is_set_key = True


class Pivot(Task):
    """Turn toward the target. ctx.face returns the end time of a turn montage (wait for it) or None (turned at once)."""

    def start(self, ctx):
        tgt = ctx.target_pos(_key(self.p, 'PivotTarget') or 'AttackTarget')
        self.until = ctx.face(tgt) if tgt is not None else None
        return RUNNING if self.until is not None else SUCCEEDED

    def update(self, ctx):
        return SUCCEEDED if ctx.now >= self.until else RUNNING


class StateMontage(Task):
    """Rage enter/exit, tired: play the montage (the state itself is set by the following state task)."""

    def start(self, ctx):
        mont = self.p.get('Montage')
        name = _asset_name(mont) if isinstance(mont, dict) else mont
        self.until = ctx.play_montage(name) if name else None
        return RUNNING if self.until is not None else SUCCEEDED

    def update(self, ctx):
        return SUCCEEDED if ctx.now >= self.until else RUNNING


TASKS = {
    'BTTask_Wait': Wait,
    'BTTask_MoveTo': MoveTo,
    'BTTask_MoveToAndFace': MoveTo,
    'BTTask_RunEQSQuery': EQSQuery,
    'BTTask_BehemothAction_C': BehemothAction,
    'BTTask_PerformActionClass_C': PerformAction,
    'try_set_behemoth_state_bttask_C': SetState,
    'bttask_set_boolean_C': SetBool,
    'set_blackboard_key_bttask_C': SetBlackboardKey,
    'pivot_towards_target_bttask_C': Pivot,
    'bttask_enter_rage_state_C': StateMontage,
    'bttask_exit_rage_state_C': StateMontage,
    'bttask_enter_tired_state_C': StateMontage,
    # instant in this server: growls/generic actions, coordination, audio, armour, flee timers
    'generic_action_bttask_C': Instant,
}


# ---------------------------------------------------------------------------------------------------------------
# services: fn(ctx, props)

def _s_evaluate_action(ctx, p):
    choice = ctx.choose_action()
    ctx.bb['TopScore'] = choice[1] if choice else 0.0
    ctx.bb['ChosenAttack'] = choice[0] if choice else None


def _s_pick_target(ctx, p):
    ctx.pick_target()


SERVICES = {
    'evaluate_action_btservice_C': _s_evaluate_action,
    'try_pick_new_target_bts_C': _s_pick_target,
    # cosmetic or not needed by this server
    'BTService_UpdateDirections_C': lambda ctx, p: None,
    'calc_knowntargetavgpos_btservice_C': lambda ctx, p: None,
    'get_action_helper_btservice_C': lambda ctx, p: None,
    'BTService_LookAlongPath_C': lambda ctx, p: None,
}
