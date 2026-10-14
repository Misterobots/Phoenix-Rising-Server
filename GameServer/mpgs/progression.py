"""Player and weapon levels: backend XP -> progression_bpc.PlayerProgress and PlayerState.WeaponExperienceDetails.

Reads GET /progression/<account> (per track: total `progress`) and the track curves (the backend's
data/progression_config.json, the GET /progression/config payload), and replicates the result. Curves:
`requirements[i].xp_required` is the XP to go from rank i to rank i+1 (rank 1 needs 0); the weapon curves
match the 2.1.1 weapon_experience_track_table export exactly.
"""
import json
import os
from urllib.parse import quote
from urllib.request import Request, urlopen

from .repl.decode import OrderedFields

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.environ.get('MPGS_PROGRESSION_CONFIG') or os.path.join(HERE, '..', 'Backend', 'data', 'progression_config.json')

# Catalog prefix of the equipped weapon (EWeaponType names in the SDK) -> experience track / EExperienceType.
WEAPON_TRACKS = {'IH': ('ExperienceTrack_Weapon_Hammer', 4), 'GA': ('ExperienceTrack_Weapon_Axe', 2),
                 'EB': ('ExperienceTrack_Weapon_Sword', 8), 'CB': ('ExperienceTrack_Weapon_ChainBlades', 3),
                 'MS': ('ExperienceTrack_Weapon_Spear', 6), 'DP': ('ExperienceTrack_Weapon_Repeaters', 5),
                 'AC': ('ExperienceTrack_Weapon_Strikers', 7)}
REPLICATED_PREFIXES = ('ExperienceTrack_', 'MasteryTrack_', 'PrestigeTrack_')


def load_curves(path=CONFIG):
    with open(path, encoding='utf-8-sig') as f:
        d = json.load(f)
    paths = (d.get('payload') or d).get('paths') or []
    out = {}
    for p in paths:
        req = sorted(p.get('requirements') or [], key=lambda r: r.get('rank_id', 0))
        if p.get('progression_id') and req:
            out[p['progression_id']] = [int(r.get('xp_required') or 0) for r in req]
    return out


def level(curve, total):
    """-> (rank, points into the rank, points needed for the next rank or 0 at max)."""
    rank, spent = 1, 0
    for i in range(1, len(curve)):
        if total >= spent + curve[i]:
            spent += curve[i]
            rank = i + 1
        else:
            return rank, total - spent, curve[i]
    return rank, total - spent, 0


def xp_for_rank(curve, rank):
    return sum(curve[1:max(1, min(rank, len(curve)))])


def load_progress(account, headers, base='http://127.0.0.1:3001'):
    with urlopen(Request(base + '/progression/' + quote(account, safe=''), headers=headers), timeout=3) as r:
        d = json.load(r)
    rows = d.get('payload') if isinstance(d, dict) else d
    return {row['progression_id']: int(row.get('progress') or 0) for row in rows or []
            if isinstance(row, dict) and row.get('progression_id')}


def player_progress_value(curves, progress):
    elems = {}
    for i, tid in enumerate(t for t in sorted(curves) if t.startswith(REPLICATED_PREFIXES)):
        total = progress.get(tid, 0)
        rank, pts, _ = level(curves[tid], total)
        e = OrderedFields()
        for k, v in (('TrackId', tid), ('Rank', rank), ('ConfirmedRank', rank), ('PremiumRank', 0),
                     ('ConfirmedPremiumRank', 0), ('RankPoints', pts), ('TotalPoints', total), ('bHasPremium', 0)):
            e['PlayerProgress[].' + k] = v
        elems[i] = e
    return {'count': len(elems), 'elems': elems}


