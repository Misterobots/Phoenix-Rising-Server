# Dauntless 2.1.1 Atlas

One reference to the game logic of the final Dauntless PC client (**2.1.1**), written for people rebuilding its
server: small teams, hobbyists, anyone who knows the game but not its insides.

The official Dauntless servers were switched off, and their code was never released. The game that players install
still contains most of the rules and numbers those servers used. The atlas reads them out of the game files once
and writes them down as plain text and JSON, so nobody has to search the game files again every time they start
work.

**New here? Start with [GUIDE.md](GameServer/tools/atlas/GUIDE.md):** a plain-language walk-through of using the atlas
to bring content back on a server, with worked examples.

## What it covers

| Area | What you get |
|---|---|
| **Behemoths** | One sheet per behemoth in the game's roster (129): attacks, when each one can hit and for how much, what it spawns, how its AI picks moves, its hitboxes |
| **Weapons** | One sheet per weapon type: sword, axe, war hammer, chain blades, war pike, repeaters, aether strikers, and the unreleased bow. Each covers items, parts and specials, power and element, abilities and cooldowns, every attack's timing and damage. A shared sheet covers what all weapons use |
| **Armor** | Every armor item with its perks per level, set bonuses (only 5 of the 27 sets grant anything), gear stats, perks and cells and what each one does |
| **Items** | Every item catalog: lanterns, potions and other quick items, grenades, cells, materials, currencies, tokens and keys, cosmetics |
| **Shops, loot and rewards** | Drop tables, bundles and containers, crafting recipes and unlocks, bounties and season challenges, the Hunt Pass, progression, and for every item, each place it comes from |
| **Islands** | One sheet per island (100), from its limits and kill rewards down to the behemoths it can spawn; hunts, escalation, gauntlet, portals, events, hunt modifiers |
| **Area rules** | What works in town versus on a hunt: which quick items can be used in Ramsgate, every piece of game code that checks the area, the menu entries switched off during hunts |

Behind the sheets: 12,822 game classes (structure, values sent to players, server-only logic, and readable code
for 4,156 of them), 10,601 data assets and tables, cross-reference indexes, 45 confirmed facts and 42 notes on the
game's built-in code. When the current extraction finds a reference with no corresponding game row or asset, the
atlas marks it. That records a discovered source-data gap; it does not prove there are no other gaps in the atlas,
or that a Phoenix implementation is complete or validated.

## How it is organised

Everything is in [`GameServer/data/atlas/`](GameServer/data/atlas/README.md):

```
behemoths/        one sheet per behemoth                 behemoths.txt   coverage and missing rows
weapons/          one sheet per weapon type, shared.txt, armor.txt       weapons.txt     prefixes and coverage
items/            item catalogs by kind                  items.txt       coverage
rewards/          loot, crafting, bounties, Hunt Pass, progression, item_sources.txt
                                                         rewards.txt     coverage
islands/          hunts, escalation, modes; specs/<island>.txt per island
                                                         islands.txt     coverage
area_rules.txt    town vs hunt
topics/           whole systems explained: armor and damage, behemoth design, behemoth scaling, area rules,
                  and what the server does for the repeaters (with a checklist for every weapon type)
proposals/        our own filled-in values for data the game lacks, clearly marked as not from the game
classes/, pseudo/ one record per game class, and its readable code
assets/           every data asset and table, as JSON (and text for the readable kinds)
index_*.json      who sets what, who calls what, what uses which table
verified.json     facts confirmed by reading the code or by testing in game
```

The file formats are described in [`GameServer/data/atlas/README.md`](GameServer/data/atlas/README.md).

## How to use it

Most of the time you just open the text sheets. For quick lookups there is a command line, run from the
`GameServer` folder with Python 3.11 or newer. Nothing needs building first:

```
python -m tools.atlas show monsterpart_bpc         # one game class: values, server-only logic, readable code
python -m tools.atlas asset weapon_power_table     # one table or asset, and what refers to it
python -m tools.atlas who-sets CurrentBreakStage   # everything that changes a value
python -m tools.atlas verified potion              # confirmed facts
python -m tools.atlas plan Adventure_IslandP_D24 --behemoth sally_normal --weapon repeaters --item QI_FROSTBITE_PYLON
python -m tools.atlas --help                       # every command
```

`plan` composes an island with selected encounter, weapon and quick-item references, and reports recorded
implementation gaps and validation still required. It keeps authored data, Phoenix runtime status and live-client
acceptance separate; a missing finding is not a pass.

### Encounter simulations

