# Armor in the 2.1.1 client

What armor does, read from the 2.1.1 exe (capstone disassembly, image base 0x140000000), the Blueprint and
effect defaults in the paks, and the exported data tables (`debug/exports/all-datatables-211`). Addresses are
offsets from the image base; `native_notes.json` has a note for each function named here. Anything not read
directly is marked **unconfirmed**. The data itself (every armor item with its perks per level, set bonuses,
gear stats, keyword perks, cell effects and their classes) is in `weapons/armor.txt`.

In short: armor gives **flat resistance** (global plus per element). Resistance lowers the attacker's
*power − resistance* number, and a curve on the **attacker** turns that number into a damage multiplier.
Armor also brings **perks** (permanent cells), one **gear stat** and a **set bonus** id. The game applies all
of it on the server; the client only receives the results.

## 1. Armor stats

### Where the numbers come from

Each armor actor (`AArchonArmor`, Blueprint base `armour_part_base_bp_C`) adds one gameplay effect,
`armour_ge_C`, to its owner. It is infinite, and its modifiers add SetByCaller values to `ArchonHealthAttributeSet`:

| SetByCaller name | Attribute |
|---|---|
| `GlobalResistance` | `GlobalResistance` |
| `FlameResistance` … `TerraResistance` | the matching `<Element>Resistance` |

The values come from one row of a resistance table (`FArmourResistanceTableData`):

- **Row name** = the armor actor's `ItemId` (replicated `ArchonEquipment.ItemId`, schema rep index 11),
  e.g. `AR_LERAWR_HELM`.
- **Level** = the armor actor's `CurrentLevel` (`ArchonArmorSetItem.CurrentLevel`, `EUpgradeableItemLevel`,
  replicated, schema rep index 14).
- **Table** = `ArchonArmor.ResistanceTable`, set by `AArchonArmor::CacheResistanceTableData` (0x26A1290) to
  `ArchonGlobalTuning.GetArmourResistanceTable(CurrentLevel)` (0x27FA650). The armor Blueprint's own default
  is ignored. The global tuning asset is `bp_archon_global_tuning`, and the table depends on two feature flags:

| Flag state | Table |
|---|---|
| `ArmourProgressionReworkFeature` on | `/Game/Gameplay/armour_progression_rework_resistance_table` |
| that flag off, `ProgressionReworkFeature` on | per level: `armour_resistance_table` (Level_00), `armour_resistance_table_level_01`, … |
| both off | `armour_resistance_table_obprog` |

Both flags use the shared `UFeatureFlag` constructor (0x228B9B0), which sets `bEnabled = true`, and the paks'
`DefaultGame.ini` doesn't override them. So locally both are **on**, and the rework table is the one in use.
**Unconfirmed:** whether a remote feature-flag override (the `PhxFeatureFlags` plugin; `IsFeatureEnabled`
0x14D45F0 checks a game-instance subsystem) turns them off.

### Value for one element at item level L

From `FArmourResistanceTableData` element getter 0x26CDA70. Element index 8 means Global; 1–6 are Flame,
Frost, Shock, Radiant, Umbral, Terra. Overrides come from `GlobalResistancePerLevelOverrides` for Global and
from `ElementalResistancePerLevelOverrides` for every element.

```
value = Base
      + PerLevelBonuses[L]              (0 if L is outside the array)
      + growth, where growth walks the overrides in order:
            rate = PerLevel; prev = 0
            for (StartingLevel, NewPerLevelValue) in overrides:
                if StartingLevel <= L:
                    c = max(StartingLevel - 1, 0)
                    growth += max(c - prev, 0) * rate
                    prev = c; rate = NewPerLevelValue
            growth += max(L - prev, 0) * rate
```

Embermane (`AR_LERAWR_HELM`/`CHEST`/`ARMS`/`LEGS`) in the rework table: every base and per-level value is 0,
so the value is the bonus array:

| L | 0 | 1 | 2 | 3 | 4 | 5 | 9 | 14 | 19 |
|---|---|---|---|---|---|---|---|---|---|
| Global | 10 | 20 | 30 | 40 | 50 | 60 | 100 | 125 | 150 |
| Flame | 2 | 3 | 5 | 7 | 8 | 10 | 17 | 20 | 25 |
| Frost | −2 | −3 | −5 | −7 | −8 | −10 | −17 | −20 | −25 |

