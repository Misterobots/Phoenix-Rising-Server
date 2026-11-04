# Axes on the server

What our server has to do for axes, built up the way `sword_server.md` was. Read from the 2.1.1 Blueprint code and
data, 2026-11-04:
- `BP_Axe_Weapon`
- `ga_determination_ab` and the determination effects and buffs
- the axe special parts (`ga_special_*`)
- the axe montages' notifies

How the power row and its abilities work for every type is in `weapon_power_and_abilities.md`. Anything marked
**not read in detail** needs a closer look before it is built.

**The short version:**
- The axe's resource is **determination**: a meter on the weapon that fills from your hits. When full, the next hit
  raises the **determination level** (1 to 3), which makes the axe hit harder for a while.
- **Charge levels** (holding an attack) decide how much each hit fills the meter.
- The special is **throwing the axe** (the special part), not an ability.
- The meter and level code is a **ServerOnly** ability, `ga_determination_ab`, so all of it is the server's job.

## 1. Charge levels

Holding a charged attack raises the weapon's charge level. The engine tells the axe through
`ArchonWeapon.OnChargeLevelChanged(NewChargeLevel, PreviousChargeLevel)`. Which native code raises it was **not
traced**; `ArchonWeapon.CanChangeChargeLevel` is part of it.

`BP_Axe_Weapon` (with authority) then:
- sets **`Current Charge Level`** = `NewChargeLevel` (replicated, rep index 21, `OnRep_Current Charge Level` drives the
  charge effects and sound)
- adds `NewChargeLevel - PreviousChargeLevel` to **`TotalChargeLevel`** (rep index 25), except when the new level is 4
- `ResetCurrentChargeLevel` (the montage notify `Notify_GAClearChargeLevel`, on the Y3 and open-attack charge
  montages at 0 s) sets the charge level and `TotalChargeLevel` to 0, and `DeterminationOverride` to 0

The charged attacks pick their damage row by charge level: `Notify_WeaponChargeDamageState` adds the level to the row
name, so `GA.X1.LVL` becomes `GA.X1.LVL0` .. `LVL4`. `verified.json`: `weapon-damage-key-level-suffix`.

**Stamina while charging.** The charge montages (`X1`, `X3`, `Y1`, `Y3`, open-attack charge) carry
`Notify_AxeMoveSkillCost` for their whole length. It sets the weapon's **`MoveSkillCost`** (rep index 18) to
`Move Skill out of charge cost` (25) while charging, and back to `Axe Base Move Skill Cost` (20) at the end.
`BP_BaseArchonWeapon.OnMoveSkill` spends `MoveSkillCost` as stamina. Slow charging (`Has Slow Charge State?`) also
costs `Slow Charge Stamina Cost Per Second` 5 (**not read in detail**).

## 2. The determination meter

The meter is the **weapon's** ASC attribute `ArchonDamageAttributeSet.Determination`. It is full at
`DeterminationMax` (12; the ability and `BP_Axe_Weapon` both default to 12).

