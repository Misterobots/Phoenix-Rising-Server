"""Role-based views of a player's actors: what OTHER clients get to see.

The template is the owner's view (autonomous proxy). A remote client (simulated proxy) gets the same actors minus everything whose real
replication condition (sdk211/lifetime.jsonl, Schema.condition) excludes non-owners, with the roles swapped, owner-directed RPCs and
ability instances removed, and its own channel numbers."""
import copy

# Conditions that are NOT sent to a non-owning (simulated) connection. ReplayOrOwner/OwnerOnly are owner data; Never/ReplayOnly never go live.
NON_OWNER_EXCLUDED = {'OwnerOnly', 'AutonomousOnly', 'ReplayOnly', 'Never', 'ReplayOrOwner'}

# Recorded channels of the per-player actors other players see: pawn, PlayerState, lantern, 4 armour pieces, weapon.
# (2 PlayerController, 7 inventory, 8 loadout, 9 party client, 15 darkness role are owner-only; 6 GameState is the shared singleton, 3 WorldSettings static.)
REMOTE_CHANNELS = (4, 5, 10, 11, 12, 13, 14, 16)

SIMULATED_PROXY = 1


def _top_name(key):
    return key.split('.')[0].split('[')[0]


def _is_ability(schema, cls_name):
    return bool(cls_name) and ('GameplayAbility' in schema.inherit.get(cls_name, []) or cls_name.endswith('_ab_C'))


def filter_values(schema, cls_name, values, keep_owner=None):
    """Drop owner-only properties from a decoded property block; swap roles for a simulated proxy."""
    layout = schema.layout(cls_name)
    by_name = {p.name: p for p, _ in layout.parents}
    out = {}
    for k, v in values.items():
        prop = by_name.get(_top_name(k))
        cond = schema.condition(cls_name, prop.ri) if prop is not None else None
        if cond in NON_OWNER_EXCLUDED:
            continue
        if k == 'Owner' and not (keep_owner is not None and v == ('obj', keep_owner)):
            continue                           # the owner (PlayerController) is not visible to other clients; the pawn is, and gear (armour, weapon, lantern) is owned by it
        if k == 'RemoteRole':
            v = SIMULATED_PROXY
        out[k] = v
    return out


def filter_fields(schema, cls_name, fields):
    """Field entries (custom-delta properties / RPCs) of a block: keep only what a non-owner receives."""
    layout = schema.layout(cls_name)
    by_name = {p.name: p for p, _ in layout.parents}
    out = []
    for idx, name, kind, payload in fields:
        fi = schema.field_index(cls_name)
        if idx < len(fi) and fi[idx][0] == 'func':
            continue                           # client RPCs are owner-directed
        prop = by_name.get(name)
        cond = schema.condition(cls_name, prop.ri) if prop is not None else None
        if cond in NON_OWNER_EXCLUDED:
            continue
        out.append((idx, name, kind, payload))
    return out


def remote_entries(schema, entries, ch_base, loc_offset=(0.0, 0.0, 0.0)):
    """Build the entries a *remote* client receives for one player. `entries` are that player's (already remapped) GenSession entries."""
    chosen = [e for e in entries if e.ch in REMOTE_CHANNELS]
    pawn_guid = next((e.parsed.open['guid'] for e in chosen if e.cls == 'BP_PlayerCharacter_C' and e.parsed.open), None)
    out = []
    for e in chosen:
        ne = copy.copy(e)
        p = copy.deepcopy(e.parsed)
        ne.parsed = p
        ne.ch = ch_base + e.ch
        ne.exports = copy.deepcopy(e.exports)
        if p.open and p.open.get('loc') is not None and any(p.open['loc']) and any(loc_offset):   # only actors that have a transform
            p.open['loc'] = [p.open['loc'][i] + loc_offset[i] for i in range(3)]
        blocks = []
        for b in p.blocks:
            if _is_ability(schema, b.cls_name):
                continue                       # ability instances are replicated to the owner only
            if b.values is not None and b.cls_name:
                b.values = filter_values(schema, b.cls_name, b.values, keep_owner=pawn_guid)
            if b.fields is not None and b.cls_name:
                b.fields = filter_fields(schema, b.cls_name, b.fields)
                if not b.fields and not b.rep:
                    continue                   # RPC-only block that carried nothing for non-owners
            blocks.append(b)
        p.blocks = blocks
        out.append(ne)
    out.sort(key=lambda x: x.t)
    return out
