"""Trace which behaviour-tree nodes, montages and floor heights a behemoth's encounter path actually reaches (ENGINE_PORT_PLAN.md
step 4). Offline: no client, no wire. The real bt.Runner and the generic
BehemothDirector (Brain, Mover, BehemothContext) on the island terrain; a scripted player lands, walks to the behemoth and
fights it.

python -m tools.trace_behemoth_bt [--hunt Adventure_IslandA_D24] [--seconds 180] [--seed 1] [--json out.json]

The report lists node types by how often the running path entered them, split into handled and unhandled (unhandled decorators
pass, tasks succeed at once, services do nothing), plus requested montages and whether a dump/root-motion curve exists for them.
"""
import argparse
import collections
import json
import math
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.normpath(os.path.join(HERE, '..'))
sys.path.insert(0, SERVER)

from mpgs.behemoth import bt  # noqa: E402
from mpgs.behemoth.director import BehemothDirector, Species  # noqa: E402
from mpgs.behemoth.rock_ai import DEFAULT_BT_DIR  # noqa: E402
from mpgs.behemoth.mover import montage_curve  # noqa: E402
from mpgs.world.terrain import Terrain  # noqa: E402

TICK = 1.0 / 15.0          # the island server's movement loop


class ScriptedPlayer:
    """Lands at the first PlayerStart, walks at run speed to stand off the behemoth, then circles it at melee range."""

    def __init__(self, start, speed=600.0, standoff=350.0):
        self.loc = list(start)
        self.speed, self.standoff = speed, standoff
        self.pawn_state = {'loc': list(start), 'vel': [0.0, 0.0, 0.0]}
        self.angle = None

    def tick(self, dt, behemoth_loc, terrain):
        dx, dy = behemoth_loc[0] - self.loc[0], behemoth_loc[1] - self.loc[1]
        d = math.hypot(dx, dy)
        if d > self.standoff + 50.0:
            step = min(self.speed * dt, d - self.standoff)
            vx, vy = dx / d * self.speed, dy / d * self.speed
            self.loc[0] += dx / d * step
            self.loc[1] += dy / d * step
        else:
            if self.angle is None:
                self.angle = math.atan2(-dy, -dx)
            self.angle += 0.6 * dt           # slow strafe around the behemoth
            nx = behemoth_loc[0] + self.standoff * math.cos(self.angle)
            ny = behemoth_loc[1] + self.standoff * math.sin(self.angle)
            vx, vy = (nx - self.loc[0]) / dt, (ny - self.loc[1]) / dt
            self.loc[0], self.loc[1] = nx, ny
        g = terrain.ground_z(self.loc[0], self.loc[1])
        if g is not None:
            self.loc[2] = g + 100.0
        self.pawn_state = {'loc': list(self.loc), 'vel': [vx, vy, 0.0]}