The atlas also composes playable encounters. `simulation template`, `acquire`, `stage` and `apply` turn an island, a
behemoth and its behavior tree into a checked bundle for the server; the Phoenix tracer then runs that encounter offline
against a scripted player (`melee` or `kite`) before anyone plays it. Current state:

| Hunt | In game | Offline | Live (stock client) |
|---|---|---|---|
| `Adventure_IslandA_D24` | Ramsgate Outskirts > Emberthorn Cove, Lesser Embermane (`lerawr_beta`) | complete: no unhandled nodes, 32 attacks in 180 s | aggro, attacks and hits accepted |
| `Adventure_IslandP_D24` | Hades Reach (`sally_normal`) | complete: no unhandled nodes, 57 attacks in 360 s | attacks; roar takeover, Frostbite Pylon and weapon swap still to confirm |

Before any live test, fill in the [pre-fight checklist](GameServer/tools/atlas/PREFIGHT_CHECKLIST.md): it takes
the composition from `plan`, verifies the server config, the character's weapon, level and quick-item loadout, and what
the client actually loaded, and marks each test item testable or not. Filled copies are kept in
[`simulations/prefight/`](GameServer/data/atlas/simulations/prefight/).

Known gaps shared by both: no rock or static-mesh collision on the server, so line of sight is assumed clear and a
player standing on a rock can be attacked from beneath. Every claim is recorded with its evidence level in
[`evidence_gap_model.json`](GameServer/data/atlas/evidence_gap_model.json); commands and results are in
[simulations/README.md](GameServer/data/atlas/simulations/README.md).

- [GUIDE.md](GameServer/tools/atlas/GUIDE.md): how to use the atlas to bring content back, step by step
- [tools/atlas/README.md](GameServer/tools/atlas/README.md): every command, every file, and how each part is generated

## Regenerating it

The generated reference corpus is committed, so most people can query it without rebuilding. Its coverage tables
report what the extractors found and which references they could not resolve; they are not a guarantee that every
relevant dependency or behavior has been identified. Phoenix implementation and stock-client validation status
are tracked separately. To rebuild or extend the generated corpus you need:

- your own **Dauntless 2.1.1** install (the game's `.pak` files)
- the **2.1.1 SDK dump** (Dumper-7), default `<workspace>/Mystic-Paradox/sdk211/5.1.1-682875+__phx-archon_release_2.1.1-Archon`
- a built **AssetDump** with its `PAK_DIR` pointing at the game's paks, default `<workspace>/Mystic-Paradox/tools/AssetDump`
- **.NET** (`dotnet`) to run AssetDump, and **Python 3.11+**

`<workspace>` is two folders above this repository. Each location can be overridden with `ATLAS_SDK`,
`ATLAS_ASSETDUMP`, `ATLAS_OUT` (default `GameServer/data/atlas`) and `ATLAS_CACHE` (default
`GameServer/.atlas_cache`, kept out of git). Then, from `GameServer`:

```
python -m tools.atlas build --all-sdk && python -m tools.atlas build      # classes and their code
python -m tools.atlas damage-windows                                      # behemoth attack timing
python -m tools.atlas behemoths --dump --build                            # behemoth sheets
python -m tools.atlas weapons --dump shared sword axe hammer chainblades warpike repeaters strikers bow armor
python -m tools.atlas areas --dump items rewards islands                  # items, rewards, islands
python -m tools.atlas area-rules --dump                                   # town vs hunt
```

The first full run takes about half an hour; later runs reuse the cache. Details and options:
[tools/atlas/README.md](GameServer/tools/atlas/README.md#regenerating).

## The atlas and the server

The server itself lives on the **`main`** branch; this **`Atlas`** branch holds the reference. They are kept apart on
purpose:

- **You don't need the atlas to run the server.** The server reads its own small copy of the game data it needs, in
  `GameServer/data/2.1.1` on `main` (`class_layouts.json` and the `talents/` tables).
- **One place connects them:** `GameServer/tools/export_runtime_data.py` on `main` writes that copy from the atlas
  (`python -m tools.export_runtime_data <atlas dir>`). Run it again when the atlas changes in a way the server needs.
- **The atlas is where server work starts.** It tells you exactly what the game expects, so a feature can be
  rebuilt from the game's own data instead of guessed. GUIDE.md shows how, and
  [topics/repeaters_server.md](GameServer/data/atlas/topics/repeaters_server.md) records everything the server had
  to do for the first weapon brought back, with the checklist for the next ones.
- This branch was cut from an earlier `main`, so the server files here are out of date. For the current server,
  use `main`.
