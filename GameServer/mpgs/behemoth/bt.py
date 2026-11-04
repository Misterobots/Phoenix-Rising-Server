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
- BTComposite_SimpleParallel (UE 5.1.1 BTComposite_SimpleParallel.cpp): child 0 is the main task, child 1 the
  background branch. A main task that finishes at once is the parallel's result. While the main task runs, the
  background branch runs and restarts whenever it finishes. FinishMode AbortBackground (enum 0, the default every
  cooked tree uses) aborts the background when the main task finishes, and the main task's result is the parallel's.
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

from . import eqs

SUCCEEDED, FAILED, RUNNING = 'succeeded', 'failed', 'running'
PARALLEL = 'BTComposite_SimpleParallel'


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
        self.children = []              # composites: [(child Node, [decorator Nodes], DecoratorOps)]
        self.services = []              # [Node]

    @property
    def is_composite(self):
        return self.type in ('BTComposite_Selector', 'BTComposite_Sequence', PARALLEL)

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
                    n.children.append((child,
                                       [d for d in (get(r) for r in c.get('Decorators') or []) if d is not None],
                                       c.get('DecoratorOps') or []))
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

    def reach_radius(self, acceptable, key, include_agent=True, include_goal=True):
        """UPathFollowingComponent::HasReachedInternal: AcceptanceRadius + goal radius + agent radius x MinAgentRadiusPct (1.1).
        The base context knows no radii."""
        return acceptable

    def action_class(self, name):
        """Blueprint class of the named action (e.g. 'lerawr_short_bite_atk_C'), or None. BTDeco_CheckChosenAction compares it."""
        return None

    def generic_action_montage(self, action_type):
        """Montage selected by Bp_Behemoth's GenericActionLookupTable for a generic action type."""
        return None

    def remove_all_targets(self):
        """aggro_bpc RemoveAllTargets."""
        self.bb['AttackTarget'] = None

    def lock_target(self, key):
        """Bp_Behemoth.LockTarget(blackboard[key]) (btdeco_lock_target on execution start)."""

    def clear_locked_target(self):
        """Bp_Behemoth.ClearLockedTarget() (btdeco_lock_target on execution finish)."""

    def patrol_center_point(self):
        """EQContext_CurrentPatrolCenterPoint_C location, or None."""
        return None

    def nav_raycast(self, start, end):
        """ARecastNavMesh::BatchRaycast for one ray -> (hit location, did hit). Without a navmesh nothing is hit."""
        return tuple(end), False