The same pieces in the legacy table give Global 5, Flame 5, Frost −5 each.

The armor effect is built in `AArchonArmor` 0x269E1C0, a virtual called when the armor is set up on its
owner; the exact caller isn't traced:

- Returns at once when net mode is client (value 3), so only the server applies it.
- Makes the spec at level `AbilityLevel` and sets the 7 SetByCaller values.
- Adds the row's `GrantedTags` to the spec's dynamic granted tags and its `AssetTags` to the spec's asset tags.
- Applies the spec to the owner's ASC and keeps the handle at armor actor +0x3B8.
- The row's `Abilities` array is granted too. For the Embermane rows it is empty.

### Attribute set and defaults

Everything lands in `ArchonHealthAttributeSet`, whose attributes are plain replicated floats. The set's
constructor (0x2609070) gives these defaults:

- `*MultiplicativeResistance` = 1.0
- `*SelfPostMigationMultiplier` = 1.0
- flat resistances = 0
- `HealRadius` = 500

Armor gives no health or defense through `armour_ge`. Health from armor comes only through perks, such as
`KW_HEALTH_MAX`.

### Player progression effect (adds 0 in 2.1.1)

`AArchonCharacter::InternalUpdateProgressionBonuses` (0x2686380) runs on the server only, behind
`ProgressionReworkFeature`, and only when all six gear slots are filled. It applies
`player_progression_ge_C`, which adds the SetByCaller values `GlobalPower` and `GlobalResistance`:

```
GlobalPower      = BasePower      + level * AddedPowerPerLevel
GlobalResistance = BaseResistance + level * AddedResistancePerLevel
```

The values come from `player_progression_tuning` row `ONLY_ROW`. In 2.1.1 all four are 0, so this effect adds
nothing.

## 2. How a hit on a player is reduced

Behemoth hits use the gameplay effect `behemoth_damage_ge_C`. Its execution is
`UArchonDamage::Execute_Implementation` (0x261CB70; vtable 0x6893DF8 slot 88). The same math also exists as
named statics, which the execution inlines or calls:

- `ArchonDamageStatics.ComputeResistanceDeltas` (0x2615E70)
- `ArchonDamageStatics.ComputeDamageMultipliers` (0x26153C0)
- `ArchonDamageStatics.ComputePostMitigationDamages` (0x2615500, which calls `MitigateAndScaleDamage` at 0x26DED40)

**Attributes captured:**

- From the **attacker** (`ArchonDamageAttributeSet`): `*Damage`, `*Power`, `*TargetPostMigationMultiplier`,
  crit and lifesteal values.
- From the **target** (`ArchonHealthAttributeSet`): `CurrentHealth`, `MaxHealth`, `CurrentShield`,
  `*Resistance`, `*MultiplicativeResistance`, `*SelfPostMigationMultiplier`.

Elements are numbered 0 Physical, 1 Flame, 2 Frost, 3 Shock, 4 Radiant, 5 Umbral, 6 Terra and 7 Raw. Raw is
`RawElementalDamage`; it uses the physical power, resistance and multiplicative resistance, but the elemental
curve.

**Step 1: damage per element.** `behemoth_damage_ge` passes the SetByCaller values `PhysicalDamage`,
`FlameDamage`, …, `RawElementalDamage`, `BaseDamage`, `TrueDamage`, `ElementalHealthDamageMod` and
`Explicit*`. `BaseDamage` is added to the attack's own element slot, or to physical if it has none.

**Step 2: resistance delta** (`ComputeResistanceDeltas`):

```
elemental(i) = i in 1..6
power_i  = Power_i      + (elemental(i) ? ElementalPower      : 0)        # attacker
resist_i = Resistance_i + (elemental(i) ? ElementalResistance : 0) + GlobalResistance   # target
delta_i  = power_i * MultiplicativeResistance_i(target) + GlobalPower(attacker) - resist_i
```

**Step 3: multiplier from a curve on the attacker** (`ComputeDamageMultipliers`). The curves are properties of
the attacker's `ArchonAbilitySystemComponent`:

```
curve = (i == 0) ? DamageCurve (+0x13D8) : ElementalDamageCurve (+0x13E0, falls back to DamageCurve)
if MultiplicativeResistance_i(target) > 1 and ElementalWeaknessCurve (+0x13E8): curve = ElementalWeaknessCurve
if MultiplicativeResistance_i(target) < 1 and ElementalStrengthCurve (+0x13F0): curve = ElementalStrengthCurve
mult_i = curve ? curve(delta_i) : 1.0
```

