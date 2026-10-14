"""Backend-backed initial component snapshots for the generated private world.

Maps the local backend's bounded bounty/cooldown contracts, hunt-pass selection,
and progression rows into the world template's initially empty replicated
component blocks. Unsupported nonempty data is rejected rather than discarded.
"""
import copy
import json
import math
from urllib.parse import quote
from urllib.request import Request, urlopen

from .backend import gameserver_api_key
from .inventory_wire import build_identity_fixture, item_identity_crc
from .inventory_v2 import post_operation_hash


def _backend_headers(auth_token=None):
    headers = {'x-phoenixrising-gameserver-apikey': gameserver_api_key()}
    if isinstance(auth_token, str) and auth_token:
        headers['Authorization'] = 'Bearer ' + auth_token
    return headers


def load_snapshots(account, base='http://127.0.0.1:3001', auth_token=None):
    if not isinstance(account, str) or not account:
        raise ValueError('initialization requires an account')
    headers = _backend_headers(auth_token)

    def get(path):
        with urlopen(Request(base + path + quote(account, safe=''), headers=headers), timeout=1) as response:
            data = json.load(response)
        if isinstance(data, dict) and 'payload' in data:
            if data.get('code') is not None or data.get('message') != 'OK':
                raise ValueError('unsuccessful initialization response')
            return data['payload']
        return data

    bounty, cooldown, huntpass = get('/bounty/'), get('/cooldown/'), get('/huntpass/')
    progression = get('/progression//')
    return snapshot_values(bounty, cooldown, huntpass, progression)


def load_player_records(account, character, base='http://127.0.0.1:3001', auth_token=None):
    """Fetch and validate the current account's inventory and selected loadout.

    These records are retained per connection; the inventory and equipment
    appliers below turn them into replicated state.
    """
    if not isinstance(account, str) or not account:
        raise ValueError('player data requires an account')
    if not isinstance(character, str) or not character:
        raise ValueError('player data requires a character')
    headers = _backend_headers(auth_token)

    def get(path):
        with urlopen(Request(base + path, headers=headers), timeout=2) as response:
            data = json.load(response)
        if isinstance(data, dict) and 'payload' in data:
            if data.get('code') is not None or data.get('message') != 'OK':
                raise ValueError('unsuccessful player-data response')
            return data['payload']
        return data

    inventory = get('/inventory/' + quote(account, safe='') + '/' + quote(character, safe=''))
    loadout_response = get('/loadout/' + quote(account, safe='') + '/' + quote(character, safe='') + '/all')
    return normalize_player_records(inventory, loadout_response, character)


def load_character_profile(account, character, base='http://127.0.0.1:3001', auth_token=None):
    """Fetch this account's selected character profile through the scoped route."""
    if not isinstance(account, str) or not account:
        raise ValueError('character profile requires an account')
    if not isinstance(character, str) or not character:
        raise ValueError('character profile requires a character')
    headers = _backend_headers(auth_token)
    path = '/character/' + quote(account, safe='') + '/' + quote(character, safe='')
    with urlopen(Request(base + path, headers=headers), timeout=2) as response:
        profile = json.load(response)
    if isinstance(profile, dict) and 'payload' in profile:
        if profile.get('code') is not None or profile.get('message') != 'OK':
            raise ValueError('unsuccessful character-profile response')
        profile = profile['payload']
    # The caller validates and maps the raw profile when applying it. Returning
    # the mapped form here would make apply_character_profile normalize it twice.
    return profile


