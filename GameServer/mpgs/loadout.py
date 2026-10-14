"""Loadout changes: ServerInternalAssignItem / ServerCommitLoadout -> backend loadout slot, and the equipped
actors' classes at join.

Backend loadout slot (GET /loadout/<acct>/<char>/all -> payload.loadouts[i]): one object per equipment slot,
{"weapon": {"item_id", "instance_id", "instance_data"}, "helmet": ..., ...}; POST /loadout/<acct>/<char>/<i>
with {"data": <the whole slot object as a JSON string>} replaces it.

Equipment actor class per catalog item: world/item_classes.json (tools/export_item_classes.py, the gear
catalogs' ItemClass). The recorded weapon actor is opened with archetype Default__<class> in the blueprint's
package; at join the archetype path is replaced with the equipped item's, so the client spawns that weapon.
"""
import json
import os
from urllib.parse import quote
from urllib.request import Request, urlopen

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ITEM_CLASSES = os.path.join(HERE, 'world', 'item_classes.json')

# ELoadoutItemSlotType -> backend loadout key
SLOT_NAMES = {1: 'weapon', 2: 'helmet', 3: 'chest', 4: 'arms', 5: 'legs', 6: 'lantern', 7: 'subweapon', 8: 'player_role'}

# Replicated properties the sword blueprint (BP_EB_Weapon_C) adds after BP_BaseArchonWeapon_C.
EB_ONLY = ('CurrentSwordcharges', 'SwordchargeFillPercent', 'SwordchargeChange', 'AoEEmpowered', 'ProjectileEmpowered')


def load_item_classes(path=ITEM_CLASSES):
    try:
        with open(path) as f:
            return json.load(f)['classes']
    except (OSError, ValueError, KeyError):
        return {}


def split_class_path(path):
    """'/Game/X/y.y_C' -> ('/Game/X/y', 'y_C')"""
    package, _, name = path.partition('.')
    return package, name


def apply_assignments(slot, assigns, instanced_items):
    """Apply [(slot type, instance id)] to a backend loadout slot object (in place).
    -> ([(slot name, catalog)] applied, [reason] rejected). An empty instance id clears the slot."""
    owned = {i.get('instanceId'): i.get('catalogId') for i in instanced_items if isinstance(i, dict)}
    applied, rejected = [], []
    for slot_type, instance in assigns:
        name = SLOT_NAMES.get(slot_type)
        if name is None:
            rejected.append('slot type %s' % slot_type)
            continue
        if not instance:
            slot[name] = None
            applied.append((name, None))
            continue
        catalog = owned.get(instance)
        if catalog is None:
            rejected.append('%s: instance %s not owned' % (name, instance))
            continue
        cur = slot.get(name) if isinstance(slot.get(name), dict) else {}
        slot[name] = {'item_id': catalog, 'instance_id': instance,
                      'instance_data': cur.get('instance_data') if cur.get('instance_id') == instance else None}
        applied.append((name, catalog))
    return applied, rejected


def _get(base, path, headers):
    with urlopen(Request(base + path, headers=headers), timeout=3) as r:
        d = json.load(r)
    return d.get('payload', d) if isinstance(d, dict) else d


def commit(account, character, index, assigns, headers, base='http://127.0.0.1:3001'):
    """Read the inventory and loadout slot, apply the assignments, save. -> (applied, rejected, HTTP status)."""
    a, c = quote(account, safe=''), quote(character, safe='')
    inv = _get(base, '/inventory/%s/%s' % (a, c), headers)
    items = inv.get('instancedItems')
    items = json.loads(items) if isinstance(items, str) else (items or [])
    loadouts = _get(base, '/loadout/%s/%s/all' % (a, c), headers).get('loadouts') or []
    if not 0 <= index < len(loadouts):
        return [], ['loadout index %d of %d' % (index, len(loadouts))], None
    slot = loadouts[index]
    slot = json.loads(slot) if isinstance(slot, str) else dict(slot)
    applied, rejected = apply_assignments(slot, assigns, items)
    if not applied:
        return applied, rejected, None
    hdr = dict(headers); hdr['Content-Type'] = 'application/json'
    body = json.dumps({'data': json.dumps(slot, separators=(',', ':'))}).encode()
    with urlopen(Request('%s/loadout/%s/%s/%d' % (base, a, c, index), data=body, headers=hdr, method='POST'), timeout=3) as r:
        return applied, rejected, r.status


# Layout used for an equipment class the schema does not know (its blueprint was never loaded when the
# replication dump was taken): the slot's shared blueprint base. Values that layout lacks are dropped.
SLOT_BASE = {1: 'BP_BaseArchonWeapon_C', 2: 'armour_part_base_bp_C', 3: 'armour_part_base_bp_C',
             4: 'armour_part_base_bp_C', 5: 'armour_part_base_bp_C', 6: 'lantern_base_bp_C', 8: 'base_player_role_bp_C'}


