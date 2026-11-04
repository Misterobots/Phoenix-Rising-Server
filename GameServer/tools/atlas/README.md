# Game logic atlas (Dauntless 2.1.1)

## Why it exists

There is no source code for the Dauntless server; this project rebuilds it in Python. The stock 2.1.1 client
still holds most of the answers: its Blueprints are readable bytecode, its C++ classes are described by the
Dumper-7 SDK, and its data tables ship in the paks. Finding a behaviour used to mean searching all of that by
hand each time, and when that took too long, guessing.

The atlas does that search once, ahead of time. It writes one file per game class that brings together what the
SDK, the Blueprint bytecode and the game data say about it, plus indexes for the questions server work keeps
asking: *who sets this property, who calls this function, what uses this table.* Facts confirmed in live tests go
in a hand-written file next to it, so they stay put when the atlas is regenerated.

**Restoring content?** Read [`GUIDE.md`](GUIDE.md): a step-by-step guide from "I want X back" to a working server
feature, with worked examples for an island hunt, a weapon special and an item.

## Quick start

Run from `GameServer/`. The generated data is already committed, so queries work without building anything.

```
python -m tools.atlas show monsterpart_bpc
python -m tools.atlas who-sets CurrentBreakStage
python -m tools.atlas who-calls InternalReviveFromBleedOut
python -m tools.atlas uses-table weapon_combat_tuning_table
python -m tools.atlas asset weapon_power_table
```

Whole systems have generated sheets: `data/atlas/behemoths/<row>.txt` (every behemoth), `data/atlas/weapons/<key>.txt`
(every weapon type, plus `shared.txt` and `armor.txt`), `data/atlas/items/` (every item catalog), `data/atlas/rewards/`
(loot, crafting, bounties, Hunt Pass, progression, and which rows grant each item) and `data/atlas/islands/` (every
island spec, hunts, escalation and game modes), with coverage tables in `behemoths.txt`, `weapons.txt`, `items.txt`,
`rewards.txt` and `islands.txt`.

## Commands

| Command | What it answers |
|---|---|
| `show <class>` | Parent chain, components, properties (R = replicated, OnRep function, server rep index), RPCs, every function with its exe address or Blueprint tags, data tables, game-data files, native notes, verified facts |
| `show <class> --inherited` | The same, plus the properties and RPCs of each parent class |
| `show <class> --fn <name>` | One function: flags, parameters, what it reads/sets/calls, authority checks, and its Blueprint code |
| `who-sets <property>` | Blueprint functions that write the property, and the classes that replicate it |
| `who-calls <function>` | Blueprint functions that call it (matches `Class.Function` or just `Function`) |
| `uses-table <table>` | Classes and functions that reference a data table, curve table or catalog table |
| `native <Class.Function>` | Address of a C++ function in the exe, plus any decompile notes |
| `verified [text]` | Hand-confirmed facts, optionally filtered by a word |
| `asset <name>` | A data asset's text view (behavior tree, montage notifies, table rows, physics bodies, class defaults), what it references and what uses it |
| `plan <hunt> [--behemoth ROW] [--weapon TYPE] [--item ID]` | Compose a hunt with selected component references; show source coverage, implementation findings, evidence boundaries, and validation still required |
| `assets <package or glob ...>` | Dump data assets into `data/atlas/assets` (see below) |
| `behemoths [--dump] [--build]` | One data sheet per behemoth in the game's roster, plus a coverage table (see below) |
| `weapons [--dump key ...]` | One data sheet per player weapon type (sword, axe, ...), one for shared weapon infrastructure and one for armor (items, perks, cells, set bonuses), plus the prefix list and a coverage table (see below) |
| `areas [--dump area ...]` | Items, shops/loot/rewards and islands: sheets per catalog group, reward kind and island spec, with every item id, class, row and behemoth they name checked, and a coverage table per area (see below) |
| `area-rules [--dump]` | What the game allows in town vs on a hunt: which quick items work in town, every Blueprint function that checks the area, menu rows with area flags (`--dump` first adds the menu tables and quick item classes) |
| `damage-windows [glob ...]` | Every damage window in every behemoth montage, with non-default settings listed first, into `data/atlas/damage_windows.json` / `.txt` |
| `build ...` | Regenerate the data (see below) |

Names are case-insensitive. For `show`, the `_C` suffix and C++ prefix (`U`, `A`) are optional. `who-*`,
`uses-table` and `native` try an exact match first, then a substring match.

### Example: compose a hunt and expose its open gaps

`plan` joins the island contract, the behemoth sheet, and optional weapon/item readiness rows. A selected behemoth
inside the island's authored candidate pool is identified as such; an outside selection is shown as a custom
combination with compatibility unknown. The command never treats static data coverage as implementation or live
acceptance. Known Phoenix failures and corrections that still need validation are listed from
`topics/weapon_item_server_readiness.json`.