`ga_determination_ab` (NetExecutionPolicy **ServerOnly**, granted from the power row's `RefAbilities`) listens to the
player's dealt damage (`player_eventdispatcher_bpc`, `OnPostMitDealtDamage`). On each hit:
1. **If the meter is full** (`Combat.Determination.ReadyToLevel` on the weapon's ASC): level up (section 3), then
   ignore hits for 1 s.
2. **Otherwise**, unless the hit's damage tags include `Combat.Axe.AttackDoesntGrantMeter`, fill the meter: apply
   `determination_ge` to the weapon's ASC with SetByCaller `DeterminationGain`.
   - A normal hit gives `DeterminationOnHitPerChargeLevel[GetCurrentDetermination()] × Axe_ChargePerStrikeScale ×
     WeaponChargeMultiplier`.
     - `DeterminationOnHitPerChargeLevel` = `[0, 3, 6, 9, 13, 17, 21]`
     - `GetCurrentDetermination()` = `Max(DeterminationOverride, TotalChargeLevel)`
     - `Axe_ChargePerStrikeScale` is 1 in every axe power row
   - Special hits and special-throw hits use other formulas: `15 × WeaponChargePerHitBonus`-style and
     `2 × WeaponChargeMultiplier` terms (**not read in detail**).
   - Hits without the `Combat.Weapon.WeaponImpact` tag add none of the weapon-impact part.
3. **When `Determination` ≥ `DeterminationMax`:** apply **`determination_ready_to_level_ge`**, which is Infinite and
   grants **`Combat.Determination.ReadyToLevel`** (cue `GameplayCue.Combat.Determination.ReadyToLevel`).

Other ways to fill or ready the meter:
- the skillshot special parts on a catch: `ga_special_skillshot_meter_bpc` applies `determination_ready_to_level_ge` or a
  `DeterminationGain`; `ga_special_skillshot_reset_bpc` gives `DeterminationGain` 15 (**not read in detail**)
- `grant_max_determination_ab` and the `cheat_add_determination_ab` cheat

## 3. Determination levels

`LevelUpDetermination` (in `ga_determination_ab`, server):
1. Picks the next level after the one the weapon has (`State.Determination.Lvl1` .. `Lvl5`), up to
   `Axe_MaxDeterminationLevel` + `BP_Axe_Weapon.BonusDeterminationLevels`. Every axe power row has
   `Axe_MaxDeterminationLevel` = 3, so **levels 1 to 3** without bonuses.
2. Adds that level's buff to the **weapon** (`TryAddBuff` on its buffable component): `ga_determination_meter_01_buff`
   .. `_05_buff`, each granting `State.Determination.LvlN`, with `MaxDuration` 150 s (replicated, buff rep index 10).
   The `ga_determination_cue_0N_ge` effects run the level visual cues, lasting 240 × `DeterminationDurationScale`.
3. Empties the meter: `scale_determination_ge` with SetByCaller `MultiplyDetermination` = 0.
4. Removes `Combat.Determination.ReadyToLevel`.

**What a level does.** `BP_Axe_Weapon.DamageMultValuesAtDeterminationLevels` = `[0, 0.125, 0.3, 0.5, 0.75]`
(the damage bonus by level, applied in `CustomModifyDamageEventData`, **not read in detail**). The catch attack's
`Notify_AxeDeterminationReadyDamageState` (`h_mr_ga_melee_RB_catch_attack_fwd_01_mot`, 1.261 s) picks its damage row by
level: `GA.Special.ThrownComboReadyLVL0` .. `LVL4`, by `State.Determination.Lvl1–4` (with
`Combat.Determination.ReadyToLevel` on).

`BP_Axe_Weapon` defaults `Determination Level Buff Times` [240, 180, 120] and `Max Determination Level` 2 are not
what `ga_determination_ab` uses (it uses 150 s and the power row's 3). Whether anything reads them was **not read in
detail**.

`Determination Special OnStart Cost` (6) and `Determination Special OnFinish Cost_0` (3) are replicated on
`BP_Axe_Weapon`, but no Blueprint in 2.1.1 reads them.

## 4. The special: throwing the axe

It is a combo move, not an ability. The special part does the work:

| Part | Component | Throw montage, part notify |
|---|---|---|
| `PART_GA_SPECIAL_DEFAULT` | `ga_special_default_bpc` | `h_mr_ga_melee_RB_throw_fwd_01_mot` at 0.7076 s |
| `PART_GA_SPECIAL_SKILLSHOT` | `ga_special_skillshot_meter_bpc` | `h_mr_ga_melee_RB_skillshot_throw_fwd_01_mot` at 1.1351 s |
| `PART_GA_SPECIAL_SKILLSHOT_RESET` | `ga_special_skillshot_reset_bpc` | the skillshot throw |

- **Default throw:** with authority, the part adds `ga_special_axe_throw_handler_buff` to the player. On the server it
  also adds a 1 s `ga_predictive_throw_buff`. The handler buff manages the thrown axe and the catch (**not read in
  detail**). The catch plays `h_mr_ga_melee_RB_catch_fwd_01_mot` or the catch attack (section 3).
- **Skillshot parts:** add their handler buff (`ga_special_skillshot_handler_buff`); on a catch before the axe hits the
  ground (`OnAxeCaughtBeforeHittingGround`) they fill or ready the meter and the meter part spawns a player-owned area
  effect (`aoe_simple_playerowned`, `OverrideDamageRow` set by the part) (**not read in detail**).

## 5. ActivateSpecialAbilities and the power rows

`BP_Axe_Weapon` calls `ActivateSpecialAbilities` once: **slot 0** (`RefSpecialAbilities1`), from
`CallWeaponSpecificFunction('ActivateSuper')`, through the server RPC `ActivateSuper`, only with authority. **No montage
in 2.1.1 calls `ActivateSuper`**, so the axe's slot-0 abilities are never started. This is the same as the sword.

Power rows (`weapon_progression_rework_power_table`, every `WP_GA_*` and `BP_AXE*` row):

| RefAbilities | RefSpecialAbilities1 | Rows |
|---|---|---|
| `ga_determination_ab` | `eb_q_projectile_raw_ab` | 12 |
| `ga_determination_ab` | `eb_shock_cone_ab` | 10 |
| `ga_determination_ab` | `eb_q_projectile_blaze_ab` | 8 |
| `ga_determination_ab` | `eb_radiant_cone_ab` / `eb_frost_cone_ab` | 4 each |
| `ga_determination_ab` | `axe_aoe_raw_ab` | 2 |
| `ga_determination_ab` | `eb_umbral_cone_ab` | 1 |
| `ga_determination_ab` | none | 1 |
| none | none | 1 (`BP_AXE_WEAPON`) |

Every row has `Axe_MaxDeterminationLevel` 3 and `Axe_ChargePerStrikeScale` 1; real items have
`Axe_DeterminationDurationScale` 1. **`ga_determination_ab` is the one ability that matters:** grant it to the pawn's
ASC as `weapon_power_and_abilities.md` describes.

## 6. Part buffs

| Part | Buff |
|---|---|
| `PART_GA_PASSIVE_CHARGE_AOE` | `ga_passive_charge_aoe_buff` |
| `PART_GA_PASSIVE_CHARGING_MOVESPEED` | `ga_passive_charging_movespeed_buff` |
| `PART_GA_PASSIVE_METER` | `ga_passive_meter_buff` |
| `PART_GA_PASSIVE_TRIALS_01` | `ga_passive_axe_out_tonics_up_buff` |
| `PART_GA_PASSIVE_TRIALS_02` | `ga_passive_perfect_charge_meter_buff` |

What each passive does was **not read in detail** (their code is in `pseudo/`). `BP_Axe_Weapon.PerfectChargeBuff`
(replicated) holds `ga_perfect_charge_handler_buff` (`CheckAddBuffHandler`).

## 7. What runs only on the server, and what the client reads

| Server only | What the client reads |
|---|---|
| `ga_determination_ab`: meter gain (`determination_ge`), ready (`determination_ready_to_level_ge`), level-up | the weapon ASC's `Determination` (and `DeterminationMax`) attributes; `Combat.Determination.ReadyToLevel` |
| level buffs on the weapon | `State.Determination.Lvl1–5`, the buffs' `MaxDuration`, the level cues |
| charge level bookkeeping (`OnChargeLevelChanged`, authority) | `Current Charge Level` (rep 21), `TotalChargeLevel` (rep 25) |
| move skill cost | `MoveSkillCost` (rep 18) |
| special throw handler buffs | the buffs |
| element colour | `ElementColor` (rep 26) |

Other replicated `BP_Axe_Weapon` fields: `PreviousChargeLevel`, `PerfectChargeBuff`, `Determination Special OnStart
Cost`, `Determination Special OnFinish Cost_0`. Their rep indexes were not found in the code (no
`MarkPropertyDirtyFromRepIndex` for them).

## What the server has to do

- [ ] Grant `ga_determination_ab` (the power row's `RefAbilities`) to the pawn's ASC, and run its logic on the server.
- [ ] Charge levels: when the combat system raises the charge level, set `Current Charge Level` (rep 21) and
      `TotalChargeLevel` (rep 25) as in section 1; clear both on `Notify_GAClearChargeLevel`.
- [ ] Meter: on each landed hit not tagged `Combat.Axe.AttackDoesntGrantMeter`, add
      `DeterminationOnHitPerChargeLevel[max(override, TotalChargeLevel)] × Axe_ChargePerStrikeScale ×
      WeaponChargeMultiplier` to the weapon's `Determination`.
- [ ] At `Determination` ≥ 12: apply `determination_ready_to_level_ge` (`Combat.Determination.ReadyToLevel`).
- [ ] With the ready tag on, the next hit levels up:
      - add the next `ga_determination_meter_0N_buff` to the weapon (150 s, up to level 3)
      - zero the meter and remove the ready tag
      - then ignore hits for 1 s
- [ ] Move skill cost: set `MoveSkillCost` (rep 18) to 25 during charge montages, 20 otherwise.
- [ ] The throw special: add the part's handler buff when the throw montage's part notify fires. The throw and catch
      flow itself still needs reading (**not read in detail**).
- [ ] Charged and catch damage rows: `GA.<move>.LVL<charge level>`, and `GA.Special.ThrownComboReadyLVL<n>` by
      determination level.
- [ ] Send the passive part buffs as buff actors on equip.
- [ ] Nothing for `ActivateSuper` / slot 0: nothing in 2.1.1 triggers it.
