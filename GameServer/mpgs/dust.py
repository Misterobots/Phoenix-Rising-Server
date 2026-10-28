"""Salvage: ServerDustItem(ItemInstanceID, Source) / ServerDustItemWithQuantity(ItemInstanceID, Quantity, Source).

What an item gives comes from data/2.1.1/dust.json (tools/export_dust.py): the catalog row's DustItemResult bundle.
Every dustable item in 2.1.1 is stacked, so the instance id is the catalog id. The server spends the stack and adds the
result in one backend transaction (crafting.build_transaction), shown with the same V2 operations as a craft.
Stand-in (labelled): ServerDustItem without a quantity dusts one item (the client's count is not in the message).
"""
import json
import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DUST = os.path.join(HERE, 'data', '2.1.1', 'dust.json')
MAX_QTY = 9999


def load(path=DUST):
    try:
        with open(path) as f:
            return json.load(f)['dust']
    except (OSError, ValueError, KeyError):
        return None


def plan(dust, item, qty, owned):
    """-> (ok, reason, spends [(catalogId, amount)], grants [(catalogId, amount)])."""
    result = (dust or {}).get(item)
    if not result:
        return False, 'no dust result for %s' % item, [], []
    qty = max(1, min(int(qty or 1), MAX_QTY))
    if owned.get(item, 0) < qty:
        return False, 'insufficient %s %d/%d' % (item, owned.get(item, 0), qty), [], []
    return True, 'ok', [(item, qty)], [(c, a * qty) for c, a in result]