If the attacker's ASC has no `DamageCurve`, the `UArchonDamage` object's own `DamageCurve` (+0x40) is used
instead.

| Attacker | DamageCurve / ElementalDamageCurve |
|---|---|
| Behemoths (`Bp_Behemoth` ASC) | `/Game/shared/curves/behemoth_to_player_damage_curve_cr20` (both) |
| Small creatures (`base_creature_bp`) | `creature_to_player_damage_curve_cr20` (same keys) |
| Players (`BP_PlayerCharacter` ASC) | `player_to_behemoth_damage_curve_cr20` (also used as the weakness and strength curves) |

Keys of `behemoth_to_player_damage_curve_cr20`, linear interpolation, delta → multiplier:
−500 → 0.5, −100 → 0.8, 0 → 1.0, 100 → 1.5, 200 → 3.0, 350 → 5.3, 500 → 10.0.

`BPGS_ArchonExploration_GameState` defines override curves, including
`behemoth_to_player_exploration_dmgcurve`. Its `bShouldOverrideDamageCurves` is not set in the asset
(default false), so the overrides are off.

**Step 4: post-mitigation multipliers** (`MitigateAndScaleDamage` 0x26DED40):

```
d_i = mult_i * damage_i + add_i                                # add_i = 0 by default
physical = d_0 * (GSelf + Self_0 - 1) * (GTarget + Target_0 + A - 1) * M
element  = d_i * (GSelf + Self_i - 1 + ESelf - 1) * (GTarget + Target_i + A - 1 + ETarget - 1) * M   (i = 1..7)
```

- **Self** = the target's `*SelfPostMigationMultiplier`: `Global…` is GSelf, `Elemental…` is ESelf, and the
  per-element ones are Self_i (index 7 uses 1.0).
- **Target** = the attacker's `*TargetPostMigationMultiplier`, read the same way.
- Every multiplier stacks additively as (m − 1). Defence buffs use this: `base_defense_perm_ge` multiplies
  `GlobalSelfPostMigationMultiplier` by 0.95 per stack, up to 20 stacks.
- **A** is `FDamageEventData.AdditiveDamageMultiplier` and **M** is `MultiplicativeDamageMultiplier`.
  They are 0 and 1 unless a pre-mitigation hook changes them (next section).

**Pre-mitigation hooks (the "extended branch").** Between steps 3 and 4 the execution packs everything so far
into an `FDamageEventData` (per-element `PreMitigationDamages`, `Multipliers`, `ResistanceDeltas`,
`PostResistDamages`, `TrueDamage`, `Explicit*`, `CoreDamageFromBP`, A and M) and offers it to two hooks:

1. The **attacker**, if it implements `ArchonDamageGiverInterface` (native virtual, which calls the Blueprint
   event `OnPreMitigateOutgoingDamageBP`).
2. The **target**, if it implements `ArchonDamageTakerInterface`: the native virtual (on `AArchonCharacter`,
   thunk 0x268AC70, which calls `OnPreMitigateDamageBP`), or the Blueprint-only interface event
   `OnPremitigateDamageNonNative` (called through 0x22EF850).

Each hook returns new data and sets `bOverrideDamageData`. If either one set it, 0x262E1B0 copies the returned
struct back over the execution's values. That function is only a field-by-field copy; there is no separate
part, stagger or crit math in it. The hook can therefore replace any of those values, including A, M,
`TrueDamage` and the per-element damages.

Who sets the flag:

| Who | When it overrides |
|---|---|
| Small creatures (`base_creature_bp.OnPreMitigateOutgoingDamageBP`) | **Always.** Sets M = M × (1 + max(ConsideringLevel − 1, 0) × `AttackDamagePercentBonusPerCreatureLevel`); the class default is 0.03, so +3% per creature level above 1 |
| Behemoths (`Bp_Behemoth.OnPreMitigateOutgoingDamageBP`) | Only if `behemoth_eventdispatcher_bpc.TryModifyOutgoingDamage` reports a change, i.e. some ability or buff registered an outgoing-damage modifier |
| Players as targets (`BP_PlayerCharacter.OnPreMitigateDamageBP`) | Only if `player_eventdispatcher_bpc.TryModifyAnyIncomingDamagePremit` (or, for some damage tags, `TryModifyIncomingStaggerDamagePremit`) reports a change. Modifiers are registered by buffs (`base_buff.RegisterDamageModifiers`) |
| Other Blueprint actors (`base_gameobject_bp`, `behemoth_umbral_debuff_cyst`) | Never (`bOverrideDamageData = false`) |

