# Candidate topics for the atlas

Systems that could get the same treatment as `topics/armor.md` and `topics/behemoth_scaling.md`: read the data
tables, Blueprint bytecode and native code, then write down the formulas, the tables and what the server has to
do. Surveyed 2026-10-27.

Counts come from the cooked paks (`.atlas_cache/packages.txt`, 61,182 packages) and the table export
(`debug/exports/all-datatables-211`, 981 tables). "In atlas" means Blueprint classes already in `classes/`.

## Status (2026-10-28)

The two tool gaps first listed here are closed: `python -m tools.atlas assets` dumps data assets and class
defaults, and the generated sheets read table rows and follow row handles. Data is now captured for every
behemoth (`behemoths/`), every weapon type and all armor, perks, cells and set bonuses (`weapons/`), every item catalog
(`items/`), the loot, crafting, bounty, Hunt Pass and progression tables (`rewards/`) and every island spec and game
mode (`islands/`). What is left
below is mostly the written explanation (`topics/`) of how each system behaves, plus the systems not dumped yet.

## Candidates, most useful first

### 1. Behemoth design (moves, damage, hitboxes, AI)

The server has to run behemoth AI, and no server code for it exists. This is also the data the planned scenario
builder needs: per-attack hit volumes and damage windows.

| Data | Count | What it holds |
|---|---|---|
| Action set tables (`Struct_ActionSet`) | 89 tables, 1,370 rows | per move: attack Blueprint, montage, play rate, cooldown and variance, stamina drain, usage count, shared cooldowns, sub-tree |
| Damage tables (`DamageTableData`) | 96 tables, 2,532 rows | per hit: physical and element damage, true and raw damage, explicit part break/stagger/expose, damage type, stagger tags |
| Hit volumes (`debug_capsuleinfo` / `boxinfo` / `sphereinfo`) | 246 tables, about 104k rows | per attack: shapes with position, rotation and size (`HitData/HitVolumes<attack>_*`) |
| Behavior trees | 221 packages | selectors, sequences, cooldown and blackboard decorators, `BTTask_BehemothAction`, shared sub-trees (stagger, patrol, deaggro, death) |
| Attack Blueprints (`*_atk`) | 404 | per-move logic (bytecode) |
| Projectiles and zones | about 578 | e.g. `lerawr_fireblast_projectile_bp`, `Lerawr_Firezone_Actor` |
| Stagger tables, part collision, severable parts | 6 + 12 + 22 | stagger animations and launch values, bones that ignore pawns, sever actors |
| Part-break loot (`breakitem_data`, `conditional_break_data`) | 26 tables | items per broken part, with quest and state conditions |

In atlas: the base classes (`Bp_Behemoth`, `BP_BehemothAction`, `Bp_BehemothActionSet`, `AiCont_Behemoth`, BT
decorators) and Embermane's `lerawr_archetype_bp` / `normal` / `heroic`. Nothing per move.
**Done for Embermane** in `topics/behemoth_design.md`. **Data captured for every behemoth** (2026-10-27):
`python -m tools.atlas behemoths` writes one sheet per roster row in `behemoths/` from the dumped tables,
trees, montages, physics assets and class defaults; `behemoths.txt` is the coverage table. Written topics
beyond Embermane are still to do. The hit volume tables turned out to be editor captures, not
runtime data; the runtime hitboxes are the physics asset bodies named in each montage's damage windows.
Original suggestion: Embermane (`Monsters/aq/lerawr`) end to end. Join its 9 actions, 15 damage rows, hit
volumes and `lerawr_bt` into one write-up, then generate the same for the other behemoths.

### 2. Island design (exploration, content, events)

The island director chooses what spawns where. The server has to do the same for any island beyond the fixed
contract in `data/2.1.1/islands`.

| Data | Count | What it holds |
|---|---|---|
| `exploration_island_spec_table` (`IslandSpecTable`) | 2 tables, 101 rows | island row -> spec asset, stars, shown in map |
| Island spec assets | 100 | spec tags, `MaxActivePrimaryContents`, `MaxBehemothPower`, keystone behemoth, content pools of choosers (behemoth row, level, weight, tuning preset row) |
| Content assets (`GameContentDataAsset`) | 107 | public events and portals: chooser trees, stages, loot rows, call-to-action actors |
| Portal and event tables | `PortalContentSpecTable`, portal pools, `PublicEventLootData` (85), `IslandMetaEventLootData` (16) | portal keys -> content and sub-level; event loot |
| Natives | `IslandDirectorComponent`, `IslandContentLocationManagerComponent`, 15 `Encounter*` classes, `PhxDangerMeterGameContent` | the selection code itself (needs decompiling) |

In atlas: `BPGM_ArchonExploration`, `exploration_game_mode_bpc`, `bp_gamemode_behemoth_spawner_component`, the
patrol points. Not in atlas: the chooser Blueprints (`specific_behemoth_set_chooser`, `danger_meter_chooser`,
`public_event_chooser`, …) and every spec asset.

**Data captured** (2026-10-28) by `python -m tools.atlas areas`: every island spec asset with its chooser tree,
content asset, spec and matchmaker table, linked to the behemoth sheets and the server's island contracts
(`islands/`). Still to do: a topic on how the island director picks content (the native selection code).

### 3. Cells and perks (what each effect does)

