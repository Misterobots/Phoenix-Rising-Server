# Swords on the server

What our server has to do for swords, built up the way `repeaters_server.md` was. Read from the 2.1.1 Blueprint code
and data, 2026-10-28:
- `BP_EB_Weapon`
- `weapon_meter_bpc`
- `eb_special_default_part_bpc` and `eb_special_crowbear_exotic_part_bpc`
- the Feast buffs
- the sword montages' notifies

How the weapon's power row and abilities work for every type is in `weapon_power_and_abilities.md`.

**The short version:** the special, the meter drain and the sword charges (pips) are all decided on the server. The
client only plays the animations and shows what the server sends. Live, 2026-10-28, with The Hunger:
- pressing the special sent `CharCombatComp.ServerStartCombo` with `PrimaryKey=8`
- the Feast animation and aura played
- the meter stayed full and no pips appeared, because the server did none of the steps below

## 1. Starting the special (Overdrive, or Feast on The Hunger)

It is a combo move, not an ability: no `ServerTryActivateAbility` is sent.

1. **The client starts the move.** With a full meter, the special key starts the combo (`ServerStartCombo`, key 8), and
   the client plays the special-activate montage `h_mr_eb_melee_RB_activate_fwd_01_mot`. The client checks the meter
   itself:
   - `eb_meter_ec`: the meter is at least its maximum
   - `BP_EB_Weapon.SpecialAttackReady`: the meter equals its maximum
2. **The montage activates the part.** At 0.2186 s, the notify `activate_weapon_part_gameplay_bpns`
   (`Weapon_Part_EB_Special`) calls the special part's `ActivateWeaponPartGameplay`. The Hunger's part derives from
   `eb_special_default_part_bpc`, so this applies to it.
3. **On the client** (not a dedicated server), the part only starts the super visual effects (`StartSuperVFX_Client`).
   That is the aura seen live.