def weapon_details(curves, progress, weapon_catalog):
    t = WEAPON_TRACKS.get((weapon_catalog or '')[3:5]) if (weapon_catalog or '').startswith('WP_') else None
    if t is None or t[0] not in curves:
        return None
    curve, total = curves[t[0]], progress.get(t[0], 0)
    rank, pts, need = level(curve, total)
    return {'WeaponExperienceDetails.ExperienceType': t[1], 'WeaponExperienceDetails.Level': rank,
            'WeaponExperienceDetails.MaxLevel': len(curve), 'WeaponExperienceDetails.LevelAmount': pts,
            'WeaponExperienceDetails.MaxLevelAmount': need, 'WeaponExperienceDetails.TotalAmount': total,
            'WeaponExperienceDetails.MaxTotalAmount': sum(curve), 'WeaponExperienceDetails.PrestigeLevel': 0}


def apply(entries, curves, progress, weapon_catalog):
    """PlayerState weapon details in place; returns the PlayerProgress array value for a progression block."""
    report = {}
    wd = weapon_details(curves, progress, weapon_catalog)
    if wd:
        for e in entries:
            for b in e.parsed.blocks:
                if b.cls_name == 'player_state_bp_C' and b.values is not None:
                    for k, v in wd.items():
                        b.values[k] = v
                    report['weapon_level'] = wd['WeaponExperienceDetails.Level']
    pp = player_progress_value(curves, progress)
    pl = curves.get('ExperienceTrack_PlayerLevel')
    if pl:
        report['player_level'] = level(pl, progress.get('ExperienceTrack_PlayerLevel', 0))[0]
    report['tracks'] = pp['count']
    return pp, report


def grant_to_level(account, curves, progress, target, headers, base='http://127.0.0.1:3001'):
    """Dev test characters: top the player and weapon experience tracks up to `target` with the backend's
    gameserver XP grant (POST /progression/<account>/<track>/<amount>). -> {track: granted}."""
    granted = {}
    for tid in ['ExperienceTrack_PlayerLevel'] + [t for t, _ in WEAPON_TRACKS.values()]:
        if tid not in curves:
            continue
        need = xp_for_rank(curves[tid], target) - progress.get(tid, 0)
        if need <= 0:
            continue
        req = Request('%s/progression/%s/%s/%d' % (base, quote(account, safe=''), tid, need), data=b'',
                      headers=headers, method='POST')
        with urlopen(req, timeout=3) as r:
            if r.status == 200:
                granted[tid] = need
    return granted


# Aetherite -> weapon XP (currency_tokens_catalog WeaponLevelXP, 2.1.1 export).
AETHERITE_XP = {'CURRENCY_WEAPONXP_SMALL': 100, 'CURRENCY_WEAPONXP_MEDIUM': 1000, 'CURRENCY_WEAPONXP_LARGE': 5000}


def weapon_track(weapon_catalog):
    return WEAPON_TRACKS.get((weapon_catalog or '')[3:5]) if (weapon_catalog or '').startswith('WP_') else None


def grant_xp(account, track, amount, headers, base='http://127.0.0.1:3001'):
    req = Request('%s/progression/%s/%s/%d' % (base, quote(account, safe=''), track, amount), data=b'',
                  headers=headers, method='POST')
    with urlopen(req, timeout=3) as r:
        return json.load(r).get('payload', {}).get('progress')


def experience_grant(curve, before, after, exp_type, item_id, increase):
    lb, pb, nb = level(curve, before)
    la, pa, na = level(curve, after)
    return {'ExperienceGrant.ExperienceType': exp_type, 'ExperienceGrant.LevelAmount': pa,
            'ExperienceGrant.MaxLevelAmount': na, 'ExperienceGrant.PreviousLevelAmount': pb,
            'ExperienceGrant.PreviousMaxLevelAmount': nb, 'ExperienceGrant.IncreaseAmount': increase,
            'ExperienceGrant.BoostAmount': 0, 'ExperienceGrant.Level': la, 'ExperienceGrant.PreviousLevel': lb,
            'ExperienceGrant.ItemId': item_id}
