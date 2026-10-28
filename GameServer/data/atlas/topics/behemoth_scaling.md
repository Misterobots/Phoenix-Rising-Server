# Behemoth scaling in the 2.1.1 client

How a behemoth's health, damage, power and resistance are scaled for a hunt: threat levels, tuning presets,
star levels and power overrides. Read from the Blueprint bytecode (`pseudo/Bp_Behemoth_C.txt`,
`pseudo/bp_gamemode_behemoth_spawner_component_C.txt`, `pseudo/threat_level_library_C.txt`), the effect defaults
in the paks, the exported data tables (`debug/exports/all-datatables-211`) and the 2.1.1 exe (addresses are
offsets from image base 0x140000000). Anything not read directly is marked **unconfirmed**.

In short: a behemoth collects a list of **tuning presets**. Each one is eight numbers, and the list is
**multiplied together**. The result sets the behemoth's max health, part health, stagger thresholds and a
**damage multiplier** on everything it deals. **Power and resistance** are a separate thing: they come from the
behemoth's class defaults unless the hunt gives a **power override**. Threat levels and presets never change
power.

## 1. The tuning preset (`behemoth_tuning_data`)

| Field | What it scales |
|---|---|
| `HealthScale` | max health (multiplied over all presets) |
| `PartHealthScale` | each part's share of max health (multiplied) |
| `BonusPctOfMaxHealthToParts` | flat extra share added to every part (summed) |
| `ExposeHealthScale` | each part's wound/expose share (multiplied) |
| `BonusPctOfMaxHealthToExpose` | flat extra expose share (summed) |
| `StaggerHealthScale` | stagger damage needed and temporary armor on stagger (multiplied) |
| `DamageScale` | damage the behemoth deals (multiplied) |
| `ElementalHealthScale` | elemental health; read by `elemental_health_updated_bpc`, not by the function below |

Threat-level rows (`threat_level_data`) carry the same eight fields plus rewards and `RecommendedPower`.
`threat_level_library.GetTuningPresetFromThreatLevelRow` copies the eight fields into a preset; it does
not touch `RecommendedPower`.

## 2. Where presets come from

`bp_gamemode_behemoth_spawner_component` builds the list when it spawns a behemoth, then calls
`AddTuningPresets_Interface(list, true)` on it:

- **Hunt threat level** (`SpawnBehemothAtLocation`): the matchmaker hunt's `HuntThreatLevel` names a row.
  When the feature flag `new_health_scaling_bpff` is on (its asset default is `bEnabled: true`), the row
  comes from `threat_level_table_newtuning` (rows 1–22). If that row is missing, the code logs an error and falls back to the behemoth's own threat
  level from its metadata row, read from `threat_level_table`. With the flag off, the row comes from
  `threat_level_table` (rows 1–30 and 165).
- **Challenge level** (`SpawnBehemothAtChallengeLevel`): the row of the given challenge-level table, plus
  `SpawnParams.AdditionalTuningPresets`.
- **Exploration level** (`Exploration_InitializeBehemothPowerAndScaling`): the row of
  `exploration_enemy_level_table` (rows 0–60). All its scales are 1.0, so it only sets power (section 4).
- **Preset assets** `zone00_btpreset` … `zone04_btpreset`, `heroic_btpreset` (Blueprint classes with a
  `TuningData` default), loaded by class.
- **Named presets** in `behemoth_tuning_presets` (event and gauntlet rows such as `PUBLIC_EVENT_HIGH_ALERT_T3`
  or `GAUNTLET_40_PERCENT_PLUS_20`). These are added by game modes through the same interface. Which mode
  adds which row is **not traced** row by row.

Selected rows:

| Source | HealthScale | PartHealthScale | StaggerHealthScale | DamageScale | ElementalHealthScale |
|---|---|---|---|---|---|
| threat level 1 | 0.9 | 0.9 | 1.0 | 0.9 | 0.75 |
| threat level 3 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| threat level 10 | 1.175 | 1.175 | 1.63 | 1.105 | 1.245 |
| threat level 16 | 1.35 | 1.35 | 2.17 | 1.195 | 1.455 |
| threat level 22 (newtuning) | 2.0 | 2.0 | 9.0 | 1.285 | 1.715 |
| threat level 22 (old table) | 2.5 | 2.0 | 9.0 | 1.285 | 1.715 |
| `heroic_btpreset` | 1.4 | 1.4 | 2.25 | 1.2 | — |
| `PUBLIC_EVENT_HIGH_ALERT_T3` | 2.0 | 1.5 | 3.0 | 1.0 | 2.0 |
| `GAUNTLET_40_PERCENT_PLUS_20` | 2.8 | 1.0 | 1.5 | 1.8 | 1.5 |
| `PUBLIC_EVENT_SUDDEN_DEATH` | 2.0 | 1.0 | 1.0 | 9999999 | 1.0 |

The two threat tables match up to row 18; from row 19 the old table has more health and less expose health.

## 3. How the behemoth applies them (`Bp_Behemoth.RefreshTuningPresets`)

Runs only with authority (`HasAuthority`). `AddTuningPresets` appends to `TuningPresetData` and calls it;
`ClearAndRefreshTuningPresets` empties the list first.

