# Bringing content back with the atlas

This guide is for anyone working on the Phoenix server: small teams, hobbyists, people who know Dauntless well but
have never taken a game apart. You don't need to be an experienced game developer to use it.

The atlas exists so you don't have to hunt for game data every time you start work. Before it, finding out how
something worked meant searching the game files, digging through code, and starting over each session. The atlas
does that search once and writes the answers down, so you can look them up in seconds.

## Why this is needed

Dauntless was made of two programs:

- **The game** that players install (people call it the *client*). It has the art, sounds, menus and animations,
  and it also has the game's data: how hard each attack hits, what each weapon's special does, what each island can
  spawn, what each reward table contains. It draws the game and sends the player's button presses.
- **The game server**, which ran on Phoenix Labs' computers. Every hunt actually happened there. It decided which
  behemoth appeared, whether a hit landed and for how much, when a part broke, what loot you got and what was saved
  to your account. Then it told each player's game what happened.

When Dauntless shut down, Phoenix Labs switched their servers off. That server program was never released. The
game still runs, but on its own it has nothing to talk to.

This project is a replacement server, written in Python (the code in `GameServer/mpgs/`). The unmodified game
connects to it as if it were the official one. For anything to work again (a behemoth, a weapon move, a reward, an
island), our server has to do what the official one did.

The good news is that the game itself still holds most of the rules and numbers the official server used. The atlas
pulls them out and organises them. Bringing something back means:

1. **Look it up** in the atlas.
2. **Work out which part the official server did**, since that is the part our server must now do.
3. **Write that part** in our server, using the game's own numbers.

The atlas never changes the game. It only reads it.

## Words you will see

You only need a handful of terms.

| Word | Plain meaning |
|---|---|
| **Table** | A spreadsheet inside the game. Each line is a **row** with a name, such as the damage table row `EB.Parry_Strike`. |
| **Catalog** | A table of items: every potion, weapon, armor piece, cosmetic and token has a row with an **item id**, such as `QI_STAMINA_POTION_00`. |
| **Class** | A piece of game logic: a behemoth, a weapon, a buff, a potion's effect. Names end in `_C` when the designers built them in the editor. |
| **Blueprint** | The visual scripting the designers used to write most classes. The atlas turns it into readable text. |
| **Montage** | An animation with timing marks, e.g. "this sword swing can hit between 0.30 s and 0.37 s". |
| **Damage window** | One of those timing marks: when an attack can hit, with which body part, and which damage row it uses. |
| **Asset** | Any file in the game: a table, a montage, an island description, a behemoth's AI. |
| **Server-only** | Logic the game skips on players' machines because the official server ran it. This is the part we must rebuild. |
| **Sent to players** | Values the server must keep each player's game up to date with (health, meters, cooldowns). |

## Getting started

You need Python and this repository. Open a terminal in the `GameServer` folder. Everything is already generated, so
nothing needs building first. Try:

```
python -m tools.atlas --help
```

Most of the time you will just open text files. The commands are for quick lookups.

## Step 1: Find what you want to bring back

The atlas has a ready-made **sheet** (a plain text file) for every major part of the game. Open the one that fits:

| You want to bring back | Open this file (in `GameServer/data/atlas/`) |
|---|---|
| A behemoth | `behemoths/<name>.txt`, e.g. `lerawr_normal.txt` for Embermane, `lerawr_beta.txt` for Lesser Embermane |
| A weapon, an attack, a special or a passive | `weapons/<type>.txt`: `sword`, `axe`, `hammer`, `chainblades`, `warpike`, `repeaters`, `strikers` |
| Something all weapons share | `weapons/shared.txt` |
| Armor, perks, cells, set bonuses | `weapons/armor.txt`, and `topics/armor.md` for how they work |
| An item: lantern, potion, grenade, material, token, cosmetic | `items/` (one file per kind: `lanterns.txt`, `quick_items.txt`, `materials.txt`, ...) |
| Where an item comes from | `rewards/item_sources.txt`, then `rewards/drops.txt`, `crafting.txt`, `bounties.txt`, `hunt_pass.txt` |
| An island or a hunt | `islands/hunts.txt`, then `islands/specs/<island>.txt` |
| Escalation, gauntlet, portals, events | `islands/escalation.txt`, `islands/modes.txt` |
| Hunt modifiers | `islands/hunt_modifiers.txt` |

Each area also has a summary file one folder up (`behemoths.txt`, `weapons.txt`, `items.txt`, `rewards.txt`,
`islands.txt`) that says how much is covered and lists anything the game is missing.

Behemoths use their internal names. `behemoths.txt` lists each one next to its in-game name.

## Step 2: Follow the trail

