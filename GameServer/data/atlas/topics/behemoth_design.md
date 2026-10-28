# Behemoth design in the 2.1.1 client: Embermane (lerawr)

How one behemoth is put together: its classes and variants, its attacks, how it chooses one, its behavior
tree, its damage windows and hitboxes, and its rage and stamina. Embermane (`lerawr`, folder
`Monsters/aq/lerawr`) is the worked example; the other behemoths are built the same way.

Read from the class defaults, behavior trees, montages, data tables and physics assets in the paks
(`python -m tools.atlas asset <name>`), and from the Blueprint bytecode (`pseudo/BP_BehemothAction_C.txt`,
`pseudo/Bp_BehemothActionSet_C.txt`, `pseudo/evaluate_action_btservice_C.txt`, `pseudo/Bp_Behemoth_C.txt`).
Anything not read directly is marked **unconfirmed**.

In short: the behemoth's **behavior tree** runs a fixed priority list (death, stagger, flee, rage change, …).
In combat, a **service scores every attack** in its action set against where the players stand, and the tree
plays the best one. Each attack is a **montage**. The montage's **damage windows** say which of the
behemoth's own physics bodies deal damage, for how long, and with which **damage table row**.

To regenerate everything below for another behemoth:

```
python -m tools.atlas assets 'Archon/Content/Monsters/aq/lerawr/*' 'Archon/Content/Monsters/_shared/ai/*'
python -m tools.atlas build <the Blueprint classes that command lists>
python -m tools.atlas asset lerawr_bt        # or lerawr_action_set_table, b_leRawr_loco_sidegore_right_Montage, lerawr_phat, …
```

## 1. Classes and variants

| Class | Parent | What it sets |
|---|---|---|
| `lerawr_archetype_bp` | `Bp_Behemoth` | `BehaviorTree = lerawr_bt`; rage: duration 9 s, montage rate × 1.5 |
| `lerawr_normal_bp` | archetype | BaseHealth 31,680; GlobalPower 100, GlobalResistance 250; `lerawr_action_set_table`, `lerawr_damage_table`; metadata row `lerawr_normal`; frost elemental health 6,000; parts (below) |
| `lerawr_alpha_bp` | normal | BaseHealth 36,450; GlobalPower 550; rage duration 15 s; `lerawr_alpha_action_set_table`, `lerawr_alpha_damage_table` |
| `lerawr_heroic_bp` | alpha | GlobalPower 850; metadata row `lerawr_heroic` |
| `lerawr_beta_bp` | archetype | BaseHealth 18,900; GlobalPower 100, GlobalResistance 80; `BehaviorTree = lerawr_beta_new_bt`; beta tables |
| `lerawr_power3_bp` | normal | GlobalPower 4.77, GlobalResistance 3.05 |

Health is then scaled by the tuning presets (`topics/behemoth_scaling.md`), and power by any override.

Parts on `lerawr_normal_bp` (`monsterpart_bpc` components): BackLeft, BackRight, FrontLeft and FrontRight each
have `RatioOfBehemothHealth` 0.1; Body and Head use the default. Head uses the `head_standard_ptpreset` preset
and spawns `lerawr_horn_sever_bp` when severed. Tail spawns `lerawr_tail_sever_bp` at `tail_sever_socket`,
removes the physics bodies `tail_06_jnt`–`tail_08_jnt` and sets the blackboard key `TailSevered`.

## 2. The action set

`lerawr_action_set_table` (row struct `Struct_ActionSet`). When the behemoth spawns, `Bp_BehemothActionSet`
spawns one actor per row (`Spawn Action Actor`). It copies the row's montage, play rate, cooldown, base value,
shared cooldowns, stamina drain, usage count and tags onto it. Every row also has `MinIntensity` Low and
`MaxIntensity` High, and a cooldown variance of 0.

