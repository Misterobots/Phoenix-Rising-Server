"""Hunt Pass: the replicated pass record (HuntPassComponent.ActiveHuntPassData) and reward claims.

A pass is a progression path in the backend's progression config: `requirements[i].xp_required` is the XP from
rank i-1 to rank i (rank 0 needs none), `free_rewards[i]` / `premium_rewards[i]` are rank i's rewards. Rank N =
the first N thresholds reached. The backend keeps the confirmed (claimed) ranks; a claim grants the free rewards
of every rank above the confirmed one, then raises it. Premium needs the path's gating entitlement (not sold).
"""
import json
from urllib.parse import quote
from urllib.request import Request, urlopen

from . import progression


def load_path(pass_id, path=None):
    try:
        with open(path or progression.CONFIG, encoding='utf-8-sig') as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        return None
    return next((p for p in (cfg.get('payload') or cfg).get('paths') or [] if p.get('progression_id') == pass_id), None)


def rank(path, xp):
    reqs = sorted(path.get('requirements') or [], key=lambda r: r['rank_id'])
    need, r = 0, 0
    for req in reqs:
        if req['rank_id'] == 0:
            continue
        need += int(req.get('xp_required') or 0)
        if xp < need:
            return r, xp - (need - int(req.get('xp_required') or 0))
        r = req['rank_id']
    return r, 0


def record(pass_id, path, xp, confirmed, confirmed_premium=0):
    """ActiveHuntPassData element (FReplicatedHuntPassData)."""
    r, into = rank(path, xp) if path else (0, 0)
    top = max((q['rank_id'] for q in (path or {}).get('requirements') or []), default=0)
    out = {}
    for k, v in (('ID', (pass_id, 0)), ('bIsPrestige', False), ('bIsCompleted', bool(top) and r >= top),
                 ('bHasPremium', False), ('Rank', r), ('ConfirmedRank', min(confirmed, r)),
                 ('ConfirmedPremiumRank', min(confirmed_premium, r)), ('RankProgress', into)):
        out['ActiveHuntPassData[].' + k] = v
    return out


def claim_plan(path, confirmed, current):
    """-> (items [(catalogId, amount)], skipped [descriptions]) for the free rewards of ranks confirmed+1..current."""
    items, skipped = [], []
    by_rank = {r['rank_id']: r for r in path.get('free_rewards') or []}
    for n in range(confirmed + 1, current + 1):
        row = by_rank.get(n) or {}
        for s in row.get('stacked_items') or []:
            if int(s.get('quantity') or 0) > 0:
                items.append((s['catalog_id'], int(s['quantity'])))
        for i in (row.get('instanced_items') or []) + (row.get('ordered_instanced_items') or []):
            cid = i.get('catalog_id') if isinstance(i, dict) else i
            if cid:
                items.append((cid, 1))
        skipped += ['entitlement %s' % e for e in row.get('entitlements') or []]
    return items, skipped


def transaction_seed(account, pass_id, confirmed, current):
    return 'huntpass:%s:%s:%d-%d' % (account, pass_id, confirmed, current)


def confirm(account, pass_id, free_rank, headers, base='http://127.0.0.1:3001'):
    hdr = dict(headers); hdr['Content-Type'] = 'application/json'
    body = json.dumps({'free_rank': free_rank}).encode()
    url = '%s/progression/%s/%s/confirm' % (base, quote(account, safe=''), quote(pass_id, safe=''))
    with urlopen(Request(url, data=body, headers=hdr, method='POST'), timeout=3) as r:
        return r.status


def load_rows(account, headers, base='http://127.0.0.1:3001'):
    with urlopen(Request(base + '/progression/' + quote(account, safe=''), headers=headers), timeout=3) as r:
        d = json.load(r)
    rows = d.get('payload') if isinstance(d, dict) else d
    return {row['progression_id']: row for row in rows or [] if isinstance(row, dict) and row.get('progression_id')}
