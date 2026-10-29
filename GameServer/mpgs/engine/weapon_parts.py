"""Weapon parts on the server: what BP_BaseArchonWeapon does on the server when a weapon is equipped, and the part abilities.

Read from the game (python -m tools.atlas show BP_BaseArchonWeapon_C; world/player/weapon_parts.json from
tools/export_weapon_parts.py):
- the weapon's parts are its catalog row's WeaponPartSlots (DefaultWeaponPartID per slot; the repeaters' are all locked).
- IsServer: for each part, SpawnAndAddComponents loads weapon_part_catalog[part].PartComponent and adds it to the weapon
  (Array_Add WeaponPartComponents, replicated, OnRep_WeaponPartComponents); the component's WeaponPartID (replicated, OnRep ->
  SetWeaponPartType) is what GetWeaponPartByType matches; dp_base_part_bpc components also get EquippedWeaponParts.
  The weapon replicates EquippedWeaponParts (ItemIdCrc + SlotIndex) and WeaponPartIDs.
- the part component finds its character from the weapon's owner in BeginPlay (weapon_part_base_bpc).
- moves call the part (activate_weapon_part_gameplay_bpns -> ActivateWeaponPartGameplay); the DP receiver / grip send the
  gameplay events Combat.Shoot.X / Combat.Shoot.Y to the character, which trigger the part's Abilities
  (dp_weapon_ability_base_ab: InstancedPerActor, ReplicateYes) - granted by the server, as abilities.PlayerAbilities does.
Without the components the client's notify finds no part and the move does nothing (live 2026-10-26: repeater heavy and Q).
"""
import json
import os
import zlib

from ..repl import model
from .abilities import _info

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.join(HERE, 'world', 'player', 'weapon_parts.json')
POWER = os.path.join(HERE, 'data', '2.1.1', 'weapon_power.json')
DP_BASE_PART = 'dp_base_part_bpc_C'


def load(path=DATA):
    if not os.path.exists(path):
        return None
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def item_crc(item_id):
    """FItemIdRepl.ItemIdCrc: CRC32 of the id as UTF-32LE (matches the recorded PART_EB_SPECIAL_DEFAULT 1660171131)."""
    return zlib.crc32(item_id.encode('utf-32le'))


def split(path):
    """'/Game/x/y.y_C' -> ('/Game/x/y', 'y_C')."""
    package, cls = path.rsplit('.', 1)
    return package, cls


def default_parts(data, item_id):
    """-> [(slot, part id, part row)] for the weapon's default parts that exist in the part catalog."""
    out = []
    for s in (data or {}).get('weapons', {}).get(item_id) or []:
        row = data['parts'].get(s.get('default') or '')
        if row and row.get('component'):
            out.append((s['slot'], s['default'], row))
    return out


def part_abilities(parts):
    """-> [(package, class, what)] in the shape abilities.PlayerAbilities.grant_entry takes."""
    out = []
    for _, pid, row in parts:
        for path in row.get('abilities') or []:
            package, cls = split(path)
            out.append((package, cls, 'part %s ability' % pid))
    return out


def part_buffs(parts):
    """-> [(package, class)]: the parts' Buffs (weapon_part_catalog), buff actors the server adds to the player."""
    return [split(path) for _, _, row in parts for path in row.get('buffs') or []]


def load_power(path=POWER):
    if not os.path.exists(path):
        return {}
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def power_abilities(power, item_id):
    """-> [(package, class, what)]: the weapon's power row (weapon_progression_rework_power_table[ItemId]) RefAbilities, then
    RefSpecialAbilities1-5. No row grants nothing, as the game does (atlas weapon_power_and_abilities.md)."""
    row = power.get(item_id) or {}
    out = [split(p) + ('power RefAbilities',) for p in row.get('RefAbilities') or []]
    for n in range(1, 6):
        out += [split(p) + ('power RefSpecialAbilities%d' % n,) for p in row.get('RefSpecialAbilities%d' % n) or []]
    return out