| Action | Class | Cooldown s | Stamina | Base value | Montage (length s) | Damage window s (row) |
|---|---|---|---|---|---|---|
| SideGore_R | `lerawr_sidegore_r_atk` | 5 | 10 | 1 | `b_leRawr_loco_sidegore_right_Montage` (2.73) | 0.90–1.52 head/neck/spine (`SideGore`) |
| SideGore_L | `lerawr_sidegore_l_atk` | 5 | 10 | 1 | `b_leRawr_loco_sidegore_left_Montage` (2.73) | 0.90–1.52 head/neck/spine (`SideGore`) |
| pounce | `lerawr_pounce_simple_atk` | 8 | 10 | **0** | `b_aq_lerawr_00_atk_pounce_fwdleft_mot` (2.05) | 0.10–0.62 arms/spine (`pounce_fly`, or `pounce_ac` when aether-charged) |
| TailSwipeL | `lerawr_tailswipe_Left_atk` | 5 | 10 | 1 | `b_aq_gen_00_atk_tailswipe_left_00_mot` (2.43) | 1.07–1.33 tail (`tailswipe`; `tailswipe_broken` once the tail is cut) + legs/pelvis (`tailswipe_body`) |
| TailSwipeR | `lerawr_tailswipe_Right_atk` | 5 | 10 | 1 | `b_aq_gen_00_atk_tailswipe_right_00_mot` (2.43) | 1.00–1.33, same rows as TailSwipeL |
| PawSwipeL | `lerawr_pawswipe_Left_atk` | 10 | 10 | 1 | `b_aq_lerawr_00_atk_short_pounce_fwdleft_00_Montage` (1.67) | 0.27–0.47 right arm (`bite`) |
| PawSwipeR | `lerawr_pawswipe_Right_atk` | 10 | 10 | 1 | `b_aq_lerawr_00_atk_short_pounce_fwdright_00_Montage` (1.67) | 0.27–0.47 left arm (`bite`) |
| Bite | `lerawr_short_bite_atk` | 10 | 10 | 1 | `b_aq_lerawr_00_atk_bite_fwd_00_Montage` (1.73) | 0.40–0.73 head/neck (`SideGore`) |
| FireBlast | `lerawr_fireblast_atk` | 15 | 5 | 1 | `b_aq_gen_00_atk_fireblastalpha_fwd_00_mot` (4.97) | 2.66–2.88 head/horn/arms (`Gore`); at 2.84 lobs 2 `lerawr_fireblast_projectile_v2_bp` at each of up to 4 players within 80 m (`fireblast`) |

Times are from the montage start at play rate 1. Row names are matched as written; the paw swipes really do
use `bite`, and the bite uses `SideGore`. The pounce row has base value 0, so the scoring below never picks
it; the tree plays the pounces itself (section 4).

**Hit areas.** Each attack class sets `HitAreaBoxes` in its defaults: boxes relative to the behemoth (X
forward, Y right, cm; extents are half-sizes) where a player must be for the attack to score. They are scaled
by the behemoth's size (`ScaleHitAreaToBehemothSize` true).

| Attack | Boxes (offset X, Y; extent X, Y) |
|---|---|
| SideGore_R | (0, 200; 250, 200), (−80, 75; 300, 135) |
| SideGore_L | (0, −200; 250, 200), (−250, −300; 300, 100) |
| pounce | (800, 0; 1000, 200); its own default cooldown 15 is replaced by the row's 8 |
| TailSwipeL / R | (−300, ∓200; 300, 200) |
| PawSwipeL / R | (100, 0; 300, 200) |
| Bite | (100, 0; 500, 500) |
| FireBlast | (750, 0; 750, 1000) |

Variants: the **alpha** table adds Firebreath (`lerawr_firearc_atk`, cooldown 30) and FireBackJump (15), and
raises the swipes and gores to cooldown 10. The **beta** table drops FireBlast and pounce, and adds
JumpReposition (base value 2).

## 3. How an attack is chosen

`evaluate_action_btservice` is a service on the root of `lerawr_bt`. On each tick (or only when the search
starts, if `EvaluateActionsOnSearchStartFeature` is on and the service asks for it), it:

1. Uses the action in the blackboard key `ForceAction` if one is set, with score 99999.
2. Otherwise calls `Evaluate` on every action and keeps the highest score.
3. Writes the score to `TopScore` and the action to `ChosenAttack` (cleared when nothing scores).

