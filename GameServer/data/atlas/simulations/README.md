# Phoenix simulation templates and bundles

The versioned template is an Atlas planning artifact. A staged bundle contains only server-owned Phoenix files under
`payload/`; Phoenix has no runtime dependency on Atlas. Generated local caches and bundle staging remain outside the
portable template identity.

## Commands

Run from `Phoenix-Atlas/GameServer`:

```powershell
python -m tools.atlas simulation template <preview-contract.json> `
  --species sally --variant sally_normal --tree sally_bt `
  --phoenix-root <Phoenix checkout> --out data/atlas/simulations/templates/adventure_islandp_sally.json

python -m tools.atlas simulation acquire <preview-contract.json> `
  --phoenix <Phoenix checkout> --species sally --variant sally_normal `
  --tree sally_bt --tree-package Archon/Content/Monsters/sq/sally/ai/sally_bt `
  --template-out data/atlas/simulations/templates/adventure_islandp_sally.json `
  --stage-out <staging directory> --allow-incomplete `
  --exporter-arg=--trace-file --exporter-arg=<trace.json>

python -m tools.atlas simulation stage data/atlas/simulations/templates/adventure_islandp_sally.json `
  --phoenix <Phoenix checkout> --out <staging directory> --allow-incomplete

python -m tools.atlas simulation apply <staging directory> --phoenix <Phoenix checkout>
python -m tools.atlas simulation rollback <staging directory> --phoenix <Phoenix checkout>
```

## Current composition inventory

| Hunt | Composition | Template | Staging result | Current blockers |
|---|---|---|---|---|
| `Adventure_IslandA_D24` | Moss Triforce (Emberthorn Cove) / `lerawr_beta` / `lerawr_beta_new_bt` | `templates/adventure_islanda_lerawr.json` | Offline complete, client accepted for attacks | No unresolved template requirements. The 180-second trace emits 32 attacks with no unhandled nodes; `BTComposite_SimpleParallel` (AbortBackground) and `wait_for_distance_bttask_C` are modeled from engine source and bytecode. The run-and-pounce runs only on combat entry (native gate in `lerawr_enter_combat_pounce_bt`). Live on the stock client LeRawr aggroed, attacked and every hit was accepted. Open: no behemoth navmesh or rock collision for this map, so off-terrain paths fail and a player on a rock is attacked from beneath; the run-cycle phase for `CanTransitToNextAnim` is approximate. |
| `Adventure_IslandP_D24` | Hades Reach / `sally_normal` / `sally_bt` | `templates/adventure_islandp_sally.json` | Research-only | 79/79 dependencies stage with no missing files. Source-defined R-hi/G-lo terrain decoding and all five collision-heightfield center samples are validated. All 1,549 Behemoth Recast tiles are present and the Phoenix `PathHitsNavMesh` query matches 675 native host captures. Childless composites pass on their parent's entry result (UE 5.1.1 `FindChildToExecute`); with that, the 120-second offline trace emits 19 attacks and 15 hits, and one stock-client session showed Sally attacking. `navtest_btdeco_C` has a single Displacement point, so it never reaches the mesh natively. Atlas maps Sally part presets, break overrides, inherited stagger defaults, and solo combat-entry defaults. Remaining gaps are `BP_BehemothAction_C.Evaluate` attack-vector navmesh rejection (SimplifiedAttackVectors not extracted), native behemoth floor height, and Sally PartCollisionComponent bone routing and runtime part data. |

This is the inventory of contracts currently present in the paired Phoenix checkout, not a claim that Atlas covers every game island or behemoth. PublicEvents remain a documented Phoenix exclusion. Map-only previews do not require behemoth dependencies.

## Scripted encounter checks

Live play is the final acceptance, but the behavior itself is checked first with a scripted player, offline, on the real
behavior tree, director, terrain and navmesh. From the Phoenix `GameServer` folder on `main`:

```
python -m tools.trace_behemoth_bt --hunt Adventure_IslandA_D24 --seconds 360 --scenario melee --json <out.json>
python -m tools.trace_behemoth_bt --hunt Adventure_IslandP_D24 --seconds 360 --scenario kite  --json <out.json>
```

`melee` walks to the behemoth and circles it at melee range. `kite` fights for 30 s, sprints straight away for 12 s and
waits out of reach for 10 s, six times, which drives the chase, reposition and gap-closing branches. The report lists
unhandled nodes (must be empty), sub-trees entered, attacks by montage and whether every montage has root motion.

Results on 2026-11-04 (360 s each):

