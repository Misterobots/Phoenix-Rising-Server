# Dauntless - Phoenix Rising

A private server for the final Dauntless PC client (**2.1.1**, CL 682875). You play with the stock, unmodified
game: it starts through its own Easy Anti-Cheat launcher and nothing in it is patched or injected.

## Play

1. Install Dauntless 2.1.1 (Epic Games Store) if you have not already.
2. Double-click **`Play.bat`**.

The first run installs what it needs and asks a few things:

- **Python 3.11+**: offered through winget if it is missing (then run `Play.bat` again).
- **Game folder**: found automatically for Epic installs, otherwise you are asked for it.
- **Player name**.
- **An administrator prompt each start** to enable local host routing and trust the server's certificate.

The game then opens: click **Continue**, then **Defend the Frontier**. `Stop.bat` stops both server processes and
removes local routing and the trusted certificate.

While the server is running, the local Epic host entries route those service requests to this PC. Use `Stop.bat`
to stop the services and restore normal Epic Launcher routing. If cleanup is interrupted, run
`tools\setup-local.ps1 -Remove` from an elevated PowerShell window.

## What works

Sign-in and new characters, Ramsgate with other players, inventory and loadouts, equipping any weapon, armour or
lantern, Aetherite infusion, player and weapon levels, NPC interactions, quests and their rewards, crafting,
Slayer's Path unlocks and the Hunt Pass (free and premium rewards).

- **Vendors**: purchases follow each item's quest unlock rule; bought and crafted items (pylons and the like) reach
  your inventory in the right quantity.
- **Salvage**: materials and parts break down into the amounts the game's salvage bundles list.
- **Weapon talents**: unlocking and equipping perks (up to 3 per row) is saved. The talent screen shows a change
  only after you rejoin.
- **Hunts (in progress)**: the island encounter with a behemoth, hit reactions, and the repeaters in full (heavy
  shot, raptor allies, legendary part, ammo, cooldowns). The other weapon types are next, one at a time; see
  `HUNT_IMPLEMENTATION.md`.

Not yet: the other weapon types' special moves in hunts, the new-player island (new characters start in Ramsgate),
item upgrades, the talent screen updating live, and the real-money store.

## What is in here

| Path | |
|---|---|
| `Play.bat`, `Stop.bat`, `deploy.ps1`, `start-server.ps1` | Start, stop and first-run setup |
| `Backend/` | Account, character, inventory and matchmaking service (`python -m phoenix_backend`; ports 3000, 3001, 443). Data is stored in `Backend/data/`. |
| `GameServer/` | The game server (`python run.py`, UDP 8790) |
| `GameServer/data/2.1.1/` | Data for the 2.1.1 build: network layouts and conditions, class/function/enum tables, the Ramsgate world, the starting quest state of a new character, quests, item classes, recipes, salvage, Slayer's Path rules and progression tracks |
| `tools/` | `setup-local.ps1` (hosts + certificate), `launch-client.ps1` (start the game against this server) |
| `HUNT_IMPLEMENTATION.md`, `ENGINE_PORT_PLAN.md` | How hunts are being rebuilt, and the plan for the engine behaviours the server re-creates |

The server and the game run on the same PC.

## Development

- **Tests**: `cd GameServer` then `python -m pytest tests -q`. Hunt tests need the generated hunt world data (see
  next point) and fail without it.
- **Hunt world data**: `GameServer/world/` (behemoth bodies, hunt sources, islands) is generated on your PC from
  your own game files by the `GameServer/tools/export_*` tools and is not in the repository; see
  `HUNT_IMPLEMENTATION.md`.
- **Runtime game data**: `GameServer/data/2.1.1/class_layouts.json` (network layouts of the game's classes) and
  `data/2.1.1/talents/` are written from the atlas by `python -m tools.export_runtime_data <atlas dir>`.
- **Live reload**: with `MPGS_DEV_RELOAD=1` (on in `start-server.ps1`), saving a change to a `GameServer/mpgs`
  file takes effect in the running server without restarting it or the game. Code that only runs while joining
  still needs a rejoin.
- **Granting items for testing**: `cd GameServer` then `python -m tools.grant_item <player name> <catalog id> [amount]`.
  It grants through the backend as a reward would; rejoin to see the item.
- **Game-logic atlas**: the reference for the game's own rules (weapons, armour, items, shops, loot and rewards,
  behemoths, islands, town and hunt rules) lives on the separate
  [`Atlas`](https://github.com/Misterobots/Phoenix-Rising-Server/tree/Atlas) branch. You don't need it to run the
  server.
- **Commits**: plain messages; no commented-out or unused code (`python -m pyflakes mpgs tests tools` is clean).

## Licence

Free to use, copy, modify and distribute, on one condition: servers running it keep the credit to
"Misterobots" and "The Undaunted Team" in the login screen's status text (the default in
`Backend/phoenix_backend/routes.py`, `LOGIN_STATUS`). See `LICENSE`; it covers the code, not the game data in
`GameServer/data/2.1.1/`.

## Credits

- **Undaunted** (SyST3MDeV, EisigesEis) — the original Dauntless private-server project and the reference for
  most of what came after.
- **Dauntless - Phoenix Rising**

Dauntless is a trademark of its respective owners. This project is unaffiliated and for preservation and
private use; it contains no game assets. You need your own copy of the game client.
