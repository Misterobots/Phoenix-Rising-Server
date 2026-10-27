"""Gear stats and armor set bonuses (GearCells2Feature on; atlas topics/armor.md s.4, AArchonEquipment::AddGearStatsToOwner 0x27F1400).

For each equipped armor piece the server adds its primary gear stat's magnitude straight onto the attribute base values
(ApplyModToAttribute, Additive; no gameplay effect), then counts distinct pieces per gear set and applies every set tier whose
RequiredSources is reached, once per tier.

Magnitude = BaseMagnitude + level x PerLevelMagnitude is UNCONFIRMED (item getter 0x28C43D0 not read).
Set tiers: only the attribute part is replicated here. A tier's buffs, effects and abilities are logged, not applied (no buff
system on this server yet); in 2.1.1 only BUGBEAR, CROWBEAR, ELECTROQUILL, FULGAR and SNOWFLAKE tiers have any.
"""
import json
import os

PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'world', 'player', 'gear.json')


def load(path=PATH):
    if not os.path.exists(path):
        return None
    with open(path, encoding='utf-8') as fh:
        return json.load(fh)


def level_of(value):
    """CurrentLevel (EUpgradeableItemLevel) as an index: an int, 'EUpgradeableItemLevel::Level_03', or None (level 0)."""
    if isinstance(value, int):
        return value
    if isinstance(value, str) and '_' in value:
        try:
            return int(value.rsplit('_', 1)[1])
        except ValueError:
            pass
    return 0


def magnitude(piece, level):
    return float(piece.get('base') or 0.0) + level * float(piece.get('per_level') or 0.0)


def equipped_armor(session):
    """-> [(ItemId, level)] of the open armor actors (ItemId AR_...)."""
    out = []
    for e in session.entries:
        if not e.open or e.parsed is None:
            continue
        a = next((b for b in e.parsed.blocks if b.actor and b.values), None)
        if a is not None and str(a.values.get('ItemId') or '').startswith('AR_'):
            out.append((a.values['ItemId'], level_of(a.values.get('CurrentLevel'))))
    return out


def compute(data, pieces):
    """-> ({(set class, attribute): added magnitude}, {set: [tiers reached]}) for the equipped pieces."""
    mods, sources = {}, {}
    for item, level in pieces:
        piece = data['armor'].get(item)
        if not piece:
            continue
        if piece.get('stat'):
            for owner, attr in data['stats'].get(piece['stat'], []):
                mods[(owner, attr)] = mods.get((owner, attr), 0.0) + magnitude(piece, level)
        if piece.get('set'):
            sources.setdefault(piece['set'], set()).add(item)
    tiers = {s: [t for t in data['sets'].get(s, []) if t['required'] <= len(ids)] for s, ids in sources.items()}
    return mods, {s: t for s, t in tiers.items() if t}


def setup(session, log=print):
    """-> replication entries raising the pawn's attribute-set base values by the gear stats."""
    from .attributes import find_block, property_update
    data = load()
    if data is None:
        log('GEAR skipped (no world/player/gear.json; python -m tools.export_gear)')
        return []
    pieces = equipped_armor(session)
    mods, tiers = compute(data, pieces)
    pawn = next((e for e in session.entries if e.open and e.cls == 'BP_PlayerCharacter_C'), None)
    out = []
    by_set = {}
    for (owner, attr), add in mods.items():
        by_set.setdefault(owner, {})[attr] = add
    for owner, values in by_set.items():
        header = find_block(session, pawn.ch, owner) if pawn is not None else None
        if header is None:
            log('GEAR %s not on the pawn; %s not sent' % (owner, values))
            continue
        cur = header.values or {}
        out.append(property_update(session, pawn.ch, header, {a: float(cur.get(a, 0.0) or 0.0) + v for a, v in values.items()}))
    log('GEAR pieces=%s stats=%s' % (pieces, {a: round(v, 4) for (_, a), v in mods.items()}))
    for s, ts in tiers.items():
        for t in ts:
            extra = t['buffs'] + t['effects'] + t['abilities']
            log('GEAR set %s %d-piece: %s' % (s, t['required'], ('NOT APPLIED (no buff system): %s' % extra) if extra else 'empty'))
    return out
