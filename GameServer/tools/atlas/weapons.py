"""Every player weapon type: dump its data and Blueprint code and write one sheet per weapon.

The game names each weapon type by an internal prefix (WEAPONS below: eb = sword, ga = axe, ...). A weapon's
packages are found in three ways:

  folders   its own folders: blueprints/Weapons/_shared/<x>, game/abilities/weapons/<x>, the montages in
            Character/Player/Anims/<x>, its anim state sets, locomotion set and combat anim blueprints
  prefix    any other logic package (not effects, UI, audio, meshes) whose name starts with one of its prefixes:
            the skin weapon actors in blueprints/Weapons/<skin>/, conditions, notifies, weapon modifier buffs, ...
  refs      Blueprint classes those packages name (spawned projectiles, area effects, buffs, legendary parts),
            followed REF_DEPTH steps like the behemoth dump

SHARED is the weapon infrastructure every type uses (base weapon actor, part and legendary base classes, ammo
and meter components, player damage notifies, the player damage and tuning tables). It is captured with the
first weapon dumped.

  dump    python -m tools.atlas weapons --dump <key ...>   assets into data/atlas/assets, code into classes/pseudo
  sheets  data/atlas/weapons/<key>.txt and data/atlas/weapons.json / weapons.txt (coverage table)
"""
import json
import os
import re

from . import assets, blueprints, paths

C = 'Archon/Content/'
# key: (name in game, name prefixes, own folders). Part and power-table rows use CODES; catalogs use CATALOG.
WEAPONS = {
    'sword': ('Sword', ('eb_', 'eblade_', 'bp_eb_', 'notify_eb', 'characterlocomotion_eblade', 'player_combat_eblade'),
              ('blueprints/Weapons/_shared/eblade/', 'game/abilities/weapons/Sword/', 'Character/Player/Anims/EmberBlade/')),
    'axe': ('Axe', ('ga_', 'gaxe_', 'axe_', 'bp_axe_', 'notify_axe', 'notify_ga', 'characterlocomotion_axe', 'player_combat_gaxe'),
            ('blueprints/Weapons/_shared/gaxe/', 'game/abilities/weapons/AxeSpecials/',
             'game/abilities/weapons/Determination/', 'Character/Player/Anims/GreatAxe/')),
    'hammer': ('War hammer', ('ih_', 'ihammer_', 'bp_ihammer', 'notify_hammer', 'notify_ih', 'reload_ammo_hammer',
                              'characterlocomotion_ihammer', 'player_combat_ihammer'),
               ('blueprints/Weapons/_shared/ihammer/', 'game/abilities/weapons/Hammer/', 'Character/Player/Anims/Hammer/')),
    'chainblades': ('Chain blades', ('cb_', 'cblades_', 'notify_cb', 'characterlocomotion_cblades', 'player_combat_cblades'),
                    ('blueprints/Weapons/_shared/cblades/', 'game/abilities/weapons/CBlades/',
                     'Character/Player/Anims/ChainBlades/')),
    'warpike': ('War pike', ('ms_', 'mspear_', 'bp_mspear', 'notify_ms', 'characterlocomotion_mspear', 'player_combat_mspear'),
                ('blueprints/Weapons/_shared/mspear/', 'game/abilities/weapons/MSpear/',
                 'Character/Player/Anims/MortarSpear/')),
    'repeaters': ('Repeaters', ('dp_', 'character_locomotion_dp', 'player_combat_dp'),
                  ('blueprints/Weapons/_shared/dp/', 'game/abilities/weapons/DP/', 'Character/Player/Anims/DP/')),
    'strikers': ('Aether strikers', ('ac_', 'acasters_', 'astrikers_', 'as_weapon', 'characterlocomotion_ac', 'player_combat_ac'),
                 ('blueprints/Weapons/_shared/acasters/', 'game/abilities/weapons/AetherCasters/',
                  'Character/Player/Anims/AC/')),
    'bow': ('Bow (unreleased)', ('bow_', 'notify_consumestamina_bow', 'player_combat_bow'),
            ('blueprints/Weapons/_shared/Bow/', 'Character/Player/Anims/Bow/')),
}
ORDER = ('sword', 'axe', 'hammer', 'chainblades', 'warpike', 'repeaters', 'strikers', 'bow')
SHEET_ORDER = ('shared',) + ORDER + ('armor',)
CODES = {'sword': 'EB', 'axe': 'GA', 'hammer': 'IH', 'chainblades': 'CB', 'warpike': 'MS', 'repeaters': 'DP',
         'strikers': 'AC', 'bow': 'BOW'}
CATALOG = {'sword': 'eblade', 'axe': 'gaxe', 'hammer': 'ihammer', 'chainblades': 'cblades', 'warpike': 'mspear',
           'repeaters': 'dp', 'strikers': 'ac', 'bow': 'bow'}
GEAR = 'Gameplay/Backend/Catalogs/Gear_Catalogs/'
SHARED_FOLDERS = ('blueprints/Weapons/_shared/legendary/', 'blueprints/Weapons/_shared/D24Talents/',
                  'blueprints/Weapons/_shared/AuraFX/', 'game/abilities/weapons/_shared/',
                  '_shared/blueprints/buffs/weapon_modifiers/', 'Character/Player/Anims/Legendary/',
                  'Character/Player/Anims/Base/', 'Character/Player/blueprints/Notifies/',
                  'Character/Player/blueprints/Conditions/', 'Character/Player/data_assets/')