So for a plain behemoth hit on a player with no such buffs active, A = 0, M = 1 and nothing is replaced.
`CoreDamageFromBP` goes only into the damage summary and the player-state damage stats; it is not added to
the total.

**Step 5: total and application:**

```
total = max(0, physical + sum(element) + RawDamage + TrueDamage)
if CurrentHealth(target) <= 0: total = 0
```

- **Effect filter:** `behemoth_damage_ge` has no tag filter, but `damage_ge` (weapon hits) ignores targets
  tagged `Cheat.God`, `State.Bleedout`, `State.InvulnerablePotion` or `State.InvulnerableEdgeRecovery`.
- **Death prevention:** if a "can't die" check passes (tags, or an interface call on the target), the damage is
  capped at health + shield − 1.
- **Server only:** the split below runs only when a role byte on the target avatar equals 3 (probably
  `ROLE_Authority`).
- **Shield first:** `shieldPart = clamp(min(total, CurrentShield), 0)`, then
  `healthPart = clamp(min(total - shieldPart, CurrentHealth), 0)`. A shield-bypass tag counts the shield as 0.
- **Output:** the execution writes −shieldPart to `CurrentShield` and −healthPart to `CurrentHealth`.

`ShouldIgnorePowerAndResistanceMultipliers` always returns false in 2.1.1.

### Other defence attributes on `ArchonHealthAttributeSet`

The execution captures none of these. Where they are used, searched over every package in the paks and every
string reference in the exe (2026-10-26):

| Attribute (offset) | Used by |
|---|---|
| `ShieldDamageReduction` (0x144) | **Behemoth shields only.** `monsterpart_bpc.OnPreMitigateDamageBP`: when the owning behemoth has `CurrentShield` > 0 and isn't `State.Invulnerable`, shield damage = `BaseDamage` × lerp(max(1 − ShieldDamageReduction, 0), 1, attacker's `ShieldDamageReductionPenetration`), or × 1 with the tag `Combat.DamageProperties.IgnoreShieldDamageReduction`. It lowers the shield by that through `cell_shield_ge` and absorbs the whole hit. Also named by `host_shield_ge`, `moonface_beta_bp`, `sq_sally_archetype_bp`, `Bp_Behemoth` and the damage tables. |
| `ChanceToReduceIncomingDamage` (0x154) | **Nothing.** No package names it; in the exe only the replication setup and its `OnRep` refer to it. |
| `DamageReturnPercent` (0x158) | **Nothing**, same as above. |
| `IncomingShieldMultiplier` (0x148) | **Nothing** in the paks; only replication and `OnRep` in the exe. |

None of the health attribute set's own virtual functions reads these four fields. The server can ignore them
for hits on players.

### Worked example

Attacker: Embermane normal (`lerawr_normal_bp`). It has GlobalPower 100 and GlobalResistance 250, and inherits
FlamePower 1.0 from `lerawr_archetype_bp`. `AArchonBehemoth` 0x265B2C0 writes these class defaults straight
into the behemoth's attributes with `SetNumericAttributeBase`.

Target: a player wearing four Embermane pieces at item level 0, so Global 40, Flame 8 and Frost −8.

| Hit | Delta | Multiplier |
|---|---|---|
| Physical, wearing the armor | 0·1 + 100 − (40 + 0) = 60 | 1.3 |
| Physical, no armor | 100 | 1.5 |
| Flame, wearing the armor | (1 + 0)·1 + 100 − (40 + 8) = 53 | about 1.27 |

The hunt's tuning presets then multiply every behemoth hit by their combined `DamageScale` (as GTarget in
step 4), and a hunt `PowerOverride` replaces the behemoth's Global power and resistance. Threat levels never
change power, and star levels change nothing. See `topics/behemoth_scaling.md`.

## 3. Perks, cells and other effects

These are taken from the armor's catalog row (`armour_gameplay_catalog`, `FPlayFabArmorCatalogTableData`).

