"""Server-side requirement checks: Slayer's Path node unlocks (parent, quests, currency) and authoritative spends.

Rules come from world/journey_rules.json (tools/export_journey_rules.py). Currency is charged with a
gameserver-authorised POST /inventory (removeStackedItems); the backend refuses an insufficient balance
with 409, so the balance check and the spend are one atomic step.
"""
import json
import os
import uuid
from urllib.request import Request, urlopen
from urllib.error import HTTPError

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES = os.path.join(HERE, 'world', 'journey_rules.json')
REDEEMED = 3


def load_rules(path=RULES):
    try:
        with open(path) as f:
            return json.load(f)['nodes']
    except (OSError, ValueError, KeyError):
        return None


def check_node(rules, node, unlocked, quest_status):
    """-> (ok, reason). quest_status(quest_id) -> EQuestStatus or None."""
    n = rules.get(node)
    if n is None:
        return False, 'unknown node'
    if node in unlocked:
        return False, 'already unlocked'
    if n['parents'] and not any(p in unlocked for p in n['parents']):
        return False, 'no unlocked parent (%s)' % ','.join(n['parents'])
    for q in n['quests']:
        if quest_status(q) != REDEEMED:
            return False, 'quest %s not redeemed (status %s)' % (q, quest_status(q))
    if n['other_objectives']:
        return False, 'objectives not tracked yet: %s' % ','.join(n['other_objectives'])
    return True, 'ok'


def spend(account, character, costs, headers, base='http://127.0.0.1:3001'):
    """Charge currencies in one transaction. -> (ok, detail)."""
    items = [{'catalogId': c['currency'], 'quantity': int(c['amount'])} for c in costs if int(c['amount']) > 0]
    if not items:
        return True, 'free'
    body = json.dumps({'accountId': account, 'characterId': character, 'transactionId': uuid.uuid4().hex.upper(),
                       'removeStackedItems': items}).encode()
    hdr = dict(headers); hdr['Content-Type'] = 'application/json'
    try:
        with urlopen(Request(base + '/inventory', data=body, headers=hdr, method='POST'), timeout=3) as r:
            return True, 'HTTP %d' % r.status
    except HTTPError as e:
        return False, 'HTTP %d %s' % (e.code, e.read()[:120].decode('utf-8', 'replace'))