SHARED_PACKAGES = (
    'blueprints/Weapons/BP_BaseArchonWeapon', 'blueprints/Weapons/base_ranged_weapon_bp', 'blueprints/Weapons/weapon_bpi', 'blueprints/Weapons/_shared/weapon_part_base_bpc',
    'blueprints/Weapons/_shared/weapon_part_bpc_bpi', 'blueprints/Weapons/_shared/weapon_part_owner_bpi',
    'blueprints/Weapons/_shared/special_active_ge', 'blueprints/Weapons/EBAttackType',
    'game/abilities/weapons/damage_ge', 'game/abilities/weapons/behemoth_damage_ge',
    'game/abilities/weapons/undodgable_damage_de', 'game/abilities/weapons/elemental_damage_tag_ge',
    'game/abilities/weapons/dot_fire_playertobehemoth_ab', 'game/abilities/weapons/dot_fire_playertobehemoth_ge',
    'game/abilities/weapons/dot_frost_damage_ab', 'game/abilities/weapons/dot_frost_damage_ge',
    'game/abilities/weapons/dot_crowbear_weapon_lifedrain_ge', 'game/abilities/weapons/eb_charge_ab',
    'Gameplay/DamageTable', 'Gameplay/weapon_combat_tuning_table', 'Gameplay/weapon_power_table',
    'Gameplay/weapon_power_table_level_01', 'Gameplay/weapon_power_table_obprog',
    'Gameplay/weapon_progression_rework_power_table', 'Gameplay/weapon_tracker_table',
    'Gameplay/Backend/Catalogs/weapon_set_info', 'Gameplay/talent_crafting_requirements',
    GEAR + 'weapon_part_catalog', GEAR + 'weapon_part_catalog_composite', GEAR + 'weapon_part_catalog_override',
    GEAR + 'weapon_talent_catalog', 'Gameplay/Backend/Catalogs/Token_Catalogs/tokens_unlock_weapons_catalog',
    'Gameplay/Experience/weapon_experience_track_table', 'Gameplay/Experience/weapon_prestige_track_table',
    'Gameplay/cell_unlock_pattern_table', 'Gameplay/weapon_recipe_patterns', 'Gameplay/weapon_recipe_patterns_obprog',
    'Character/Player/blueprints/ammo_bpc', 'Character/Player/blueprints/adjust_ammo_bpn',
    'Character/Player/blueprints/reload_ammo_bpn', 'Character/Player/blueprints/archon_damage_window',
    'Character/Player/blueprints/override_weapon_ability_dmgrow_ns',
    'Character/Player/blueprints/activate_special_abilities_bpn',
    'Character/Player/blueprints/Notify_WeaponDamageState', 'Character/Player/blueprints/Notify_WeaponChargeDamageState',
    'Character/Player/blueprints/Notify_WeaponDamageState_TalentOption',
    'Character/Player/blueprints/Notify_AmmoQualityDamageState', 'Character/Player/blueprints/Notify_ConsumeStamina',
    'Character/Player/blueprints/Notify_ConsumeStamina_RepeatingElements',
    'Character/Player/blueprints/Notify_SetOpenAttackAvailable', 'Character/Player/blueprints/Notify_Invincibility',
    'Character/Player/blueprints/stagger_montage_data_str', 'Character/Player/blueprints/player_requirements_str',
    'Character/Player/blueprints/set_weapon_sheathe_state', 'Character/Player/blueprints/Set_Weapon_Attachment',
)
# Armor, perks (cells) and set bonuses: captured as the pseudo-type 'armor' with the same dump and class sheets.
ARMOR_FOLDERS = ('blueprints/Armour/', '_shared/blueprints/cells/', '_shared/blueprints/buffs/cell_building_blocks/',
                 '_shared/blueprints/buffs/setbonuses/', 'Character/Player/blueprints/Cellsv2/',
                 'Character/Player/blueprints/KeywordBonuses/', 'game/abilities/armor/')
ARMOR_PREFIXES = ('armour_', 'armor_', 'cell_', 'cells_', 'perk_', 'gear_set', 'gear_stat', 'keyword_',
                  'generated_cell_', 'obsolete_cell_', 'dt_cells_', 'dt_cell_', 'containers_core_cellcores',
                  'healthscale_ge')
# Folders whose packages are never weapon logic (matched case-insensitively against the package path).
NOT_LOGIC = ('/effects/', '/ui/', '/audio/', '/characterprops/', '/world/', '/environment/', '/cinematics/',
             '/materials/', '/textures/', '/meshes/', '/_designvfx/', '/vfx/', '/fx/', '/sounds/', '/maps/')
SKIP_TYPES = set(assets.SKIP_TYPES) | {'BlendSpace', 'BlendSpace1D', 'AimOffsetBlendSpace', 'AimOffsetBlendSpace1D',
                                       'PoseAsset', 'MaterialFunction'}
REF_DEPTH = 2
REF_SKIP = ('/UI/', '/Cinematics/', '/Camera/', '/atmospheres/', '/Audio/', '/Effects/', '/Monsters/', '/World/')
SHEETS_DIR = os.path.join(paths.OUT, 'weapons')
OUT_JSON = os.path.join(paths.OUT, 'weapons.json')
OUT_TXT = os.path.join(paths.OUT, 'weapons.txt')
_BGC = re.compile(r'BlueprintGeneratedClass ([A-Za-z0-9_\-]+_C)\b')


def _logic(p):
    return not any(s in p.lower() for s in NOT_LOGIC)


def _listing(folder):
    """{package: type} under one folder (AssetDump class listing, cached per top-level folder)."""
    parts = folder.strip('/').split('/')
    top = C + '/'.join(parts[:3 if parts[0] in ('Character', 'game') else 2]) + '/'
    return {p: t for p, t in assets.class_listing(top).items() if p.startswith(C + folder)}


def packages(key, idx):
    """{package: why} of one weapon type ('shared' for the infrastructure)."""
    every = sorted({p for ps in idx.values() for p in ps})
    out = {}
    if key == 'armor':
        folders, prefixes = ARMOR_FOLDERS, ARMOR_PREFIXES
    elif key == 'shared':
        folders, prefixes = SHARED_FOLDERS, ()
        for p in SHARED_PACKAGES:
            for hit in idx.get(p.rsplit('/', 1)[-1].lower(), []):
                if hit == C + p:
                    out[hit] = 'shared'
    else:
        _, prefixes, folders = WEAPONS[key]
        prefixes += ('weapon_%s_' % CATALOG[key],)   # weapon_eblade_gameplay_catalog, ..._cosmetic_catalog
    for f in folders:
        for p, t in _listing(f).items():
            if t not in SKIP_TYPES:
                out[p] = 'folder'
    for p in every:
        n = p.rsplit('/', 1)[-1].lower()
        if p not in out and n.startswith(prefixes) and _logic(p) and '/anims/' not in p.lower()                 and not (key == 'armor' and '/monsters/' in p.lower()):   # behemoth armour part presets
            out[p] = 'prefix'
    return out


def _class_types(pkgs, al):
    """Which of these packages are Blueprint classes (from the converted asset files)."""
    out = set()
    for p in pkgs:
        rec = _load(_file(p, al))
        if rec and rec['type'] == 'ClassDefaults':
            out.add(p)
    return out


def _load(name):
    p = paths.class_file(name, '.json', assets.ASSETS_DIR)
    return json.load(open(p, encoding='utf-8')) if os.path.exists(p) else None


def _referenced(names, idx):
    """Packages of Blueprint classes named in these assets (and their code, and their parents)."""
    out = set()
    for n in names:
        rec = _load(n)
        if not rec:
            continue
        found = set(_BGC.findall(json.dumps(rec['exports'])))
        d = blueprints.dump([rec['package']]).get(rec['package'])
        sup = blueprints.super_of(d, rec.get('class') or n + '_C') if d else None
        if sup and sup.endswith('_C'):
            found.add(sup)
        cf = paths.class_file(n + '_C')
        if os.path.exists(cf):
            for f in json.load(open(cf, encoding='utf-8')).get('functions', []):
                found |= {x.split(' ', 1)[1] for x in f.get('assets', []) if x.startswith('BlueprintGeneratedClass ')}
        for c in found:
            p = blueprints.package_for_class(c, idx)
            if p and not any(s.lower() in p.lower() for s in REF_SKIP):
                out.add(p)
    return out


