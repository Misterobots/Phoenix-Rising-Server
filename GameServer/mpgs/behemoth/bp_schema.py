"""Register the Rock Blueprint class chain in the replication Schema (the live dump only knows classes that were loaded).

The ClassReps order of a Blueprint class is its parent's reps followed by its own net properties in ChildProperties order
(checked against Bp_Behemoth_C in the live repdump: names and order match). The chain data comes from
world/behemoths/rock_schema.json (python -m tools.export_behemoth_schema).
"""
import json
import os

from ..repl.schema import Prop

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROCK_SCHEMA = os.path.join(HERE, 'world', 'behemoths', 'rock_schema.json')


def load(path=ROCK_SCHEMA):
    with open(path) as f:
        return json.load(f)


def extend(schema, data=None):
    """Add the chain (root-first: sq_genus_bp_C, sq_rock_archetype_bp_C, sq_rock_normal_bp_C) to `schema`. Returns the leaf class name."""
    data = data or load()
    leaf = None
    for c in reversed(data['classes']):                         # parents first
        parent = c['super']
        first = len(schema.net_props(parent)) if parent in schema.classes or parent in schema.inherit else 0
        props = []
        for i, p in enumerate(c['props']):
            d = {'n': p['n'], 'ri': first + i, 'rc': 0, 'off': 0, 'dim': 1, 'fl': p['fl'], 'k': p['k']}
            for k in ('st', 'oc', 'bsz'):
                if k in p:
                    d[k] = p[k]
            props.append(Prop(d))
        schema.classes[c['class']] = (parent, props)
        schema.inherit[c['class']] = [parent] + schema.inherit.get(parent, [])
        schema._layouts.pop(c['class'], None)
        leaf = c['class']
    return leaf