def normalize_character_profile(profile, character):
    """Convert the backend wire profile to the appearance fields we can serialize."""
    if not isinstance(profile, dict):
        raise ValueError('character profile must be an object')
    if profile.get('id') != character:
        raise ValueError('character profile belongs to another character')
    name = profile.get('name')
    if not isinstance(name, str) or not name:
        raise ValueError('character profile has no usable name')

    data = profile.get('data')
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError as exc:
            raise ValueError('invalid character data JSON') from exc
    if not isinstance(data, dict):
        raise ValueError('character data must be an object')
    appearance = data.get('AppearanceData')
    if isinstance(appearance, str):
        try:
            appearance = json.loads(appearance)
        except json.JSONDecodeError as exc:
            raise ValueError('invalid character appearance JSON') from exc
    if not isinstance(appearance, dict):
        raise ValueError('character appearance must be an object')

    creation_state = appearance.get('CreationState')
    enum_values = {
        'EArchonCharacterCreationState::NewCharacter': 0,
        'NewCharacter': 0,
        'EArchonCharacterCreationState::FaceComplete': 1,
        'FaceComplete': 1,
    }
    if not isinstance(creation_state, str) or creation_state not in enum_values:
        raise ValueError('unsupported character creation state')
    meshes = appearance.get('Data')
    strings = appearance.get('StringData')
    assets = appearance.get('AssetReferences', [])
    if not isinstance(meshes, list) or not isinstance(strings, list) or not isinstance(assets, list):
        raise ValueError('character appearance arrays have an invalid shape')
    if assets:
        # Soft object references in appearance data are not supported yet.
        raise ValueError('nonempty appearance asset references are not yet supported')
    if len(meshes) > 65535 or len(strings) > 65535:
        raise ValueError('character appearance array exceeds the wire count limit')

    mesh_elems, string_elems = {}, {}
    for index, mesh in enumerate(meshes):
        if not isinstance(mesh, dict) or not isinstance(mesh.get('SkeletalMeshComponentName'), str):
            raise ValueError('invalid appearance mesh entry')
        morphs = mesh.get('MorphData', [])
        if not isinstance(morphs, list) or len(morphs) > 65535:
            raise ValueError('invalid appearance morph array')
        morph_elems = {}
        for morph_index, morph in enumerate(morphs):
            if not isinstance(morph, dict) or not isinstance(morph.get('MorphName'), str):
                raise ValueError('invalid appearance morph entry')
            value = morph.get('Value')
            if type(value) not in (int, float):
                raise ValueError('invalid appearance morph value')
            try:
                value = float(value)
            except (OverflowError, ValueError) as exc:
                raise ValueError('invalid appearance morph value') from exc
            if not math.isfinite(value):
                raise ValueError('invalid appearance morph value')
            morph_elems[morph_index] = {
                'AppearanceData.MeshData[].MorphData[].MorphName': morph['MorphName'],
                'AppearanceData.MeshData[].MorphData[].Value': value,
            }
        mesh_elems[index] = {
            'AppearanceData.MeshData[].SkeletalMeshComponentName': mesh['SkeletalMeshComponentName'],
            'AppearanceData.MeshData[].MorphData': {'count': len(morph_elems), 'elems': morph_elems},
        }

    for index, item in enumerate(strings):
        if not isinstance(item, dict) or not isinstance(item.get('Key'), str) or not isinstance(item.get('Data'), str):
            raise ValueError('invalid appearance string-data entry')
        string_elems[index] = {
            'AppearanceData.StringData[].Key': (item['Key'], 0),
            'AppearanceData.StringData[].Data': item['Data'],
        }

    return {
        'name': name,
        'appearance': {
            'AppearanceData.CreationState': enum_values[creation_state],
            'AppearanceData.MeshData': {'count': len(mesh_elems), 'elems': mesh_elems},
            'AppearanceData.StringData': {'count': len(string_elems), 'elems': string_elems},
        },
    }


def apply_character_profile(entries, schema, profile, character):
    """Apply one validated profile to the initial PlayerState block."""
    normalized = normalize_character_profile(profile, character)
    candidates = [block for entry in entries for block in entry.parsed.blocks
                  if block.cls_name == 'player_state_bp_C' and block.values is not None]
    if len(candidates) != 1:
        raise ValueError('expected one decoded initial PlayerState block')
    block = candidates[0]
    names = {cmd.name for cmd in schema.layout(block.cls_name).cmds}
    updates = {'PlayerNamePrivate': normalized['name'], **normalized['appearance']}
    if not set(updates) <= names:
        raise ValueError('character profile contains unsupported PlayerState fields')
    # Assign per key: OrderedFields.order (read by the encoder) only learns
    # keys through __setitem__; dict.update would silently drop new ones.
    for key, value in copy.deepcopy(updates).items():
        block.values[key] = value
    return 1