def aliases(pkgs):
    """{package: file name} for packages whose basename another of these packages shares (the sword keeps its
    pre-rework montages beside the Rework ones under the same names). The one in a Rework folder, else the first,
    keeps the plain name; the others get '~<parent folder>'."""
    by = {}
    for p in sorted(pkgs):
        by.setdefault(p.rsplit('/', 1)[-1].lower(), []).append(p)
    out = {}
    for ps in by.values():
        if len(ps) > 1:
            keep = next((p for p in ps if '/rework/' in p.lower()), ps[0])
            out.update({p: '%s~%s' % (p.rsplit('/', 1)[-1], p.split('/')[-2]) for p in ps if p != keep})
    return out


def _file(p, al):
    return al.get(p) or p.rsplit('/', 1)[-1]


def _list_path(key):
    return os.path.join(paths.CACHE, 'weapon_packages_%s.json' % key)


def dump(keys, refresh=False, log=print, find=None):
    """Dump each weapon type's packages and referenced classes, then build their Blueprint code. find(key, idx)
    replaces packages() for other areas (areas.py: items, rewards, islands)."""
    from .build import build
    idx = blueprints.package_index()
    for key in keys:
        pk = (find or packages)(key, idx)
        log('%s: %d packages (%d by folder, %d by prefix)' % (
            key, len(pk), sum(v != 'prefix' for v in pk.values()), sum(v == 'prefix' for v in pk.values())))
        skipped = []
        quiet = lambda m: skipped.append(m) if m.startswith('skip') else None
        al = aliases(pk)
        assets.build_packages(sorted(pk), idx, refresh, log=quiet, aliases=al)
        extra, frontier = set(), [_file(p, al) for p in pk]
        if key in CODES:   # classes the item, part and power rows name (part components, buffs, abilities)
            seeds = {blueprints.package_for_class(c, idx) for c in catalog_classes(key)} - {None} - set(pk)
            if seeds:
                log('  classes named by catalog rows: %d packages' % len(seeds))
                assets.build_packages(sorted(seeds), idx, refresh, log=quiet)
                extra |= seeds
                frontier += [p.rsplit('/', 1)[-1] for p in seeds]
        for step in range(REF_DEPTH + 1):
            classes = sorted(p.rsplit('/', 1)[-1] + '_C' for p in _class_types(set(pk) | extra, al))
            build(classes, refresh=refresh, log=lambda m: None)
            if step == REF_DEPTH:
                break
            new = sorted(_referenced(frontier, idx) - extra - set(pk))
            if not new:
                break
            log('  referenced classes, step %d: %d packages' % (step + 1, len(new)))
            assets.build_packages(new, idx, refresh, log=quiet)
            extra |= set(new)
            frontier = [p.rsplit('/', 1)[-1] for p in new]
        with open(_list_path(key), 'w', encoding='utf-8') as f:
            json.dump({'packages': pk, 'referenced': sorted(extra), 'aliases': al, 'skipped': skipped}, f, indent=1)
        log('  done; %d referenced packages, %d skipped' % (len(extra), len(skipped)))


# ---------------------------------------------------------------- sheets

# Kinds by the first matching class in the parent chain; the order is the sheet's section order.
KINDS = (('weapon actor', ('ArchonWeapon',)), ('armor piece', ('ArchonArmor',)), ('legendary part', ('legendary_weapon_part_base_bpc_C',)),
         ('part', ('ArchonEquipmentPart',)), ('ability', ('GameplayAbility',)), ('effect', ('GameplayEffect',)),
         ('buff', ('ArchonBuff',)), ('projectile', ('ArchonProjectileBase',)), ('area effect', ('ArchonAoe',)),
         ('condition', ('ArchonCondition',)), ('notify', ('AnimNotify', 'AnimNotifyState')),
         ('anim blueprint', ('AnimInstance',)), ('widget', ('UserWidget',)), ('component', ('ActorComponent',)),
         ('actor', ('Actor',)), ('interface', ('Interface',)))
# Class default settings that only change looks or sound.
COSMETIC = re.compile(r'particle|niagara|sound|audio|akevent|^ak|mesh|material|texture|icon|vfx|fx$|color|colour|'
                      r'impulse|aoeelement|timeline|navigation|areaclass|stepupon|raytracing|hiding|^hide|dye|cutmask|'
                      r'decal|light|camera|shake|rumble|trail|tint|glow|emissive|widget|brush|^ui|display|thumbnail|'
                      r'preview|editor|debug|socket', re.I)
NOISY_OBJECTS = ('Particle', 'Niagara', 'Audio', 'Ak', 'Light', 'Decal', 'Widget')
# Scene-graph and replication bookkeeping in class defaults.
NOISE = {'SkinnedAsset', 'BodyInstance', 'AttachParent', 'RootComponent', 'TickExecFilter', 'bEditableWhenInherited',
         'bUpdateOverlapsOnAnimationFinalize', 'bUseAttachParentBound', 'PhysicsAssetOverride',
         'ReplicatedPredictionKeyMap', 'RelativeLocation', 'RelativeRotation', 'RelativeScale3D',
         'UCSSerializationIndex', 'bNetAddressable', 'CreationMethod', 'ComponentDelegateBindings',
         'UberGraphFrame', 'bAutoActivate', 'AttachSocketName', 'BlueprintCreatedComponents'}
TYPE_ENUM = {'sword': 'EBLADE', 'axe': 'GAXE', 'hammer': 'IHAMMER', 'chainblades': 'CBLADES', 'warpike': 'MSPEAR',
             'repeaters': 'DP', 'strikers': 'AC', 'bow': 'BOW'}


def _class(cls):
    p = paths.class_file(cls)
    return json.load(open(p, encoding='utf-8')) if os.path.exists(p) else None


def _kind(parents):
    for k, bases in KINDS:
        if any(b in parents for b in bases):
            return k
    return 'other'


def _defaults(name):
    """The class's own default settings, cosmetic ones left out: [(object, {prop: value})]."""
    rec = _load(name)
    if not rec or rec['type'] != 'ClassDefaults':
        return []
    out = []
    for ename, e in rec['exports'].items():
        if any(n in e['type'] for n in NOISY_OBJECTS):
            continue
        props = {k: v for k, v in e['props'].items()
                 if v not in (None, [], {}, 'None') and k not in NOISE and not COSMETIC.search(k)}
        if props:
            out.append(('defaults' if ename.startswith('Default__') else ename.replace('_GEN_VARIABLE', ''), props))
    return out


def _damage_tables(index):
    """{row: [tables]} over every dumped DataTable whose row struct is DamageTableData."""
    out = {}
    for n, r in index.items():
        if r['type'] != 'DataTable':
            continue
        rec = _load(n)
        props = rec['exports'].get(n, {}).get('props', {}) if rec else {}
        if 'DamageTableData' in str(props.get('RowStruct')):
            for row in props.get('Rows') or {}:
                out.setdefault(row, []).append(n)
    return out


def _windows(rec):
    from .windows import window_records
    return window_records(rec) if rec and rec['type'] == 'AnimMontage' else []


def _row_names(v):
    from .behemoths import _rows_named
    return _rows_named(v)