def trace(hunt='Adventure_IslandA_D24', seconds=180.0, seed=1):
    with open(os.path.join(SERVER, 'data', '2.1.1', 'islands', hunt + '.engine_data.json'), encoding='utf-8') as f:
        manifest = json.load(f)
    with open(os.path.join(SERVER, 'data', '2.1.1', 'islands', hunt + '.contract.json'), encoding='utf-8') as f:
        contract = json.load(f)
    enc = contract['encounter']
    start = contract['starts'][0]['location']
    center = min(enc['centers'], key=lambda c: math.dist(c['location'][:2], start[:2]))
    terrain = Terrain.load(os.path.join(SERVER, 'world', 'maps', manifest['terrain']))
    rng = random.Random(seed)
    player = ScriptedPlayer(start)
    lines = []

    def log(msg):
        lines.append(msg)

    trees = bt.TreeSet.load(DEFAULT_BT_DIR)
    d = BehemothDirector(Species.load(hunt), terrain, center['location'], center['rotation'][1], lambda: player.pawn_state,
                         trees=trees, rng=rng, log=log)
    ctx, runner = d.ctx, d.runner

    entered = collections.Counter()
    names = collections.defaultdict(set)
    subtrees = collections.Counter()
    orig_handler, orig_check, orig_services = runner._task_handler, runner._check, runner._run_services

    def handler(node):
        entered[('task', node.type)] += 1
        names[('task', node.type)].add('%s:%s' % (node.tree, node.name))
        return orig_handler(node)

    def check(dec, peek=False):
        if not peek:
            entered[('decorator', dec.type)] += 1
            names[('decorator', dec.type)].add('%s:%s' % (dec.tree, dec.name))
        return orig_check(dec, peek)

    def services(now):
        for f in runner.stack or [bt.Frame(runner.root)]:
            for s in f.node.services:
                if now >= runner._service_due.get(id(s), 0.0):
                    entered[('service', s.type)] += 1
                    names[('service', s.type)].add('%s:%s' % (s.tree, s.name))
        return orig_services(now)

    runner._task_handler, runner._check, runner._run_services = handler, check, services
    orig_advance = runner._advance

    def advance(now):
        before = len(runner.stack)
        orig_advance(now)
        for f in runner.stack[before:]:
            if f.node is trees.roots.get(f.node.tree):
                subtrees[f.node.tree] += 1
    runner._advance = advance

    floor, path_log, states_seen, events = [], [], set(), []
    t = 0.0
    while t < seconds:
        player.tick(TICK, d.loc, terrain)
        events.extend(d.tick(t))
        states_seen |= set(ctx.states)
        if int(t * 15) % 15 == 0:
            g = terrain.ground_z(d.loc[0], d.loc[1])
            floor.append((round(t, 1), [round(v, 1) for v in d.loc], None if g is None else round(g, 1)))
            path_log.append((round(t, 1), runner.path()[-4:]))
        t += TICK

    attack_montages = {a['montage'] for a in d.species.actions.values() if a['montage'] != 'b_aq_lerawr_00_loco_jumpoverturn_180_right_00_Montage'}
    handled = lambda kind, typ: (typ in bt.TASKS or typ == 'BTTask_RunBehavior') if kind == 'task' else \
        (typ in bt.DECORATORS) if kind == 'decorator' else (typ in bt.SERVICES)
    rows = [{'kind': k, 'type': typ, 'count': n, 'handled': handled(k, typ), 'nodes': sorted(names[(k, typ)])[:6]}
            for (k, typ), n in entered.most_common()]
    xy = [p for _, p, _ in floor]
    return {
        'hunt': hunt, 'tree': manifest['behaviourTree'], 'seconds': seconds, 'seed': seed,
        'center': center['name'], 'vent_ground_z': center.get('groundZ'),
        'nodes': rows, 'unhandled': [r for r in rows if not r['handled']],
        'subtrees_entered': dict(subtrees.most_common()),
        'montages': [(round(e[3], 2), e[1], montage_curve(e[1]) is not None) for e in events if e[0] == 'montage'],
        'attacks': sum(1 for e in events if e[0] == 'montage' and e[1] in attack_montages),
        'events': collections.Counter(e[0] for e in events),
        'states_seen': sorted(states_seen), 'blackboard_keys_set': sorted(k for k, v in ctx.bb.items() if v is not None),
        'max_distance_from_center': round(max(math.dist(p[:2], center['location'][:2]) for p in xy), 1),
        'floor_samples': floor[::5], 'path_samples': path_log[::10], 'log': lines[:200],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--hunt', default='Adventure_IslandA_D24')
    ap.add_argument('--seconds', type=float, default=180.0)
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--json')
    a = ap.parse_args()
    r = trace(a.hunt, a.seconds, a.seed)
    if a.json:
        with open(a.json, 'w', encoding='utf-8') as f:
            json.dump(r, f, indent=1)
    print('tree %s, %.0fs, %d attacks, states %s, max %.0f from %s' % (r['tree'], r['seconds'], r['attacks'], r['states_seen'],
                                                                       r['max_distance_from_center'], r['center']))
    print('sub-trees entered:', r['subtrees_entered'])
    print('UNHANDLED on the path:')
    for x in r['unhandled']:
        print('  %5d %-9s %-45s %s' % (x['count'], x['kind'], x['type'], ', '.join(x['nodes'][:3])))
    print('handled:', ', '.join('%s(%d)' % (x['type'], x['count']) for x in r['nodes'] if x['handled']))
    print('montages:', collections.Counter((m[1], m[2]) for m in r['montages']).most_common())
    print('events:', dict(r['events']))


if __name__ == '__main__':
    main()