**Permanent cells.** `PermanentCellsPerLevel` lists, for each `ItemLevel`, `PermanentCellEffects` (rows of
`keyword_cell_effects_composite`) and `RanksGranted`. Embermane at Level_00:

| Piece | Perk |
|---|---|
| Helm | `KW_DODGE_IFRAME` ×1 |
| Arms | `KW_DODGE_IFRAME` ×1 |
| Chest | `KW_KILL_FAUNA_DMG` ×1 |
| Legs | `KW_KILL_FAUNA_DMG` ×1 |

Later levels list other sets. Reading each level entry as the full list for that level (rather than an
addition) is **unconfirmed**. A keyword works by rank: `KW_DODGE_IFRAME` (Evasion) grants
`CELL_EFFECT_IFRAME_DURATION` with magnitude 0.4 only from rank 4. That effect applies `cell_iframe_duration_ge`,
which adds SetByCaller `Magnitude` to `EvadeInvulnerabilityMultiplier`. With two Embermane pieces giving the
perk, the rank is 2, so it does nothing. Every armor piece's perks per level, every keyword perk's rank grants
and every cell effect's buffs, effects and abilities are listed in `weapons/armor.txt`.

**Socketed cells.** `CellUnlockPattern` sets the slots, e.g. `CELL_UNLOCK_PATTERN_ANY`. The cells sit on the
inventory item (`ArchonInventoryItem_CellContainer`) for each loadout.

**How the game applies them** (server only):

1. `AArchonCharacter` 0x2694A20 calls `ArchonCharacter.GetAllEquippedCellEffects` (0x2680E00). That
   function finds the inventory item for each of the four armor slots, the lantern and the weapon, and asks
   each item for its cell effects under the current loadout (0x2681640 → 0x28BFEA0). Whether this includes
   the permanent cells is **unconfirmed**, since 0x28BFEA0 was not decompiled.
2. It passes the result to `cell_manager_bpc_C.ApplyCellBonuses`, a `BlueprintAuthorityOnly` event, through
   the native wrapper 0x22C73F0.
3. The Blueprint applies each effect's `GameplayEffects`/`Abilities`/`Buffs` and writes the replicated
   `CurrentEffectsBeingApplied` (schema rep index 2).
4. On the client, `OnRep_CurrentEffectsBeingApplied` only fires `Client_CellEffectsChanged`, for the UI.

It runs from `HandleActiveGameplayStateChanged` and from loadout changes.

**Gear stat.** `PrimaryGearStat` (`FGearStatTuningData`) names a `gear_stat_table` row with `BaseMagnitude`
and `PerLevelMagnitude`. Embermane helm and arms give `GENERAL_CRIT_DAMAGE` (`CriticalStrikeBonusDamage`)
0.2 + 0.01/level; chest and legs give `GENERAL_CRIT_CHANCE` 0.05 + 0.005/level. How it is applied is in
section 4, because the same function also does the set bonus.

## 4. Gear stats and set bonuses: `AArchonEquipment::AddGearStatsToOwner`