def _row_fields(props):
    """[(row, field path)] for the damage rows in a class's settings."""
    rows = _row_names(props)
    out = []
    for k, v in _paths(props):
        if isinstance(v, str) and (v in rows or any(v.endswith('[%s]' % r) for r in rows)):
            r = v if v in rows else v[v.rindex('[') + 1:-1]
            if (r, k) not in out:
                out.append((r, k))
    return out


def _paths(v, path=''):
    """(dotted property path, value) for every scalar inside a cleaned property value; list items keep the path."""
    if isinstance(v, dict):
        for k, x in v.items():
            yield from _paths(x, '%s.%s' % (path, k) if path else k)
    elif isinstance(v, list):
        for x in v:
            yield from _paths(x, path)
    else:
        yield path, v


_CONST = re.compile(r"^\s*\[\d+\] (\w+) = '([^']*)'\s*$")
_SELF_SET = re.compile(r"^\s*\[\d+\] self\.(.+?) = '([^']+)'\s*$")
_SPAWN = re.compile(r"BeginDeferredActorSpawnFromClass\([^,]+, (\w+_C),")
_SET_NAME = re.compile(r"SetNamePropertyByName\([^,]+, '(\w+)', (.+)\)\s*$")


def spawn_overrides(classes):
    """{(spawned class, property): [(spawner, assigned value)]}: name properties a Blueprint sets on an actor it
    spawns, between BeginDeferredActorSpawnFromClass and the spawn finishing (read from the pseudo code)."""
    out = {}
    for c in classes:
        p = os.path.join(paths.PSEUDO_DIR, os.path.basename(paths.class_file(c, '.txt')))
        if not os.path.exists(p):
            continue
        spawned, consts = None, {}
        for line in open(p, encoding='utf-8'):
            m = _CONST.match(line)
            if m:
                consts[m.group(1)] = m.group(2)
            m = _SPAWN.search(line)
            if m:
                spawned = m.group(1)
            m = _SET_NAME.search(line)
            if m and spawned:
                vals = re.findall(r"'([^']+)'", m.group(2))
                vals += [consts[w] for w in re.findall(r'\w+', m.group(2)) if w in consts]
                out.setdefault((spawned, m.group(1)), []).append((c, ' or '.join(vals) or m.group(2)))
            m = _SELF_SET.search(line)
            if m:   # the class assigns the property itself (e.g. picks a row by rank before applying damage)
                own = out.setdefault((c, m.group(1)), [])
                if own and own[-1][0] == 'its own code':
                    own[-1] = ('its own code', own[-1][1] + ' or ' + m.group(2))
                else:
                    own.append(('its own code', m.group(2)))
            if line.startswith('=== '):
                spawned, consts = None, {}
    return out


def _suffixes_keys(cls):
    """True when a damage notify class appends a number to each damage key at runtime (ModifyWeaponDamageKeys
    building '<key><int>': charge level for Notify_WeaponChargeDamageState, combo level for the war pike)."""
    p = os.path.join(paths.PSEUDO_DIR, os.path.basename(paths.class_file(cls, '.txt')))
    if not os.path.exists(p):
        return False
    text = open(p, encoding='utf-8').read()
    i = text.find('=== ModifyWeaponDamageKeys')
    body = text[i:text.find('\n=== ', i + 1)] if i >= 0 else ''
    return 'Conv_IntToString' in body and 'BuildString_Name' in body


def collect(key, index):
    """Everything known about one weapon type, from the files the dump wrote; None when not dumped."""
    lp = _list_path(key)
    if not os.path.exists(lp):
        return None
    lst = json.load(open(lp, encoding='utf-8'))
    pkgs = sorted(set(lst['packages']) | set(lst['referenced']))
    sp = _list_path('shared')
    if (key in CODES or key == 'armor') and os.path.exists(sp):   # infrastructure is listed once, in shared.txt
        sh = json.load(open(sp, encoding='utf-8'))
        pkgs = [p for p in pkgs if p not in sh['packages'] and p not in sh['referenced']]
    al = lst.get('aliases', {})
    info = {'key': key, 'classes': {}, 'montages': {}, 'tables': [], 'other_assets': [], 'not_captured': [],
            'rows': {}, 'stems': {}}
    for p in pkgs:
        n = _file(p, al)
        rec = _load(n)
        if not rec:
            info['not_captured'].append(p)
        elif rec['type'] == 'ClassDefaults':
            c = rec.get('class') or n + '_C'
            cr = _class(c)
            parents = cr['parents'] if cr else []
            v = info['classes'][c] = {'package': p, 'kind': _kind(parents), 'parents': parents, 'code': bool(cr),
                                      'defaults': _defaults(n), 'used_by': index.get(n, {}).get('used_by', [])}
            for _, props in v['defaults']:
                for r, field in _row_fields(props):
                    info['rows'].setdefault(r, []).append('%s.%s' % (c, field))
        elif rec['type'] == 'AnimMontage':
            ws = info['montages'][n] = _windows(rec)
            for w in ws:
                stem = _suffixes_keys(w['class'])
                for r in w['keys'] + w['keys_after_first_hit'] + _row_names(w.get('other', {})):
                    info['rows'].setdefault(r, []).append(n)
                    if stem:
                        info['stems'][r] = w['class']
        elif rec['type'] in ('DataTable', 'CompositeDataTable', 'CurveTable', 'CurveFloat', 'CurveVector'):
            info['tables'].append(n)
        else:
            info['other_assets'].append((n, rec['type']))
    info['overrides'] = spawn_overrides(info['classes'])
    return info


def _actors(info):
    """(base weapon actors, skins): a base is a captured weapon actor another one derives from."""
    actors = [c for c, v in info['classes'].items() if v['kind'] == 'weapon actor']
    if info['key'] == 'shared':
        return sorted(actors), []
    parents = {p for c in actors for p in info['classes'][c]['parents']}
    return sorted(c for c in actors if c in parents), sorted(c for c in actors if c not in parents)


def _fmt_props(props):
    return ', '.join('%s=%s' % (k, assets._fmt(v)) for k, v in props.items())


def _stem_rows(stem, rows_index):
    return sorted(r for r in rows_index if r and re.fullmatch(re.escape(stem) + r'\d+', r, re.I))


def _mark(row, rows_index):
    """+ in a table, ~ in a table under another letter case (FName lookups ignore case), ? in none."""
    if row in rows_index:
        return '+'
    return '~' if row.lower() in rows_index.get('', {}) else '?'