```
# base values, only when the game mode's UseNewHealthTuning is true: the mode's
# GetPerGameModeBehemothTuningOverrides if it uses overrides, else
# behemoth_function_library.GetPerPlayerScalingValuesForNewTuning. Otherwise they stay 1.0 and
# PerPlayerHealthIncRatio keeps the class default.
BaseHealthScale, PerPlayerHealthIncRatio, BasePartHealthScale, BasePartWoundHealthScale, BaseStaggerScale

H = S = P = E = D = 1;  partBonus = exposeBonus = 0
for preset in TuningPresetData:
    D *= DamageScale;   S *= StaggerHealthScale;   E *= ExposeHealthScale
    P *= PartHealthScale;   H *= HealthScale
    partBonus += BonusPctOfMaxHealthToParts;   exposeBonus += BonusPctOfMaxHealthToExpose

for part in MonsterParts:
    part.RatioOfBehemothHealth       = part.BaseRatioOfBehemothHealth * P * BasePartHealthScale + partBonus
    if part.BaseRatioOfBehemothExposeHealth > 0:
        part.RatioOfBehemothExposeHealth = part.BaseRatioOfBehemothExposeHealth * E * BasePartWoundHealthScale + exposeBonus

FromPresetsAdditiveHealthMultiplier = BaseHealthScale * H - 1
PerPlayerAdditiveHealthMultiplier   = (players - 1) * PerPlayerHealthIncRatio       # players = GetConnectedPlayerCount, or 1
MaxHealth = BaseHealthScale * BaseHealth * H * (1 + PerPlayerAdditiveHealthMultiplier)
InitializeHealth(MaxHealth)

staggerable_bpc.ArmorScalingFactor = S
for each stagger stage: StaggerDamageRequired and TemporaryArmorOnStagger *= S * BaseStaggerScale
staggerable_bpc.ChangeStaggerStage(...); every part RefreshHealth()

remove effects tagged Buffs.Behemoth.PresetDamageScale
apply behemoth_preset_damage_scale_ge with SetByCaller Magnitude = D
```

**Damage.** `behemoth_preset_damage_scale_ge` is infinite and has one modifier:
`GlobalTargetPostMigationMultiplier` × SetByCaller `Magnitude`, on the behemoth itself. That is the attacker's
**GTarget** in the post-mitigation step of the damage formula (`topics/armor.md`, section 2, step 4), so every
behemoth hit is multiplied by `D` after resistance. It does not change the power − resistance delta or the
curve.

## 4. Power and resistance

`AArchonBehemoth` keeps its powers and resistances as plain properties (`GlobalPower` at +0x874, …,
`GlobalResistance` at +0x89C, …, the multiplicative resistances at +0x8C0 onward). Class defaults set them
(e.g. `lerawr_normal_bp`: GlobalPower 100, GlobalResistance 250). `UpdateBasePowers` (thunk 0x22B6C50,
body 0x265AFC0) and `UpdateBaseResists` (thunk 0x22B6C70, body 0x265B2C0) copy each property into the matching
attribute base with `SetNumericAttributeBase`.

`bp_gamemode_behemoth_spawner_component.SetBehemothPowerOverride(Behemoth, Power)` sets **both**
`GlobalPower` and `GlobalResistance` to `Power`, then calls `UpdateBasePowers()` and `UpdateBaseResists(true)`.
It is called from:

| Caller | Power used |
|---|---|
| `TrySetBehemothPowerOverride` (hunt spawn) | the matchmaker hunt's `SpecificBehemoth.PowerOverride`, or a matching entry in its additional behemoth lists, when the name matches; e.g. arena elite hunts use 700 and 850 |
| `SpawnBehemothAtChallengeLevel` | `OptionalPowerOverride` if > 0, else the challenge row's `RecommendedPower` (`RecommendedPower_ProgressionRework` when `ProgressionReworkFeature` is on) |
| `Exploration_InitializeBehemothPowerAndScaling` | `exploration_enemy_level_table[level].RecommendedPower` (or the rework column); 10 at level 0, 100 at level 10, 300 at level 24 |

`SetLevelFromGlobalPower` then picks the behemoth's level as the first `exploration_enemy_level_table` row
(1–20) whose `RecommendedPower` is at least its `GlobalPower`, or 60 if none is.

Element powers and resistances other than Global are not touched by the override.
`override_player_power_and_resist_ge` is a different thing: `BPGM_ArchonExploration.HandlePlayerCharacterSpawned`
applies it to **players** when the island director has a power override > 0. It overrides the player's
`GlobalPower` and `GlobalResistance` with that value and zeroes every element power and resistance. It is
tagged `Cheat.PlayerPowerOverrideEffect`.

## 5. Star levels

`Bp_Behemoth.GetBehemothStarLevel` just returns a field of the behemoth's metadata row (logging
"Error finding Behemoth Star Level." and returning 0 if the row is missing).

`exploration_enemy_star_tuning_table` (stars 0–5: HealthScale 1.0 → 2.0, DamageScale 1.0 → 1.25,
StaggerHealthScale 1.0 → 3.0) is **not referenced** by any package in the paks or by any string in the exe
(searched 2026-10-26). In 2.1.1 star levels do not scale behemoths; the table is unused.

## 6. What the server has to do

1. Choose the hunt's threat level (from the matchmaker hunt row) and any event/gauntlet preset rows.
2. Multiply the presets as in section 3. Set max health, part and expose ratios, and stagger thresholds from
   the result, with the per-player term for the number of connected players.
3. Multiply every hit the behemoth deals by the product of `DamageScale` (it is a post-mitigation multiplier).
4. Use the behemoth's class-default powers and resistances, unless the hunt row has a `PowerOverride`; then
   set its Global power and Global resistance both to that value.

## Still unconfirmed

- Which game modes add which `behemoth_tuning_presets` rows, and the values `GetPerGameModeBehemothTuningOverrides`
  returns per mode.
- The values of `GetPerPlayerScalingValuesForNewTuning` (in `behemoth_function_library`, not summarised here).
- Whether a remote override turned `new_health_scaling_bpff` off (our backend decides now).
- What the `true` argument of `UpdateBaseResists` does past the attribute copies.
