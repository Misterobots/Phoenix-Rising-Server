"""Weapon talents: unlock / equip perks in the city, and their gameplay tags in a hunt.

What the game does (atlas ArchonInventory / ArchonInventoryItem_Weapon / ArchonWeapon, AssetDump of weapon_talent_catalog,
weapon_dp_gameplay_catalog, talent_crafting_requirements, crafting_recipes; read 2026-10-28):
- A weapon's catalog row lists its WeaponTalents (WP_DP_UMBRAL_01: WTP_DPISTOLS_ALLURING_TALENT .. TALENT5, in that order -
  the ...ByIndex RPCs index this list). Each talent has a TalentCategory and five TalentPerks; a perk grants GameplayEffects
  (the D24 ones each grant one weapon.talent.d24.* tag) and/or Buffs.
- Unlocking: ServerSetWeaponTalentPerkUnlockedById / ByIndex(ItemId, TalentId|TalentIndex, TalentPerkIndex, Payload,
  NewUnlockValue, bBypassLockRules). Cost: talent_crafting_requirements[TALENT_CRAFTING_SHARED] gives, per category, the
  recipe of each perk index (TALENT_SHARED_1..5, the same for every category). The server answers
  ClientSetWeaponTalentPerkUnlockState(ItemId, TalentId, TalentPerkIndex, NewUnlockValue).
- Equipping: ServerSetWeaponTalentPerkEquippedById(ItemId, TalentId, TalentPerkIndex, NewEquipValue) ->
  ClientSetWeaponTalentPerkEquipState(same + NewEquipValue).
- In a hunt the weapon (ApplyWeaponTalentGameplayModifiers, native, server) applies the equipped perks' effects to the
  player; the abilities read the tags with legendary_ability_function_library.GetTalentStatusSP / UE / Act.

Where the client keeps it (decompiled 2026-10-28, Dauntless-Win64-Shipping 2.1.1): a weapon item's custom data string is JSON,
parsed by UArchonInventoryItem_Weapon::FromCustomData (vtable +0x2c8, 0x28d56f0) through a two-way visitor (0x28d95f0):
  {"TransmogCatalogID": str, "UnlockedAsTransmog": bool, "CurrentLevel": int (EUpgradeableItemLevel, item +0xf0),
   "EquippedWeaponParts": [...], "CurrentXP": int, "ItemProgressionLevel": int (+0xf8), "ItemProgressionXP": int,
   "WeaponTalentProgression": {TalentId: {"CurrentLevel": int, "CurrentXP": int, "UnlockedPerks": bitmask,
                                          "EquippedPerks": bitmask}}, "EquippedCells": [...]}
IsTalentPerkUnlocked is `bt UnlockedPerks, perk` (0x28cf2c0). The unlock gate (0x28b8b50): WeaponProgressionReworkFeature on, the
perk not unlocked, and perks unlocked in that talent < min(item level / 10, 5), item level = ItemProgressionLevel (vtable
+0x3a0, 0x28c0940). ClientSetWeaponTalentPerk*State only queue a pending change (0x287eeb4); they do not edit the item. So
the server sends this JSON as the weapon's V2 inventory customData. State lives in the backend's /talents store.
Stand-in (labelled): ItemProgressionLevel = the weapon type's ExperienceTrack level (per-item weapon XP is not tracked yet).
Stand-ins (labelled): conditional_progression_breaks ingredients (escalation kill counts) are not checked or spent; perk Buffs
(might / defense) are not applied yet; only the D24 tag effects are.
"""
import json
import os
from urllib.request import Request, urlopen

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(HERE, 'data', 'atlas', 'assets')
WEAPON_CATALOGS = ('weapon_dp_gameplay_catalog',)          # dumped so far: the repeaters
BACKEND = 'http://127.0.0.1:3001'


def _log(msg):
    print('TALENT ' + msg, flush=True)


def _rows(o, want):
    if isinstance(o, dict):
        for k, v in o.items():
            if want(k, v):
                yield k, v
            else:
                yield from _rows(v, want)
    elif isinstance(o, list):
        for v in o:
            yield from _rows(v, want)


def _ref(s):
    """'table[ROW]' -> 'ROW'; '/Game/x.y_C' -> 'y_C'."""
    if '[' in s:
        return s.split('[', 1)[1].rstrip(']')
    return s.rsplit('.', 1)[-1]


def _load(name):
    path = os.path.join(ASSETS, name + '.json')
    if not os.path.exists(path):
        return None
    with open(path, encoding='utf-8') as f:
        return json.load(f)