def sheet(key, info, rows_index):
    if key == 'shared':
        name, prefixes, folders = 'Shared weapon infrastructure', (), SHARED_FOLDERS
    elif key == 'armor':
        name, prefixes, folders = 'Armor, perks (cells) and set bonuses', ARMOR_PREFIXES, ARMOR_FOLDERS
    else:
        name, prefixes, folders = WEAPONS[key]
    cls = info['classes']
    out = ['%s  (%s)' % (name, key),
           'Generated by python -m tools.atlas weapons from the 2.1.1 paks. Class code: show <class>; asset: asset <name>.',
           'Name prefixes: %s' % (', '.join(prefixes) or '-'),
           'Folders: %s' % ', '.join(folders)]
    if key in TYPE_ENUM:
        out.append('Native enum: EWeaponType::WeaponType_%s' % TYPE_ENUM[key])
    if os.path.exists(os.path.join(paths.OUT, 'topics', '%s_server.md' % key)):
        out.append('What the server does for this type, and why: topics/%s_server.md' % key)
    if key != 'armor':
        out.append('How a weapon finds its power row and gets its abilities: topics/weapon_power_and_abilities.md')
    if key in CODES:
        for title, fn in (('Items (gameplay catalog: the weapon items players own, their actor class and part slots)',
                           item_lines),
                          ('Parts (weapon_part_catalog rows of type Weapon_Part_%s_*)' % CODES[key], part_lines),
                          ('Power table (damage, element, granted abilities, weapon-type settings)', power_lines)):
            lines = fn(key)
            out += ['', '== ' + title] + (lines or ['  none found'])
    if key == 'armor':
        out.append('How armor stats, perks, gear stats and set bonuses are applied: topics/armor.md')
        for title, fn in ARMOR_SECTIONS:
            out += ['', '== ' + title] + (fn() or ['  none found'])
    bases, skins = _actors(info)
    if key == 'armor':
        bases = skins = ()
    else:
        out += ['', '== Weapon actor: base classes (the skins below derive from them)']
    for c in bases:
        v = cls[c]
        out.append('%s  parents: %s  [%s]' % (c, ' -> '.join(v['parents']), v['package']))
        out += ['  %s: %s' % (obj, _fmt_props(props)) for obj, props in v['defaults']]
    if not bases and key != 'armor':
        out.append('  none captured')
    if key != 'armor':
        out += ['', '== Skin weapon actors (%d): parent, then the settings each one sets itself' % len(skins)]
    for c in skins:
        v = cls[c]
        out.append('  %-44s < %s' % (c, v['parents'][0] if v['parents'] else '?'))
        out += ['      %s: %s' % (obj, _fmt_props(props)) for obj, props in v['defaults']]
    for kind, _ in KINDS[1:] + (('other', ()),):
        group = sorted(c for c, v in cls.items() if v['kind'] == kind)
        if not group or kind == 'widget':
            continue
        out += ['', '== %s classes (%d)' % (kind.capitalize(), len(group))]
        for c in group:
            v = cls[c]
            out.append('%s  < %s%s' % (c, ' < '.join(v['parents'][:3]), '' if v['code'] else '  [no code built]'))
            out += ['    %s: %s' % (obj, _fmt_props(props)) for obj, props in v['defaults']]
            users = [u for u in v['used_by'] if u.split('.')[0] not in (c, c[:-2])]
            if users:
                out.append('    used by: %s%s' % (', '.join(users[:12]), ' ...' if len(users) > 12 else ''))
    widgets = sorted(c for c, v in cls.items() if v['kind'] == 'widget')
    if widgets:
        out += ['', '== HUD widgets (%d; client only): %s' % (len(widgets), ', '.join(widgets))]
    with_w = {m: ws for m, ws in info['montages'].items() if ws}
    out += ['', '== Montages (%d; %d with damage windows): start s, duration s, window class, damage rows, bodies'
            % (len(info['montages']), len(with_w))]
    for m in sorted(with_w):
        out.append('  %s' % m)
        for w in with_w[m]:
            roles = '; '.join('%s: %s' % (r.replace('WeaponRole_', ''), ','.join(x['bodies'] + x['skel_bodies']))
                              for r, x in w['roles'].items())
            keys = ','.join(k + ('*' if k in info['stems'] and _mark(k, rows_index) == '?' else _mark(k, rows_index))
                            for k in w['keys']) or '-'
            extra = ' '.join(x for x in (_fmt_props(w['settings']), _fmt_props(w.get('other', {}))) if x)
            out.append('    %7s %7s  %s  %s  %s%s' % (assets._fmt(w['start']), assets._fmt(w['duration']),
                                                    w['class'].removesuffix('_C'), keys, roles,
                                                    ('  ' + extra) if extra else ''))
    rest = sorted(m for m, ws in info['montages'].items() if not ws)
    if rest:
        out.append('  without damage windows: %s' % ', '.join(rest))
    out += ['', '== Damage rows named (+ in a DamageTableData table; ~ in one under another letter case, which an '
            'FName lookup ignores; * a stem the notify completes with a level number; ? in none)']
    overrides = info['overrides']
    for r in sorted(info['rows'], key=str.lower):
        users = sorted(set(info['rows'][r]))
        tables = rows_index.get(r) or rows_index.get(rows_index[''].get(r.lower()), [])
        case = '' if r in rows_index or not tables else ' (as %s)' % rows_index[''][r.lower()]
        mark = '*' if r in info['stems'] and _mark(r, rows_index) == '?' else _mark(r, rows_index)
        out.append('  %s%s%s  %s  <- %s%s' % (r, mark, case, ','.join(tables), ', '.join(users[:6]),
                                             ' ...' if len(users) > 6 else ''))
        if r in info['stems']:
            out.append('      %s adds a level number at runtime: %s' % (
                info['stems'][r].removesuffix('_C'), ', '.join(_stem_rows(r, rows_index)) or 'no such rows'))
        for u in users:
            for spawner, val in overrides.get(tuple(u.split('.', 1)), []):
                out.append('      %s is set %s: %s' % (u, 'by its own code' if spawner == 'its own code'
                                                       else 'when %s spawns it' % spawner, val))
    out += ['', '== Tables and curves (%d): %s' % (len(info['tables']), ', '.join(info['tables']) or '-')]
    if info['other_assets']:
        out += ['', '== Other data assets (%d)' % len(info['other_assets'])]
        out += ['  %s  (%s)' % x for x in info['other_assets']]
    if info['not_captured']:
        out += ['', '== Packages not captured (%d; render-only or empty)' % len(info['not_captured'])]
        out += ['  ' + p for p in info['not_captured']]
    return out