4. **On the server** (`GetOwner().HasAuthority()`), the part:
   1. multicasts `OnSpecialActivated`
   2. applies **`special_active_ge`** to the **pawn's** ASC: Infinite, granting **`Combat.SpecialActive`**
   3. starts the meter draining: `MeterComponent.StartMeterDecay(Decay Rate)`
      - 5 per second (the default part's `Decay Rate`)
      - The Hunger uses the same 5; 4 with talent `weapon.talent.d24.sp05`
   4. adds **1 sword charge** at once (`GenerateSwordcharge(1)`), and starts a 0.2 s repeating timer that fills pips
      (section 3)
   5. **The Hunger only** (`eb_special_crowbear_exotic_part_bpc`): Feast on (`FeastOn`), the Hunger effects
      (`StartHungerVFX` multicast), and with talent `weapon.talent.d24.sp04`, **5 sword charges** at once.

**There is no up-front meter cost.** The spend is the drain.

These `BP_EB_Weapon` defaults are read by no Blueprint in 2.1.1; they are leftovers from the sword before the rework:
- `BASE SUPER DECAY RATE` (5; The Hunger's actor sets 1)
- `EMBER CHARGE SPECIAL ATTACK COST` (20)
- `Super Mode Attack Rate` (1.2)
- the `BASE CHARGE ...` values

## 2. The meter drain, and what ends the special

The meter is the weapon ASC's `Determination` attribute. `weapon_meter_bpc` sits on the sword actor.

- **`StartMeterDecay(rate)`** is a server RPC. It marks the meter as decaying at that rate.
- **Every tick while decaying** (`ReceiveTick` → `Auth_DecayMeter`): apply `determination_ge` to the **weapon's** ASC,
  with SetByCaller `DeterminationGain` = `-rate × DeltaSeconds`.
- **The drain speeds up.** `BP_EB_Weapon`'s meter has `MeterCompoundingDecay` on with `MeterCompoundingDecayRate` 0.2,
  so the rate itself grows by `0.2 × DeltaSeconds` each tick: 5/s at the start, 6/s after 5 s, and so on.
- **At 0:** `StopMeterDecay` and `OnStopMeterDecay`.

**The special ends** when the meter runs out. The part's `MeterEmpty` handler (bound to the meter component when the
part is equipped):
- removes `special_active_ge` (so `Combat.SpecialActive` goes)
- multicasts `Multi_DisableSuperModeEffects`
- stops the pip timer

It also ends early on unequip (`EndEarly`). The Hunger also empties the meter (`EmptyMeter`) when combat ends, when no
threat is left at the encounter.

## 3. Sword charges (the pips)

Stored on the sword actor, replicated:

| Field | Meaning |
|---|---|
| `CurrentSwordcharges` (int, schema rep index 21) | Pips held. **Maximum 6** (`MaxSwordcharges`; the add clamps to 6) |
| `SwordchargeFillPercent` (double, rep index 22) | Progress towards the next pip, 0 to 1 |

**Only the server changes them.** `GenerateSwordcharge(n)` and `RemoveSwordcharges(n)` both check `IsServer`, mark the
field dirty, and call `ReplicatedSwordchargesToUI`. With the passive tag `Combat.Sword.Passive.SwordchargeHealing`,
every pip gained or spent also heals the player for 30.

**Gaining pips:**
- **From hits.** When the player's hit lands, `BP_EB_Weapon` gets the hit's damage tags (the same event that feeds the
  meter, `HandleMeterGain`). If the tags include **`Combat.Sword.TierAdvancer`** and pips are ready:
  - `Combat.Sword.TokenGranter`: +1 pip
  - `Combat.Sword.TokenGranterTwo`: +2 pips
  - then pips are blocked for 0.25 s (`SwordchargeReady`, reset by a timer)

  The tags come from the attack's damage row (`DamageTable` tags).
- **During the special.** The part's 0.2 s timer calls `AddPercentPipFill(0.05)`: +5% fill each time, so **one pip
  every 4 seconds** (+1 when `SwordchargeFillPercent` reaches 1).
- **Other sources:**
  - the passive `eb_passive_rework_regenerating_projectile_regen_buff` adds 6.67% fill
  - the parry special adds pips: `GenerateSwordcharge(1)` in two places of its code (the parry branches, not read in
    detail)
  - the spin special fills like Overdrive

**`eb_swordcharge_stacks_ge`** (tag `Combat.Sword.HasComboToken`) is applied by no Blueprint in 2.1.1. Pips are the
replicated integer, not an effect.

**Spending pips.** A move's montage notify `call_weapon_function_bpn` calls `CallWeaponSpecificFunction(FunctionName)`
on the weapon, which removes pips on the server:

| Montage | Notify | At | Pips |
|---|---|---|---|
| `h_mr_eb_melee_X6_fwd_01_mot` | `SpendOneToken` | 0.257 s | 1 |
| `h_mr_eb_melee_X1_hold_fwd_01_mot` (dash) | `SpendThreeTokens` | 0 s | 3 |
| `h_mr_eb_melee_Y1_hold_fwd_01_mot1` | `SpendHoldTokens` (+ `FireSlicer`) | 0.475 s | 3, or 1 with `Combat.Sword.Passive.HoldReduction` |
| `h_mr_eb_melee_Y4_aoe_fwd_01_mot` | `SpendThreeTokens` (+ `StabGround`) | 1.108 s | 3 |
| `h_mr_eb_melee_Y5_fwd_01_mot` | `SpendThreeTokens` | 0.610 s | 3 |
| `h_mr_eb_melee_Y5_fwd_02_mot` | `SpendThreeTokens` | 0.287 s | 3 |
| `h_mr_eb_melee_RB_dash_crowbear_fwd_01_mot` (The Hunger's Umbral Flight) | `SpendOneToken` | 0 s | 1 |

The client only lets these moves start when there are enough pips. `eb_combo_swordcharges_available_ec`
(`TokensRequired` 1 or 3) compares `GetNumSwordcharges()`, that is `CurrentSwordcharges`.

## 4. Feast (The Hunger)

Its three part buffs (`weapon_part_catalog[PART_EB_SPECIAL_CROWBEAR_EXOTIC].Buffs`) are buff actors on the player. All
three switch on and off with **`Combat.SpecialActive`**:

| Buff | While Feast is on |
|---|---|
| `eb_special_attack_speed_buff` | `eb_special_attack_speed_ge` (Infinite): `CombatPlayRateRateModifier` + `AttackSpeedIncrease` 0.2 |
| `ce_crowbear_eb_exotic_drain_lifesteal` | life drain: 7 damage per second to the player (tags `DamageType.Self`, `DamageType.Dot`, `Stagger.None`); `ce_crowbear_eb_exotic_damage_ge` with SetByCaller `Magnitude` = `DamageBonus` 0.25; replicates `IsLifeDrainActive` |
| `crowbear_eb_exotic_lifesteal_buff` | heals 12% (`LifestealPercent`) of the damage the player deals after mitigation |

The Feast part also adds an umbral area effect to attacks (`SpawnAoE`, on the server). It handles the behemoth's death
(`BehemothDeath`, not read in detail).

## 5. The dash (Aether Dash; Umbral Flight on The Hunger)

While the special is active, the special key plays the dash montage, which activates the part at 0 s.
- `h_mr_eb_melee_RB_dash_fwd_01_mot` is the default dash.
- The Hunger's is `h_mr_eb_melee_RB_dash_crowbear_fwd_01_mot`, which **spends 1 pip**.

**Dash cooldown:** the part's `StartDashCooldown`, on the server, applies `eb_special_attack_dash_cooldown_ge` to the
pawn's ASC, with SetByCaller `Duration` = the part's `Cooldown`. It grants `Combat.Sword.DashCooldown`.
- It only applies when `Cooldown` > 0.
- The parry part sets 2 s.
- The default and Crowbear parts leave it at 0, so there is no cooldown effect. The Hunger's dash is limited by its
  pip cost instead.

## 6. What runs only on the server, and what the client reads

| Server only (our server must do it) | What the client reads to show it |
|---|---|
| `special_active_ge` on the pawn's ASC | `Combat.SpecialActive` on the pawn's ASC (`InSuperMode`, `IsSpecialActive`; the buffs) |
| meter drain: `determination_ge` on the weapon's ASC each tick | the weapon ASC's `Determination` attribute (the meter bar) |
| adding and removing pips (`IsServer`) | `CurrentSwordcharges` (rep index 21) and `SwordchargeFillPercent` (rep index 22) on the sword actor |
| the Feast buffs (buff actors) | the buffs, `IsLifeDrainActive`, the damage and attack-speed effects |
| dash cooldown effect | `Combat.Sword.DashCooldown` |
| `OnSpecialActivated`, `StartHungerVFX`, `Multi_DisableSuperModeEffects` multicasts | the effects they start and stop |

`BP_EB_Weapon` also replicates `AoEEmpowered` (rep index 24) and `ProjectileEmpowered` (rep index 25), used by the
rework passives.

## What the server has to do

- [ ] Keep the meter (done: `weapon_meter.py`).
- [ ] Track the special per player. When `ServerStartCombo` (key 8) enters the special-activate move, at 0.2186 s:
      - apply `special_active_ge` (`Combat.SpecialActive`) to the pawn's ASC and multicast `OnSpecialActivated`
      - give 1 pip (5 more for The Hunger with `sp04`)
      - start the drain: the weapon ASC's `Determination` falls by `rate × dt`, starting at `Decay Rate` (5; 4 with
        `sp05`) and growing by 0.2/s
      - start the pip fill: +0.05 every 0.2 s
- [ ] When the meter reaches 0: remove `special_active_ge`, stop the fill, multicast `Multi_DisableSuperModeEffects`.
      The Hunger also empties the meter when combat ends.
- [ ] Pips from hits: on each landed hit whose damage row's tags include `Combat.Sword.TierAdvancer`, +1
      (`TokenGranter`) or +2 (`TokenGranterTwo`), at most once per 0.25 s; at most 6 pips.
- [ ] Pip spend: when a move from the table in section 3 plays, remove its pips at the notify time.
- [ ] Send `CurrentSwordcharges` and `SwordchargeFillPercent` on the sword actor whenever they change.
- [ ] Send the special part's buffs as buff actors on equip (The Hunger: attack speed, drain and damage bonus,
      lifesteal). They react to `Combat.SpecialActive` on their own.
- [ ] Dash cooldown effect when the part's `Cooldown` > 0 (parry 2 s).
- [ ] Grant the sword's power-row abilities to the pawn's ASC (`eb_charge_ab` for the beginner sword). The Hunger and
      `WP_EB_SHOCK_01` have none.