class Catalog:
    """Talent data read from the game's tables."""

    def __init__(self):
        self.weapons = {}       # weapon catalog id -> [talent id]
        self.talents = {}       # talent id -> {'category', 'unlock_level', 'perks': [{'effects': [cls], 'buffs': [(cls, mag)]}]}
        self.recipes = {}       # category -> [recipe row per perk index]
        self.ingredients = {}   # recipe row -> [(catalog id, amount, kind)] kind 'stack' | 'progression'
        self.upgrade_level = {} # weapon catalog id -> StartingUpgradeLevel (EUpgradeableItemLevel value)
        for name in WEAPON_CATALOGS:
            d = _load(name)
            for k, v in _rows(d, lambda k, v: isinstance(v, dict) and 'WeaponTalents' in v):
                if v['WeaponTalents']:
                    self.weapons[k] = [_ref(t) for t in v['WeaponTalents']]
                lvl = str(v.get('StartingUpgradeLevel') or '').rsplit('_', 1)[-1]
                if lvl.isdigit():
                    self.upgrade_level[k] = int(lvl)
        d = _load('weapon_talent_catalog')
        for k, v in _rows(d, lambda k, v: isinstance(v, dict) and 'TalentPerks' in v):
            self.talents[k] = {
                'category': v['TalentCategory'].split('::')[-1], 'unlock_level': v.get('WeaponUnlockLevel', 0),
                'perks': [{'effects': [_ref(g) for g in p.get('GameplayEffects') or []],
                           'buffs': [(_ref(b['Buff']), b.get('Magnitude')) for b in p.get('Buffs') or []]}
                          for p in v['TalentPerks']]}
        d = _load('talent_crafting_requirements')
        for _, v in _rows(d, lambda k, v: isinstance(v, dict) and 'TalentRequirements' in v):
            for req in v['TalentRequirements']:
                self.recipes[req['TalentCategory'].split('::')[-1]] = [_ref(r) for r in req['CraftingRecipes']]
        d = _load('crafting_recipes')
        wanted = {r for rs in self.recipes.values() for r in rs}
        for k, v in _rows(d, lambda k, v: k in wanted and isinstance(v, dict)):
            self.ingredients[k] = [(_ref(i['Item']), int(i['Amount']),
                                    'progression' if i['Item'].startswith('conditional_progression_breaks') else 'stack')
                                   for i in v.get('Ingredients') or []]

    def talent_id(self, weapon, index):
        ts = self.weapons.get(weapon) or []
        return ts[index] if 0 <= index < len(ts) else None

    def cost(self, talent, perk):
        """-> [(catalog id, amount)] stacks to spend for that perk, or None when unknown."""
        t = self.talents.get(talent)
        rs = self.recipes.get(t['category']) if t else None
        if not rs or not 0 <= perk < len(rs) or rs[perk] not in self.ingredients:
            return None
        return [(c, a) for c, a, kind in self.ingredients[rs[perk]] if kind == 'stack']

    def equipped_effects(self, state, weapon_instance):
        """-> [GE class] of the perks equipped on that weapon."""
        out = []
        for talent, st in ((state or {}).get(weapon_instance) or {}).items():
            perks = (self.talents.get(talent) or {}).get('perks') or []
            for i in st.get('equipped') or []:
                if 0 <= i < len(perks):
                    out += perks[i]['effects']
        return out


_CATALOG = None


def catalog():
    global _CATALOG
    if _CATALOG is None:
        _CATALOG = Catalog()
    return _CATALOG


# ---- backend ---------------------------------------------------------------------------------------------------------
def fetch(account, character, headers, base=BACKEND):
    with urlopen(Request('%s/talents/%s/%s' % (base, account, character), headers=headers), timeout=2) as r:
        return json.load(r).get('payload') or {}


def store(account, character, state, headers, base=BACKEND):
    hdr = dict(headers, **{'Content-Type': 'application/json'})
    req = Request('%s/talents/%s/%s' % (base, account, character), data=json.dumps(state).encode(), headers=hdr, method='POST')
    with urlopen(req, timeout=3) as r:
        return json.load(r).get('payload')


def store_item_data(account, character, data, headers, base=BACKEND):
    """{instance id: custom data JSON} -> the backend items' itemData (what a client inventory refresh reads)."""
    hdr = dict(headers, **{'Content-Type': 'application/json'})
    req = Request('%s/itemdata/%s/%s' % (base, account, character), data=json.dumps(data).encode(), headers=hdr, method='POST')
    with urlopen(req, timeout=3) as r:
        return json.load(r).get('payload')