def normalize_player_records(inventory, loadout_response, character):
    """Normalize backend JSON variants without discarding unknown item fields."""
    if not isinstance(inventory, dict):
        raise ValueError('inventory response must be an object')
    if inventory.get('characterId') not in (None, character):
        raise ValueError('inventory response belongs to another character')

    def array_field(obj, key):
        value = obj.get(key)
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError as exc:
                raise ValueError('invalid serialized inventory ' + key) from exc
        if not isinstance(value, list):
            raise ValueError('inventory ' + key + ' must be an array')
        return copy.deepcopy(value)

    if not isinstance(loadout_response, dict):
        raise ValueError('loadout response must be an object')
    loadouts = loadout_response.get('loadouts')
    persistent = loadout_response.get('persistent')
    active_index = loadout_response.get('active_index', 0)
    if not isinstance(loadouts, list):
        raise ValueError('loadouts must be an array')
    if type(active_index) is not int or active_index < 0:
        raise ValueError('active loadout index must be a nonnegative integer')
    if loadouts and active_index >= len(loadouts):
        raise ValueError('active loadout index is outside the loadout array')
    if persistent is not None and not isinstance(persistent, dict):
        raise ValueError('persistent loadout must be an object or null')
    instanced_items = array_field(inventory, 'instancedItems')
    # A set of owned pairs alone hides duplicate instance identities. Native
    # loadout lookup requires one unambiguous item, including across catalogs.
    instance_ids = set()
    for item_index, item in enumerate(instanced_items):
        if not isinstance(item, dict):
            raise ValueError('inventory instance %d must be an object' % item_index)
        catalog, instance = item.get('catalogId'), item.get('instanceId')
        if not isinstance(catalog, str) or not catalog or not isinstance(instance, str) or not instance:
            raise ValueError('inventory instance %d has an incomplete identity' % item_index)
        if instance in instance_ids:
            raise ValueError('inventory has a duplicate instance identity')
        instance_ids.add(instance)
    stacked_items = array_field(inventory, 'stackedItems')
    stacked_catalogs = set()
    for item_index, item in enumerate(stacked_items):
        if not isinstance(item, dict) or not isinstance(item.get('catalogId'), str) or not item['catalogId']:
            raise ValueError('inventory stack %d has an incomplete identity' % item_index)
        if type(item.get('quantity')) is not int or not 0 <= item['quantity'] <= 0xFFFFFFFF:
            raise ValueError('inventory stack %d has an invalid quantity' % item_index)
        if item['catalogId'] in stacked_catalogs:
            raise ValueError('inventory has a duplicate stacked catalog')
        stacked_catalogs.add(item['catalogId'])
    active = copy.deepcopy(loadouts[active_index]) if loadouts else None
    if active is not None and not isinstance(active, dict):
        raise ValueError('active loadout must be an object')

    # The wire loadout slots refer to owned inventory instances, not catalog
    # IDs alone. Reject inconsistent pairs before any serializer can publish a
    # CRC that has no matching inventory row (the client's loadout resolver
    # dereferences the result of that lookup).
    owned_pairs = {
        (item.get('catalogId'), item.get('instanceId'))
        for item in instanced_items if isinstance(item, dict)
    }
    for loadout_index, loadout in enumerate(loadouts):
        if not isinstance(loadout, dict):
            raise ValueError('loadout %d must be an object' % loadout_index)
        for slot_name in ('player_role', 'weapon', 'subweapon', 'helmet', 'chest', 'arms', 'legs', 'lantern'):
            slot = loadout.get(slot_name)
            if slot is None:
                continue
            if not isinstance(slot, dict):
                raise ValueError('loadout %d %s slot must be an object or null' % (loadout_index, slot_name))
            catalog_id, instance_id = slot.get('item_id'), slot.get('instance_id')
            if catalog_id is None and instance_id is None:
                continue  # unknown shape: leave it untouched
            if not isinstance(catalog_id, str) or not catalog_id or not isinstance(instance_id, str) or not instance_id:
                raise ValueError('loadout %d %s slot has an incomplete item reference' % (loadout_index, slot_name))
            if (catalog_id, instance_id) not in owned_pairs:
                raise ValueError('loadout %d %s item is not present in this character inventory' % (loadout_index, slot_name))

    return {
        'inventory': {
            'characterId': character,
            'instancedItems': instanced_items,
            'stackedItems': stacked_items,
        },
        'loadouts': copy.deepcopy(loadouts),
        'persistent': copy.deepcopy(persistent),
        'active_index': active_index,
        'active_loadout': active,
    }