class Frame:
    def __init__(self, node, decorators=(), started=0.0):
        self.node = node
        self.decorators = list(decorators)      # the decorators that guarded entry into this node
        self.idx = 0                            # next child to try (composites)
        self.started = started
        self.forced = any(d.type == 'BTDecorator_ForceSuccess' for d in self.decorators)
        self.time_limit = next((float(d.props.get('TimeLimit', 5.0)) for d in self.decorators
                                if d.type == 'BTDecorator_TimeLimit'), None)
        self.main = None                        # simple parallel: (main task Frame, handler) while it runs
        self.background_started = None          # simple parallel: tick the background branch last started


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
        self._tick_parallel_mains()
        if self.task is not None:
            if self._should_abort(now):
                self._abort_task()
                for f in reversed(self.stack):        # aborted frames finish too (UE calls ReceiveExecutionFinishAI)
                    self._abort_main(f)
                    self._on_exit(f.decorators)
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
            if node.type == PARALLEL:
                if frame.main is None:
                    self._start_parallel_main(frame, now)
                    continue
                if len(node.children) < 2 or frame.background_started == now:
                    return                         # wait for the main task (UE postpones a repeat within one search)
                if frame.idx >= len(node.children):
                    frame.idx = 1                  # the background branch restarts while the main task runs
                frame.background_started = now
            if node.is_composite:
                if frame.idx >= len(node.children):
                    self.stack.pop()
                    if node.children:
                        self._finish(frame, FAILED if node.type == 'BTComposite_Selector' else SUCCEEDED)
                    else:
                        # UBTCompositeNode::FindChildToExecute returns to the parent with LastResult unchanged, so an
                        # empty composite passes on the result its parent entered it with: a selector only moves on
                        # after a failure, a sequence after a success (sally_bt's empty Sequence_18 sits before the
                        # attack selector and must not end the combat selector).
                        parent = self.stack[-1].node if self.stack else None
                        self._finish(frame, FAILED if parent is not None and parent.type == 'BTComposite_Selector'
                                     else SUCCEEDED)
                    continue
                child, decos, ops = node.children[frame.idx]
                frame.idx += 1
                if not self._check_decorators(decos, ops):
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
        for f in self.stack:
            self._abort_main(f)
        self.stack = []

    # ---- simple parallel -----------------------------------------------------------------------------------------
    def _start_parallel_main(self, frame, now):
        """Run child 0 of a simple parallel. A main task that finishes at once finishes the parallel."""
        child, decos, ops = frame.node.children[0]
        frame.idx = 1
        if not self._check_decorators(decos, ops):
            self.stack.pop()
            self._finish(frame, SUCCEEDED if any(d.type == 'BTDecorator_ForceSuccess' for d in decos) else FAILED)
            return
        self._on_enter(decos)
        main = Frame(child, decos, now)
        h = self._task_handler(child)
        r = h.start(self.ctx)
        self.last_leaf = child
        if r == RUNNING:
            frame.main = (main, h)
            return
        self._on_exit(decos)
        self.stack.pop()
        self._finish(frame, SUCCEEDED if main.forced else r)

    def _tick_parallel_mains(self):
        """Update running main tasks; one that finishes aborts its background branch (AbortBackground)."""
        for i, frame in enumerate(self.stack):
            if frame.main is None:
                continue
            main, h = frame.main
            r = h.update(self.ctx)
            if r == RUNNING:
                continue
            frame.main = None
            self._on_exit(main.decorators)
            if self.task is not None:
                self._abort_task()
            for f in reversed(self.stack[i + 1:]):
                self._abort_main(f)
                self._on_exit(f.decorators)
            del self.stack[i:]
            self._finish(frame, SUCCEEDED if main.forced else r)
            return

    def _abort_main(self, frame):
        if frame.main is not None:
            main, h = frame.main
            h.abort(self.ctx)
            self._on_exit(main.decorators)
            frame.main = None

    def _finish_child(self, parent, child_frame, result):
        if child_frame.forced:
            result = SUCCEEDED
        self._apply(parent, result)

    def _finish(self, frame, result):
        """`frame` has been popped with `result`; apply it to its parent (the new top of stack)."""
        self._on_exit(frame.decorators)
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
            for child, decos, ops in node.children[:max(active, 0)]:
                aborting = [d for d in decos if str(d.props.get('FlowAbortMode', '')).endswith(('LowerPriority', 'Both'))]
                if aborting and self._check_decorators(decos, ops, peek=True):
                    return True
        return False

    def _check_decorators(self, decos, ops=None, peek=False):
        """Evaluate UE's serialized DecoratorOps prefix expression.

        A cooked stream such as And(2), Test(0), Or(2), Test(1), Test(2)
        means decorator 0 AND (decorator 1 OR decorator 2). Assets without an
        operation stream retain UE's default all-decorators behavior.
        """
        if not ops:
            return all(self._check(d, peek=peek) for d in decos)
        pos = 0

        def expression():
            nonlocal pos
            if pos >= len(ops):
                raise ValueError('truncated BT decorator logic operation stream')
            op = ops[pos]
            pos += 1
            name = str(op.get('Operation', '')).rsplit('::', 1)[-1]
            number = int(op.get('Number', 0))
            if name == 'Test':
                if number < 0 or number >= len(decos):
                    raise ValueError('BT decorator logic references missing decorator %d' % number)
                return self._check(decos[number], peek=peek)
            if name in ('And', 'Or'):
                values = [expression() for _ in range(number)]
                return all(values) if name == 'And' else any(values)
            if name == 'Not':
                return not expression()
            raise ValueError('unsupported BT decorator logic operation %r' % name)

        result = expression()
        if pos != len(ops):
            raise ValueError('BT decorator logic operation stream has unused entries')
        return result

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
            elif d.type == 'cooldown_btdeco_C':
                mem = self._deco_state.setdefault(id(d), {})
                mem['active'] = True                # ReceiveObserverActivatedAI
                if int(d.props.get('CooldownResetType', 0)) == 1:
                    mem['last_finished'] = self.ctx.now
                    mem['duration'] = _new_blueprint_cooldown(self.ctx, d.props)
            elif d.type == 'btdeco_lock_target_C':  # ReceiveExecutionStartAI: LockTarget(bb[LockToTargetName])
                if self.ctx.bb.get(d.props.get('LockToTargetName', 'AttackTarget')) is not None:
                    self.ctx.lock_target(d.props.get('LockToTargetName', 'AttackTarget'))

    def _on_exit(self, decos):
        for d in decos:
            if d.type == 'cooldown_btdeco_C':
                mem = self._deco_state.setdefault(id(d), {})
                mem['active'] = False               # ReceiveObserverDeactivatedAI
                if int(d.props.get('CooldownResetType', 0)) == 0:
                    mem['last_finished'] = self.ctx.now
                    mem['duration'] = _new_blueprint_cooldown(self.ctx, d.props)
            if d.type == 'btdeco_lock_target_C':    # ReceiveExecutionFinishAI: ClearLockedTarget()
                self.ctx.clear_locked_target()

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