def parts_entry(session, ch, weapon_cls, parts):
    """One reliable weapon-channel entry: the part component classes exported, one dynamic component per part (outer: the
    weapon actor) with its WeaponPartID, then the weapon's EquippedWeaponParts / WeaponPartIDs / WeaponPartComponents.
    -> (Entry, {part id: component guid})."""
    from ..genworld import Entry
    from ..loadout import register_atlas_class
    schema = session.world.schema
    g = session.next_live_guid
    exports, must_map, blocks, comps = [], [], [], {}
    equipped = [{'EquippedWeaponParts[].WeaponPartID.ItemIdCrc': item_crc(pid), 'EquippedWeaponParts[].SlotIndex': slot}
                for slot, pid, _ in parts]
    for slot, pid, row in parts:
        package, cls = split(row['component'])
        layout = cls if register_atlas_class(schema, cls, props_only=True) else 'weapon_part_base_bpc_C'
        pkg = _info(g() + 1, package)
        cls_info = _info(g() + 1, cls, outer=pkg)
        exports.append(cls_info)
        must_map.append(cls_info.guid)
        inst = g()
        values = {'WeaponPartID': pid}
        if DP_BASE_PART in ([layout] + schema.inherit.get(layout, [])):
            values['EquippedWeaponParts'] = {'count': len(equipped), 'elems': dict(enumerate(equipped))}
        b = model.Block()
        b.actor, b.rep, b.guid, b.stable, b.destroy = False, True, inst, False, False
        b.cls, b.outer = cls_info.guid, None                    # outer None: the weapon actor is the outer
        b.cls_name, b.values = layout, values
        b.tail = b.raw = b.fields = None
        b.ref = b.ref_name = None
        b.partial_properties = False
        blocks.append(b)
        comps[pid] = inst
    actor = model.Block()
    actor.actor, actor.rep, actor.guid, actor.stable, actor.destroy = True, True, 0, False, False
    actor.cls = actor.outer = actor.ref = actor.ref_name = None
    actor.cls_name = weapon_cls
    actor.values = {
        'EquippedWeaponParts': {'count': len(equipped), 'elems': dict(enumerate(equipped))},
        'WeaponPartIDs': {'count': len(parts), 'elems': {i: {'WeaponPartIDs[]': pid} for i, (_, pid, _) in enumerate(parts)}},
        'WeaponPartComponents': {'count': len(parts),
                                 'elems': {i: {'WeaponPartComponents[]': ('obj', comps[pid])} for i, (_, pid, _) in enumerate(parts)}}}
    actor.tail = actor.raw = actor.fields = None
    actor.partial_properties = False
    e = Entry()
    e.t, e.ch, e.cls, e.exports = 0, ch, session.chan_cls.get(ch), exports
    e.open = e.close = False
    e.must_map = True
    e.reliable = True
    e.patch_pairs = ()
    e.parsed = model.Parsed()
    e.parsed.must_map = must_map
    e.parsed.blocks = blocks + [actor]
    return e, comps


def equipped_weapon(session):
    """-> (channel, actor class, catalog ItemId) of the open weapon actor (equipment slot 1), or None."""
    for e in session.entries:
        if not e.open or e.parsed is None:
            continue
        a = next((b for b in e.parsed.blocks if b.actor and b.values), None)
        if a is None or not str(a.values.get('ItemId') or '').startswith('WP_'):
            continue
        return e.ch, e.cls, a.values['ItemId']
    return None


def setup(session, log=print):
    """The weapon's part components, the part abilities and the power-row abilities, as entries to send now.
    -> ([Entry], PlayerAbilities or None)."""
    from .abilities import PlayerAbilities
    data = load()
    weapon = equipped_weapon(session)
    if data is None or weapon is None:
        log('WEAPON PARTS skipped (%s)' % ('no world/player/weapon_parts.json' if data is None else 'no weapon actor'))
        return [], None
    ch, cls, item = weapon
    parts = default_parts(data, item)
    out = []
    if parts:
        entry, comps = parts_entry(session, ch, cls, parts)
        log('WEAPON PARTS %s ch%d: %s' % (item, ch, ', '.join('%s=%s' % (pid, comps[pid]) for _, pid, _ in parts)))
        out.append(entry)
    else:
        log('WEAPON PARTS %s: none in the catalog' % item)
    abilities = part_abilities(parts) + power_abilities(load_power(), item)
    pa = None
    if abilities:
        pa = PlayerAbilities(session)
        grant, handles = pa.grant_entry(abilities)
        out.append(grant)
        for h in handles:
            log('WEAPON PARTS granted handle %d: %s (%s)' % (h, pa.granted[h]['cls'], pa.granted[h]['what']))
    return out, pa