```text
python -m tools.atlas plan Adventure_IslandP_D24 --behemoth sally_normal --weapon repeaters --item QI_FROSTBITE_PYLON
```

Use `python -m tools.atlas plan --help` for options. This is an implementation-planning view over the current
reference data, not a server template generator; unsupported or unrecorded cross-component compatibility stays
explicitly unknown.

### Example: tracking down a behaviour

How does a part sever replicate, and what spawns the piece?

```
python -m tools.atlas who-sets PartSevered
#   monsterpart_bpc_C (replicated, OnRep OnRep_PartSevered)
#   monsterpart_bpc_C.AuthHandleBreak
python -m tools.atlas show monsterpart_bpc --fn AuthHandleBreak     # tags + code: IsServer check, calls
python -m tools.atlas show monsterpart_bpc --fn OnRep_PartSevered   # what the client does when it arrives
python -m tools.atlas verified sever                                 # what has already been confirmed live
```

## Where each answer comes from

| Layer | Contents | Source | How much to trust it |
|---|---|---|---|
| 1 Structure | parents, components, properties (type, offset, Net, RepNotify + OnRep function), RPCs with params | SDK `Dumpspace/*.json` and `CppSDK/SDK/*_classes.hpp`; Blueprint property lists; `data/2.1.1/layouts.jsonl` (`schema_ri` = the rep index the server's replication schema uses) | read from the game |
| 2 Blueprint logic | every Blueprint function as readable code, tagged with properties read/set, functions called, authority checks, tables used | AssetDump of the cooked paks, rendered with `kismet_pseudo.py` | read from the game |
| 3 Native code | `Class.Function` -> address of its native thunk (`rva`, offset from image base 0x140000000) | SDK `FunctionsInfo.json` | the address is read from the game; what the code *does* goes in `native_notes.json` once decompiled |
| 4b Data assets | behavior trees, montages and their notifies, data tables, physics assets, blackboards, enums, island specs, content assets, and the class defaults of Blueprints | AssetDump of the cooked paks, cleaned up (`assets.py`) | read from the game |
| 4 Game data | data tables, curve tables and PlayFab catalog tables a class references; files in `data/2.1.1` that name it | bytecode object constants and class defaults; text search over `data/2.1.1` | the `data/2.1.1` links are name matches only |
| 5 Verified | facts confirmed live or read directly, with source and date | `data/atlas/verified.json` (hand-written) | confirmed |

Limits to keep in mind:
- The SDK does not record which function a C++ property's RepNotify calls. For native classes, `OnRep=` is the
  `OnRep_<Prop>` function if one exists, and the JSON labels it as a name match. Blueprint classes record
  the real `RepNotifyFunc`.
- `who-sets` sees Blueprint writes and replication only. If a property is written only in C++, it shows no
  setter; decompile the native functions (layer 3) to find it.
- `SwitchHasAuthority` is a Blueprint macro. In bytecode it becomes `HasAuthority`/`GetLocalRole`, and those
  are what `auth=` shows.
- In Blueprint code, `[n]` is the statement's byte offset and `@n` a jump target. The code is transcribed
  token by token, not decompiled into if/else blocks.
- `build --all-sdk` covers the Blueprints that were loaded when the SDK was dumped. To add any other
  Blueprint, give its class name to `build`, which finds its package in the paks.

## Files

Tool (`GameServer/tools/atlas/`):

| File | Role |
|---|---|
| `__main__.py` | The command line: argument parsing and every query command |
| `build.py` | Generation: merges the layers into one record per class, writes class files, Blueprint code and indexes |
| `sdk.py` | Reads the Dumper-7 SDK: parent chains and functions from Dumpspace, property flags from the C++ headers |
| `blueprints.py` | Finds and dumps Blueprint packages with AssetDump, renders bytecode, tags each function |
| `assets.py` | Dumps data assets and Blueprint class defaults, cleans them up, renders the text views, writes `index_assets.json` |
| `weapons.py` | The `weapons` command: finds each weapon type's (and armor's) packages, dumps them and their code, writes the sheets |
| `proposals/` | Generators for our own proposed data (not from the game), e.g. `beaver_frost_damage.py` |
| `areas.py` | The `areas` command: dumps items, rewards and islands (reusing `weapons.dump`), follows the tables, classes and assets their rows name, writes the sheets |
| `area_rules.py` | The `area-rules` command: reads quick item settings, area checks in the code and menu flags into `area_rules.txt` |
| `behemoths.py` | The `behemoths` command: dumps every behemoth data package, builds their Blueprint code, writes the sheets |
| `windows.py` | The `damage-windows` survey: finds every AnimMontage under `Archon/Content/Monsters` and collects its damage windows |
| `paths.py` | Every input and output location, with environment overrides |
| `GUIDE.md` | How to use the atlas to bring content back on the server, with worked examples |
| `starter.txt` | The ~80 classes the server works with now (behemoths, player, inventory, weapons, armor); plain `build` regenerates these |