def _d_has_generic_action(ctx, p, mem, peek):
    """has_generic_action_btdeco_C: FindMontageInGenericActionSet(ActionType) on the behemoth returns a valid montage."""
    return bool(ctx.generic_action_montage(p.get('ActionType')))


def _d_in_state(ctx, p, mem, peek):
    in_state = _enum_index(p.get('State')) in ctx.states
    return in_state if _enum_index(p.get('StateRequirement'), 0) == 0 else not in_state


def _d_requirements(ctx, p, mem, peek):
    req = p.get('BehemothRequirements') or {}
    def meets(value, actual, condition):
        if value in (None, '', 'None'):
            return True
        if actual is None:
            return False
        operation = str(condition or 'Require').rsplit('::', 1)[-1]
        exclude = operation in ('Exclude', 'NewEnumerator1')
        matched = str(value).lower() == str(actual).lower()
        return not matched if exclude else matched

    classification = next((v for k, v in req.items()
                           if k.startswith('RequireBehemothClassificationName')), None)
    classification_condition = next((v for k, v in req.items()
                                     if k.startswith('ClassificationCondition')), None)
    if not meets(classification, ctx.variant, classification_condition):
        return False

    archetype = next((v for k, v in req.items() if k.startswith('RequireBehemothArchetypeName')), None)
    archetype_condition = next((v for k, v in req.items() if k.startswith('ArchetypeCondition')), None)
    if not meets(archetype, getattr(ctx, 'archetype', None), archetype_condition):
        return False

    state = next((v for k, v in req.items() if k.startswith('StateRequirement')), None)
    state_condition = next((v for k, v in req.items() if k.startswith('StateRequirementType')), None)
    if state not in (None, '', 'None'):
        in_state = _enum_index(state) in ctx.states
        op = str(state_condition or 'RequiresState').rsplit('::', 1)[-1]
        if op in ('ExcludedByState', 'Exclude', 'NewEnumerator1'):
            if in_state:
                return False
        elif not in_state:
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


def _simplified_bb_float(ctx, value, default=0.0):
    """Evaluate the float form used by cooldown_btdeco_C's Min/MaxCooldown fields."""
    if not isinstance(value, dict):
        return float(value) if isinstance(value, (int, float)) else float(default)
    key = next((str(v) for k, v in value.items()
                if k.startswith('BlackboardKeyName') and v not in (None, '', 'None')), None)
    if key:
        current = ctx.bb.get(key)
        if isinstance(current, (int, float)):
            return float(current)
    constant = next((v for k, v in value.items() if k.startswith('ConstantFloat')), default)
    return float(constant if constant is not None else default)


def _new_blueprint_cooldown(ctx, p):
    lo = _simplified_bb_float(ctx, p.get('MinCooldown'), 0.0)
    hi = _simplified_bb_float(ctx, p.get('MaxCooldown'), lo)
    return ctx.rng.uniform(min(lo, hi), max(lo, hi))


def _d_blueprint_cooldown(ctx, p, mem, peek):
    """Model the 2.1.1 cooldown_btdeco_C PerformConditionCheckAI bytecode.

    The Blueprint lazily selects a cooldown, then checks elapsed time since the later of
    CurrentFightStateStartTime and LastCooldownFinishedTime. The two observer callbacks
    start/finish the cooldown according to CooldownResetType; Runner mirrors those callbacks
    in _on_enter/_on_exit for the child path.
    """
    if mem.get('active'):
        return True
    if not mem.get('initialised'):
        mem['initialised'] = True
        mem['duration'] = _new_blueprint_cooldown(ctx, p)
    start_key = p.get('CooldownStartTimeBlackboardName', 'CurrentFightStateStartTime')
    start = max(float(ctx.bb.get(start_key) or 0.0), float(mem.get('last_finished', -99999.0)))
    return ctx.now - start >= float(mem.get('duration', 0.0))


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