Both are applied by `AArchonEquipment::AddGearStatsToOwner` (body 0x27F1400; the reflected
`ArchonEquipment.AddGearStatsToOwner` thunk 0x22F4E40 jumps to it). No Blueprint calls it. The native caller
is `AArchonEquipment::HandleEquipForGameplay` (0x28018D0), which refuses to run on a client ("Attempt to
equip for gameplay on the client"), so this is server-only.

**Gate: `GearCells2Feature`.** The function first checks this feature flag and returns at once if it is off.
Its constructor (0x2292B10) sets `bEnabled = false` (`bVisibleInUI = true`), unlike most flags. No ini file
in the paks mentions it. So in the stock client **gear stats and set bonuses are both off** unless a remote
feature-flag override turns `GearCells2Feature` on. Our backend now plays that role.

When the flag is on:

1. It skips some owner classes (one class check, not identified) and refuses to run twice ("Attempt to apply
   gear stats multiple times").
2. It needs the owner's ability system component and the owner's `ArchonPlayerController`. It looks up the
   inventory item by the equipment's `ItemId`.
3. **Gear stats:** `AppliedGearStats` = the item's gear stats (0x28C43D0; `FGearStatData`: `DisplayName`,
   `Attributes[]`, `Magnitude`). For every stat, and every attribute in it, it calls
   `UAbilitySystemComponent::ApplyModToAttribute(attribute, Additive, Magnitude)` (0xDBBE30). That adds
   `Magnitude` straight onto the attribute's **base value**; there is no gameplay effect. How the item turns
   `BaseMagnitude`/`PerLevelMagnitude` into `Magnitude` (0x28C43D0) was not read. Base + level × per-level
   is the obvious reading but **unconfirmed**.
4. **Set bonus:** `AppliedGearSetBonusId` = the item's set bonus row. If it is set, it calls
   `AArchonCharacter::AddGearSetBonusSource(SetBonusId, ItemId)` (0x26728D0). That forwards to
   `UGearSetBonusComponent::AddGearSetBonusSource` (0x276A960):
   - Find the `EquippedGearSets` entry for that set, or add one.
   - Add the item id to its `SourceIds` (one per distinct item), so count = number of distinct pieces.
   - Read the `gear_set_bonus_table` row (`FGearSetBonusTableData.GearSetBonuses[]`; each `FGearSetBonus`
     has `Description`, `RequiredSources` and `GameplayModifiers`).
   - For every tier with `RequiredSources` ≤ count that isn't in `AppliedEffectsMap` yet, create a
     `UArchonGameplayModifierGroup`, apply the tier's `GameplayModifiers` (effects, buffs, abilities) to the
     owner, and store it under `RequiredSources`.
5. **Damage modifiers:** finally it registers the item's damage modifiers with the owner and keeps the handle
   in `AppliedDamageModifiersHandle` (details not read).

`RemoveGearStatsFromOwner` (thunk 0x22F6210) undoes this on unequip. It was not decompiled.

Set bonus data:

- **LERAWR:** the 2-piece and 4-piece entries are empty (placeholder text, no effects, buffs, abilities or
  keywords). Embermane has no set bonus in 2.1.1.
- **Rows with effects:** only BUGBEAR (2-piece), CROWBEAR (2), ELECTROQUILL (4), FULGAR (4) and SNOWFLAKE (4),
  one tier each; the other 22 of the 27 sets (BEGINNER has no tiers) are placeholders. Every row, item, perk
  and cell is listed in `weapons/armor.txt` (`python -m tools.atlas weapons`).
- **Set description:** the text in `armour_set_info` ("improve dodge invulnerability and attack speed…") is
  UI copy only.

## 5. What the server has to do

The client runs none of the authority code above, so the Python server has to produce its results.

1. **On equip:** spawn the armor actor with `ItemId`, `CurrentLevel`, `ArmourType`, `TransmogId` and
   `CurrentDyeVariant` replicated. This already works for visuals.
2. **Player resistances:** compute them from the rework table: sum over the four pieces of
   `value(element, CurrentLevel)`, plus the lantern's resistance (`lantern_resistance_table`, not detailed
   here). Set the player's replicated `ArchonHealthAttributeSet` floats (`GlobalResistance`,
   `<Element>Resistance`). Replicating them is only needed if anything client-side reads them; the
   server does the damage math itself.
3. **Behemoth hits:** use the formula in section 2, with the behemoth's powers (its class defaults) and the
   curve keys above. Shield first, then health. (`damage_ge` skips bleedout and invulnerable targets;
   `behemoth_damage_ge` has no such filter in its defaults.)
4. **Perks:** resolve the keyword ranks over all equipped gear, apply the resulting attribute changes (e.g.
   `EvadeInvulnerabilityMultiplier`, `MaxHealth`) on the player's attribute set, and optionally replicate
   `cell_manager_bpc.CurrentEffectsBeingApplied` for the UI.
5. **Gear stats and set bonuses:** only if we serve `GearCells2Feature` as on, since the stock default is off.
   If on: add each gear stat's magnitude to the attribute's base value, count distinct pieces per set, and
   apply each set tier whose `RequiredSources` is reached. Nothing for Embermane's set. The five sets listed
   in section 4 need their table entries.
6. **Pre-mitigation hooks:** apply the creature-level damage bonus for small creatures. Apply buff damage
   modifiers if we implement such buffs. Otherwise A = 0, M = 1.

## Still unconfirmed

- The remote feature-flag state (rework table vs. per-level table; `GearCells2Feature`).
- Whether the permanent cells go through `GetAllEquippedCellEffects`, and how per-level entries combine.
- How an item turns `BaseMagnitude`/`PerLevelMagnitude` into the applied gear stat `Magnitude` (0x28C43D0).
- The caller of the armor effect builder (0x269E1C0).