# Backend loadout slot name -> 2.1.1 EItemSlot value (SDK Archon_structs.hpp).
EQUIPMENT_SLOT_TYPES = {
    'weapon': 1, 'helmet': 2, 'chest': 3, 'arms': 4, 'legs': 5,
    'lantern': 6, 'subweapon': 7, 'player_role': 8,
}


def apply_equipment_inventory_test(entries, schema, records, slots=None, update_slot=True):
    """Stage the active loadout's equipped items as experimental wire rows.

    Test-only: Quantity=1 is a display/lookup probe because no 2.1.1 backend
    RemainingUses source has been verified. ``slots`` limits which backend
    slots are published (default: every populated slot). When ``update_slot``
    is set, each published slot's CRC is rewritten to its row alias; all other
    slot fields and unpublished slots keep the baseline. No backend data is
    changed and no account IDs are persisted.
    """
    if not isinstance(records, dict) or not isinstance(records.get('active_loadout'), dict):
        raise ValueError('equipment probe requires an active loadout')
    if type(update_slot) is not bool:
        raise ValueError('update_slot must be a boolean')
    loadout = records['active_loadout']
    wanted = list(EQUIPMENT_SLOT_TYPES) if slots is None else list(slots)
    if not wanted or any(s not in EQUIPMENT_SLOT_TYPES for s in wanted):
        raise ValueError('unknown equipment slot requested')
    owned_items = [item for item in records['inventory']['instancedItems'] if isinstance(item, dict)]

    equipment, items, seen = {}, [], set()
    for name in wanted:
        reference = loadout.get(name)
        if reference is None and slots is None:
            continue  # unequipped (e.g. subweapon); leave captured slot alone
        if not isinstance(reference, dict):
            raise ValueError('equipment probe requires an active %s reference' % name)
        catalog, instance = reference.get('item_id'), reference.get('instance_id')
        owned = [i for i in owned_items if i.get('catalogId') == catalog and i.get('instanceId') == instance]
        if len(owned) != 1:
            raise ValueError('active %s must match exactly one owned inventory row' % name)
        equipment[name] = reference
        if instance not in seen:
            seen.add(instance)
            items.append({'catalogId': catalog, 'instanceId': instance, 'Quantity': 1})
    if not equipment:
        raise ValueError('active loadout has no equipped items to publish')
    fixture = build_identity_fixture(items, equipment)

    inventory_blocks = [block for entry in entries for block in entry.parsed.blocks
                        if block.cls_name == 'bp_archon_inventory_C' and block.values is not None]
    slot_blocks = [block for entry in entries for block in entry.parsed.blocks
                   if block.cls_name == 'ArchonLoadoutItemSlot' and block.values is not None]
    if len(inventory_blocks) != 1:
        raise ValueError('expected one inventory actor')
    targets = {}
    if update_slot:
        for name in equipment:
            matches = [b for b in slot_blocks if b.values.get('ItemSlotType') == EQUIPMENT_SLOT_TYPES[name]]
            if len(matches) != 1:
                raise ValueError('expected one %s loadout slot' % name)
            targets[name] = matches[0]
    inventory = inventory_blocks[0].values
    if 'ReplicatedItems' not in {cmd.name for cmd in schema.layout('bp_archon_inventory_C').cmds}:
        raise ValueError('inventory replication array is absent from schema')

    # Mutate only after every validation has passed.
    rows = fixture['rows']
    inventory['ReplicatedItems'] = {
        'count': len(rows), 'elems': {i: {'ReplicatedItems[]': row} for i, row in enumerate(rows)}}
    inventory['ExpectedNumItems'] = len(rows)
    for name, block in targets.items():
        block.values['ItemInstanceID.ItemInstanceIdCrc'] = fixture['slot_crcs'][name]
    fixture['published_slots'] = sorted(equipment)
    return fixture