def _bb_float(ctx, sbd, default=0.0):
    """archon_function_library.SimplifiedBBData_GetFloat (inferred from the struct: a named key wins, else ConstantFloat)."""
    if not isinstance(sbd, dict):
        return default
    key = next((v for k, v in sbd.items() if k.startswith('BlackboardKeyName')), None)
    if key not in (None, '', 'None'):
        v = ctx.bb.get(key)
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else default
    return float(next((v for k, v in sbd.items() if k.startswith('ConstantFloat')), default))


def _d_check_chosen_action(ctx, p, mem, peek):
    """BTDeco_CheckChosenAction (bytecode): GetObjectClass(bb[ChosenAttackKeyName]) == ActionClass. With ActionClass unset (None),
    as in lerawr_beta_new_bt, it passes exactly when no attack is chosen; the inverted copy guards PerformActionClass."""
    chosen = ctx.bb.get(p.get('ChosenAttackKeyName', 'ChosenAttack'))
    have = ctx.action_class(chosen) if chosen else None
    want = _asset_name(p.get('ActionClass')) if p.get('ActionClass') else None
    if chosen and have is None and want is None:
        return False                              # a chosen action whose class is unknown is still an object, not None
    return have == want


def _d_archon_distance(ctx, p, mem, peek):
    """archon_distance_check_btdeco (bytecode): target (horizontally predicted by PredictTargetHorizFuturePosTime when it is an
    actor) within MaxDistance and not within MinDistance of Origin; both bounds are SimplifiedBBData floats. False when either
    location is missing. IsWithinDistanceFast is taken as a 3D distance (inferred)."""
    okey, tkey = _key(p, 'Origin') or 'SelfPos', _key(p, 'Target') or 'AttackTarget'
    o, t = ctx.target_pos(okey), ctx.target_pos(tkey)
    if o is None or t is None:
        return False
    pt = float(p.get('PredictTargetHorizFuturePosTime', 0.0))
    if pt > 0.0:
        vx, vy = ctx.target_vel(tkey)
        t = (t[0] + vx * pt, t[1] + vy * pt, t[2])
    d = math.dist(o, t)
    return d <= _bb_float(ctx, p.get('MaxDistance')) and not d <= _bb_float(ctx, p.get('MinDistance'))


def _d_lock_target(ctx, p, mem, peek):
    """btdeco_lock_target (bytecode): aggro_bpc.IsTargetValid(current target) == ShouldBeValid (CDO: True). Lock/clear on enter/exit."""
    return (ctx.bb.get('AttackTarget') is not None) == bool(p.get('ShouldBeValid', True))


# ---- creature (ally raptor) nodes: read from their Blueprint bytecode (atlas pseudo/<class>.txt, 2026-10-26) -------------------
def _d_in_range(ctx, p, mem, peek):
    """BTD_InRange: DistanceMin <= ControlledPawn.GetDistanceTo(bb[AttackTarget]) <= DistanceMax (3D)."""
    a, b = ctx.target_pos('SelfPos'), ctx.target_pos(_key(p, 'AttackTarget') or 'AttackTarget')
    if a is None or b is None:
        return False
    return float(p.get('DistanceMin', 0.0)) <= math.dist(a, b) <= float(p.get('DistanceMax', 1e12))


def _d_random_choice(ctx, p, mem, peek):
    """BTD_RandomChoice: RandomFloatInRange(0, 1) <= RandomChoice."""
    return ctx.rng.random() <= float(p.get('RandomChoice', 0.5))


def _d_class_equal(*classes):
    """BTD_ClassEqualTo<X>raptor: ClassIsChildOf(class of bb[SelfActor], ...). Araptor's version also accepts uraptor."""
    return lambda ctx, p, mem, peek: getattr(ctx, 'creature_class', None) in classes


def _d_distance_from_behemoth(ctx, p, mem, peek):
    """BTDecorator_DistanceFromBehemothCheck: 2D distance from bb[Origin] to the behemoth's bounds centre (bb[TargetBehemoth],
    moved by its predicted horizontal movement) in (MinDistance, bound sphere radius + MaxDistance). False without a behemoth."""
    o = ctx.target_pos(_key(p, 'Origin'))
    b = ctx.behemoth_bounds(_key(p, 'TargetBehemoth'), float(p.get('PredictTargetHorizFuturePosTime', 0.0)))         if hasattr(ctx, 'behemoth_bounds') else None
    if o is None or b is None:
        return False
    centre, radius = b
    d = _dist2d(o, centre)
    return float(p.get('MinDistance', 0.0)) < d < radius + float(p.get('MaxDistance', 0.0))