`BP_BehemothAction.Evaluate` returns 0 unless all of these hold:
- the action does not have the tag `BehemothAction.Base.IgnoreInActionEvaluate`;
- `IsActionAvailable`: not on cooldown, base value > 0, the threat intensity is between the row's min and max,
  the behemoth passes `BehemothRequirements`, it is in every `RequireStates` state (default: Combat) and in no
  `ExcludedStates` state;
- it has a current target;
- the simplified attack path (`AttackVectors1/2`, scaled) does not hit the navmesh edge before 90 % of its
  length (`PathHitsNavMesh`).

It then scores the players standing in the hit areas (`EvaluateActionAtTransform` → `GetIdealTargetForAction` →
`ScoreTargetArray`):

```
targets = live players inside the action's hit areas (targets' positions predicted
          PredictTargetPositionsTimeMin..Max s ahead)
score   = sum over targets of  target.GetAttackTargetScore() * BaseValue * AttackWeighting * RuntimeWeightingPercent
score   = score * (1 - RandomWeightPercent) + RandomWeightPercent * score * random(0, RandomWeightMax)

GetAttackTargetScore (players, BP_PlayerCharacter) = ArchonCharacter.AttackTargetScore * max(AggroMultiplier attribute, 0)
```

Defaults on `BP_BehemothAction`: `AttackWeighting` 1, `RuntimeWeightingPercent` 1, `RandomWeightMax` 2, and
`RandomWeightPercent` is not set, so it is 0. None of Embermane's attacks change these. So for Embermane the
score is just the sum of the target scores in the hit areas × base value, with no randomness. A later action
must score strictly higher to win, so ties keep the earlier row (the action actors are spawned in row order). Attacks with `EffectAreaOfAttack` entries score candidate positions instead; Embermane's
attacks have none.

After an attack is chosen, `Bp_BehemothActionSet.UpdateWeightsBasedOnChosenAction` sets the chosen action's
`RuntimeWeightingPercent` to 1 (`RuntimeWeightingToSetWhenActionChosen`). It adds
`RuntimeWeightingToAddWhenNotChosen` to every other action, up to `MaxRuntimeWeighting` 2. That amount is not
set, so it is 0: every weight stays 1 for Embermane.

**Cooldowns** (`BP_BehemothAction.StartCooldown`). Each use lowers `UsesLeft`; at 0 the action goes on cooldown
for:

```
Cooldown + (several behemoths active ? CooldownDeltaInMultiBehemothFights : 0) + random(-CooldownVariance, CooldownVariance)
```

`BTTask_PerformActionClass` plays the chosen montage (`ChooseMontageToPlay`, `PlayBehemothMontage`) and drains
`StaminaDrainAmount`. It calls `StartCooldown` and `AddToCooldown`, and then `UpdateActionWeightsBasedOnChosenAction`.
Shared cooldowns are in `Bp_BehemothActionSet.SetSharedCooldowns`; Embermane's rows set none.

## 4. The behavior tree (`lerawr_bt`)

`python -m tools.atlas asset lerawr_bt` prints the whole tree. The root selector runs the first child that can
run:

| # | Child | When |
|---|---|---|
| 1–6 | sub-trees `shared_death_bt`, `lerawr_enter_arena_fly_bt`, `shared_handle_stagger_bt`, `sub_puppetmode_bt`, `aq_flee_fly_bt`, `lerawr_deaggro_bt` | each one's own root decorators decide |
| 7 | StateChange: exit rage (`b_leRawr_state_rage_exit_mot`) if `ReadyToExitRageState`, else enter rage (`b_leRawr_state_rage_enter_mot`) if `ReadyForRageState` | |
| 8 | `shared_generic_ai_coordination_bt`, `lerawr_enter_combat_pounce_bt`, `shared_manage_target_lost_bt` | entering combat: if the target is within `EnterCombatRange`, set state Combat, turn, then run-and-pounce |
| 9 | `PerformActionClass` on `ChosenAttack` | in Combat, an action is chosen, 1 s cooldown |
| 10 | Pounce: EQS `find_pounce_location_eqs` (17.5–22.5 m from itself, 10–14 m from the target), jump there (`lerawr_jumpto_bt`), pounce (`b_aq_lerawr_00_atk_pounce_fwdleft_fastblend`) | in Combat, 15 s cooldown, target locked |
| 11 | `lerawr_runandpounce_bt` | in Combat, target 10–100 m away, nothing scored (`TopScore` = 0), target valid |
| 12 | turn to the target and `MoveToAndFace` | in Combat, **no** action chosen, 2 s cooldown, target valid |
| 13 | `shared_patrol_bt` | otherwise |