def apply_weapon_inventory_test(entries, schema, records, update_slot=True):
    """Stage only the active weapon (one-slot case of the equipment probe)."""
    return apply_equipment_inventory_test(entries, schema, records, slots=['weapon'], update_slot=update_slot)


def apply_backend_weapon_reference(entries, records):
    """Resolve one V2 weapon slot against the backend's full instance ID.

    Uses the HTTP-loaded inventory identity; publishes no numeric aliases.
    """
    reference = records['active_loadout']['weapon']
    catalog, instance = reference['item_id'], reference['instance_id']
    owned = [item for item in records['inventory']['instancedItems']
             if item.get('catalogId') == catalog and item.get('instanceId') == instance]
    if len(owned) != 1:
        raise ValueError('backend weapon reference must match one owned item')
    slots = [block for entry in entries for block in entry.parsed.blocks
             if block.cls_name == 'ArchonLoadoutItemSlot' and block.values is not None
             and block.values.get('ItemSlotType') == 1]
    if len(slots) != 1:
        raise ValueError('expected one weapon slot')
    crc = item_identity_crc(instance)
    slots[0].values['ItemInstanceID.ItemInstanceIdCrc'] = crc
    return {'catalog': catalog, 'instance_crc': crc}


def build_v2_weapon_add_update(records, quantity, custom_data=''):
    """Build one exact account-owned weapon AddItem operation for offline/live diagnostics.

    The caller must use the extracted catalog's verified quantity/default and
    preserve the database item's data. This helper only accepts null itemData
    and an empty CustomData FString; it never invents or overwrites item data.
    """
    if not isinstance(records, dict) or not isinstance(records.get('active_loadout'), dict):
        raise ValueError('V2 weapon update requires an active loadout')
    if type(quantity) is not int or not 0 <= quantity <= 0xFFFFFFFF:
        raise ValueError('V2 weapon quantity must fit u32')
    if custom_data != '':
        raise ValueError('V2 starter weapon custom data is not verified; only empty data is accepted')
    reference = records['active_loadout'].get('weapon')
    if not isinstance(reference, dict):
        raise ValueError('active loadout has no weapon reference')
    catalog, instance = reference.get('item_id'), reference.get('instance_id')
    owned = [item for item in records['inventory']['instancedItems']
             if isinstance(item, dict) and item.get('catalogId') == catalog and item.get('instanceId') == instance]
    if len(owned) != 1:
        raise ValueError('active weapon must resolve to exactly one owned inventory item')
    if owned[0].get('itemData') is not None:
        raise ValueError('V2 custom-data conversion is unsupported for non-null backend itemData')
    catalog_crc = item_identity_crc(catalog)
    state = {'instanceId': instance, 'catalogIdCrc': catalog_crc,
             'quantity': quantity, 'customData': custom_data}
    return {
        'mismatchCount': 0,
        'operation': 'AddItem',
        'postOperationHash': post_operation_hash([state]),
        'instanceId': instance,
        'catalogIdCrc': catalog_crc,
        'quantity': quantity,
        'customData': custom_data,
    }


def apply_backend_equipment_references(entries, records):
    """Point every equipped loadout slot at its owned backend instance-ID CRC.

    Covers each populated active-loadout slot. Unequipped slots keep the
    template value.
    """
    loadout = records.get('active_loadout') if isinstance(records, dict) else None
    if not isinstance(loadout, dict):
        raise ValueError('equipment references require an active loadout')
    owned = [item for item in records['inventory']['instancedItems'] if isinstance(item, dict)]
    slot_blocks = [block for entry in entries for block in entry.parsed.blocks
                   if block.cls_name == 'ArchonLoadoutItemSlot' and block.values is not None]
    targets = {}
    for name, slot_type in EQUIPMENT_SLOT_TYPES.items():
        reference = loadout.get(name)
        if reference is None:
            continue
        if not isinstance(reference, dict):
            raise ValueError('active %s reference must be an object' % name)
        catalog, instance = reference.get('item_id'), reference.get('instance_id')
        if len([i for i in owned if i.get('catalogId') == catalog and i.get('instanceId') == instance]) != 1:
            raise ValueError('active %s must match exactly one owned inventory item' % name)
        matches = [b for b in slot_blocks if b.values.get('ItemSlotType') == slot_type]
        if len(matches) != 1:
            raise ValueError('expected one %s loadout slot' % name)
        targets[name] = (matches[0], catalog, item_identity_crc(instance))
    # Mutate only after every slot has validated.
    for block, _, crc in targets.values():
        block.values['ItemInstanceID.ItemInstanceIdCrc'] = crc
    return {name: {'catalog': catalog, 'instance_crc': crc}
            for name, (_, catalog, crc) in targets.items()}


