"""Slayer's Path (player journey) persistence: unlocked nodes are saved in the backend (gameserver-authorised
GET/POST /pjm/{account}) and replayed to the client at join as one ClientHandlePlayerJourneyUpdates."""
import json
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = 'Slayer_00'
UNLOCKED = 2


def load_unlocked(account, headers, base='http://127.0.0.1:3001'):
    with urlopen(Request(base + '/pjm/' + quote(account, safe=''), headers=headers), timeout=2) as r:
        d = json.load(r)
    nodes = ((d.get('payload') if isinstance(d, dict) else None) or {}).get('nodes') or {}
    return {k for k, n in nodes.items() if isinstance(n, dict) and n.get('node_status') == UNLOCKED}


def save_unlocked(account, node, headers, base='http://127.0.0.1:3001'):
    hdr = dict(headers); hdr['Content-Type'] = 'application/json'
    body = json.dumps({'node_id': node, 'node_status': UNLOCKED}).encode()
    with urlopen(Request(base + '/pjm/' + quote(account, safe=''), data=body, headers=hdr, method='POST'), timeout=3) as r:
        return r.status


def update_elems(nodes, rmap=('PlayerJourneyMap', 0)):
    """PlayerJourneyUpdates array value: one Unlocked status per node."""
    from .repl.decode import OrderedFields
    out = []
    for n in sorted(nodes):
        up = OrderedFields()
        for k, v in (('RuntimeMapId', rmap), ('NodeID', (n, 0)), ('OperationType', 0), ('NodeStatus', UNLOCKED),
                     ('ObjectiveOnlineId', ''), ('ObjectiveAmount', 0), ('ObjectiveStatus', 0)):
            up['PlayerJourneyUpdates[].' + k] = v
        out.append(up)
    return out