**Data captured** (2026-10-28) in `weapons/armor.txt`: the cell catalogs, `cell_effect_table` (344 effects with
the buffs, effects and abilities each applies), `keyword_cell_effects` (117 perks with their rank grants) and the
516 cell content packages, with code, and the Cells v2 tables (`cell_output_table`, state-based and triggered
input), which only the four test cells in `cells_v2_catalog` use. Still to do: a topic explaining what each effect
does.

### 3b. Weapons (moves, parts, abilities)

**Data captured** (2026-10-28) in `weapons/<key>.txt` for every type: items, parts, power-table rows, actors,
abilities, effects, projectiles, montage damage windows and damage rows. Still to do: topics like
`behemoth_design.md` for each weapon's combo flow (anim state sets, input buffering, meters and charge levels).

### 4. Buffs and status effects (elements, DoTs, debuffs)

Blaze, frost, shock and the other elemental statuses, behemoth debuffs (`behemoth_onfire_debuff`,
`behemoth_frost_debuff`, `behemoth_overload_buff`) and the shared buff base classes. `_shared/blueprints/buffs/`
has 981 packages (128 in atlas), and there are about 701 `*_ge` effects. Combat needs these for every elemental
hit. They overlap with cells (3) and hunt modifiers (5). Dumped so far: the cell and set-bonus buffs (358
classes), the weapon buffs (`weapons/`) and the behemoth debuffs the behemoth data reaches (`behemoth_onfire_debuff`,
`behemoth_frost_debuff`, `behemoth_overload_buff`, ...). The rest of `_shared/blueprints/buffs/` and a topic are
still to do.

### 5. Hunt modifiers (mutations, danger zones)

`hunt_modifiers` (188 rows: threat level, complexity, tags, text) -> 161 `Hunt_Modifier` assets -> behemoth buff
Blueprints (`_shared/blueprints/buffs/HuntModifiers/`), with pattern tables such as `fireballswalls_mod_patterns`.
They are small and self-contained, and backup encounters and escalation rounds use them.

**Data captured** (2026-10-28): all 161 `Hunt_Modifier` assets with their buffs in `islands/hunt_modifiers.txt`, the
buff classes with code, and the `hunt_modifiers` table. Still to do: a topic.

### 6. Game modes: escalation, gauntlet, trials, scheduled hunts

| Data | Count |
|---|---|
| Escalation tables (talents, buffs, progression tuning) | 37 tables (`EscalationTalentDefinition` 111 rows, `EscalationBuffDefinition` 144) |
| Gauntlet round, tuning and season specs | 5 + 5 + 1 tables |
| Backup encounters (`BackupEncounterTableData`) | 18 tables, 3,381 rows: round -> behemoths and modifiers with generator levels |
| Matchmaker hunts | 5 tables, 514 rows (already used for threat level and power override) |
| Schedules (`ScheduleData`) | 4 tables, 201 rows |
| Natives | `EscalationGameModeComponent`, `GauntletGameModeComponent` and about 85 related classes |

**Data captured** (2026-10-28): escalation tables, backup encounters, gauntlet objects, portals, events and
schedules in `islands/escalation.txt`, `islands/modes.txt` and `islands/hunts.txt`. The native mode components are
not decompiled.

### 7. Loot and rewards

PlayFab drop tables (24 tables, 2,165 rows), the item catalogs (generic 65 tables, plus weapon, armor, lantern,
part and quick-item catalogs), bounties (37 tables, 10,367 rows), `loot_dropper_bpc` and the native
`ArchonLootDropper`. Our backend has to hand these out. The weapon, weapon part,
weapon talent and armor catalogs are dumped (`weapons/`); drop tables, bounties and the other catalogs are not.

**Data captured** (2026-10-28): every backend catalog, drop table, bundle, container and break reward in
`rewards/drops.txt` and `items/`; bounties in `rewards/bounties.txt`; `rewards/item_sources.txt` lists the rows that
grant each item. Still to do: how `ArchonLootDropper` rolls them.

### 8. Lanterns and consumables

Lantern catalog (139 rows), quick-item catalog (479), `Ability_QuickItem_*` (potions, flares, supply drop,
revive talisman), grenades, lantern Blueprints and `ArchonLanternAttributeSet`. `verified.json` has
`potion-heal`; the rest is open.

**Data captured** (2026-10-28): lantern and quick-item catalogs, their Blueprints and abilities
(`items/lanterns.txt`, `items/quick_items.txt`, `items/classes.txt`). The 418 flare colour variants are not dumped.

### 9. Wildlife, creatures and gatherables

`creature_metadata_table` (35), `Content/Fauna` (278 packages, 28 in atlas), `Content/creatures` (762, 19 in atlas),
gatherables and harvest nodes (about 457). These are needed once islands spawn more than behemoths.

**Partly captured** (2026-10-28): `creature_metadata_table` and the wildlife classes the island and spawn data name
(`af_*` boars, slimes, ...) came in with `areas`; the rest of `Content/Fauna` and `creatures` is not dumped.

### 10. Player progression

Experience tracks, mastery, player journey (378 nodes), unlock criteria (112). `data/2.1.1` already has
`progression.json`, `quests.json` and `slayers_path.json`, so this is mostly linking what exists. `player_journey_table`, the unlock-criteria tables and the weapon
experience and prestige tracks are now dumped in `assets/`.

**Data captured** (2026-10-28) in `rewards/progression.txt`, `rewards/hunt_pass.txt` and `rewards/crafting.txt`.

Left out: UI, cosmetics, audio and credits. They have plenty of tables but nothing for the server to rebuild.