def apply_equipment_actor_identity(entries, schema, records):
    """Fill ArchonEquipment LoadoutItemSlotType/ItemInstanceID on equipped actors.

    The recorded weapon actor replicates only ItemId, leaving its slot type at
    ItemSlot_Unknown and its instance ID empty, so nothing ties it to the
    equipped loadout slot. Both are plain COND_None ArchonEquipment properties
    (SDK repdump/lifetime). Only actors whose ItemId equals an equipped catalog
    are touched, and only blocks whose full property stream was decoded.
    """
    loadout = records.get('active_loadout') if isinstance(records, dict) else None
    if not isinstance(loadout, dict):
        raise ValueError('equipment actor identity requires an active loadout')
    by_catalog = {}
    for name, slot_type in EQUIPMENT_SLOT_TYPES.items():
        reference = loadout.get(name)
        if isinstance(reference, dict) and isinstance(reference.get('item_id'), str):
            by_catalog.setdefault(reference['item_id'], []).append((slot_type, reference.get('instance_id')))
    targets = []
    for entry in entries:
        for block in entry.parsed.blocks:
            if not block.actor or block.values is None or 'ItemId' not in block.values:
                continue
            names = {cmd.name for cmd in schema.layout(block.cls_name).cmds}
            if not {'LoadoutItemSlotType', 'ItemInstanceID'} <= names:
                continue
            matches = by_catalog.get(block.values['ItemId'], [])
            if len(matches) != 1:
                continue  # not equipped, or ambiguous; keep the template value
            if block.partial_properties:
                raise ValueError('equipment actor %s has a partial property stream' % block.cls_name)
            targets.append((block, matches[0]))
    for block, (slot_type, instance) in targets:
        block.values['LoadoutItemSlotType'] = slot_type
        block.values['ItemInstanceID'] = instance
    return [(block.cls_name, slot_type) for block, (slot_type, _) in targets]


def spawn_weapons_at_origin(entries, schema):
    """Zero the spawn transform of weapon actors (ArchonWeapon subclasses).

    The template's weapon actor carries a world-space socket pose
    (e.g. (260.5, 519.5, 761.2)). The client uses it as the root's transform
    and the sheathe attach then keeps it as a relative offset, so the mesh
    rendered ~957 units (one spawn-vector length) from the correct socket.
    The eblade_root_jnt
    socket is correct; only the spawn offset is wrong.
    """
    changed = []
    for entry in entries:
        o = entry.parsed.open
        if not o or not entry.cls or 'ArchonWeapon' not in schema.inherit.get(entry.cls, []):
            continue
        if o.get('loc') != [0, 0, 0] or any(o.get('rot') or []):
            o['loc'], o['rot'] = [0, 0, 0], [0, 0, 0]
            changed.append(entry.cls)
    return changed


