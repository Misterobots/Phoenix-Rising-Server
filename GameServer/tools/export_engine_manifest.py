"""Record which locally generated engine data an island encounter uses (ENGINE_PORT_PLAN.md step 3).

python -m tools.export_engine_manifest [--hunt Adventure_IslandA_D24]

The data itself is game data generated from the user's own 2.1.1 install and stays out of git (GameServer/world/). This writes
data/2.1.1/islands/<hunt>.engine_data.json: the SHA-256 of every file the encounter's behemoth runtime reads (action set,
Blueprint schema, behaviour trees reachable from the species' tree, montage and root-motion dumps for each action, terrain grid),
so a later run can tell whether its local data is the data that was verified.
"""
import argparse
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.normpath(os.path.join(HERE, '..'))
sys.path.insert(0, SERVER)

from mpgs.behemoth import bt  # noqa: E402
from mpgs.behemoth.mover import MONTAGE_DIR, ROOTMOTION_DIR, sequences_for_montage  # noqa: E402
from mpgs.behemoth.rock_ai import DEFAULT_BT_DIR  # noqa: E402

# Per hunt: the species variant, its behaviour tree (lerawr_beta_bp_C defaults: BehaviorTree = lerawr_beta_new_bt) and terrain name.
HUNTS = {
    'Adventure_IslandA_D24': {'variant': 'lerawr_beta', 'tree': 'lerawr_beta_new_bt', 'terrain': 'adventure_moss_triforce'},
}


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def rel(path):
    return os.path.relpath(path, SERVER).replace(os.sep, '/')


def tree_files(root_name):
    """Dump files of root_name and every sub-tree it runs, by BehaviorTree export name."""
    by_tree = {}
    for fn in sorted(os.listdir(DEFAULT_BT_DIR)):
        if fn.endswith('.json'):
            with open(os.path.join(DEFAULT_BT_DIR, fn), encoding='utf-8') as f:
                exports = json.load(f)
            if isinstance(exports, list):
                for e in exports:
                    if isinstance(e, dict) and e.get('Type') == 'BehaviorTree':
                        by_tree[e['Name']] = os.path.join(DEFAULT_BT_DIR, fn)
    ts = bt.TreeSet.load(DEFAULT_BT_DIR)
    seen, missing, todo = set(), set(), [root_name]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        root = ts.roots.get(name)
        if root is None:
            missing.add(name)
            continue
        stack = [root]
        while stack:
            n = stack.pop()
            if n.type == 'BTTask_RunBehavior':
                todo.append(bt._asset_name(n.props.get('BehaviorAsset')))
            stack.extend(c for c, _ in n.children)
    return {t: by_tree[t] for t in sorted(seen - missing)}, sorted(missing)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--hunt', default='Adventure_IslandA_D24')
    a = ap.parse_args()
    cfg = HUNTS[a.hunt]
    world = os.path.join(SERVER, 'world')
    files, problems = {}, []

    action_set = os.path.join(world, 'behemoths', cfg['variant'] + '.json')
    schema = os.path.join(world, 'behemoths', cfg['variant'] + '_schema.json')
    for p in (action_set, schema):
        files[rel(p)] = sha256(p)

    trees, missing = tree_files(cfg['tree'])
    problems += ['behaviour tree not dumped: ' + m for m in missing]
    for p in trees.values():
        files[rel(p)] = sha256(p)

    with open(action_set, encoding='utf-8') as f:
        actions = json.load(f)['actions']
    for key, act in actions.items():
        mp = os.path.join(MONTAGE_DIR, act['montage'] + '.json')
        if not os.path.exists(mp):
            problems.append('%s: montage dump missing (%s)' % (key, act['montage']))
            continue
        files[rel(mp)] = sha256(mp)
        for seq in sequences_for_montage(mp):
            rp = os.path.join(ROOTMOTION_DIR, seq + '.rootmotion.json')
            if os.path.exists(rp):
                files[rel(rp)] = sha256(rp)
            else:
                problems.append('%s: root motion missing for %s' % (key, seq))

    for ext in ('.bin', '.json'):
        p = os.path.join(world, 'maps', cfg['terrain'] + '_height' + ext)
        files[rel(p)] = sha256(p)

    out = {
        'hunt': a.hunt, 'variant': cfg['variant'], 'behaviourTree': cfg['tree'], 'terrain': cfg['terrain'] + '_height',
        'actions': sorted(actions), 'trees': sorted(trees), 'problems': problems,
        'regenerate': [
            'dotnet <AssetDump.dll> dumpprefix <abs dir> archon/content/monsters/aq/lerawr/ai/   (and .../blueprints/, _shared/ai/; '
            'create each dir first) -> copy the tree dumps to world/dumps/bt/<asset>.json',
            'world/behemoths/all/<variant>.json from python -m tools.export_behemoth_all <classes.tsv> <work dir>; '
            'copy to world/behemoths/<variant>.json',
            'python -m tools.export_behemoth_schema --label <variant> --chain world/hunt_source/<variant>_bp.json '
            'world/hunt_source/<archetype>_bp.json --base world/hunt_source/Bp_Behemoth.json --out world/behemoths/<variant>_schema.json',
            'dotnet <AssetDump.dll> texture <abs dir> Archon/Content/Maps/islands/adventure/Moss_Triforce/adventure_moss_triforce.umap; '
            'python -m tools.export_terrain world/hunt_source/adventure_moss_triforce.json <tex dir> --name adventure_moss_triforce',
        ],
        'files': files,
    }
    path = os.path.join(SERVER, 'data', '2.1.1', 'islands', a.hunt + '.engine_data.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(out, f, indent=1)
        f.write('\n')
    print('%s: %d files, %d trees, %d actions, problems: %s' % (rel(path), len(files), len(trees), len(actions), problems or 'none'))


if __name__ == '__main__':
    main()