def load_item_slots(path=ITEM_CLASSES):
    try:
        with open(path) as f:
            return json.load(f).get('slots', {})
    except (OSError, ValueError):
        return {}


def equipment_entry(entries, schema, slot, slots=None):
    """The session's open bunch of the equipment actor in `slot` (matched by its ItemId's catalog slot)."""
    slots = load_item_slots() if slots is None else slots
    for e in entries:
        if not e.parsed.open or not e.cls or 'ArchonEquipment' not in schema.inherit.get(e.cls, []) + [e.cls]:
            continue
        a = next((b for b in e.parsed.blocks if b.actor and b.values is not None), None)
        if a is not None and slots.get(a.values.get('ItemId')) == slot:
            return e
    return None


def weapon_entry(entries, schema):
    return equipment_entry(entries, schema, 1)


def swap_equipment_actor(entries, schema, slot, catalog, classes=None, slots=None):
    """Point the equipment actor in `slot` at `catalog`'s blueprint: archetype path + ItemId. A class unknown to
    the schema encodes with the slot's base layout. -> (old class, new class) or None when nothing changed."""
    from .repl.guid import flatten
    classes = load_item_classes() if classes is None else classes
    slots = load_item_slots() if slots is None else slots
    path = classes.get(catalog or '')
    if not path or slots.get(catalog) != slot:
        return None
    package, cls_name = split_class_path(path)
    e = equipment_entry(entries, schema, slot, slots)
    if e is None:
        return None
    a = next(b for b in e.parsed.blocks if b.actor and b.values is not None)
    if a.values.get('ItemId') == catalog:
        return None
    if slot == 1 and not cls_name.startswith(e.cls.split('_')[0] + '_') and os.environ.get('MPGS_EQUIP_CROSS_TYPE', '1') != '1':
        return None      # opt-out of cross-type swaps (other weapon types use the base layout)
    o = e.parsed.open
    infos = {i.guid: i for r in e.exports for i in flatten(r)}
    arch = infos.get(o.get('archetype'))
    if arch is None or arch.outer_info is None:
        return None
    old = e.cls
    arch.name, arch.flags, arch.checksum = 'Default__' + cls_name, 1, None
    arch.outer_info.name, arch.outer_info.flags, arch.outer_info.checksum = package, 1, None
    a.values['ItemId'] = catalog
    if cls_name in schema.inherit:
        layout = cls_name
    elif slot == 1 and cls_name.startswith('eblade_') and e.cls.startswith('eblade_'):
        layout = e.cls          # sword -> sword keeps the sword layout
    else:
        layout = SLOT_BASE.get(slot, e.cls)
    if layout != e.cls:
        names = {c.name.split('.')[0].split('[')[0] for c in schema.layout(layout).cmds}
        for k in list(a.values):
            if k.split('.')[0].split('[')[0] not in names:
                a.values.pop(k)
        a.cls_name = layout
        e.cls = layout
    return old, cls_name


def swap_weapon_actor(entries, schema, catalog, classes=None):
    return swap_equipment_actor(entries, schema, 1, catalog, classes)


def respawn_equipment(entries, schema, slot, catalog, new_ch, next_guid, classes=None):
    """Live equipment change: a copy of the slot actor's open bunch with the new class, a new channel and fresh
    GUIDs for the actor, everything inside it and its archetype (the old channel is closed, destroying the old
    actor; the client keeps the first path it learned for a GUID, so none may be reused).
    `next_guid()` -> a fresh even GUID. -> (old channel, new entry, (old class, new class)) or None."""
    import copy
    from .repl import remap
    from .repl.guid import flatten
    src = equipment_entry(entries, schema, slot)
    if src is None:
        return None
    work = copy.deepcopy(src)
    work.exports = copy.deepcopy(src.exports)
    swapped = swap_equipment_actor([work], schema, slot, catalog, classes)
    if swapped is None:
        return None
    actor = work.parsed.open['guid']
    owned = {actor} | {b.guid for b in work.parsed.blocks if b.guid}
    infos = {i.guid: i for r in work.exports for i in flatten(r)}
    for i in infos.values():
        o = i.outer_info
        while o is not None:
            if o.guid == actor:
                owned.add(i.guid)
                break
            o = o.outer_info
    a = infos.get(work.parsed.open.get('archetype'))
    while a is not None:
        owned.add(a.guid)
        a = a.outer_info
    table = {}
    for g in sorted(owned):
        n = next_guid()
        table[g] = n + 1 if g & 1 else n          # static path GUIDs stay odd
    m = lambda g: table.get(g, g)
    ne = remap.instantiate([work], m)[0][0]
    ne.parsed.open['archetype'] = m(ne.parsed.open['archetype'])
    ne.ch, ne.cls, ne.t = new_ch, work.cls, 0
    return src.ch, ne, swapped


def respawn_weapon(entries, schema, catalog, new_ch, next_guid, classes=None):
    return respawn_equipment(entries, schema, 1, catalog, new_ch, next_guid, classes)