def build_v2_inventory_add_updates(records, include_stacks=False):
    """Build AddItem operations for every owned instanced item, in one sync.

    Each item uses the live-verified weapon contract: full backend instance ID,
    Quantity=1 (all owned instanced rows are nonstackable with MaxQuantity<=1 in
    the 2.1.1 catalogs) and empty CustomData (backend itemData is null). Each
    operation's PostOperationHash covers the cumulative client state after it,
    starting from the empty V2 map the accepted weapon test observed. Equipped
    items go first (weapon leading, as proven), then the rest in backend order.
    With ``include_stacks``, stacked items follow as AddItem operations too.
    The 2.1.1 handler (HandleItemStateUpdates, AddItem branch at 0x288E902)
    has no stacked path: it resolves the catalog CRC, copies InstanceId and
    Quantity and calls AddInstancedItem. A stack is therefore one item with a
    quantity under any unique instance ID; the backend keys stacks by catalog
    ID, so instanceId = catalogId (deterministic, maps back on the server).
    """
    loadout = records.get('active_loadout') if isinstance(records, dict) else None
    if not isinstance(loadout, dict):
        raise ValueError('V2 inventory sync requires an active loadout')
    items = [item for item in records['inventory']['instancedItems'] if isinstance(item, dict)]
    if len(items) != len(records['inventory']['instancedItems']):
        raise ValueError('malformed instanced inventory row')
    seen = set()
    for item in items:
        instance = item.get('instanceId')
        if not isinstance(instance, str) or not instance or not isinstance(item.get('catalogId'), str):
            raise ValueError('instanced item requires catalog and instance IDs')
        if instance in seen:
            raise ValueError('duplicate instance ID in inventory')
        seen.add(instance)
        if item.get('itemData') is not None:
            raise ValueError('V2 custom-data conversion is unsupported for non-null backend itemData')
    equipped = [loadout[name]['instance_id'] for name in EQUIPMENT_SLOT_TYPES
                if isinstance(loadout.get(name), dict)]
    rank = {instance: i for i, instance in enumerate(equipped)}
    ordered = sorted(items, key=lambda item: rank.get(item['instanceId'], len(rank)))
    state, updates = [], []
    for item in ordered:
        catalog_crc = item_identity_crc(item['catalogId'])
        state.append({'instanceId': item['instanceId'], 'catalogIdCrc': catalog_crc,
                      'quantity': 1, 'customData': ''})
        updates.append({
            'mismatchCount': 0,
            'operation': 'AddItem',
            'postOperationHash': post_operation_hash(state),
            'instanceId': item['instanceId'],
            'catalogIdCrc': catalog_crc,
            'quantity': 1,
            'customData': '',
        })
    if include_stacks:
        for stack in records['inventory'].get('stackedItems') or []:
            if not isinstance(stack, dict):
                raise ValueError('malformed stacked inventory row')
            catalog, quantity = stack.get('catalogId'), stack.get('quantity')
            if not isinstance(catalog, str) or not catalog or type(quantity) is not int or not 0 < quantity <= 0xFFFFFFFF:
                raise ValueError('stacked item requires a catalog ID and positive quantity')
            if catalog in seen:
                raise ValueError('stack catalog ID collides with an instance ID or another stack')
            seen.add(catalog)
            catalog_crc = item_identity_crc(catalog)
            state.append({'instanceId': catalog, 'catalogIdCrc': catalog_crc, 'quantity': quantity, 'customData': ''})
            updates.append({
                'mismatchCount': 0,
                'operation': 'AddItem',
                'postOperationHash': post_operation_hash(state),
                'instanceId': catalog,
                'catalogIdCrc': catalog_crc,
                'quantity': quantity,
                'customData': '',
            })
    return updates


