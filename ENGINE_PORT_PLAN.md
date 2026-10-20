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
   **Result (2026-10-19):** done, manifest `GameServer/data/2.1.1/islands/Adventure_IslandA_D24.engine_data.json`
   (`python -m tools.export_engine_manifest`), checked by `tests/test_island_engine_data.py`.
   - Action set: 8 beta actions (SideGore L/R, TailSwipe L/R, PawSwipe L/R, JumpReposition, Bite), all with montage and
     root-motion curves. Taken from the earlier all-species run (`Mystic-Paradox/_dumps/allact/out`).
   - Schema: `lerawr_beta_bp_C` → `lerawr_archetype_bp_C` → `Bp_Behemoth_C` (no genus class). The 48 net properties are identical to
     the island contract's schema. The contract also has RPC names, so step 4 keeps `island_encounter.install_schema` and does not use
     `bp_schema`.
   - BT: `lerawr_beta_new_bt` (from the beta Blueprint's defaults) plus 19 sub-trees, all resolved. `TreeSet.load` now loads any
     BehaviorTree dump; `_shared/ai/sub_flee_arrive` has no `_bt` suffix.
   - Terrain: `adventure_moss_triforce` landscape, 28 components, 187×249 grid. It matches the live player position (root Z 127.5,
     landscape 25.2). The plan's check failed as written: the aether vent at `BP_PatrolCenterPoint_263` is 43 cm above the
     landscape (160 cm at `BP_PatrolCenterPoint3`). **Resolved in step 4:** the vent Z is the actor's pivot, not the floor. See below.

   Step 4 backlog found here: about 40 node types in reachable sub-trees are not implemented. Step 4 traced which ones the
   encounter actually reaches (below).

4. **Generalise the director.** Turn `RockDirector`/`RockAI` into `behemoth/director.py`: one
   director per spawned behemoth, given species data, terrain and a target provider. Add
   `brain.load_species`. Keep a Rock regression test.
   *Done when* an offline tick loop drives the Embermane through its BT to patrol, aggro and pick an
   attack, and emits movement with root motion and montage entries that decode bit-exactly.

   **Result (2026-10-19):** done. `mpgs/behemoth/director.py` (`Species` takes the place of `brain.load_species`;
   `BehemothDirector`, `BehemothContext`; `RockContext` now subclasses it), `tools/trace_behemoth_bt.py`,
   `tests/test_behemoth_director.py`. 174 tests pass.
   - **Trace first** (real `bt.Runner` on `lerawr_beta_new_bt`, a scripted player, island terrain, 180 s). Only 5 unhandled node
     types are on the actual path, not 40. One of them explained **"never attacks"**: `BTDeco_CheckChosenAction_C` compares the
     class of `bb[ChosenAttack]` with its `ActionClass`, which the beta tree leaves unset. The plain node means "no attack chosen"
     (approach) and the inverted node guards `PerformActionClass`. Treated as always-pass, it blocked every attack and kept the
     actor approaching forever, matching the live symptom. Implemented from bytecode: that decorator,
     `archon_distance_check_btdeco_C`, `btdeco_lock_target_C` (with LockTarget/ClearLockedTarget, including on abort) and
     `distance_from_target_service_C`. After that: 31 actions in 180 s (20 attacks, 11 `JumpReposition`), all with root motion.
     Only `audio_exit_flee_state_bttask_C` (cosmetic) remains unhandled.
   - **Floor:** across 682 grounded player samples from the live logs, root minus landscape is a constant ~102 cm everywhere,
     including within 500 units of the arena center. **The landscape is the arena floor.** The vent Z is a pivot. So the current
     `island_encounter` spawn (vent Z + 149) floats the Embermane about 43 cm, which likely contributes to the bad posture.
     `Mover` (landscape + capsule offset) is correct.
   - **Director:** a pure simulation that returns `move`/`montage`/`lock`/`state`/`target`/`brain` events, with no wire output.
     A test encodes its first attack (`MulticastPlayMontage` with the real montage package, `ReplicatedMovement` +
     `RepRootMotion` + server timestamp) with the Embermane's own schema and decodes it back.
   - Open for step 5: there are no Embermane turn-in-place montages, so turns snap. The Embermane drifts up to about 4,300
     units from its center while chasing a strafing player; check whether a leash/deaggro applies live. `damage` rows are
     still empty (step 6). The `MulticastPlayMontage` field choice (PlayRate vs InPlayRate, bPlayBit) is still marked
     "confirm live" in `spawn.py`.
5. **Rewire `island_encounter.py`** behind `MPGS_BEHEMOTH_ENGINE=1`: `start` → `spawn.spawn_entry`
   + director; `step_movement` → `director.tick`; `handle_damage` → `damage` + `BehemothHealth`
   (via engine attributes once step 6 lands); `reveal` becomes a BT/aggro event. Remove
   `DisableIkinema`, the fixed Z, the circle patrol and the mode-0 workaround on that path. Rewards,
   XP and quest code stay as they are.
   *Done when* the existing encounter tests pass under both flag values, plus a new test for an
   Embermane attack montage reaching the wire.
   **Result (2026-10-20):** done. The scripted encounter stays the default; `MPGS_BEHEMOTH_ENGINE=1` switches to the director.
   179 tests pass with the flag at 0 and at 1 (`tests/test_island_encounter.py` is pinned to 0,
   `tests/test_island_encounter_engine.py` to 1).
   - `start`: the same proven spawn bunch, but the root sits on the landscape + capsule offset (about 43 cm lower than vent
     Z + 149), with `ReplicatedMovementMode = 1` and no `DisableIkinema`. It attaches a `BehemothDirector`. Species, terrain
     and trees are loaded once per world.
   - `step_movement` → `_engine_step`: ticks the director in the existing 15 Hz loop. `move` → unreliable `ReplicatedMovement`
     + `ReplicatedServerLastTransformUpdateTimeStamp`, plus `RepRootMotion` (with the montage's path export) while a root-motion
     montage plays, cleared when it ends. `montage` → reliable `NetworkedMontageComponent.MulticastPlayMontage`, exporting the
     component on first use. `lock` → `LockedTarget`. `target` → `reveal()`: the tree's aggro now decides spotting, not the
     4,000-unit distance check. Movement waits while reliable encounter entries are queued, so it never overtakes the spawn,
     reveal or a montage.
   - `handle_damage`: unchanged health/combat text/defeat/rewards, plus `director.health.hits` (a hit acquires the target) and
     `director.kill()` at 0. Deviation from the plan: `BehemothHealth` was not swapped in, because Phoenix's health path already
     works live. The engine `damage` module waits for step 6.
   - Not verified live. The restart helper is still suspended over the 10-19 machine crashes; a live run needs
     `MPGS_BEHEMOTH_ENGINE=1` in the island server's environment.

6. **E3 for the behemoth.** Replicate the behemoth ASC and attribute set so the client shows the
   health bar and hit reactions from server state, and send behemoth→player damage through
   `damage.behemoth_to_player`.
   *Done when* offline tests cover the attribute update and an attack hit on the player.
   **Result (2026-10-20):** done offline, engine flag only. 185 tests pass with the flag at 0 and at 1.
   - **Health bar:** `Bp_Behemoth`'s `ArchonAbilitySystem` spawns the native `ArchonHealthAttributeSet` and
     `ArchonDamageAttributeSet` (its `DefaultStartingData`). The engine spawn bunch now creates both as dynamic sub-objects with
     static class GUIDs 41/43 that the client knows from the join, health = MaxHealth = 18,900. The ASC lists them in
     `SpawnedAttributes`, the same shape the join uses for the player's sets. Each player hit sends a `CurrentHealth` update with
     the creating header. **Inferred**: no behemoth ASC has been seen on the wire, and the Mystic attribute path was never
     confirmed live.
   - **Behemoth → player:** the director emits `hit` the first tick the player stands in an open damage window's attack area
     (authored HitAreaBoxes, a stand-in for the native per-frame sweep). The adapter sends
     `player_hittable_bpc.ClientTryHit` (HitID, Damager, the authored `FDamageTableData` row). Only `ServerAcceptHit` applies
     damage: the player's `CurrentHealth` plus `ClientHandlePostMitigationDamage`. `ServerRefuseHit` (dodge) and unknown or
     duplicate IDs change nothing. Amount = row element damage × `behemoth_to_player_exploration_dmgcurve`, with **equal powers as a
     stand-in** (SideGore = 75). Damage rows come from `tools/export_behemoth_damage.py` (in the manifest).
   - New wire format from UE 5.1.1 source: `FHitResult::NetSerialize` (`repl/structs.py`, `tests/test_hit_result.py`), needed
     because a struct RPC parameter is sent whole.
   - Not done: downed/revive at 0 health, the row's stagger/camera tags (`AssetTags`), the real power values, and resistances.

7. **Live run** (user joins; no menu automation). Check against the old symptom list: on the
   ground, natural posture, patrols, notices the player, attacks, takes damage with a health bar,
   dies, and rewards arrive.
   *Done when* the user confirms it. Then make the engine path the default and delete the puppet code.

   **Live test plan (2026-10-20).**

   *What the 10-19 crashes were.* Both bugchecks (11:41 and 15:57) are `0x1A / 0x61941 PAGE_TABLE_RESERVED_BITS_SET`. The faulting
   stack is inside `EasyAntiCheat_EOS.sys`, and WinDbg buckets it as `hardware_ram` (`logs/crash-analysis-20261019-18*`). That is
   kernel page-table corruption: user-mode Python servers cannot cause it. The restart flow coincided with it because each attempt
   also (re)started the stock client and its EAC kernel driver. After the 15:58 reboot, three island sessions ran 18:37–20:21
   without a crash. The suspension guard exists only in the backup `logs/restart-island-before-regression-rollback-20261019.ps1.txt`.
   The tracked `tools/restart-island.ps1` has no guard, but it needs a saved PID, and nothing is running now.

   *Before the session (the user's call, not code):* a memory test (Windows Memory Diagnostic or MemTest86). Stock BIOS memory
   settings make the "RAM" bucket easier to rule in or out. Save other work first; a repeat bugcheck reboots the machine.

   *Prep (offline, no client):*
   1. `python -m unittest discover -s GameServer/tests` with `MPGS_BEHEMOTH_ENGINE=1` (185 pass) and
      `python -m unittest tests.test_island_engine_data` from `GameServer/`. The engine data must be present and match the manifest.
      Without it, the island fails at arrival, not at startup.
   2. Bisect switch (done, 2026-10-20): `MPGS_BEHEMOTH_ASC=0` (engine path only; default on) drops just the step 6 ASC and
      attribute-set blocks from the spawn, and their health updates. Movement, attacks and behemoth → player hits are unchanged.
      A test checks that the lean spawn equals the full one minus those blocks. 189 tests pass with ENGINE/ASC at 0/1, 1/1 and 1/0.
      Use it only after a failure: if the full run gets `client rejected actor ch=19`, relaunch the island with
      `$env:MPGS_BEHEMOTH_ASC = '0'` as well. Acceptance there means the inferred ASC blocks (step 6) are at fault; a rejection
      there too points at the step 5 spawn/movement. The island log then prints `ability system left out`.

   *Start (one clean launch, no restart helper):* in a PowerShell window, `$env:MPGS_BEHEMOTH_ENGINE = '1'`, then
   `.\start-server.ps1` in the same window, so the island inherits the variable. The city server also inherits it; only the
   island encounter reads it. Confirm the island log prints `ENCOUNTER engine on: lerawr_beta tree=lerawr_beta_new_bt` after
   arrival. Then launch the stock EAC client (`MP211_NO_INJECT=1 MP211_EAC=1`); the user does every menu click and joins the hunt.

   *One run, checked in order* (each line has a log signature in `logs/islandserver_*.log`):
   | Check | Pass looks like | Log |
   |---|---|---|
   | Spawn accepted | Embermane visible, no disconnect | no `client rejected actor ch=19` |
   | Floor/posture | feet on the ground, legs not stretched (compare the 10-18 screenshots) | `floor z=` in the engine line |
   | Patrol, then aggro at about 4,000 | walks; compass icon only once spotted | `ENCOUNTER spotted` |
   | Attacks | bite/side-gore/tail-swipe play; no snap back after root motion | `ENCOUNTER montage ...` |
   | Health bar | bar shows and drops per hit | `ENCOUNTER hit guid=` |
   | Gets hit | dodging avoids damage; standing in it costs about 75 | `ENCOUNTER hit attempt` / `accepted` / `refused` |
   | Kill + rewards | dissolve, slain banner, loot | `ENCOUNTER defeated` |

   *Stop rules:* stop at the first failing row and report it. Don't patch mid-session; the failing row goes back to the layer it
   belongs to. On a client crash or disconnect, collect the island log and the client log before relaunching. On a machine
   bugcheck, stop live testing entirely and treat it as a hardware/EAC problem; the server logs before the reboot show what was
   running. Rollback: close the PowerShell window or set `$env:MPGS_BEHEMOTH_ENGINE = '0'`, then `.\Stop.bat` and
   `.\start-server.ps1`. The scripted encounter is the default again.

## Live finding: the client never answers ClientTryHit (2026-10-20)

Every hit the Embermane lands produces a `ClientTryHit` on the player's `player_hittable_bpc`. The stock client sends back
neither `ServerAcceptHit` nor `ServerRefuseHit`: no damage and no hit reaction. Investigated in the 2.1.1 executable (stock exe ==
workbench exe, SHA-256 934325AC...; Dumpspace function offsets are real `.pdata` function starts):

- `ClientTryHit` exec thunk `0x2337460` reads the parameters and calls vtable slot `+0x4A8` unconditionally.
- `UArchonHittableComponent::ClientTryHit_Implementation` (`0x2700320`, vtable `0x6901F60`) always appends the hit to
  `OutstandingHits_Client` (this+0xF0, 0x200 bytes per entry) and calls `PrimaryComponentTick.SetTickFunctionEnable(true)`.
- The answer is sent from `TickComponent` (slot 118, `0x2722610`): for each queued hit, `GetInvulnerabilityTagsForAttack`, then
  `ServerRefuseHit` if any tags, else `ServerAcceptHit` (the only callers of the two stubs), then clear the list.
- Constructor `0x26F86A0` sets `bCanEverTick` and clears `bStartWithTickEnabled`: ticking on demand works.
- So if the RPC were dispatched, a reply would always follow. The server logs every client RPC and saw none, and nothing
  unresolved on channel 4. The client never runs `ClientTryHit_Implementation`.
- Our wire, decoded from the session's real `due()` output: a stably named block for GUID 48010, export
  `player_hittable_bpc` with outer = the pawn (1050, the same outer the join uses), field index 2 (consistent with
  `DamageComponent`'s working index 7). The live object dump confirms the runtime name `BP_PlayerCharacter_C_*.player_hittable_bpc`.
- UE 5.1.1 drops exactly this case silently: `UActorChannel::ReadContentBlockHeader` returns null at Log level for a stably named
  sub-object it cannot resolve. No path in stock `GetObjectFromNetGUID`/`RegisterNetGUIDFromPath_Client` explains a failure,
  but the client runs the Phoenix Labs fork (5.1.1-682875+__phx-archon).
- Key difference: the real server's join exports 19 named, replicated pawn components (flags 7 with checksum: `hit_reaction_bpc`,
  `floating_combat_text_bpc`, `player_state_machine_bpc`...). `player_hittable_bpc` is not among them (not replicated).

**Bisect in place:** each hit also sends `hit_reaction_bpc.ClientStartStaggerAction` to the GUID the join already maps
(`MPGS_HIT_STAGGER_PROBE=0` turns it off). A visible stagger with still no reply puts the fault on the hittable addressing;
no stagger puts it on server->client RPCs on the pawn channel.

## Open questions

- `structs.py` divergence (120 lines): confirm none of Phoenix's newer readers conflict with
  Mystic's root-motion readers before merging.
- Mystic's `damage.py` counts a hit by range (`MPGS_HIT_RANGE`), not the decoded `FHitResult`.
  Keep it for now, flagged as a stand-in.
- Machine crashes on 10-19 (`MEMORY_MANAGEMENT`, see `tools/analyze-machine-crashes.ps1`):
  `tools/restart-island.ps1` stays suspended. Step 7 needs this resolved or a deliberate decision.
- Multiplayer: the director is per actor, but the encounter is still per session. Shared
  encounter simulation is out of scope here.