Data (`GameServer/data/atlas/`; see the README there for the JSON fields):

| Path | Contents | Regenerated? |
|---|---|---|
| `classes/<Class>.json` | One record per class with layers 1-4 | yes |
| `pseudo/<Class>.txt` | Readable Blueprint code for that class | yes |
| `index_*.json` | Cross-reference indexes: classes, who_sets, who_calls, uses_table, native | yes, over every class file present |
| `assets/<name>.json` / `.txt` | One data asset (all exports, cleaned up) and its text view | yes, by `assets` |
| `index_assets.json` | asset -> type, package, assets it references, assets and classes that use it | yes, over every asset file present |
| `behemoths/<row>.txt`, `behemoths.json` / `.txt` | One sheet per roster row; the roster with links and what is missing | yes, by `behemoths` |
| `weapons/<key>.txt`, `weapons.json` / `.txt` | One sheet per weapon type, `shared.txt` (shared weapon infrastructure) and `armor.txt` (armor, perks, cells, set bonuses); prefixes and coverage table | yes, by `weapons` |
| `proposals/` | Our proposed data, clearly marked as not from the game (e.g. frost Gnasher damage rows) | yes, by its generator in `tools/atlas/proposals/` |
| `items/`, `rewards/`, `islands/`, `items.txt` / `rewards.txt` / `islands.txt`, `areas.json` | Area sheets (tables row by row, asset trees, classes), coverage tables, counts and unresolved names per area | yes, by `areas` |
| `area_rules.txt` / `.json` | What is allowed where (town vs hunt) | yes, by `area-rules` |
| `damage_windows.json` / `.txt` | Every behemoth damage window: start, duration, keys, bodies per role, requirements, non-default settings | yes, by `damage-windows` |
| `verified.json` | Layer 5 facts | never; hand-written |
| `native_notes.json` | Decompile notes per native function | never; hand-written |
| `topics/<system>.md` | Write-ups of whole systems (e.g. `armor.md`: resistances and the damage formula) | never; hand-written |

`GameServer/.atlas_cache/` holds AssetDump output, the package list and the parsed SDK headers. It is
git-ignored and rebuilt when needed.

## Regenerating

```
python -m tools.atlas build                        # the starter classes in starter.txt
python -m tools.atlas build lerawr_alpha_bp_C Foo  # add or refresh specific classes
python -m tools.atlas build --all-sdk              # every class in the SDK dump (~7,500; about 5 min)
python -m tools.atlas build --refresh              # re-list and re-dump Blueprint packages (after a paks change)
```

To regenerate the full committed set, run `build --all-sdk` and then plain `build`. The second run adds the
starter Blueprints that are missing from the SDK dump, such as the lerawr ones. Indexes always cover every
class file in `data/atlas/classes`, so separate builds add up.

Data assets are added by package name or glob over the pak paths. Globs skip render-only folders (anims,
materials, textures, meshes) and types (meshes, textures, materials, skeletons); name one exactly to force it.
Blueprint packages give their class defaults here; use `build` for their code.

```
python -m tools.atlas assets 'Archon/Content/Monsters/aq/lerawr/*'     # one behemoth's trees, montages, tables, defaults
python -m tools.atlas assets 'Archon/Content/Monsters/_shared/ai/*'   # shared behavior trees and their node classes
python -m tools.atlas asset lerawr_bt
```

`damage-windows` reads montages without writing an asset file for each. The montage list comes from
AssetDump's class listing of `Archon/Content/Monsters` (cached in `.atlas_cache/classes_monsters.tsv`; montage
names vary), so `--refresh` re-lists and re-dumps. A glob limits it to some folders, and the output then
covers only those, so run it without one before committing:

```
python -m tools.atlas damage-windows                                    # all behemoths (seconds; the first dump takes ~1 min)
python -m tools.atlas damage-windows 'Archon/Content/Monsters/aq/*'     # one family
```

Requirements: the SDK (`Mystic-Paradox/sdk211/5.1.1-682875+__phx-archon_release_2.1.1-Archon`), a built
AssetDump (`Mystic-Paradox/tools/AssetDump`, which reads the paks from its configured `PAK_DIR`), and `dotnet`.
Locations can be overridden:

| Variable | Default |
|---|---|
| `ATLAS_SDK` | `<workspace>/Mystic-Paradox/sdk211/5.1.1-682875+__phx-archon_release_2.1.1-Archon` |
| `ATLAS_ASSETDUMP` | `<workspace>/Mystic-Paradox/tools/AssetDump` |
| `ATLAS_OUT` | `GameServer/data/atlas` |
| `ATLAS_CACHE` | `GameServer/.atlas_cache` |

Every behemoth at once:

```
python -m tools.atlas damage-windows                   # first: the sheets read damage_windows.json
python -m tools.atlas behemoths --dump --build         # dump ~4,500 packages (~3 min), build ~1,900 classes (~1.5 min), sheets
python -m tools.atlas behemoths                        # sheets only, from what is already in data/atlas
```

`--dump` takes every package under `Archon/Content/Monsters` and `Archon/Content/Character/Monsters` whose type
is not render-only (from AssetDump's class listing, cached in `.atlas_cache/classes_*.tsv`), plus the roster
tables and `DamageTable_Generic`. It then follows the Blueprint classes that data names (in exports, and in
built code) and their parents, three steps out, skipping UI, cinematics, camera, atmosphere and audio folders: about
350 shared area effects, projectiles, beams, buffs and minions (listed in `.atlas_cache/behemoth_referenced_packages.txt`,
which `--build` also builds). The `HitVolumes*` editor captures are left out (220 tables, 42 MB); dump one with
`assets` when needed.
Eight package names occur in two folders; the asset file holds whichever was written last.

Weapons, one type at a time (`shared` first: the base weapon actor, part and legendary base classes, ammo and
meter components, player damage notifies, `DamageTable`, the power, part and talent tables):

```
python -m tools.atlas weapons --dump shared sword      # ~2 min per type the first time; then sheets
python -m tools.atlas weapons --dump armor            # armor, perks/cells, set bonuses (~3 min)
python -m tools.atlas weapons                          # sheets only
```

A type's packages are its own folders (`blueprints/Weapons/_shared/<x>`, `game/abilities/weapons/<x>`, the
montages in `Character/Player/Anims/<x>`), every logic package whose name starts with one of its prefixes (skin
weapon actors in `blueprints/Weapons/<skin>/`, conditions, notifies, weapon modifier buffs, its gameplay and
cosmetic catalogs), the classes its catalog, part and power-table rows name, and the classes all of that names,
two steps out. Classes already listed under `shared` are left out of the type sheets. When two of a type's
packages share a name (the sword's pre-rework montages beside the `Rework` ones), the one in a `Rework` folder
keeps the name and the other is filed as `<name>~<folder>` (`h_mr_eb_melee_X1_fwd_01_mot~EmberBlade`).

Items, rewards and islands:

```
python -m tools.atlas areas --dump items rewards islands   # ~8 min the first time; then sheets
python -m tools.atlas areas                                # sheets only
```

`items` takes every backend catalog (`Gameplay/Backend`), lanterns, inventory items, consumables and boosts;
`rewards` the bounty, experience, mastery, player journey, progression, Hunt Pass, achievement and VIP folders;
`islands` all of `gamemodes/` (island specs, content assets, escalation, portals, events, encounters), `Gameplay/hunts`
(modifiers, atmosphere and behemoth lists, matchmaker hunts, schedules) and the gauntlet objects. After the dump it
follows the tables, classes and assets their rows name (three steps). The 418 flare colour variants are left out
(marked `~`, like other looks-only paths) until `area-rules --dump` adds their class defaults. Each table goes to the first sheet whose name pattern matches
(`areas.SHEETS`), so loot, crafting and unlock catalogs land in `rewards/` whatever folder they come from.

## Adding knowledge

- **Confirmed a behaviour?** Add an entry to `data/atlas/verified.json` with `id`, `fact`,
  `classes`/`props`/`functions`, `source` (commit, capture or file) and `date`. `show`, `who-sets` and
  `verified` print it wherever one of its names comes up.
- **Decompiled a native function?** Add `"Class.Function": {"summary", "details", "source", "date"}` to
  `data/atlas/native_notes.json`. `show` and `native` print it.
- **Mapped a whole system?** Write it up in `data/atlas/topics/<system>.md`. Put the per-function details in
  `native_notes.json` and the confirmed facts in `verified.json`, so the CLI can still find them.
- **Mapped a behemoth?** Run `assets` on its folder and `build` on the classes it lists, then write it up in
  `topics/` like `topics/behemoth_design.md`.
- **Mapped a weapon or armor system?** The data is in `weapons/<key>.txt`; refresh it with `weapons --dump <key>`
  and write the behaviour up in `topics/` (armor already has `topics/armor.md`).
- **The server starts using a new class?** Add it to `starter.txt` and run `build`.
