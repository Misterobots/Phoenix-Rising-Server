# Engine and behemoth layer port plan (Mystic-Paradox → Phoenix) — 2026-10-19

Baseline commit: `31c0087` on `game-data` (35 tests pass).

## Why

Phoenix's `mpgs/island_encounter.py` drives the Lesser Embermane as a puppet: a fixed
ground Z, a circular patrol, a 420-unit "melee stop", mode toggles to stop the client
correcting terrain, `DisableIkinema = 1`, and no attacks. Every live symptom
(posture, floating, snapping, never attacking) comes from that. Mystic-Paradox hit the same
list with the Rock on 10-14/15 and answered it with shared layers
(`docs/BEHEMOTH_RUNTIME_PLAN_2026-10-15.md`, `docs/ENGINE_LAYER_PLAN_2026-10-16.md`).
Phoenix rebuilt the encounter without those layers.

Rule carried over: **a new combat or behemoth blocker goes into a shared layer, never into
`island_encounter.py`.** When the port is done, `island_encounter.py` only picks the species
and spawn point, and handles rewards.

## What exists and its state

| Mystic module | Role | Generic or Rock-only | Port |
|---|---|---|---|
| `repl/*` | wire codec | identical except `structs.py` (Phoenix is newer: `RepMovement_Behemoth`); `rpc.py`, `schema.py`, `views.py` differ by 2–5 lines | merge Mystic's root-motion structs into Phoenix's `structs.py`, keep Phoenix's version of everything else |
| `world/terrain.py` + `tools/export_terrain.py` | ground height + walkable quads | generic; data is per map | port; export `Adventure_IslandA_D24` |
| `behemoth/spawn.py` | open bunch, movement, montage, stagger entries | mostly generic (`root_motion_values`, `movement_entry`, `montage_play_entry`) | port as is; replaces the hand-built `_entry/_block` code in `island_encounter.start` |
| `behemoth/mover.py` | authoritative mover, root motion from montage curves, ground clamp | generic | port; replaces `step_movement` |
| `behemoth/bt.py` | cooked behaviour-tree interpreter | generic | port |
| `behemoth/brain.py` | action scoring, cooldowns, timelines | generic code; `load_rock` is Rock-only | port, add `load_species(name)` |
| `behemoth/geometry.py` | hit-area tests | generic | port |
| `behemoth/damage.py` | hit decoding, damage curves, health | `RockHealth` named for Rock, model is generic | port, rename to `BehemothHealth`; replaces `handle_damage`'s health math |
| `behemoth/bp_schema.py` | Blueprint class chain into Schema | Rock-only path | merge with Phoenix's `island_encounter.install_schema` (already per-species) |
| `behemoth/rock_ai.py`, `session.py` (`RockDirector`) | glue: BT decides, director acts | Rock-specific | generalise into `behemoth/director.py` (per-actor, not per-session) |
| `engine/abilities.py`, `attributes.py` | E3 GAS, attribute replication | generic | port; needed for the health bar and player damage |
| `engine/weapon_meter.py`, `gameplay_state.py` | sword meter, island hunt substate | generic | port when E3 is wired |

Data gap: only `rock.json`, `rock_schema.json` and `damage_model.json` were exported.
Embermane (`lerawr_beta`) needs its action set, behaviour tree, montage root-motion curves
and Blueprint schema. Island `Adventure_IslandA_D24` needs terrain.

## Steps (in order; tests after each)

Ground rules from the Mystic plan still apply: stock client only, the user does menu clicks,
new behaviour behind an env flag (`MPGS_BEHEMOTH_ENGINE=1`) with the current puppet as default
until a live run confirms it, generated game data stays out of git (`GameServer/world/` is
ignored), never invent values: write open questions down.

1. **Wire codec merge.** Bring Mystic's `RepRootMotion` / `FRootMotionSourceGroup` and
   `ReplicatedServerLastTransformUpdateTimeStamp` structs into Phoenix `repl/structs.py`. Port
   Mystic's struct round-trip tests.
   *Done when* both repos' struct tests pass in Phoenix and the 35 existing tests still pass.
2. **Port the generic modules unchanged** into `GameServer/mpgs/behemoth/` and
   `GameServer/mpgs/engine/`, plus `mpgs/world/terrain.py` and the exporters into
   `GameServer/tools/`. Port their offline tests (`test_behemoth_session.py`, `test_damage.py`,
   the bt/mover tests) and run them against Rock data first, with the paths repointed.
   *Done when* the ported tests pass in Phoenix with nothing wired into a session.
3. **Export Embermane and island data.** Run `export_behemoth_all` (filtered to `lerawr`),
   `export_behemoth_schema`, the montage root-motion export, and `export_terrain` for
   `Adventure_IslandA_D24`. Record the hashes in the island contract.
   *Done when* `world/behemoths/lerawr_beta.json` has actions with montages, the BT parses with
   `bt.TreeSet`, and terrain gives a ground Z at `BP_PatrolCenterPoint_263` that matches the
   aether-vent Z (18.33) to within a few units.
4. **Generalise the director.** Turn `RockDirector`/`RockAI` into `behemoth/director.py`: one
   director per spawned behemoth, given species data, terrain and a target provider. Add
   `brain.load_species`. Keep a Rock regression test.
   *Done when* an offline tick loop drives the Embermane through its BT to patrol, aggro and pick an
   attack, and emits movement with root motion and montage entries that decode bit-exactly.
5. **Rewire `island_encounter.py`** behind `MPGS_BEHEMOTH_ENGINE=1`: `start` → `spawn.spawn_entry`
   + director; `step_movement` → `director.tick`; `handle_damage` → `damage` + `BehemothHealth`
   (via engine attributes once step 6 lands); `reveal` becomes a BT/aggro event. Remove
   `DisableIkinema`, the fixed Z, the circle patrol and the mode-0 workaround on that path. Rewards,
   XP and quest code stay as they are.
   *Done when* the existing encounter tests pass under both flag values, plus a new test for an
   Embermane attack montage reaching the wire.
6. **E3 for the behemoth.** Replicate the behemoth ASC and attribute set so the client shows the
   health bar and hit reactions from server state, and send behemoth→player damage through
   `damage.behemoth_to_player`.
   *Done when* offline tests cover the attribute update and an attack hit on the player.
7. **Live run** (user joins; no menu automation). Check against the old symptom list: on the
   ground, natural posture, patrols, notices the player, attacks, takes damage with a health bar,
   dies, and rewards arrive.
   *Done when* the user confirms it. Then make the engine path the default and delete the puppet code.

## Open questions

- `structs.py` divergence (120 lines): confirm none of Phoenix's newer readers conflict with
  Mystic's root-motion readers before merging.
- Mystic's `damage.py` counts a hit by range (`MPGS_HIT_RANGE`), not the decoded `FHitResult`.
  Keep it for now, flagged as a stand-in.
- Machine crashes on 10-19 (`MEMORY_MANAGEMENT`, see `tools/analyze-machine-crashes.ps1`):
  `tools/restart-island.ps1` stays suspended. Step 7 needs this resolved or a deliberate decision.
- Multiplayer: the director is per actor, but the encounter is still per session. Shared
  encounter simulation is out of scope here.