The sheets point to each other. When a sheet mentions something, it adds a small mark so you know where to look
next:

| You see | It means | Where to look |
|---|---|---|
| `something_C` | a piece of game logic | `python -m tools.atlas show something_C` |
| `table[Row]` | a row in another table | `python -m tools.atlas asset table` shows the whole table |
| `ITEM_ID{catalog}` | an item, and the catalog it is in | the matching file in `items/` |
| `lerawr_beta<behemoths>` | a behemoth with its own sheet | `behemoths/lerawr_beta.txt` |
| `+` after a damage row | the row exists | |
| `~` after a damage row | the row exists, spelled with different capitals (the game doesn't mind) | |
| `*` after a damage row | the game adds a number to it while playing (a charge or combo level) | the sheet lists the real rows |
| `?` after anything | **the game doesn't have it** | see step 5 |
| `~` after a file or class name | only affects looks (animations, effects, colours) | you can ignore it |

Follow the marks until there is nothing new to open. Jot down what you passed through. That list becomes your to-do
list.

## Step 3: Work out what the server has to do

For each piece of game logic on your list, run:

```
python -m tools.atlas show <name>
```

You get a summary. Three things in it matter most:

- **`auth=HasAuthority`** or **`auth=IsDedicatedServer`** next to a function: that part only ran on the official
  server. **Our server has to do it.**
- **Messages marked `server`** (under "RPCs"): things a player's game asks the server. Our server has to answer them.
- **Lines marked `R`** (replicated): values the server has to send to players.

To read the logic itself, open `data/atlas/pseudo/<name>.txt`. It is the designers' Blueprint turned into text, read
top to bottom. These help too:

```
python -m tools.atlas show <name> --fn <function>   # just one function
python -m tools.atlas who-sets <value>              # everything that changes a value
python -m tools.atlas who-calls <function>          # everything that starts a function
python -m tools.atlas verified <word>               # facts someone has already confirmed
```

If a whole system is involved (damage, armor, behemoth difficulty), read the matching write-up in
`data/atlas/topics/` first. It explains the system end to end in plain words.

## Step 4: Write it in our server

Three rules save a lot of trouble.

**Use the game's numbers straight from the atlas.** Every table and asset is saved as a file in
`data/atlas/assets/<name>.json`. Have the server read those files rather than typing numbers in by hand. If a number
is wrong, it is wrong in one place, and it is easy to check. Reading a table looks like this:

```python
import json

def rows(table):
    data = json.load(open(f'data/atlas/assets/{table}.json', encoding='utf-8'))
    return data['exports'][table]['props']['Rows']

hunt = rows('matchmaker_hunts_table')['Adventure_IslandA_D24']
```

Some tables have `_composite` in their name. They combine other tables; `tools.atlas.areas.table_rows(name)` reads
them correctly.

**Use exactly the names the game uses.** Item ids, row names, tags and class names must match letter for letter. The
game looks everything up by name. If a name is slightly off, the game silently shows nothing.

**Do things in the same order the game does.** The order often changes the result. For example, an attack uses up
its target before its damage is looked up, so an attack with a missing damage number still blocks later hits from
the same swing. Facts like this are written down in `verified.json` and `native_notes.json`, and `show` prints them.

## Step 5: When the game is missing something

Sometimes the game refers to something it doesn't contain. The sheets mark it with `?`. Usually it is content
removed before the last version, or data that only ever lived on the official server. Every case is listed at the
end of the summary files.

What to do:

1. **Check whether it is actually used.** The sheets show what refers to it. Some missing names are never used. The
   sword's `EB.SuperAoE`, for example, sits in a setting the ability never reads. Others get replaced while
   playing, like the sword's slicer, whose damage row is filled in when the sword fires it.
2. **If it is needed, make a careful guess and label it as ours.** Base it on the closest real data, write down how
   you got the numbers, and keep it clearly separate from the game's data. The example to copy is
   `data/atlas/proposals/beaver_frost_damage_table.md`. It fills in 17 missing frost Gnasher attacks using the blaze
   Gnasher's numbers, explains both rules it used, and marks every value as ours.

Never present a guessed value as if it came from the game.

## Step 6: Test it, then write down what you learned

- Test with the **unmodified game**. Save up several changes and test them together, rather than restarting for
  each one.
- When something is confirmed in game, add a short note to `data/atlas/verified.json`: what is true, which logic it
  is about, how you know, and the date. The next person who looks that logic up sees your note.
- If you worked out how a whole system fits together, write it up in `data/atlas/topics/`.
- If the server starts using a new piece of logic, add its name to `tools/atlas/starter.txt`.

This is how the atlas keeps growing, and how nobody has to rediscover the same thing twice.

## Example 1: bringing back an island hunt

*Goal:* the Island A hunt, where players fight a Lesser Embermane.

1. Open **`islands/hunts.txt`** and find `Adventure_IslandA_D24`. It points to the island description `island_a_d24`.
2. Open **`islands/specs/island_a_d24.txt`**. It tells you:
   - which hunt uses this island, and that our server already has its map data in
     `data/2.1.1/islands/Adventure_IslandA_D24.contract.json`
   - the island's limits: one main encounter at a time, behemoth power up to 125
   - what spawns: the "StandardBehemoth" slot always picks `lerawr_beta` (Lesser Embermane) at level 1, shown on
     the hunt screen, followed by the danger meter
3. `lerawr_beta` is marked `<behemoths>`, so open **`behemoths/lerawr_beta.txt`**. It has the whole fight: its
   attacks, when each one can hit and for how much, what it spawns, how its AI picks moves, and its hitboxes.
4. Now the server's job is clear:
   - spawn what the island says
   - run the behemoth's attacks from its sheet, scaled for difficulty as `topics/behemoth_scaling.md` explains
   - hand out the rewards listed in `rewards/`
5. Is anything missing? `behemoths.txt` has a "missing" column. For `lerawr_beta` it is empty, so nothing is missing.

## Example 2: bringing back a weapon special

*Goal:* the sword's Parry.

1. Open **`weapons/sword.txt`** and find **Parts**. `PART_EB_SPECIAL_PARRY` uses the logic
   `eb_special_parry_part_bpc_C`. The **Items** section shows which swords come with it.
2. Further down, under **Part classes**, that logic has a 2-second cooldown, starts on cooldown, and switches the
   sword to its parry animations.
3. Run `python -m tools.atlas show eb_special_parry_part_bpc_C`. Its main function checks `HasAuthority`. That part
   ran on the official server, so ours has to do it.
4. Under **Buff classes**, `buff_parry_window_C` makes the player invulnerable during the parry. When a weapon hit
   comes in, it applies `eb_riposte_window_ge_C`, the riposte window.
5. Under **Montages**, the parry strike animation (`h_mr_eb_melee_ParryStrike_fwd_01_mot`) can hit for its first
   0.21 seconds. It uses damage row `EB.Parry_Strike`, or `EB.Parry_StrikeTalent` if the player has the matching
   talent. Both rows exist.
6. Server job: enforce the cooldown, apply the invulnerability window, start the riposte when the player is hit
   during it, and score the strike with the right damage row.

## Example 3: bringing back an item and where it drops

*Goal:* a potion players can craft and find.

1. Open **`items/quick_items.txt`** and find `QI_STAMINA_POTION_00`. It shows the potion's logic
   (`QuickItem_StaminaPotion_00_C`), what unlocks crafting it, what unlocks buying it, and its recipe.
2. **`items/classes.txt`** has the potion logic with its settings. For the healing potion, the exact healing formula
   is already confirmed: run `python -m tools.atlas verified potion`.
3. **`rewards/crafting.txt`** has the recipe and unlock rows. **`rewards/item_sources.txt`** lists everywhere an
   item can come from. The attack speed potion, for example, comes from 18 places: patrol chests, pet finds, event
   gifts, login rewards and more.
4. Server job: give the item the same id, let players unlock and craft it as those rows say, drop it where those
   tables say, and apply its effect when it is used.

## Quick reference

| Command | What it does |
|---|---|
| `show <name>` | Summary of a piece of game logic, with links to its readable code |
| `show <name> --fn <function>` | One function only |
| `asset <name>` | Opens a table or other asset, with what it refers to and what refers to it |
| `who-sets <value>` / `who-calls <function>` | Finds what changes a value or starts a function |
| `uses-table <table>` | Finds what reads a table |
| `verified [word]` | Facts already confirmed |
| `native <name>` | For the game's built-in C++ code: where it is and any notes on it |
| `behemoths`, `weapons`, `areas` | Rebuilds the sheets (add `--dump ...` to re-read the game files first) |
| `assets <name>` / `build <name>` | Adds something the atlas doesn't have yet |

## Checklist

For each thing you bring back:

- [ ] Found its sheet and followed every mark to the end
- [ ] Listed the server-only parts, the messages to answer and the values to send to players
- [ ] Read the readable code to get the order of events right
- [ ] Checked `verified.json` and `topics/` for what is already known
- [ ] Made the server read numbers from `data/atlas/assets/`, with names matching the game exactly
- [ ] Dealt with every `?`: left out on purpose, or filled with a clearly labelled guess
- [ ] Tested with the unmodified game
- [ ] Wrote down anything new you confirmed