def summary(info, rows_index):
    cls = info['classes'].values()
    count = lambda k: sum(v['kind'] == k for v in cls)
    bases, skins = _actors(info)
    missing = []
    for r, users in info['rows'].items():
        if _mark(r, rows_index) != '?' or (r in info['stems'] and _stem_rows(r, rows_index)):
            continue
        # a class default that every spawner replaces is not a missing row
        if users and all(info['overrides'].get(tuple(u.split('.', 1))) for u in users):
            continue
        # a class with two row fields whose code assigns one of them (cell_dodge_stagger_aoe: 'Damage Row Name')
        if users and all(any(info['overrides'].get((u.split('.')[0], x.split('.', 1)[1]))
                             for x in users if x.split('.')[0] == u.split('.')[0]) for u in users):
            continue
        # the placeholder in a damage notify class's own defaults; each montage's notify sets its keys
        if users and all(info['classes'].get(u.split('.')[0], {}).get('kind') == 'notify' for u in users):
            continue
        missing.append(r)
    key = info['key']
    parts = _my_parts(key, 'weapon_part_catalog') if key in CODES else {}
    slot = lambda r: str(r.get('WeaponPartType')).rsplit('_', 1)[-1]
    return {'base_actors': bases, 'actor': len(bases), 'skins': len(skins), 'abilities': count('ability'),
            'items': len(_rows('weapon_%s_gameplay_catalog' % CATALOG[key])) if key in CATALOG else 0,
            'specials': sum(slot(r) == 'Special' for r in parts.values()),
            'passives': sum(slot(r) not in ('Special', 'Legendary') for r in parts.values()),
            'legendaries': sum(slot(r) == 'Legendary' for r in parts.values()),
            'power_rows': len(_my_power(key)) if key in CODES else 0,
            'effects': count('effect'), 'buffs': count('buff'), 'projectiles': count('projectile'),
            'aoes': count('area effect'), 'parts': count('part'), 'legendary': count('legendary part'),
            'montages': len(info['montages']), 'windows': sum(len(w) for w in info['montages'].values()),
            'rows': len(info['rows']), 'rows_missing': sorted(missing),
            'no_code': sorted(c for c, v in info['classes'].items() if not v['code']),
            'not_captured': len(info['not_captured'])}