`BTDeco_CheckChosenAction` is true when the class of the object in `ChosenAttack` equals its `ActionClass`.
Neither instance in `lerawr_bt` sets `ActionClass`, so it is true when nothing is chosen. Child 9 inverts it
("an action is chosen"); child 12 does not ("no action chosen").

Root services: `UpdateDirections`, `distance_from_target_service` (every 0.2 s), `calc_knowntargetavgpos_btservice`
(2 s), `try_pick_new_target_bts` (2 s) and `evaluate_action_btservice`.

`lerawr_runandpounce_bt`: when the target is 20 m or more away, it does a butt wiggle (not for alphas), then
runs until it is within 15 m. Otherwise it turns and does a reposition pounce (`pounce_repo`). Then it
pounces:
- alphas 80 % of the time play `b_aq_lerawr_00_atk_pounce_fwd_01_mot`;
- otherwise 50 % left (`…_pounce_fwdleft_fastblend`), else right (`…_pounce_fwdright_fastblend`).

Half the time a personality animation follows (not for Shockmane alphas). The pounce montages hit with
`pounce_fly` (0.10–0.62 s, or 0.21–1.15 s for the forward pounce), or with `pounce_ac` when aether-charged.

## 5. Damage windows and damage rows

A damage window is the montage notify `archon_damage_window` (a Blueprint subclass of the native
`DamageNotifyState`). Its settings:

- `PhysicsBodyNameMap`: weapon role → the behemoth's physics bodies that deal damage (e.g. SideGore:
  `head_jnt`, `neck_01_jnt`, `neck_02_jnt`, `spine_05_jnt`, `spine_04_jnt`);
- `DamageKeys`: rows of the behemoth's damage table (`DamageComp.DamageTable`, here `lerawr_damage_table`);
- optional `BehemothRequirements`, e.g. a state, or a part state ("Tail is Severable").

It runs on the dedicated server (`bTriggerOnDedicatedServer` true). The Blueprint part only broadcasts
`OnDamageWindowStarted` / `OnDamageWindowEnded` through `damage_eventdispatcher_bpc` and draws debug shapes.
The real work is native and split between the notify and the owner's `DamageComponent`. Addresses and
offsets are in `native_notes.json`: `python -m tools.atlas native DamageNotifyState` and `… DamageComponent`.

**Window start** (`DamageNotifyState` NotifyBegin, 0x2742C00; the class overrides only NotifyBegin and
NotifyEnd, not NotifyTick):
1. Calls `ShouldApplyDamage`, which is where `archon_damage_window` checks `BehemothRequirements`. This happens
   **once**, at the start; if it fails, the whole window does nothing.
2. Picks the keys. `ModifyDamageKeys` (or `ModifyWeaponDamageKeys` with the owner's current weapon when
   `UseCurrentWeapon` is set) may replace `DamageKeys` / `DamageKeysAfterFirstHitPerTarget`.
3. For **each weapon role** in `PhysicsBodyNameMap`, adds an active damager to the owner's `DamageComponent`.
   The component keeps one entry per role, holding the mesh registered for that role (`SetMeshComponent(s)`).
   The bodies are looked up in that mesh's physics asset. A role with no registered mesh is ignored. Embermane's
   windows all use `WeaponRole_PRIMARY`, which is the behemoth's own mesh.