def snapshot_values(bounty, cooldown, huntpass, progression=None):
    if not isinstance(bounty, dict) or bounty.get('bounties') != []:
        raise ValueError('only an explicit empty bounty snapshot is supported')
    if cooldown != {}:
        raise ValueError('only an explicit empty cooldown snapshot is supported')
    if not isinstance(huntpass, str) or not huntpass:
        raise ValueError('missing hunt pass selection')
    empty = {'count': 0, 'elems': {}}
    snapshots = {
        'bounty_bpc_C': {
            'ReplicatedBountyInitData.bIsServerReady': True,
            'ReplicatedBountyInitData.Bounties': copy.deepcopy(empty),
        },
        'cooldown_bpc_C': {
            'CooldownBackendData.BackEndDataReceived': True,
            'CooldownBackendData.CooldownStatusData': copy.deepcopy(empty),
        },
        'HuntPassComponent': {
            'MainHuntPassId': (huntpass, 0),
            'SelectedHuntPassId': (huntpass, 0),
            'bSelectedPassActive': True,
            'bReplicated': True,
        },
    }
    if progression is not None:
        if not isinstance(progression, list) or not progression:
            raise ValueError('progression snapshot must be a nonempty row list')
        elems = {}
        seen = set()
        for index, row in enumerate(progression):
            if not isinstance(row, dict):
                raise ValueError('progression row must be an object')
            track = row.get('progression_id')
            points = row.get('progress')
            confirmed_rank = row.get('confirmed_fremium_rank', 0)
            confirmed_premium_rank = row.get('confirmed_premium_rank', 0)
            if not isinstance(track, str) or not track or track in seen:
                raise ValueError('progression track id is missing or duplicated')
            if type(points) is not int or points < 0:
                raise ValueError('progression points must be a nonnegative integer')
            if type(confirmed_rank) is not int or confirmed_rank < 0:
                raise ValueError('confirmed freemium rank must be a nonnegative integer')
            if type(confirmed_premium_rank) is not int or confirmed_premium_rank < 0:
                raise ValueError('confirmed premium rank must be a nonnegative integer')
            seen.add(track)
            values = {
                'PlayerProgress[].TrackId': track,
                # The backend exposes confirmed ranks, not the game's derived
                # rank-point/reward state. Keep the replicated visible ranks at
                # least at the confirmed floor instead of silently sending 0
                # when a returning account has already claimed progression.
                'PlayerProgress[].Rank': confirmed_rank,
                'PlayerProgress[].ConfirmedRank': confirmed_rank,
                'PlayerProgress[].PremiumRank': confirmed_premium_rank,
                'PlayerProgress[].ConfirmedPremiumRank': confirmed_premium_rank,
                'PlayerProgress[].TotalPoints': points,
            }
            elems[index] = values
        snapshots['progression_bpc_C'] = {
            'PlayerProgress': {'count': len(elems), 'elems': elems},
            'SelectedHuntPassId': huntpass,
        }
        # The selected pass's record: without it the Hunt Pass screen has no data for its claim button
        # (the stock client crashes on "Claim rewards" without it).
        from . import huntpass as hp
        path = hp.load_path(huntpass)
        if path is not None:
            row = next((r for r in progression if r.get('progression_id') == huntpass), {})
            snapshots['HuntPassComponent']['ActiveHuntPassData'] = {'count': 1, 'elems': {0: hp.record(
                huntpass, path, row.get('progress', 0), row.get('confirmed_fremium_rank', 0),
                row.get('confirmed_premium_rank', 0))}}
    return snapshots


def apply_snapshots(entries, schema, snapshots):
    """Replace only empty creation blocks; validate everything before mutation."""
    targets = []
    for cls, values in snapshots.items():
        names = {cmd.name for cmd in schema.layout(cls).cmds}
        if not values or not set(values) <= names:
            raise ValueError('unknown initialization properties for ' + cls)
        blocks = [b for e in entries for b in e.parsed.blocks if b.cls_name == cls]
        if cls == 'progression_bpc_C':
            # The template contains later progression updates as well as the
            # initial subobject block. Seed only its first appearance so the
            # initial OnRep runs; leave later captured traffic untouched.
            if not blocks:
                raise ValueError('missing initial component block for ' + cls)
            block = blocks[0]
        else:
            if len(blocks) != 1:
                raise ValueError('expected one initial component block for ' + cls)
            block = blocks[0]
        if block.destroy or block.values is not None or block.fields or block.tail or block.raw not in (None, (0, 0)):
            raise ValueError('refusing to overwrite existing component data for ' + cls)
        targets.append((block, values))
    for block, values in targets:
        block.rep = 1
        block.values = copy.deepcopy(values)
        block.raw = None
    return len(targets)


def suppress_replay_banner(entries):
    """Clear only the template's banner references.

    The template advertises a banner instance that the player does not own.
    This prevents that
    single unresolved cosmetic reference from being used; it does not alter
    account records or any other loadout equipment.
    """
    changed = 0
    for entry in entries:
        for block in entry.parsed.blocks:
            if block.cls_name != 'ArchonLoadoutSlotData' or not isinstance(block.values, dict):
                continue
            if not block.values.get('BannerInstanceId') and not block.values.get('BannerCustomizationIds'):
                continue
            block.values['BannerInstanceId'] = ''
            block.values['BannerCustomizationIds'] = ''
            changed += 1
    return changed
