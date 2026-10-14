"""Replace template values with server-derived data (per session).

Each applier edits decoded template values in place (OrderedFields keys are
assigned individually so the encoder sees them) and returns a short report.
Sources: backend player records (inventory / loadout / persistent loadout),
backend entitlements, and the server clock. Values with no server source yet
(player journey, weapon experience) keep the template defaults, which are a
fresh-player state.
"""
import json
from urllib.request import Request, urlopen

from .inventory_wire import item_identity_crc

# Equipped weapon catalog prefix -> EWeaponType (SDK Archon_structs.hpp).
WEAPON_TYPES = {'IH': 1, 'GA': 2, 'EB': 3, 'CB': 4, 'MS': 5, 'DP': 6, 'AC': 7, 'BW': 8}
_TICKS_PER_SECOND = 10_000_000
_UNIX_EPOCH_TICKS = 621355968000000000      # FDateTime ticks at 1970-01-01


def _blocks(entries, cls):
    return [b for e in entries for b in e.parsed.blocks if b.cls_name == cls and b.values is not None]


def _rpc_fields(entries, func):
    return [(b, i) for e in entries for b in e.parsed.blocks
            for i, f in enumerate(b.fields or []) if f[1] == func and f[2] == 'rpc']


def _persistent(records):
    p = records.get('persistent') if isinstance(records, dict) else None
    if isinstance(p, str):
        p = json.loads(p)
    return p if isinstance(p, dict) else {}


def load_entitlements(base='http://127.0.0.1:3001', headers=None):
    with urlopen(Request(base + '/entitlementsv2', headers=headers or {}), timeout=1) as response:
        data = json.load(response)
    ents = data.get('entitlements') if isinstance(data, dict) else None
    if not isinstance(ents, list):
        raise ValueError('entitlements response has no list')
    return ents


def apply_player_state(entries, records, entitlements=None):
    """PlayerState: Loot (pending grants), PlayerRoleId, Weapon, Entitlements."""
    ps = _blocks(entries, 'player_state_bp_C')
    if len(ps) != 1:
        raise ValueError('expected one decoded PlayerState block')
    v = ps[0].values
    report = {}
    # Loot = items granted at login (AwardImmediately). The template's rows are
    # another profile's starter grants; the backend has no pending-grant
    # queue, so nothing is pending: send the default (empty) array.
    if 'Loot' in v:
        del v['Loot']
        report['loot'] = 0
    loadout = records.get('active_loadout') or {}
    role = (loadout.get('player_role') or {}).get('item_id')
    if isinstance(role, str) and role:
        v['PlayerRoleId'] = role
        report['role'] = role
    weapon = (loadout.get('weapon') or {}).get('item_id') or ''
    wtype = WEAPON_TYPES.get(weapon[3:5]) if weapon.startswith('WP_') else None
    if wtype is not None:
        v['Weapon'] = wtype
        report['weapon_type'] = wtype
    if entitlements is not None:
        elems = {}
        for i, ent in enumerate(e for e in entitlements if isinstance(e, dict) and isinstance(e.get('name'), str)):
            elems[i] = {'Entitlements[].Name': ent['name'],
                        'Entitlements[].ActivationDateUtc': 0,
                        'Entitlements[].ExpirationDateUtc': 0,
                        'Entitlements[].ExperienceBoostPercentage': 0.0}
        if elems:
            v['Entitlements'] = {'count': len(elems), 'elems': elems}
        elif 'Entitlements' in v:
            del v['Entitlements']
        report['entitlements'] = len(elems)
    return report


def apply_manual_emotes(entries, records):
    """ArchonPublicLoadoutComponent.ManualEmoteItemIdCrcs from the persistent loadout's emote wheel."""
    emotes = _persistent(records).get('manual_emotes')
    if not isinstance(emotes, list) or not any(isinstance(x, str) and x for x in emotes):
        return {'manual_emotes': 0}          # nothing equipped: default (empty) array, as recorded
    blocks = _blocks(entries, 'ArchonPublicLoadoutComponent')
    if len(blocks) != 1:
        raise ValueError('expected one public loadout component')
    crcs = [item_identity_crc(x) if isinstance(x, str) and x else 0 for x in emotes]
    blocks[0].values['ManualEmoteItemIdCrcs'] = {
        'count': len(crcs), 'elems': {i: {'ManualEmoteItemIdCrcs[]': c} for i, c in enumerate(crcs)}}
    return {'manual_emotes': sum(1 for c in crcs if c)}


def unix_to_ticks(t):
    return _UNIX_EPOCH_TICKS + int(t * _TICKS_PER_SECOND)


def apply_schedule(entries, now_unix):
    """Roll each recorded rotation window forward by whole periods so it covers `now`."""
    now = unix_to_ticks(now_unix)
    rolled = 0
    for block, i in _rpc_fields(entries, 'ClientReceiveCurrentSchedule'):
        idx, name, kind, params = block.fields[i]
        for item in params.get('CurrentScheduleItems', []):
            start, end = item['CurrentScheduleItems[].StartTime'], item['CurrentScheduleItems[].EndTime']
            period = end - start
            if period <= 0:
                continue
            k = (now - start) // period
            if k:
                item['CurrentScheduleItems[].StartTime'] = start + k * period
                item['CurrentScheduleItems[].EndTime'] = end + k * period
                rolled += 1
    return {'schedule_rolled': rolled}


GAUNTLET_SEASON = 'Season05'          # newest row of gauntlet_mode_seasons_setup (2.1.1 data table)
GAUNTLET_SEASON_DAYS = 28


def apply_gauntlet(entries, now_unix, season=GAUNTLET_SEASON, days=GAUNTLET_SEASON_DAYS):
    """PlayerGauntletSeasonData.SeasonData: the dedicated server fills it from the Gauntlet config service.
    The template's is empty (EndDateTime 0 -> "ends in 0 seconds"). Use the newest season, in a window of
    whole `days` periods that contains now."""
    blocks = _blocks(entries, 'PlayerGauntletSeasonData')
    if len(blocks) != 1:
        return {'gauntlet': 0}
    period = days * 86400
    start = int(now_unix // period * period)
    v = blocks[0].values
    v['SeasonData.GauntletSeasonID'] = season
    v['SeasonData.StartDateTime'] = unix_to_ticks(start)
    v['SeasonData.EndDateTime'] = unix_to_ticks(start + period)
    v['SeasonData.bInitialized'] = True
    return {'gauntlet': season, 'gauntlet_ends_unix': start + period}


def apply_streaming(entries):
    """Every loaded Ramsgate sublevel visible. The template hides 9 loaded district sublevels (pub, cave,
    shady alley, ...); a hidden level has no collision, so walking into its area falls through the map.
    Unloaded levels (events, cinematics) stay unloaded."""
    shown = 0
    for block, i in _rpc_fields(entries, 'ClientUpdateMultipleLevelsStreamingStatus'):
        params = block.fields[i][3]
        for item in next(iter(params.values()), []):
            lk = next((k for k in item if k.endswith('bNewShouldBeLoaded')), None)
            vk = next((k for k in item if k.endswith('bNewShouldBeVisible')), None)
            if lk and vk and item[lk] and not item[vk]:
                item[vk] = 1
                shown += 1
    return {'sublevels_shown': shown}


def apply_all(entries, records, entitlements, now_unix):
    report = {}
    report.update(apply_player_state(entries, records, entitlements))
    report.update(apply_manual_emotes(entries, records))
    report.update(apply_schedule(entries, now_unix))
    report.update(apply_gauntlet(entries, now_unix))
    report.update(apply_streaming(entries))
    return report
