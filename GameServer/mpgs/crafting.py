"""Crafting and catalog purchases: ServerCraftItemFromCatalogData / ServerPurchaseItemFromCatalog.

Costs come from world/recipes.json (tools/export_recipes.py): the Level_00 crafting or purchase recipe of the
item. The server checks the quest unlock gate and the client's stacks, then spends the ingredients and adds
the item(s) in one backend transaction (all or nothing), and shows both with V2 inventory operations.
"""
import json
import os
import uuid

from . import rewards

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECIPES = os.path.join(HERE, 'world', 'recipes.json')
MAX_QTY = 99


def load_recipes(path=RECIPES):
    try:
        with open(path) as f:
            return json.load(f)['recipes']
    except (OSError, ValueError, KeyError):
        return None


def plan(recipes, item, kind, qty, owned, quest_status):
    """-> (ok, reason, spends [(catalogId, amount)], grants [(catalogId, amount)]).
    kind 'craft' or 'purchase'; owned {catalogId: quantity} of the client's stacks; quest_status(name) -> status."""
    rec = (recipes or {}).get(item)
    if rec is None or kind not in rec:
        return False, 'no %s recipe for %s' % (kind, item), [], []
    qty = max(1, min(int(qty or 1), MAX_QTY)) if kind == 'purchase' else 1
    if kind == 'craft':
        for quest, allowed in rec.get('unlock') or []:
            if quest_status(quest) not in allowed:
                return False, 'locked: quest %s status %s, needs %s' % (quest, quest_status(quest), allowed), [], []
    spends = [(c, a * qty) for c, a in rec[kind]]
    short = [(c, a, owned.get(c, 0)) for c, a in spends if owned.get(c, 0) < a]
    if short:
        return False, 'insufficient %s' % ', '.join('%s %d/%d' % (c, h, a) for c, a, h in short), spends, []
    return True, 'ok', spends, [(item, rec.get('num', 1) * qty)]


def build_transaction(account, character, spends, grants):
    tx = uuid.uuid4().hex.upper()
    body = rewards.build_transaction(account, character, 'craft:%s' % tx, grants)
    body['transactionId'] = tx
    body['removeStackedItems'] = [{'catalogId': c, 'quantity': a} for c, a in spends]
    return body


def v2_operations(state, body):
    """Spends first, then the added items (the order the client applies them)."""
    spends = [(r['catalogId'], r['quantity']) for r in body.get('removeStackedItems', [])]
    return rewards.v2_spend_operations(state, spends) + rewards.v2_operations(state, body)
