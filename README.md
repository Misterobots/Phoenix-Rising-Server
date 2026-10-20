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
Slayer's Path unlocks and the Hunt Pass.

Not yet: hunts and the new-player island (new characters start in Ramsgate), item upgrades, the real-money store.

## What is in here

| Path | |
|---|---|
| `Play.bat`, `Stop.bat`, `deploy.ps1`, `start-server.ps1` | Start, stop and first-run setup |
| `Backend/` | Account, character, inventory and matchmaking service (`python -m phoenix_backend`; ports 3000, 3001, 443). Data is stored in `Backend/data/`. |
| `GameServer/` | The game server (`python run.py`, UDP 8790) |
| `GameServer/data/2.1.1/` | Data for the 2.1.1 build: network layouts and conditions, class/function/enum tables, the Ramsgate world, the starting quest state of a new character, quests, item classes, recipes, Slayer's Path rules and progression tracks |
| `tools/` | `setup-local.ps1` (hosts + certificate), `launch-client.ps1` (start the game against this server) |

The server and the game run on the same PC.

## Licence

Free to use, copy, modify and distribute, on one condition: servers running it keep the credit to
"Misterobots" and "The Undaunted Team" in the login screen's status text (the default in
`Backend/phoenix_backend/routes.py`, `LOGIN_STATUS`). See `LICENSE`; it covers the code, not the game data in
`GameServer/data/2.1.1/`.

## Credits

- **Undaunted** (SyST3MDeV/Undaunted) — the original Dauntless private-server project and the reference for
  most of what came after.
- **Dauntless - Phoenix Rising** — the Python backend and game server.

Dauntless is a trademark of its respective owners. This project is unaffiliated and for preservation and
private use; it contains no game assets. You need your own copy of the game client.