def _d_percent_chance(ctx, p, mem, peek):
    """btdeco_percentchance: RandomFloat() <= bb[BlackboardPercentChanceKey] when that key is set and > 0, else
    PercentChance > 0 and RandomFloat() <= PercentChance."""
    k = _key(p, 'BlackboardPercentChanceKey')
    v = ctx.bb.get(k) if k else None
    if isinstance(v, (int, float)) and v > 0:
        return ctx.rng.random() <= v
    pc = float(p.get('PercentChance', 0.0))
    return pc > 0 and ctx.rng.random() <= pc


def _d_has_tags(ctx, p, mem, peek):
    """actor_has_gameplaytags_btdeco: the actor in bb[ActorBBKey] has the RequireTags (ctx.actor_tags)."""
    tags = set(getattr(ctx, 'actor_tags', lambda key: ())(_key(p, 'ActorBBKey') or 'SelfActor'))
    want = [t if isinstance(t, str) else (t or {}).get('TagName') for t in p.get('RequireTags') or []]
    return all(w in tags for w in want)


def _d_navtest(ctx, p, mem, peek):
    """Source-mapped navtest_btdeco_C: negate PathHitsNavMesh for scaled Displacement.

    The context must supply an actual query result. Missing Recast data fails closed and is
    reported, instead of treating this reached Sally decorator as unconditional success.
    """
    scale = getattr(ctx, 'behemoth_scale', lambda: 1.0)()
    if isinstance(scale, dict):
        scale = tuple(float(scale.get(k, 1.0)) for k in ('X', 'Y', 'Z'))
    elif isinstance(scale, (int, float)):
        scale = (float(scale),) * 3
    path = [tuple(float(point.get(k, 0.0)) * scale[i]
                  for i, k in enumerate(('X', 'Y', 'Z')))
            for point in p.get('Displacement') or []]
    query = getattr(ctx, 'path_hits_navmesh', None)
    if query is None:
        ctx.log('navtest_btdeco_C cannot query PathHitsNavMesh: navmesh data unavailable')
        return False
    result = query(path, 'NavigationQueryFilter_NoDynamic')
    if result is None:
        ctx.log('navtest_btdeco_C PathHitsNavMesh result unavailable')
        return False
    return not bool(result)


