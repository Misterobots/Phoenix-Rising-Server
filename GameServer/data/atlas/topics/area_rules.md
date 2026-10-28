# What is allowed where (town vs hunt)

Generated sheet: `data/atlas/area_rules.txt` / `.json` (`python -m tools.atlas area-rules`). Check it before chasing a
"doesn't work" report: the client may simply refuse the action in that area.

## Rules read from the client (2.1.1)
- **Quick items** (potions, pylons, grenades, flares, airship calls): `Ability_QuickItem_Base.K2_CanActivateAbility`
  returns `!IsInCity() || CanUseInCity`. `CanUseInCity` is a class default; no combat quick item sets it, so **none of
  them can be used in Ramsgate**. The client never sends the use, so there is nothing for the server to handle.
  Only the 35 curiosities (fireworks, gifts, transforms, the Frostfall cheer emote) have it on.
- **Cells**: `cell_manager_bpc` sets `L_IsInCity = IsInCity() || IsInLobby()` and, in town, skips cell effects except on
  abilities tagged `Abilities.ActivateInCity`.
- **Main menu** (`mainmenu_*_panel_data_table`): `bDisableDuringHunts` greys out Store, Map, Vault, Trials store and
  Gauntlet store during a hunt; `bDisableDuringDeparture` greys out Return to Ramsgate while departing;
  `bShowDuringTutorial` lists what the tutorial shows (Return to Ramsgate, Inbox, Options, Help, Quit).
- **Bounty menu / bounty store**: gated by `IsInBountyFriendlyLocation`.
- 194 Blueprint functions branch on `IsInCity` / `IsInLobby` / `IsInIsland` (HUD quick slots, chat rooms, weapon
  audio and visuals, out-of-combat move speed ...): listed in the sheet, one row per function.

## Not covered yet
- Feature unlocks by quest or progress (crafting / purchase unlock criteria, Slayer's Path nodes) are per item:
  `recipes.json` `unlock` / `purchase_unlock`.
- Native C++ area checks (no Blueprint code) are not in the sheet; `IsInCity` itself is native.
