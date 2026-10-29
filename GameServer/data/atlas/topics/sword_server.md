# Swords on the server

What our server has to do for swords, built up the way `repeaters_server.md` was. This first part covers the
special: Overdrive, and Feast on The Hunger. Read from the 2.1.1 Blueprint code and data, 2026-10-28. How the
weapon's power row and abilities work for every type is in `weapon_power_and_abilities.md`.

## The sword special is part gameplay, not an ability

Pressing the special button with a full meter does **not** send `ServerTryActivateAbility`. Nothing activates the
sword's ability slots (`weapon_power_and_abilities.md`, section 4). Instead the special is a **move**.

**1. The client starts the move.** The special button moves the combo into the special-activate state. The client
sends `ServerStartNextMove` into it, and the state's montage plays: `h_mr_eb_melee_RB_activate_fwd_01_mot`. The client
only allows this when the meter is full:
- `eb_meter_ec`: the meter is at least its maximum (`eblade_rework_base_animstateset`)
- `BP_EB_Weapon.SpecialAttackReady`: the meter equals its maximum

The meter is the weapon ASC's `Determination` attribute, which the server already sends.

**2. The montage activates the part.** At 0.2186 s, the move's notify `activate_weapon_part_gameplay_bpns`
(`WeaponPartToActivate` = `Weapon_Part_EB_Special`) calls the special part's `ActivateWeaponPartGameplay`.

**3. The part does the rest on the server.** In `eb_special_default_part_bpc`, which The Hunger's part
`eb_special_crowbear_exotic_part_bpc` derives from, the authority branch (`GetOwner().HasAuthority()`):
1. multicasts `OnSpecialActivated`
2. applies **`special_active_ge`** to the **pawn's** ASC: Infinite, granting **`Combat.SpecialActive`**
3. starts the meter draining: `MeterComponent.StartMeterDecay(Decay Rate)`
   - 5 per second (`eb_special_default_part_bpc`)
   - The Hunger uses the same 5; 4 with talent `weapon.talent.d24.sp05`
4. starts sword-charge generation (`GenerateSwordCharge` every 0.2 s)

**4. The special ends when the meter is empty.** `MeterEmpty` removes `special_active_ge` and multicasts
`Multi_DisableSuperModeEffects`. The meter spend is that decay; there is no up-front cost.

## Feast (The Hunger)

`eb_special_crowbear_exotic_part_bpc` adds, on the server (`IsDedicatedServer`):
- `FeastOn`, the Hunger visual effects (`StartHungerVFX` / `EndHungerVFX` multicasts) and the weapon's eye animation
- an umbral area effect on its attacks (`SpawnAoE`)
- the meter emptied when combat ends (`EmptyMeter` when no threat is left near the encounter); it also handles the
  behemoth's death (`BehemothDeath`, not read in detail)

Its three part buffs are on the weapon from equip (`weapon_part_catalog[PART_EB_SPECIAL_CROWBEAR_EXOTIC].Buffs`). All
three switch on with **`Combat.SpecialActive`**:
- `eb_special_attack_speed_buff`: attack speed, watching `Combat.SpecialActive`
- `ce_crowbear_eb_exotic_drain_lifesteal`: the health drain, watching `Combat.SpecialActive`
- `crowbear_eb_exotic_lifesteal_buff`: 12% lifesteal (`LifestealPercent` 0.12)

So the one effect that turns Feast on is `special_active_ge` on the pawn.

## Aether Dash / Umbral Flight

While the special is active, the special button plays `h_mr_eb_melee_RB_dash_fwd_01_mot`. Its notify activates the
same part at 0 s.

- **Cooldown:** on the server, the part's `StartDashCooldown` applies **`eb_special_attack_dash_cooldown_ge`** to the
  pawn's ASC, with SetByCaller `Duration` = the part's `Cooldown`. It grants `Combat.Sword.DashCooldown`.
- **The cooldown only applies when `Cooldown` > 0.** The parry part sets it to 2 s (and `AbilityStartsOnCooldown`).
- **For the default and Crowbear parts, `Cooldown` is not in the cooked class defaults**, which means 0: no cooldown
  effect is applied.

## What the server has to do

- [ ] Keep the meter (done: `weapon_meter.py`) and let the client's own check allow the special move.
- [ ] When the client's `ServerStartNextMove` enters the special-activate state (the `RB_activate` montage), act as the
      part's authority branch at 0.2186 s:
      - apply `special_active_ge` (`Combat.SpecialActive`) to the pawn's ASC
      - multicast `OnSpecialActivated`
      - drain the meter at `Decay Rate` per second (5; 4 with `sp05`)
- [ ] When the meter reaches 0: remove `special_active_ge` and multicast `Multi_DisableSuperModeEffects`.
- [ ] Send the part's buffs as buff actors on equip. They react to `Combat.SpecialActive` on their own. The Hunger
      also needs `FeastOn` behaviour (Hunger effects, umbral area effect, meter emptied out of combat).
- [ ] Dash: when the `RB_dash` move starts during the special, apply `eb_special_attack_dash_cooldown_ge` with the
      part's `Cooldown`, if it is above 0 (parry 2 s; default and Crowbear 0).
- [ ] Grant the sword's power-row abilities to the pawn's ASC (`eb_charge_ab` for the beginner sword). The Hunger and
      `WP_EB_SHOCK_01` have none.
