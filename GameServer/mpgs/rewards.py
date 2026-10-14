"""Quest rewards: backend grant (gameserver POST /inventory) plus the matching V2 inventory operations.

Reward definitions come from world/quest_catalog.json (optional, generated locally):
`rewards` are granted on redeem, `on_accept` on start. Only Reward_GrantItem is granted for now; the other
kinds (weapon experience, entitlements, pass progress, FTUE advance, Generic UI-only) are logged.
"""
import hashlib
import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .inventory_v2 import post_operation_hash
from .inventory_wire import item_identity_crc

# Gear is instanced (one row per item); currencies, tokens, dyes, emotes, materials, ... are stacks
# (the split the live V2 inventory sync uses).
INSTANCED_PREFIXES = ('WP_', 'AR_', 'PART_', 'LT_', 'PR_')


def is_instanced(catalog_id):
    return catalog_id.startswith(INSTANCED_PREFIXES)


def grant_items(rewards):
    """[(catalogId, amount)] for the grantable rewards; the rest -> skipped list."""
    items, skipped = [], []
    for r in rewards or []:
        if r.get('class') == 'Reward_GrantItem' and r.get('item') and int(r.get('amount') or 0) > 0:
            items.append((r['item'], int(r['amount'])))
        elif r.get('class') != 'Reward_Generic':
            skipped.append(r.get('class'))
    return items, skipped


def _instance_id(seed, catalog_id, i):
    return hashlib.sha256(('quest-reward-instance:%s:%s:%d' % (seed, catalog_id, i)).encode()).hexdigest()[:26].upper()


def build_transaction(account, character, seed, items):
    """Deterministic transaction (seed = account + quest + kind): a retried grant is idempotent in the backend."""
    inst, stacks = [], {}
    for catalog, amount in items:
        if is_instanced(catalog):
            for i in range(amount):
                inst.append({'catalogId': catalog, 'instanceId': _instance_id(seed, catalog, i),
                             'itemData': None, 'updateVersion': 0})
        else:
            stacks[catalog] = stacks.get(catalog, 0) + amount
    tx = hashlib.sha256(('quest-reward:%s' % seed).encode()).hexdigest()[:32].upper()
    body = {'accountId': account, 'characterId': character, 'transactionId': tx}
    if inst:
        body['addInstancedItems'] = inst
    if stacks:
        body['addStackedItems'] = [{'catalogId': c, 'quantity': q} for c, q in sorted(stacks.items())]
    return body


def post_transaction(body, headers, base='http://127.0.0.1:3001'):
    hdr = dict(headers); hdr['Content-Type'] = 'application/json'
    try:
        with urlopen(Request(base + '/inventory', data=json.dumps(body).encode(), headers=hdr, method='POST'), timeout=3) as r:
            return True, r.status
    except HTTPError as e:
        return False, '%d %s' % (e.code, e.read()[:120].decode('utf-8', 'replace'))


def state_from_updates(updates):
    """The client's V2 item map after the initial sync (each update is an AddItem)."""
    state = []
    for u in updates or []:
        state.append({'instanceId': u['instanceId'], 'catalogIdCrc': u['catalogIdCrc'],
                      'quantity': u['quantity'], 'customData': u.get('customData', '')})
    return state


def v2_operations(state, body):
    """V2 ops for an applied transaction, mutating `state`: Add per new instance / new stack, Update for an
    existing stack (stacks use instanceId = catalogId)."""
    ops = []
    for it in body.get('addInstancedItems', []):
        rec = {'instanceId': it['instanceId'], 'catalogIdCrc': item_identity_crc(it['catalogId']), 'quantity': 1, 'customData': ''}
        state.append(rec)
        ops.append(dict(rec, mismatchCount=0, operation='AddItem', postOperationHash=post_operation_hash(state)))
    for it in body.get('addStackedItems', []):
        cur = next((r for r in state if r['instanceId'] == it['catalogId']), None)
        if cur is None:
            cur = {'instanceId': it['catalogId'], 'catalogIdCrc': item_identity_crc(it['catalogId']),
                   'quantity': it['quantity'], 'customData': ''}
            state.append(cur)
            op = 'AddItem'
        else:
            cur['quantity'] += it['quantity']
            op = 'UpdateItem'
        ops.append(dict(cur, mismatchCount=0, operation=op, postOperationHash=post_operation_hash(state)))
    return ops


def v2_spend_operations(state, spends):
    """V2 ops for consumed stacks [(catalogId, quantity)]: UpdateItem with the new quantity, RemoveItem at 0."""
    ops = []
    for catalog, qty in spends:
        cur = next((r for r in state if r['instanceId'] == catalog), None)
        if cur is None:
            continue
        cur['quantity'] -= qty
        if cur['quantity'] <= 0:
            state.remove(cur)
            rec = dict(cur, quantity=0)
            ops.append(dict(rec, mismatchCount=0, operation='RemoveItem', postOperationHash=post_operation_hash(state)))
        else:
            ops.append(dict(cur, mismatchCount=0, operation='UpdateItem', postOperationHash=post_operation_hash(state)))
    return ops