**Every tick** (`DamageComponent::TickComponent`, 0x2722960; skipped in the city):
- For each body of each active damager, every shape in the body's physics-asset geometry (spheres, capsules,
  boxes) is swept from the pose it had **last tick** to its pose **now**. The bone's scale applies.
- When the bone rotated, the move is split into sub-steps of at most `MaxStepAngle` (default **45°**) and
  each step is swept separately, so fast swings don't skip over a player.
- Each step is a single `SweepSingleByObjectType` (trace tag `DamageNotifySweep`). It returns only the first
  blocking hit. The object types come from `DamageComponent.ObjectTypes`, which `Bp_Behemoth` sets to
  `ObjectTypeQuery3` (Pawn) and `ObjectTypeQuery7`. Query7 is the first custom object channel,
  `ECC_GameTraceChannel1`, named **`Player`** in the game's `DefaultEngine.ini` (archonkit's extract of the
  2.1.1 paks). `BP_PlayerCharacter`'s `PlayerCollision` capsule has that object type. Queries 1–6 are the
  engine's object channels (WorldStatic, WorldDynamic, Pawn, PhysicsBody, Vehicle, Destructible), and the
  custom object channels follow in order, skipping trace channels.
- A hit counts only if it passes the per-target rule below. Accepted hits are collected, sorted and then
  applied with the key's damage table row (see "After a hit is accepted").
- The bodies are `PhysicsBodies` and `SkelPhysicsBodies` merged into one list, duplicates removed (NotifyBegin
  builds a set from both). Only `ap/crudge` uses `SkelPhysicsBodies` (section 5b).

**Once per target.** The component keeps one map from actor to last-hit time (montage position), shared by all
roles and all windows:
- **First contact:** the target is added and takes the `DamageKeys` row.
- **Already in the map:** hit again only if `MultipleHitLockoutPerTarget` > 0 and that many seconds have
  passed. Repeat hits use `DamageKeysAfterFirstHitPerTarget` if it is set.
- **When the map is cleared:** when a montage starts. Also when a window ends, unless
  `bExcludedHitActorsForDurationOfMontage` is set.

The native defaults are lockout **−1** and exclude-for-montage **true**, and Embermane's windows change
neither. So **a player is hit at most once per montage**, across all of its windows. In the tail swipe, a
player hit by the tail cannot also be hit by the `tailswipe_body` window that runs at the same time.

**Dodging.** `DodgeDetectPreDamageWindow` (default 0, unset for Embermane) makes the first N seconds of a
window harmless: no hit is accepted then. A player touched during that time who has the gameplay tag
**`State.InvulnerableDodge`** (on their ability system, own or parent tags) is added to the hit map, so the
window cannot hit them later either. A player without the tag is not added and can still be hit once the N
seconds are over. The tag comes from the native tag table (0x8890A10 + 0x240, set at 0x2601F34). Only four
windows in the game use this setting (section 5b).

**Window end** (NotifyEnd, 0x2744370): for each role, one last sweep of `LastChancePhysicsBody` if it is set,
then the damager is removed and the map is cleared (unless exclude-for-montage is set). No behemoth montage sets
`LastChancePhysicsBody` (section 5b).

**After a hit is accepted.** Before keeping a hit, the per-target check also asks the target, for each damage
row, `ArchonDamageTakerInterface.OnSweepHit(DamageGiver, row.AssetTags, true, HitResult)`. A false answer
drops the hit. Every implementation in the game returns true (`BP_PlayerCharacter`, `base_creature_bp`,
`base_gameobject_bp`, `behemoth_umbral_debuff_cyst`), so in practice nothing is dropped here.

Accepted hits are stored as 0x150-byte records: the hit result, the squared distance from the impact point to
the swept shape's transform, a direction and the keys. **Unconfirmed:** which end of the sub-step that
transform is. After all damagers have swept, the tick (0x26FC840):
1. **Sorts** the hits. Hits that did not start inside the target come first (`bStartPenetrating` false), then
   nearer hits before farther ones.