# ---- state changes ---------------------------------------------------------------------------------------------------
def set_flag(state, item, talent, perk, key, value):
    """Copy of state with perk index added to / removed from state[item][talent][key]."""
    new = json.loads(json.dumps(state or {}))
    lst = new.setdefault(item, {}).setdefault(talent, {'unlocked': [], 'equipped': []}).setdefault(key, [])
    if value and perk not in lst:
        lst.append(perk)
        lst.sort()
    elif not value and perk in lst:
        lst.remove(perk)
    return new


def is_unlocked(state, item, talent, perk):
    return perk in (((state or {}).get(item) or {}).get(talent) or {}).get('unlocked', [])


def replay_rpcs(state):
    """-> [(func, params)]: the Client* RPCs that put a fresh client into `state`."""
    out = []
    for item, talents in sorted((state or {}).items()):
        for talent, st in sorted(talents.items()):
            for i in st.get('unlocked') or []:
                out.append(('ClientSetWeaponTalentPerkUnlockState',
                            {'ItemId': item, 'TalentId': talent, 'TalentPerkIndex': i, 'NewUnlockValue': True}))
            for i in st.get('equipped') or []:
                out.append(('ClientSetWeaponTalentPerkEquipState',
                            {'ItemId': item, 'TalentId': talent, 'TalentPerkIndex': i, 'NewEquipValue': True}))
    return out


def active_tags(state, weapon_instance):
    """-> set of weapon.talent.* tags the equipped perks grant (each D24 GE owns one tag: d24_weapon_talent_sp_03_ge ->
    weapon.talent.d24.sp03, AssetDump InheritableOwnedTagsContainer)."""
    tags = set()
    for ge in catalog().equipped_effects(state, weapon_instance):
        if ge.startswith('d24_weapon_talent_') and ge.endswith('_ge_C'):
            kind, num = ge[len('d24_weapon_talent_'):-len('_ge_C')].split('_')
            tags.add('weapon.talent.d24.%s%s' % (kind, num))
    return tags


def mask(indices):
    return sum(1 << i for i in set(indices or ()) if 0 <= i < 32)


def item_data(cat, weapon_catalog, talent_state, item_level):
    """-> the weapon's custom data JSON (see the module doc). talent_state: {talent id: {'unlocked': [..], 'equipped': [..]}}."""
    prog = {t: {'CurrentLevel': 0, 'CurrentXP': 0, 'UnlockedPerks': mask((talent_state or {}).get(t, {}).get('unlocked')),
                'EquippedPerks': mask((talent_state or {}).get(t, {}).get('equipped'))}
            for t in cat.weapons.get(weapon_catalog, [])}
    # every key, in the writer's order (ToCustomData, vtable +0x2c0, 0x28d9610): the V2 state hash is the CRC of the
    # client's own re-serialized string, so this must read back byte for byte. TransmogCatalogID is an FName (empty -> None).
    # Stand-in: EquippedWeaponParts / EquippedCells empty (the repeaters' parts are their catalog defaults, not item data).
    return json.dumps({'TransmogCatalogID': 'None', 'UnlockedAsTransmog': False,
                       'CurrentLevel': cat.upgrade_level.get(weapon_catalog, 0), 'EquippedWeaponParts': [], 'CurrentXP': 0,
                       'ItemProgressionLevel': int(item_level), 'ItemProgressionXP': 0,
                       'WeaponTalentProgression': prog, 'EquippedCells': [], 'EquippedCellsv2': []},
                      separators=(',', ':'))


ITEM = '__item__'                 # per-instance key in the talent store: {'level': ItemProgressionLevel}


def item_level(state, instance, fallback):
    """The weapon's own ItemProgressionLevel if stored, else `fallback` (the weapon type's level: a stand-in, the type track
    has 20 levels where the per-item table goes far higher; perks open at item level 10, 20 .. 50)."""
    lvl = (((state or {}).get(instance) or {}).get(ITEM) or {}).get('level')
    return int(lvl) if isinstance(lvl, int) else fallback


def custom_data(state, instanced_items, weapon_levels):
    """-> {instance id: custom data JSON} for every owned weapon with talents. weapon_levels: {weapon catalog id: level}."""
    cat = catalog()
    out = {}
    for it in instanced_items or ():
        c = it.get('catalogId')
        if c in cat.weapons:
            inst = it['instanceId']
            out[inst] = item_data(cat, c, (state or {}).get(inst), item_level(state, inst, weapon_levels.get(c, 1)))
    return out
