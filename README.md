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
- **One administrator prompt** to add the server's hostnames to the Windows hosts file, and a Windows prompt to
  trust the server's local certificate. Both stay on this PC.

The game then opens: click **Continue**, then **Defend the Frontier**. Later runs go straight to the game.
`Stop.bat` stops the server.

While the hosts entries are active the Epic Games Launcher cannot sign in to Epic; start the game with
`Play.bat`. `tools\setup-local.ps1 -Remove` (administrator) removes the hosts entries and the certificate.

## What works

Sign-in and new characters, Ramsgate with other players, inventory and loadouts, the starter weapons and gear,
equipping, Aetherite infusion, NPC interactions and quests.

Not yet: hunts and the new-player island (new characters start in Ramsgate), item upgrades, the real-money store.

Some features use game data you can generate from your own install (not included):

| Feature | Generate with |
|---|---|
| Equipping items beyond the starter kit | `cd GameServer; python -m tools.export_item_classes <DataTable export folder>` |
| Crafting | `cd GameServer; python -m tools.export_recipes <DataTable export folder>` |
| Slayer's Path unlock rules | `cd GameServer; python -m tools.export_journey_rules <player_journey_table.json>` |
| Player levels, Hunt Pass | `python Backend\tools\make_progression_config.py <progression config JSON>` |

## What is in here

| Path | |
|---|---|
| `Play.bat`, `Stop.bat`, `deploy.ps1`, `start-server.ps1` | Start, stop and first-run setup |
| `Backend/` | Account, character, inventory and matchmaking service (`python -m phoenix_backend`; ports 3000, 3001, 443). Data is stored in `Backend/data/`. |
| `GameServer/` | The game server (`python run.py`, UDP 8790) |
| `GameServer/data/2.1.1/` | Data for the 2.1.1 build: network layouts and conditions, class/function/enum tables, the Ramsgate world and the starting quest state of a new character |
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