DECORATORS = {
    'BTDeco_CheckChosenAction_C': _d_check_chosen_action,
    'archon_distance_check_btdeco_C': _d_archon_distance,
    'btdeco_lock_target_C': _d_lock_target,
    'BTDecorator_Blackboard': _d_blackboard,
    'BTDecorator_ForceSuccess': lambda ctx, p, m, peek: True,
    'BTDecorator_TimeLimit': lambda ctx, p, m, peek: True,
    'BTDecorator_Cooldown': _d_cooldown,
    'cooldown_btdeco_C': _d_blueprint_cooldown,
    'behemoth_in_state_btdeco_C': _d_in_state,
    'has_generic_action_btdeco_C': _d_has_generic_action,
    'passes_behemoth_requirements_btdeco_C': _d_requirements,
    'btdeco_variablecooldown_C': _d_variable_cooldown,
    'is_island_gamemode_btdeco_C': _d_island_mode,
    'BTDeco_MemberOfClassIsPresent_C': _d_member_present,
    'current_target_is_valid_btdeco_C': _d_target_valid,
    'enter_combat_check_btdeco_C': _d_enter_combat,
    'BTDecorator_DistanceCheck_C': _d_distance,
    'BTDecorator_ConeCheck': _d_cone,
    'BTD_InRange_C': _d_in_range,
    'BTD_RandomChoice_C': _d_random_choice,
    'BTD_ClassEqualToAraptor_C': _d_class_equal('araptor_00_bp_C', 'uraptor_00_bp_C'),
    'BTD_ClassEqualToPraptor_C': _d_class_equal('praptor_00_bp_C'),
    'BTD_ClassEqualToSraptor_C': _d_class_equal('sraptor_00_bp_C'),
    'BTD_ClassEqualToUraptor_C': _d_class_equal('uraptor_00_bp_C'),
    'BTDecorator_DistanceFromBehemothCheck_C': _d_distance_from_behemoth,
    'actor_has_gameplaytags_btdeco_C': _d_has_tags,
    'btdeco_percentchance_C': _d_percent_chance,
    # no allies / no messages / no flee despawn in a one-behemoth session
    'check_next_message_type_btdeco_C': lambda ctx, p, m, peek: False,
    'should_despawn_on_flee_btdeco_C': lambda ctx, p, m, peek: False,
    # cosmetic or always-true in this server
    'set_behemoth_loco_mode_btdeco_C': lambda ctx, p, m, peek: True,
    'set_state_btdeco_C': lambda ctx, p, m, peek: True,
    'navtrace_btdeco_C': lambda ctx, p, m, peek: True,
    'navtest_btdeco_C': _d_navtest,
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
        # BTTask_MoveTo defaults (UE 5.1.1 BTTask_MoveTo.cpp / AISystem.cpp): AcceptableRadius 5, and the reach test includes the
        # agent's and the goal's collision radii (bFinishMoveOnGoalOverlap = true). The context adds the radii (reach_radius).
        self.radius = ctx.reach_radius(float(self.p.get('AcceptableRadius', 5.0)), _key(self.p, 'BlackboardKey'),
                                       bool(self.p.get('bReachTestIncludesAgentRadius', True)),
                                       bool(self.p.get('bReachTestIncludesGoalRadius', True)))
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
        cfg = {c.get('ParamName'): c.get('Value') for c in req.get('QueryConfig') or []}
        if hasattr(ctx, 'run_eqs'):                    # creatures: the context knows their queries by name
            return SUCCEEDED if ctx.run_eqs(name, _key(self.p, 'BlackboardKey') or 'TempLocation', cfg) else FAILED
        if name in eqs.QUERIES:
            p = eqs.run(name, cfg, ctx.patrol_center_point(), ctx.nav_raycast)
            if p is None:
                return FAILED
            ctx.bb[_key(self.p, 'BlackboardKey') or 'MoveDestination'] = p
            return SUCCEEDED
        if 'patrol' not in name.lower() and 'NextPointAroundTargetPoint' not in name:
            return FAILED
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


class GenericAction(Task):
    """generic_action_bttask_C: resolve ActionType to a generic montage and wait for it to finish."""

    def start(self, ctx):
        montage = ctx.generic_action_montage(self.p.get('ActionType'))
        if not montage:
            return FAILED
        self.until = ctx.play_montage(montage)
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


class WaitForDistance(Task):
    """wait_for_distance_bttask (bytecode; CDO MaximumTimeToWait 10, OriginPosKeyName SelfPos, RequiredBlackboardKeyWaitType 1,
    BlackBoardCompareOperator ==). ReceiveExecuteAI stamps the start; each tick (ReceiveTickAI):
    - distance: the target (horizontally predicted by TimeInFutureToPredictTargetPos when it is an actor) is in band when it is
      within MaxDistance and not within MinDistance of the origin (IsWithinDistanceFast: 3D). It passes when in band with
      TimeWaitType 1 (RunUntilConditionIsTrue) or out of band with TimeWaitType 0; a missing location skips the test.
    - blackboard: CompareBlackboardValues(BlackboardKeyNameToCheck) passes when true with RequiredBlackboardKeyWaitType 1 or
      false with 0; a key name of None skips it.
    Both passing finishes true; otherwise MaximumTimeToWait > 0 elapsed finishes false."""

    def start(self, ctx):
        self.started = ctx.now
        return RUNNING

    def update(self, ctx):
        p = self.p
        origin = ctx.target_pos(p.get('OriginPosKeyName', 'SelfPos'))
        tkey = _key(p, 'TargetKey')
        target = ctx.target_pos(tkey) if tkey else None
        ok = True
        if origin is not None and target is not None:
            future = float(p.get('TimeInFutureToPredictTargetPos', 0.0))
            if ctx.bb.get(tkey) == 'player' and future > 0.0:
                vx, vy = ctx.target_vel(tkey)
                target = (target[0] + vx * future, target[1] + vy * future, target[2])
            d = math.dist(origin, target)
            in_band = d <= _bb_float(ctx, p.get('MaxDistanceData')) and not d <= _bb_float(ctx, p.get('MinDistanceData'))
            ok = in_band == (_enum_index(p.get('TimeWaitType'), 0) == 1)
        name = p.get('BlackboardKeyNameToCheck', 'None')
        if ok and name not in (None, '', 'None'):
            ok = _compare_bool_key(ctx, name, p) == (_enum_index(p.get('RequiredBlackboardKeyWaitType'), 1) == 1)
        if ok:
            return SUCCEEDED
        limit = float(p.get('MaximumTimeToWait', 10.0))
        if limit > 0.0 and self.started + limit <= ctx.now:
            return FAILED
        return RUNNING


def _compare_bool_key(ctx, name, p):
    """archon_function_library.CompareBlackboardValues for a Bool key (blackboard_comparable_key_type value 2) with the CDO
    operator ==: GetValueAsBool(name) == the right side (a named key, else ConstantBool). An unset key reads false."""
    right = p.get('BlackboardCompareData') or {}
    rkey = next((v for k, v in right.items() if k.startswith('BlackboardKeyName')), None)
    want = bool(ctx.bb.get(rkey)) if rkey not in (None, '', 'None') else \
        bool(next((v for k, v in right.items() if k.startswith('ConstantBool')), False))
    return bool(ctx.bb.get(name)) == want


class Pivot(Task):
    """Turn toward the target. ctx.face returns the end time of a turn montage (wait for it) or None (turned at once)."""

    def start(self, ctx):
        tgt = ctx.target_pos(_key(self.p, 'PivotTarget') or _key(self.p, 'BlackboardKey') or 'AttackTarget')
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


class AudioExitFleeState(Task):
    """Set the behemoth music state to 6, then finish with that call's result.

    The Blueprint bytecode performs no blackboard or gameplay mutation. Music
    state is a presentation side effect that this server simulation does not
    implement, so preserve the task's zero-duration control-flow success.
    """

    pass


class HandleStagger(Task):
    """handle_stagger_bttask (shared_handle_stagger_bt, gated by 'StaggerCount Is Greater Than 0'): plays the picked stagger montage,
    then SetValueAsInt(StaggerCount, 0) and FinishExecute. The director already plays the stagger and holds the tree until it ends
    (BehemothDirector.stagger / staggered_until), so here only the count is cleared - without it the branch reran forever and the
    behemoth stood still until the next stagger (2026-10-27)."""

    def start(self, ctx):
        ctx.bb['StaggerCount'] = 0
        return SUCCEEDED


class AddStaggerArmor(Task):
    """Source-mapped add_stagger_armor_bttask_C for authored single-behemoth trees.

    The exported Sally node overrides ArmorDuration to 10 seconds while its
    inherited UnscaledArmorAmount remains 0.0. Debug1HitStagger is omitted
    from the component CDO and defaults false, so the amount remains zero.
    The context records its local GUID stack entry; the source stack is not
    replicated. Non-zero authored amounts still require component scaling and
    timed armor behavior and fail closed here.
    """

    def start(self, ctx):
        amount = _simplified_bb_float(ctx, self.p.get('UnscaledArmorAmount'), 0.0)
        duration = _simplified_bb_float(ctx, self.p.get('ArmorDuration'), 0.0)
        if amount == 0.0:
            ctx.add_stagger_armor(amount, duration)
            return SUCCEEDED
        ctx.log('BT unsupported non-zero stagger armor amount %.3f' % amount)
        return FAILED


class PushCoordinationMessage(Task):
    """Model the Sally AllOtherAgents combat-entry message in a solo encounter.

    Phoenix currently has no AI coordination-group consumer. With the
    single-behemoth composition there are no other recipients, so submission
    has no target-side effect and the task preserves sequence control flow.
    """

    def start(self, ctx):
        if ctx.push_coordination_message(self.p.get('Message') or {}):
            return SUCCEEDED
        return FAILED


class RemoveAllTargets(Task):
    """remove_all_targets_bttask_C: the owner's aggro component RemoveAllTargets (native, body RVA 0x2650AE0: removes
    every target entry, current target index -1, OnCurrentTargetChanged broadcast), then FinishExecute(true)."""

    def start(self, ctx):
        ctx.remove_all_targets()
        return SUCCEEDED


class ResetFrustration(Task):
    """reset_frustration_bttask_C: the owner's aggro component ResetFrustration, then FinishExecute(true)."""

    def start(self, ctx):
        ctx.reset_frustration()
        return SUCCEEDED


TASKS = {
    'BTTask_Wait': Wait,
    'reset_frustration_bttask_C': ResetFrustration,
    'remove_all_targets_bttask_C': RemoveAllTargets,
    'handle_stagger_bttask_C': HandleStagger,
    'add_stagger_armor_bttask_C': AddStaggerArmor,
    'push_coordination_message_bttask_C': PushCoordinationMessage,
    'BTTask_MoveTo': MoveTo,
    'BTTask_MoveToAndFace': MoveTo,
    'BTTask_RunEQSQuery': EQSQuery,
    'BTTask_BehemothAction_C': BehemothAction,
    'BTTask_PerformActionClass_C': PerformAction,
    'try_set_behemoth_state_bttask_C': SetState,
    'bttask_set_boolean_C': SetBool,
    'set_blackboard_key_bttask_C': SetBlackboardKey,
    'wait_for_distance_bttask_C': WaitForDistance,
    'pivot_towards_target_bttask_C': Pivot,
    'bttask_enter_rage_state_C': StateMontage,
    'bttask_exit_rage_state_C': StateMontage,
    'bttask_enter_tired_state_C': StateMontage,
    'audio_exit_flee_state_bttask_C': AudioExitFleeState,
    'generic_action_bttask_C': GenericAction,
    'perform_action_montage_bttask_C': BehemothAction,   # NetworkedMontage.PlayMontage(Montage); finishes when it blends out
    'BTTask_RotateToFaceBBEntry': Pivot,
    'BTT_FocusTarget_C': Instant,                          # SetFocus: the creature faces its target while moving (ctx.face on move)
    'BTT_ClearFocus_C': Instant,
}


# ---------------------------------------------------------------------------------------------------------------
# services: fn(ctx, props)

def _s_evaluate_action(ctx, p):
    choice = ctx.choose_action()
    ctx.bb['TopScore'] = choice[1] if choice else 0.0
    ctx.bb['ChosenAttack'] = choice[0] if choice else None


def _s_pick_target(ctx, p):
    ctx.pick_target()


def _s_action_helper(ctx, p):
    """get_action_helper_btservice_C tick: EQ_FindBestActionHelper's best item into ActionHelperKey, cleared when none."""
    ctx.bb[_key(p, 'ActionHelperKey') or 'CurrentHelper'] = ctx.best_action_helper()


def _s_distance_from_target(ctx, p):
    """distance_from_target_service (bytecode): bb[Distance_key] = |pawn - bb[Target_key] actor| (3D), or 0 without a target."""
    t = ctx.target_pos(_key(p, 'Target_key') or 'AttackTarget') if ctx.bb.get(_key(p, 'Target_key') or 'AttackTarget') else None
    ctx.bb[_key(p, 'Distance_key') or 'DistanceFromAttackTarget'] = math.dist(ctx.target_pos('SelfPos'), t) if t else 0.0


def _s_rng_float(ctx, p):
    """rng_float_service_C: RandomFloat() -> Blackboard.SetValueAsFloat(RNG_Float_key)."""
    key = _key(p, 'RNG_Float_key')
    if key:
        ctx.bb[key] = ctx.rng.random()


SERVICES = {
    'rng_float_service_C': _s_rng_float,
    'distance_from_target_service_C': _s_distance_from_target,
    'evaluate_action_btservice_C': _s_evaluate_action,
    'try_pick_new_target_bts_C': _s_pick_target,
    'get_action_helper_btservice_C': _s_action_helper,
    'ally_try_pick_new_target_bts_C': lambda ctx, p: ctx.pick_ally_target(),   # bb[AttackTarget] = nearest ArchonBehemoth
    'set_loco_state_btservice_C': lambda ctx, p: ctx.set_loco(_enum_index(p.get('LocoState'))),   # loco table MovementSpeed
    'BTService_RunEQS': lambda ctx, p: ctx.run_eqs(_asset_name((p.get('EQSRequest') or {}).get('QueryTemplate')),
                                                   _key(p, 'BlackboardKey'), {}) if hasattr(ctx, 'run_eqs') else None,
    'set_gameplay_focus_btservice_C': lambda ctx, p: None,
    'enable_strafe_btservice_C': lambda ctx, p: None,
    # cosmetic or not needed by this server
    'BTService_UpdateDirections_C': lambda ctx, p: None,
    'calc_knowntargetavgpos_btservice_C': lambda ctx, p: None,
    'get_action_helper_btservice_C': lambda ctx, p: None,
    'BTService_LookAlongPath_C': lambda ctx, p: None,
}