def sheets(log=print):
    index = json.load(open(assets.INDEX, encoding='utf-8'))
    rows_index = _damage_tables(index)
    rows_index[''] = {r.lower(): r for r in list(rows_index)}   # case-insensitive lookup for _mark
    os.makedirs(SHEETS_DIR, exist_ok=True)
    data = {}
    for key in SHEET_ORDER:
        info = collect(key, index)
        if info is None:
            continue
        with open(os.path.join(SHEETS_DIR, key + '.txt'), 'w', encoding='utf-8') as f:
            f.write('\n'.join(sheet(key, info, rows_index)) + '\n')
        data[key] = dict(summary(info, rows_index), prefixes=list(WEAPONS[key][1]) if key in WEAPONS else [])
        if key == 'armor':
            data[key].update(armor_items=len(_rows('armour_gameplay_catalog')),
                             armor_pieces=sum(v['kind'] == 'armor piece' for v in info['classes'].values()),
                             set_bonuses=len(_rows('gear_set_bonus_table')), gear_stats=len(_rows('gear_stat_table')),
                             perks=len(_rows('keyword_cell_effects')), cell_effects=len(_rows('cell_effect_table')),
                             cells=len(_rows('generated_cell_catalog')))
    with open(OUT_JSON, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    with open(OUT_TXT, 'w', encoding='utf-8') as f:
        f.write('\n'.join(render(data)) + '\n')
    log('%d weapon sheets -> data/atlas/weapons/' % len(data))


COLUMNS = (('items', 'items'), ('power_rows', 'power'), ('specials', 'spec'), ('passives', 'other'),
           ('legendaries', 'legnd'), ('actor', 'base'), ('skins', 'skins'), ('parts', 'partC'),
           ('abilities', 'abil'), ('effects', 'GEs'), ('buffs', 'buffs'), ('projectiles', 'proj'), ('aoes', 'aoe'),
           ('montages', 'monts'), ('windows', 'wins'), ('rows', 'rows'))
LEGEND = ('items = gameplay catalog rows; power = weapon_power_table rows; spec/other/legnd = weapon_part_catalog rows '
          'by slot (other = passive, barrel, grip, ...); base = base weapon actor classes; skins = weapon actors deriving from them; partC = part component '
          'classes of this type; abil/GEs/buffs/proj/aoe = classes; monts = montages; wins = damage windows; rows = '
          'damage rows named')


def render(data):
    out = ['Weapon types (python -m tools.atlas weapons). One sheet each in data/atlas/weapons/<key>.txt.',
           '', 'Internal names: the game calls each weapon type by a prefix; EWeaponType is the client\'s native enum.']
    out += ['  %-12s %-18s WeaponType_%-8s prefixes %s' % (k, WEAPONS[k][0], TYPE_ENUM[k], ', '.join(WEAPONS[k][1]))
            for k in ORDER]
    import textwrap
    out += ['', 'Coverage per type (a dash = not dumped yet).'] + textwrap.wrap(LEGEND, 116, initial_indent='  ',
                                                                              subsequent_indent='  ')
    out += [
            '  %-12s ' % 'type' + ' '.join('%5s' % h for _, h in COLUMNS) + '  missing rows  no code']
    for k in ('shared',) + ORDER:
        d = data.get(k)
        if not d:
            out.append('  %-12s %s' % (k, '-'))
            continue
        out.append('  %-12s ' % k + ' '.join('%5s' % d[c] for c, _ in COLUMNS)
                   + '  %12d  %7d' % (len(d['rows_missing']), len(d['no_code'])))
    if data.get('armor'):
        d = data['armor']
        out += ['', 'Armor (data/atlas/weapons/armor.txt): %d armor items, %d armor piece classes, %d set bonus sets, %d gear '
                'stats, %d keyword perks, %d cell effects, %d cells; classes: %d effects, %d buffs, %d abilities.' % (
                    d['armor_items'], d['armor_pieces'], d['set_bonuses'], d['gear_stats'], d['perks'], d['cell_effects'],
                    d['cells'], d['effects'], d['buffs'], d['abilities'])]
    out += ['', 'Damage rows a type names that are in no dumped DamageTableData table, and classes without built code:']
    for k, d in data.items():
        if d['rows_missing'] or d['no_code']:
            out.append('  %s' % k)
            if d['rows_missing']:
                out.append('    rows: %s' % ', '.join(d['rows_missing']))
            if d['no_code']:
                out.append('    no code: %s' % ', '.join(d['no_code']))
    return out


# ---------------------------------------------------------------- item, part and power-table rows

def _rows(table):
    rec = _load(table)
    return (rec['exports'].get(table, {}).get('props', {}).get('Rows') or {}) if rec else {}


def _cls_name(v):
    """'/Game/.../dp_ostian_00_bp.dp_ostian_00_bp_C' or 'BlueprintGeneratedClass x_C' -> 'x_C'."""
    if not isinstance(v, str) or v in ('', 'None'):
        return None
    return v.rsplit('.', 1)[-1].split(' ')[-1]


def _my_parts(key, table):
    code = CODES[key]
    return {k: r for k, r in _rows(table).items()
            if str(r.get('WeaponPartType', '')).split('::')[-1].startswith('Weapon_Part_%s_' % code)}


def _my_power(key):
    code = CODES[key]
    rows = _rows('weapon_power_table')
    return {k: r for k, r in rows.items() if k.startswith(('WP_%s_' % code, 'PART_%s_' % code))
            or (k.startswith('BP_') and (code in k.split('_') or (key == 'axe' and k.startswith('BP_AXE'))))}


def catalog_classes(key):
    """Blueprint classes named by this weapon's item, part and power-table rows."""
    out = set()
    for t in ('weapon_%s_gameplay_catalog' % CATALOG[key], 'weapon_%s_gameplay_catalog_composite' % CATALOG[key]):
        out |= {_cls_name(r.get('ItemClass')) for r in _rows(t).values()}
    for t in ('weapon_part_catalog', 'weapon_part_catalog_override', 'weapon_part_catalog_composite'):
        for r in _my_parts(key, t).values():
            out |= {_cls_name(x) for x in [r.get('PartComponent')] + (r.get('Abilities') or []) + (r.get('Buffs') or [])}
    for r in _my_power(key).values():
        for k in ('RefAbilities',) + tuple('RefSpecialAbilities%d' % i for i in range(1, 6)):
            out |= {_cls_name(a) for a in r.get(k) or []}
    return sorted(c for c in out if c and c.endswith('_C'))


def item_lines(key):
    """The weapon's items from its gameplay catalog: actor class, gear set, part slots, progression, tags."""
    out, seen = [], {}
    for table in ('weapon_%s_gameplay_catalog' % CATALOG[key], 'weapon_%s_gameplay_catalog_composite' % CATALOG[key]):
        rows = _rows(table)
        if not rows:
            continue
        same = next((t for t, r in seen.items() if r == rows), None)
        if same:
            out.append('%s: the same %d rows as %s' % (table, len(rows), same))
            continue
        seen[table] = rows
        out.append('%s (%d items)' % (table, len(rows)))
        for rid, r in rows.items():
            slots = ', '.join('%s%s' % (str(x.get('WeaponPartType')).split('Weapon_Part_')[-1],
                                        '=' + x['DefaultWeaponPartID'] if x.get('DefaultWeaponPartID') else '')
                              for x in r.get('WeaponPartSlots') or [] if 'Tracker' not in str(x.get('WeaponPartType')))
            gid = r.get('GenericItemData') or {}
            bits = ['class=%s' % _cls_name(r.get('ItemClass')), 'set=%s' % r.get('GearSetName'),
                    'rarity=%s' % assets._fmt(gid.get('RarityLevel')), 'tier=%s' % gid.get('TierLevel')]
            for k in ('ItemProgressionTable', 'CellUnlockPattern', 'TalentCraftingRequirements'):
                if r.get(k) and 'None' not in str(r[k]):
                    bits.append('%s=%s' % (k, r[k]))
            for k in ('SpecialAbility', 'PassiveAbility', 'LegendaryAbility'):
                if (r.get(k) or {}).get('Name'):
                    bits.append('%s="%s"' % (k, r[k]['Name']))
            out.append('  %s  %s' % (rid, '  '.join(bits)))
            if slots:
                out.append('      slots: %s' % slots)
            tags = [t for t in r.get('Tags') or [] if t not in ('Weapon',)]
            if tags:
                out.append('      tags: %s' % ', '.join(tags))
    return out


def part_lines(key):
    """weapon_part_catalog rows whose part type is this weapon's (Weapon_Part_EB_Special, ...)."""
    code = CODES[key]
    out, seen = [], {}
    for table in ('weapon_part_catalog', 'weapon_part_catalog_override', 'weapon_part_catalog_composite'):
        rows = _my_parts(key, table)
        if not rows:
            continue
        same = next((t for t, r in seen.items() if r == rows), None)
        if same:
            out.append('%s: the same %d rows as %s' % (table, len(rows), same))
            continue
        seen[table] = rows
        out.append('%s (%d parts)' % (table, len(rows)))
        for rid, r in sorted(rows.items(), key=lambda kv: (str(kv[1].get('WeaponPartType')), kv[0])):
            abil = [_cls_name(a) for a in r.get('Abilities') or [] if _cls_name(a)]
            buffs = [_cls_name(a) for a in r.get('Buffs') or [] if _cls_name(a)]
            bits = ['component=%s' % _cls_name(r.get('PartComponent'))]
            if abil:
                bits.append('abilities=%s' % ','.join(abil))
            if buffs:
                bits.append('buffs=%s' % ','.join(buffs))
            if r.get('GearSetName') not in (None, '', 'None'):
                bits.append('set=%s' % r['GearSetName'])
            name = r.get('RewardName') or (r.get('GenericItemData') or {}).get('Name')
            if name:
                bits.append('name="%s"' % name)
            out.append('  %-40s %-10s %s' % (rid, str(r['WeaponPartType']).split('Weapon_Part_%s_' % code)[-1],
                                            '  '.join(bits)))
    return out


# Power-table fields that every weapon type has; the rest are named after a type (EBlade_, IHammer_, Axe_, ...).
POWER_COMMON = ('GlobalPower', 'PhysicalType', 'ElementalType', 'ElementalPower', 'RawElementalPower')
POWER_TYPE_FIELDS = {'sword': 'EBlade_', 'axe': 'Axe_', 'hammer': 'IHammer_', 'chainblades': 'CBlade_',
                     'warpike': 'MSpear_', 'repeaters': 'DP_', 'strikers': 'AC_', 'bow': 'Bow_'}


def power_lines(key):
    """weapon_power_table rows of this type: power, element, abilities, and the type's own fields (those that differ
    from the type's first BP_ row, which is printed in full)."""
    mine = _my_power(key)
    if not mine:
        return []
    pre = POWER_TYPE_FIELDS[key]
    base_id = next((k for k in sorted(mine) if k.startswith('BP_')), sorted(mine)[0])
    base = mine[base_id]
    out = ['weapon_power_table (%d rows; per-row type fields are the ones that differ from %s)' % (len(mine), base_id)]
    for rid in [base_id] + sorted(k for k in mine if k != base_id):
        r = mine[rid]
        bits = ['%s=%s' % (k, assets._fmt(r.get(k))) for k in POWER_COMMON if r.get(k) not in (None, 0, 0.0)]
        for k in ('RefAbilities',) + tuple('RefSpecialAbilities%d' % i for i in range(1, 6)):
            v = [_cls_name(a) for a in r.get(k) or [] if _cls_name(a)]
            if v:
                bits.append('%s=%s' % (k, ','.join(v)))
        own = {k: v for k, v in r.items() if k.startswith(pre) and (rid == base_id or v != base.get(k))}
        if own:
            bits.append(', '.join('%s=%s' % (k[len(pre):], assets._fmt(v)) for k, v in own.items()))
        out.append('  %s  %s' % (rid, '  '.join(bits)))
    return out


# ---------------------------------------------------------------- armor sections

def _handle_row(v):
    """'keyword_cell_effects_composite[KW_X]' -> 'KW_X'."""
    m = re.match(r'^\S+\[(.+)\]$', str(v))
    return m.group(1) if m and m.group(1) != 'None' else None


def _mods(m):
    """A GameplayModifiers struct -> 'buffs=a,b  effects=...' (empty parts left out)."""
    out = []
    for k, label in (('BuffClasses', 'buffs'), ('GameplayEffectClasses', 'effects'),
                     ('GameplayAbilityClasses', 'abilities'), ('KeywordBonusClasses', 'keyword bonuses')):
        v = [_cls_name(x) for x in (m or {}).get(k) or [] if _cls_name(x)]
        if v:
            out.append('%s=%s' % (label, ','.join(v)))
    if (m or {}).get('BuffMagnitudes'):
        out.append('magnitudes=%s' % assets._fmt(m['BuffMagnitudes']))
    return '  '.join(out)


def _effect_classes(me):
    return '  '.join('%s=%s' % (k, ','.join(c for c in map(_cls_name, me.get(k) or []) if c))
                     for k in ('Buffs', 'GameplayEffects', 'Abilities') if any(map(_cls_name, me.get(k) or [])))


def armor_item_lines():
    out, seen = [], {}
    for table in ('armour_gameplay_catalog', 'armour_gameplay_catalog_composite'):
        rows = _rows(table)
        if not rows:
            continue
        same = next((t for t, r in seen.items() if r == rows), None)
        if same:
            out.append('%s: the same %d rows as %s' % (table, len(rows), same))
            continue
        seen[table] = rows
        out.append('%s (%d items; perks = permanent keyword cell effects by upgrade level, x ranks)' % (table, len(rows)))
        for rid, r in sorted(rows.items(), key=lambda kv: (str(kv[1].get('GearSetName')), kv[0])):
            gid = r.get('GenericItemData') or {}
            bits = ['%s' % str(r.get('ArmourSlot')).split('ArmorType_')[-1], 'class=%s' % _cls_name(r.get('ItemClass')),
                    'set=%s' % r.get('GearSetName'), 'rarity=%s' % assets._fmt(gid.get('RarityLevel')),
                    'tier=%s' % gid.get('TierLevel')]
            if _handle_row(r.get('GearSetBonusId')):
                bits.append('set bonus=%s' % _handle_row(r['GearSetBonusId']))
            gs = r.get('PrimaryGearStat') or {}
            if _handle_row(gs.get('GearStat')):
                bits.append('gear stat=%s %s+%s/level' % (_handle_row(gs['GearStat']), assets._fmt(gs.get('BaseMagnitude')),
                                                         assets._fmt(gs.get('PerLevelMagnitude'))))
            for k in ('CellUnlockPattern', 'ItemProgressionTable'):
                if _handle_row(r.get(k)):
                    bits.append('%s=%s' % (k, _handle_row(r[k])))
            if r.get('DisplayName'):
                bits.append('"%s"' % r['DisplayName'])
            out.append('  %s  %s' % (rid, '  '.join(bits)))
            perks = []
            for lv in r.get('PermanentCellsPerLevel') or []:
                cells = ['%s%s' % (_handle_row(c.get('CellEffectRef')), ' x%d' % c['RanksGranted']
                                   if c.get('RanksGranted', 1) != 1 else '')
                         for c in lv.get('PermanentCellEffects') or [] if _handle_row(c.get('CellEffectRef'))]
                cells += [str(c) for c in lv.get('PermanentCells') or []]
                if cells:
                    perks.append('L%s: %s' % (str(lv.get('ItemLevel')).split('Level_')[-1], ', '.join(cells)))
            if perks:
                out.append('      perks: %s' % '; '.join(perks))
            slots = r.get('CellSlotOverridesPerLevel') or []
            if slots:
                out.append('      cell slots: %s' % assets._fmt(slots))
    return out


def set_bonus_lines():
    rows = _rows('gear_set_bonus_table')
    live = ['%s %s' % (sid, b.get('RequiredSources')) for sid, r in rows.items() for b in r.get('GearSetBonuses') or []
            if _mods(b.get('GameplayModifiers'))]
    out = ['  %d sets; %d bonuses grant something (%s); the rest are placeholders with no effect.'
           % (len(rows), len(live), ', '.join(live))]
    for sid, r in rows.items():
        out.append('  %s  "%s"' % (sid, r.get('DisplayName')))
        for b in r.get('GearSetBonuses') or []:
            out.append('      %s pieces: %s  %s' % (b.get('RequiredSources'), _mods(b.get('GameplayModifiers')) or 'no effect',
                                                   '"%s"' % b['Description'] if b.get('Description') else ''))
    return out


def gear_stat_lines():
    out = []
    for sid, r in _rows('gear_stat_table').items():
        a = r.get('Attribute') or {}
        out.append('  %-44s %s.%s  "%s"' % (sid, _cls_name(a.get('AttributeOwner')) or '?', a.get('AttributeName'),
                                          r.get('DisplayName')))
    return out


def cell_effect_lines(table):
    out = []
    for cid, r in _rows(table).items():
        me = r.get('MagnitudeEffects') or {}
        bits = [str(r.get('CellType')).split('CellType_')[-1], str(r.get('CellEffectType')).split('::')[-1]]
        cls = _effect_classes(me)
        if cls:
            bits.append(cls)
        ranked = ['%s at rank %s' % (_handle_row(u.get('GrantedCellEffect')),
                                    '/'.join('%s=%s%s' % (m.get('RankRequired'), assets._fmt(m.get('GrantedMagnitude')),
                                                          ' (removed)' if m.get('bEffectIsRemovedAtThisLevel') else '')
                                             for m in u.get('MagnitudesGrantedPerLevel') or []))
                  for u in r.get('UniqueRankedEffects') or []]
        if ranked:
            bits.append('grants ' + '; '.join(ranked))
        if r.get('Description'):
            bits.append('"%s"' % r['Description'])
        out.append('  %s  %s' % (cid, '  '.join(bits)))
    return out


def cell_lines():
    out = []
    for table in ('generated_cell_catalog', 'generated_cell_catalog_override', 'obsolete_cell_catalog'):
        rows = _rows(table)
        if not rows:
            continue
        out.append('%s (%d cells)' % (table, len(rows)))
        for cid, r in rows.items():
            eff = ', '.join('%s x%s' % (_handle_row(m.get('CellEffect')), assets._fmt(m.get('Magnitude')))
                            for m in r.get('Magnitudes') or [])
            out.append('  %s  %s  "%s"  %s' % (cid, str(r.get('CellType')).split('CellType_')[-1], r.get('DisplayName'),
                                             eff))
    rows = _rows('cells_v2_catalog')
    if rows:
        out.append('cells_v2_catalog (%d test cells; unused in 2.1.1)' % len(rows))
        out += ['  %s  %s' % (cid, _mods(r.get('PassiveModifiers'))) for cid, r in rows.items()]
    return out


ARMOR_SECTIONS = (('Armor items (armour_gameplay_catalog)', armor_item_lines),
                  ('Set bonuses (gear_set_bonus_table)', set_bonus_lines),
                  ('Gear stats (gear_stat_table)', gear_stat_lines),
                  ('Perks: keyword cell effects (keyword_cell_effects; what armor and cells grant by rank)',
                   lambda: cell_effect_lines('keyword_cell_effects')),
                  ('Cell effects (cell_effect_table; the classes each effect applies)',
                   lambda: cell_effect_lines('cell_effect_table')),
                  ('Cells (cell catalogs)', cell_lines))