| Hunt | Scenario | Attacks | Unhandled | Notes |
|---|---|---|---|---|
| Island A / LeRawr | melee | 44 | 0 | run-and-pounce entered once (combat entry) |
| Island A / LeRawr | kite | 19 | 0 | retreats do not re-enter run-and-pounce, as natively gated |
| Island P / Sally | melee | 57 | 0 | 18 attacks took over a roar's root motion |
| Island P / Sally | kite | 25 | 0 | `shared_combat_reposition_bt` entered 5 times; 8 roar takeovers |

Quick-item and weapon-part behavior is not reached by the tracer; it is covered by the Phoenix unit tests and needs a
live session for acceptance.

## Generated review artifacts

The current workstation snapshot is organized under `D:\PhoenixAtlasArtifacts`, with one directory per hunt. Each hunt
directory has `bundle_current/` (refreshed staged payload and `bundle.json`), `bundle/` (preserved historical snapshot), `inputs/` (template, source contract, engine manifest, acquisition report, behavior trace, and terrain header), `artifact.json` (copied-input hashes and readiness summary), and a short `README.md`. The root
`index.json` lists both compositions. This snapshot is outside git and is for inspection; the Phoenix runtime inputs
remain in the Phoenix checkout. Refresh `bundle_current/` and its artifact record after any acquisition or manifest change; do not overwrite the historical snapshot.

`template` selects a stable hunt, map, species, variant, behavior-tree, placement, loadout, and modifier identity. The
variant must be the preview contract's selected behemoth or an authored candidate. Selecting an alternate species does
not inherit the primary species' defaults. Map-only contracts produce `kind=map_preview`, omit behemoth requirements,
and retain the PublicEvents bypass as an explicit exclusion.

`stage` validates the Phoenix contract, engine manifest identities, required behemoth/terrain inputs, content hashes,
action rows, and reachable behavior-tree closure. `--allow-incomplete` writes a research bundle with explicit missing
dependencies and unresolved requirements. It never labels that bundle runnable. `apply` rechecks staged identities,
hashes, required files, and Phoenix build marker before writing. Its journal and backups support repeated apply, recovery
after interrupted writes, and explicit rollback. Only paths listed in `payload/` are managed.

`acquire` runs Phoenix `tools/export_simulation_inputs.py` with the selected identity, forwards each repeated
`--exporter-arg` as one exporter argument, regenerates the selected template from the supplied contract, then stages it. The exporter remains
the source of extraction behavior; pass source/cache inputs such as `--map-source`, `--height-base`, `--schema-chain`,
or `--trace-file` when the selected composition needs them. The command reports missing dependencies and unresolved
evidence; `--allow-incomplete` is required to retain a research bundle when acquisition is not complete.

## Evidence boundary

`dataPresent`, `behaviorUnderstood`, `consumerImplemented`, `offlineChecksPassed`, and `clientAccepted` are independent
evidence classes. An asset in the payload does not prove its behavior is understood or consumed correctly. Offline
preflight and tests do not prove stock-client arrival, grounded spawning, attacks, weapon/item effects, no disconnect, or
expected state changes. Regenerate the template after acquisition changes; review any manually maintained incident
record before replacing it.

## Hades Reach / Sally

`templates/adventure_islandp_sally.json` selects `Adventure_IslandP_D24`, map `adventure_arid_rose`, Sally variant `sally_normal`, and tree `sally_bt`. The acquisition validates R-hi/G-lo Landscape decoding and all five patrol-center samples against the collision heightfield. The 180-second offline trace emitted 29 attacks and 23 hit events. The bundle stages 79 dependencies with none missing and remains research-only.

Atlas maps 11 Sally Normal part components, six preset assets, Normal Blueprint break overrides, four vent health overrides, inherited stagger defaults, and the false `FailIfNotInA Group` default. Phoenix records the local zero-armor GUID entry; `StaggerArmorStack` is non-replicated. The solo `AllOtherAgents` message has no other AI recipient.

A montage requested while another is playing now takes over root motion, as UE 5.1.1 `Montage_PlayInternal` does; the old "a montage is already moving the actor" refusal is gone.

Open requirements are the Behemoth RecastNavMesh compressed tile payloads needed for `PathHitsNavMesh`, the reached `navtest_btdeco_C` consumer, Sally PartCollisionComponent bone routing and runtime part data, and matching-engine confirmation for empty Sequence behavior. The trace's empty Sequence is currently modeled as failure. The current bundle has not been applied.
