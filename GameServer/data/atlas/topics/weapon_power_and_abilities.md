# How a weapon finds its power row and gets its abilities

Every weapon type works the same way here. Read from the 2.1.1 client exe (capstone disassembly, image base
0x140000000) and the Blueprint code, 2026-10-28. Addresses are offsets from the image base; each function has a
note in `native_notes.json` (`python -m tools.atlas native ArchonWeapon`).

## 1. Which power table

`ArchonGlobalTuning.GetWeaponPowerTable(level)` (0x2801480) picks the table by feature flag:

| Feature flags | Table |
|---|---|
| `WeaponProgressionReworkFeature` on | `WeaponProgressionReworkPowerTable` = `weapon_progression_rework_power_table` |
| that flag off, `ProgressionReworkFeature` on | `WeaponPowerTablePerLevels`: `weapon_power_table` (level 0), `weapon_power_table_level_01`, ... |
| both off | `WeaponPowerTable` = `weapon_power_table_obprog` |

The talent unlock rules need `WeaponProgressionReworkFeature` on, and talents work on our server, so **the rework table
is the one in use**. This matters: `WP_EB_SHOCK_01` has a row only in the rework table.

## 2. Which row: the item id, with no fallback

`AArchonWeapon::CachePowerTableData` (0x2E3E7B0):
- sets `PowerTable` (weapon +0x7F8) = `GetWeaponPowerTable(GetCurrentLevel())`
- finds the row named after the weapon's **`ItemId`** (the replicated `ArchonEquipment.ItemId`, the catalog id such as
  `WP_EB_BEGINNER`), and keeps a pointer to it (weapon +0x800). The pointer is null when there is no such row.

`ArchonWeapon.GetPowerTableData` (0x2E49990) copies that cached row, or returns false when there is none.
**There is no fallback** to the class's own `WeaponPowerTableData`. That value on `BP_EB_Weapon` is a Blueprint
variable of its own, not part of the lookup.

The Blueprints look rows up the same way: `BP_BaseArchonWeapon` calls `LookupPowerTableRow(Conv_StringToName(GetItemId()))`
for its element (hit effects, audio).

## 3. Where the abilities are granted, and onto which ability system

The grant is an `ArchonWeapon` virtual (0x2E3A780, vtable slot 0xFB0, beside `AArchonEquipment::HandleEquipForGameplay`
at slot 0xFA0). It:

1. Takes the weapon's **owner** (the player pawn, `AActor::Owner`) through its ability-system interface, and uses
   **the pawn's ASC**. It never uses the weapon actor's own ASC. Granting onto the weapon's ASC crashed the client
   (live 2026-10-16).
2. Finds `PowerTable[ItemId]`. With no row it logs "Weapon: no entry in weapon tables for this weapon (%s)" (also on
   screen) and **grants nothing**.
3. Copies the row's `PhysicalType` and `ElementalType` to the weapon (+0x660 / +0x661) and applies its power values.
4. **Only with authority** (the pawn's role is `ROLE_Authority`) does it grant:
   - every class in the weapon's own `Abilities` array (+0x5A0), then every class in the row's **`RefAbilities`**
     (row +0x130, soft class paths loaded here): each one is given to the pawn's ASC at the weapon's `AbilityLevel`
     (0x2E3B0F0)
   - **`RefSpecialAbilities1` .. `5`** (row +0x140 .. +0x180) through 0x2E3B300, as ability slots 0 .. 4: granted the same
     way, and their handles kept per slot in a map on the weapon (+0x678)

So the server grants them; the client never does. Which caller runs the grant was not traced. It sits beside
`HandleEquipForGameplay`, which only runs on the server.

## 4. Using a slot ability

`ArchonWeapon.ActivateSpecialAbilities(SlotID)` (0x2E3A5D0) finds the slot's handles in that map and calls
`TryActivateAbility` on each, on the pawn's ASC. Callers in 2.1.1:
- the montage notify `activate_special_abilities_bpn` (hammer dodge blasts and Y attacks)
- `BP_Axe_Weapon`
- `cb_push_special_and_invincibility_bpns` (chain blades)
- `ih_special_sally_exotic_bpc`

**No sword montage or sword class calls it.** The sword's specials are part gameplay instead (`sword_server.md`).

The map is filled only where the grant ran, which is the server. So a slot ability starts on the server (from a
montage notify the server plays). Its ServerInitiated policy then tells the client.

## 5. Resolved rows (rework table)

| Item | RefAbilities | RefSpecialAbilities1 .. 5 | Element |
|---|---|---|---|
| `WP_EB_BEGINNER` | `eb_charge_ab` | 1: `eb_q_projectile_raw_ab` | Physical |
| `WP_EB_CROWBEAR_EXOTIC` (The Hunger) | none | none | Umbral |
| `WP_EB_SHOCK_01` | none | none | Shock (row only in the rework table) |

The other three tables have the same values for `WP_EB_BEGINNER` and `WP_EB_CROWBEAR_EXOTIC`, and no row at all for
`WP_EB_SHOCK_01`.

- `eb_charge_ab` triggers itself on the gameplay event `Event.OnDamageGiven`; it is ServerInitiated.
- `eb_q_projectile_raw_ab` (ServerInitiated, no trigger) has no caller in the 2.1.1 Blueprints we found, since swords
  never call `ActivateSpecialAbilities`.

## What the server has to do

- Look the row up by the item's catalog id in `weapon_progression_rework_power_table`.
- Grant the weapon class's `Abilities` and the row's `RefAbilities` and `RefSpecialAbilities1-5` **to the pawn's ASC**,
  and remember which slot each special went into.
- Grant nothing when there is no row, as the game does.
- Start slot abilities on the server, when the move whose montage carries `activate_special_abilities_bpn` plays.