2. Runs **TryDoDamage** (0x2719190, log string `UArchonDamage::TryDoDamage`) on each hit and each of its keys.
   On the server a behemoth counts as locally controlled (UE's actor-info rule: no player controller plus
   authority counts as local), so its hits go on to
   **InternalTryDoDamage** (0x270E9A0). Clients send `ServerTryDoDamage` instead, and a server handling a
   remote player's weapon sends `ClientTryDoDamage`.
3. InternalTryDoDamage, with authority, passes a check (0x26FE340; not traced, it reads the attacker's
   current weapon), makes a prediction key and calls **RespondToHit** (0x26FBCB0, named by its profiling
   string).
4. RespondToHit looks up the key in `DamageComponent.DamageTable`. The gameplay effect is the row's `Effect`,
   or `DamageComponent.DefaultEffect` when the row has none. It applies that effect (0x26FC250) first through
   the hit component, then to the hit actor's ability system. The second step is skipped when the first worked
   and `bApplyDamageToComponentAndActor` is false. Then it broadcasts `OnHitFromAbilitySystemDelegate` and
   tells the attacker through `ArchonDamageGiverInterface`. When no effect applied, it broadcasts
   `OnHitDelegate` instead (or, on the server with nothing bound, takes another apply path, 0x26C3270).
5. Applying the effect to a **player** goes through `ArchonHittableComponent`. This needs the
   `ClientAuthoritativeDamageFeature` flag, a server, and a target that is an `ArchonAbilityCharacter` with
   that component. The server first works out the invulnerability tags (an empty result means
   `bHitWouldHaveSucceededOnServer`). Then, if the player pawn is not locally controlled, it stores the hit
   under a new GUID in `OutstandingHits_Server` and sends **`ClientTryHit`**. The client answers
   `ServerAcceptHit` or `ServerRefuseHit`, which matches what mpgs does since 69bb604. Otherwise the hit is
   processed on the server at once. Other targets get the effect directly; the code also calls
   `GameplayStatics.ApplyDamage` and records damage telemetry.

The damage numbers themselves come from the effect's SetByCaller values and the formula in `topics/armor.md`.

`lerawr_damage_table` (15 rows). These are the SetByCaller inputs of the damage formula in `topics/armor.md`
(section 2, step 1):

| Row | Physical | Flame | Other | Stagger tag |
|---|---|---|---|---|
| `SideGore` | 100 | 25 | | Heavy |
| `SideGore_BrokenHead` | 75 | 0 | | Heavy |
| `Gore` | 75 | 75 | | Heavy |
| `bite` | 75 | 0 | | Light |
| `pounce_fly` | 50 | 75 | | Heavy |
| `pounce_ac` | 50 | 125 | | Heavy |
| `pounce_repo` | 50 | 25 | | Light |
| `pounce_brokenhead` | 0 | 150 | | Heavy |
| `tailswipe` | 125 | 0 | | Heavy |
| `tailswipe_broken` | 0 | 75 | | Heavy |
| `tailswipe_body` | 100 | 0 | | Medium |
| `fireblast` | 0 | 100 | | Light |
| `fire_arc` | 0 | 200 | | Light |
| `rage_entry` | 0 | 50 | | Heavy (the `aoe_rage_entry` spawned 2.63 s into the rage-enter montage) |
| `ForceDismount` | | | BaseDamage 100 | |

Most rows use `DamageType_Cutting`; `bite`, `pounce_repo`, `tailswipe`, `tailswipe_broken` and
`tailswipe_body` have no damage type. `ElementalHealthDamageMod` is 1 throughout.

Other notifies that matter to the server:

| Notify | What it does | Embermane example |
|---|---|---|
| `Notify_StaggerProneBehemoth` | while active, `StaggerProneDamageThreshold` damage to the listed parts causes the given stagger | pounce 0–0.95 s: 5 damage to Head/FrontLeft/FrontRight → Interrupt |
| `archon_turn_warp_window_notify` / `archon_translate_warp_window_notify` | turn or slide toward the target during the window | sidegore turns 60°/s, up to 30°, at 1.00–1.48 s |
| `ModifyPartCollisionChannelNotify` | bones stop blocking players (Pawn → Ignore) | sidegore 0.90–1.52 s |
| `add_buffs_to_attacks_ns` | hits apply a buff during the window | pounce: `onfire_debuff` for alphas with a scarred head |
| `peristent_spawn_notify` | spawns actors along the path | aether-charged pounce: `lerawr_small_firezone_actor` every 250 cm |
| `spawn_lobbed_projectile_bpn` / `spawn_aoe_class_bpn` | projectiles / area effects with a damage row | FireBlast, rage entry |
| `notify_rage_montageratemod` | montage plays faster in rage (`RageStateMontageRateModifier`, 1.5 for Embermane) | most attacks |

## 5b. Damage windows of every behemoth

`python -m tools.atlas damage-windows` reads every AnimMontage under `Archon/Content/Monsters` (1,579
montages, found with AssetDump's class listing) and writes `data/atlas/damage_windows.json` and `.txt`. There
are 1,133 damage windows in 569 montages across 41 folders. For each window it records the start, duration,
damage keys, bodies per role, `BehemothRequirements` (127 windows set some) and any setting that differs from
the native defaults. The `.txt` lists those differences first. So the settings below are already known for
later behemoths; nothing has to wait.

| Setting | Windows | Where |
|---|---|---|
| `bExcludedHitActorsForDurationOfMontage` = false | 100 | multi-hit attacks in 18 folders, most in aq/paper (16, the 360 swipes), aq/paper_umbral (10), sq/bullseye (10), mb/crowbear and tp/beaver_frost (8 each). The map is cleared at each window end, so a target can be hit once **per window** instead of once per montage. |
| `MultipleHitLockoutPerTarget` = 0.5 | 2 | aq/shadowcat infinity charge exit (both montages): the same target can be hit again every 0.5 s during the window |
| `DodgeDetectPreDamageWindow` | 4 | aq/amber wing swipe L/R (0.05 s), tp/host radiant tail attack L/R (0.1 s) |
| `SkelPhysicsBodies` | 6 | ap/crudge only (spawn, death, rage enter, quick-teleport intro, tail-break stagger) |
| a role other than PRIMARY | 1 | ap/crudge rage enter: an extra `WeaponRole_NONE` entry, which damages only if a mesh is registered for that role |
| `LastChancePhysicsBody`, `DamageKeysAfterFirstHitPerTarget`, `UseCurrentWeapon`, `bUsePreAttackRaycastTest` | 0 | not used by any behemoth |

Folder names are the internal ones; matching them to in-game names is a separate job. The run reads every
cooked montage, so montages that no action set plays are listed too. Which montages each behemoth actually
uses comes from its action set tables (section 2).

## 6. Hitboxes

The bodies named in damage windows are in **`lerawr_phat`** (34 bodies, kinematic capsules;
`python -m tools.atlas asset lerawr_phat`). For example, `head_jnt` r = 78.4, length 161.9; `neck_01_jnt`
r = 75.2, length 33.7; `horn_jnt` r = 29.0, length 94.5. **`lerawr_player_col_phat`** (10 larger capsules)
is, by its name, the shape that blocks players. **Unconfirmed:** that `lerawr_phat` is also what player
weapons hit, and how parts map onto its bodies. The sever asset `lerawr_tail_sever_00_phat` is the cut tail's
own body.

The **`HitVolumes<attack>_Capsule`** tables (`debug_capsuleinfo_struct`: radius, half-height, rotation,
position) are **editor captures**, not runtime data. The attack's construction script draws them only in the
editor or with `bDebugAreaPrimitivesInPIE`, and `BTTask_PerformALLActionsInSet` records them under the name
`HitVolumes{filename}`. They are the damage-window capsules sampled over the window: radius and half-height
match `lerawr_phat` (`neck_01_jnt` r 75.18, half-height 33.67 / 2 + 75.18 = 92.02). For example,
`sidegore_r` has 128 rows = 16 samples × 8 capsules. They are useful as a reference for where an attack
sweeps; **unconfirmed**: positions are relative to the behemoth actor, and the sample rate is not known. The
`pounce_atk` table is empty and `pounce_simple_atk` has 968 rows.

## 7. Rage and stamina

From `Bp_Behemoth` (the defaults are on `Bp_Behemoth` unless noted):

```
RageCDMax   = MaxHealth * RageCDMaxHealthRatio (0.33)            # UpdateRageValuesWithPercentOfMaxRage
on damage taken (behemoth_received_damage_ab): RageCooldown -= damage
on a part break with IncreaseRageOnPartBreak: RageCooldown -= RageCDMax * 0.15
every 5 s after the first hit: RageCooldown += 140, up to RageCDMax  (not while ready for or in rage)
every 5 s in combat: RageCDMax -= 1 %, down to 65 % of its starting value
RageCooldown <= 0  ->  blackboard ReadyForRageState  ->  tree plays the rage-enter montage
```

Damage only builds rage while the behemoth is not tired, not ready to flee, not ready to tire, and passes
`IsInState(11)` (`CanBuildRage`). Which state 11 is has not been confirmed (see the end). Rage lasts `RageDuration`: 9 s for Embermane (15 s for alphas, 25 s by default). Each rage
adds 10 s, up to 90 s. Afterwards `RageCooldown` resets to `RageCDMax`.

Stamina: each action drains `StaminaDrainAmount` (10, or 5 for FireBlast). It isn't drained while in or ready
for rage. At 0 the behemoth becomes tired: 90 s at montage rate 0.8, then stamina refills. But `MaxStamina` is
99999 by default and Embermane does not override it, so in practice Embermane **never tires**.

## 8. What the server has to do

1. Spawn the action actors from the action set table, with the row values in section 2.
2. Run the behavior tree priority list (section 4), or a faithful copy of it, with the blackboard keys it reads
   (`ChosenAttack`, `TopScore`, `AttackTarget`, `ReadyForRageState`, `ReadyToExitRageState`, `TailSevered`, …).
3. Score attacks as in section 3: hit-area boxes around the behemoth, target score × base value, cooldowns.
4. Play the montage. During each damage window, sweep the named `lerawr_phat` bodies' shapes from last tick's
   pose to this tick's (sub-steps of ≤ 45° rotation) against players. Hit each player at most once per montage
   (or per window / per lockout where section 5b says so). Skip players in a window's dodge time with
   `State.InvulnerableDodge`. Apply hits nearest first. Send each one to the player as `ClientTryHit` and
   apply it on `ServerAcceptHit`. Deal the key's damage row through the damage formula (`topics/armor.md`),
   then multiply by the tuning presets' damage scale (`topics/behemoth_scaling.md`).
5. Track rage (section 7) and run the rage enter/exit montages; rage speeds attacks up by 1.5.
6. Apply the notify side effects that matter: stagger-prone windows, turn/translate warps, buffs on hit,
   projectiles and area effects.

## Still unconfirmed

- In the accepted-hit path: the check in InternalTryDoDamage (0x26FE340), how the effect is applied to
  non-player targets in detail (0x26FC250 after the hittable branch, and 0x26C3270), and which transform the
  sort distance is measured from.
- The client side of `ClientTryHit`: which invulnerability tags make the client refuse.
- The default `ArchonCharacter.AttackTargetScore` and what changes it (aggro, threat).
- What the threat intensity levels are and how `ThreatTrackingComponent` sets them.
- The coordinate frame and sample rate of the `HitVolumes` tables.
- What the other sub-trees do in detail (`shared_handle_stagger_bt`, `aq_flee_fly_bt`, `lerawr_deaggro_bt`,
  `shared_patrol_bt`); they are in `assets/` but not summarised here.
- The beta tree `lerawr_beta_new_bt` and the alpha-only attacks (Firebreath, FireBackJump).
- How the numeric states in `Bp_Behemoth` code map to `behemoth_states_enum`. In display-name order, 1 Rage
  and 3 Tired fit the code, but 11 would be EnteringRage, which is odd for `CanBuildRage`. The cooked enum
  does not list its values in order.
