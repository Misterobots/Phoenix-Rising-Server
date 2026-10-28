# Repeaters on the server

What our server has to do so the repeaters work in a hunt, why, and how we know. The repeaters were the first
weapon brought back, so this page is also the model for the others: the checklist at the end is what every weapon
type needs, and swords are next.

The game data for the repeaters is in `weapons/repeaters.txt` and `weapons/shared.txt`. The server code is on the
`main` branch, under `GameServer/mpgs/`. Every item below names the file that does it, so you can read the code
next to the explanation.

How sure each item is:
- **Live** = confirmed in game with the unmodified client, on the date given.
- **Read** = read from the game's code or data (atlas, Blueprint code or the decompiled exe), not yet seen failing
  or working in game.
- **Stand-in** = a value or behaviour we chose because the game data doesn't hold it. Each one is labelled as a
  stand-in in the code too, so it can be replaced when the real value turns up.

## What a repeater is made of

A repeater item (for example `WP_DP_UMBRAL_01`, Alluring Moons) is:

- an **actor**: a skin of `dp_weapon_bp`, with an ammo component `ammo_bpc`
- **parts**: barrel, receiver, grip, mod, prism, and for legendaries a legendary part. Each part is a row in
  `weapon_part_catalog` naming a component class (receivers and grips derive from `dp_base_part_bpc`)
- **abilities**, which the parts trigger:
  - X, the heavy shot: `dp_x_ability_piercing_ab`, which fires `dp_x_ability_piercing_proj`
  - Y/Q: `dp_y_ability_spawn_raptor_ab`, which calls the raptor pack
- **cooldown effects**: `dp_x_ability_cooldown_ge`, `dp_y_ability_cooldown_ge`
- a **legendary ability** for the Alluring Moons: `legendary_weapon_part_spawn_raptor_bpc`, the ultra raptor
- **talents**: five per weapon, from `weapon_talent_catalog`

The atlas sheets list all of these. This page is about what the server has to *do* with them.

## 1. Add the weapon's part components on equip

**Server:** when the weapon is equipped, create one component per part, from the part's `PartComponent` class, and
send it with the weapon. The weapon also sends `EquippedWeaponParts` and `WeaponPartIDs`.

**Why:** the moves call their part. A move's montage notify (`activate_weapon_part_gameplay_bpns`) looks up the part
by type on the weapon. With no component there, the move does nothing.

**Evidence:**
- **Read:** `BP_BaseArchonWeapon` runs `SpawnAndAddComponents` only on the server (`IsServer`).
- **Live, 2026-10-26:** without the components, the repeater heavy shot and Q did nothing.

**Code:** `mpgs/engine/weapon_parts.py`. **Test:** `tests/test_weapon_parts.py`.

## 2. Grant the part abilities, and confirm the client's activations

**Server:** give the player the abilities the parts trigger. When the client asks to use one
(`ServerTryActivateAbility`), reply that it succeeded, using **the client's own prediction key**.

**Why:**
- The client never creates ability instances itself; it waits for the server to grant them. On a real server that
  happened in native equip code that our join never ran.
- The receiver and grip send the events `Combat.Shoot.X` / `Combat.Shoot.Y`, which trigger the abilities.
- Part abilities are *LocalPredicted*: the client has already started them under its own key, so a new server key
  would not match.

**Evidence:**
- **Read:** `dp_weapon_ability_base_ab` is InstancedPerActor and replicated.
- **Live, 2026-10-16:** the client only sends `ServerTryActivateAbility` for abilities it has been granted.
- **Live, 2026-10-22:** replying with the client's own key works. This was first seen with the potion.

**Code:** `mpgs/engine/abilities.py`. **Test:** `tests/test_part_ability_rpcs.py` (the activation request matches a
live capture).

## 3. Keep the ammo count, and answer reloads

**Server:** set the ammo component's `MaxAmmoCount` (12) and `AuthoritativeAmmoCount`. Lower the count on each
shot (`ServerShoot`) and refill it on `Server_Reload`.

**Why:** the count belongs to the server. While it is 0, the client treats every attack as a reload.

**Evidence:**
- **Read:** `dp_weapon_bp`'s `ammo_bpc` has `MaxAmmoCount` 12, and `BeginPlay` starts full.
- **Live, 2026-10-26:** with the count at 0, every attack played a reload.

**Code:** `mpgs/island_encounter.py` (`_ammo_entries`, `handle_ranged`).

## 4. Score normal shots

**Server:** on `ServerShoot`, if the hit actor is the behemoth, apply damage with the weapon's damage row:
- `DP.PrimaryFire` normally
- `DP.ExoticBombs.PrimaryFire` for the exotic and some legendary skins (their own class default)

**Why:** `ServerShoot` runs `AuthOnHitRangedTarget` on the server. The client sends the hit; the server decides
the damage.

**Evidence:**
- **Read:** `dp_weapon_bp.ServerShoot` and the weapon class defaults' `DamageRowName`.
- **Stand-in:** the distance and ammo-quality damage scales are both taken as 1.

**Code:** `mpgs/island_encounter.py` (`handle_ranged`, `RANGED_ROWS`).

## 5. Spawn the heavy shot's projectile on the server

**Server:** when the X ability instance sends `SpawnProjectile(Loc, Rot)`, spawn the piercing projectile and apply
its damage with row `DP.PiercingShot`.

**Why:** the ability spawns the projectile with `bSpawnOnlyOnAuthority`, so only the server spawns it, and its hit
is handled on the server.

**Evidence:**
- **Read:** `pseudo/dp_x_ability_piercing_ab_C.txt` and `dp_x_ability_piercing_proj_C.txt`.
- **Stand-in:** the flight isn't simulated. The hit is a straight line against the behemoth's bodies up to `RANGE`,
  because the projectile's speed and lifetime weren't read.

**Code:** `mpgs/engine/projectiles.py`. **Test:** `tests/test_part_ability_rpcs.py` (`SpawnProjectile` layout, ray hit).

## 6. Put the ability cooldowns on the weapon, not the player

**Server:** when X or Q is used, apply its cooldown effect on the **weapon actor's own ability system**:
- X: `dp_x_ability_cooldown_ge`, 15 s
- Q: `dp_y_ability_cooldown_ge`, 60 s

Each effect has a duration set by the caller (`Duration`), and each grants a tag: `Combat.Pistols.XCooldown` /
`Combat.Pistols.YCooldown`. Send the effect along with an update of the game's server clock.

**Why:** the HUD (`weapon_dp_bpw`) shows the timer by asking the weapon how much of an effect with that tag is
left. Only the replicated effect counts.
- For X, the client predicts the cooldown too, so the server's effect carries the client's prediction key and
  replaces the predicted one.
- For Q, the client never applies the effect at all (see 7).

**Evidence:**
- **Read:** `dp_weapon_bp` `StartXCooldown` / `GetXCooldown`, and the cooldown effects' settings (2026-10-27).
- **Live, 2026-10-27:** with the effect on the player instead, no timer showed and X fired again inside 15 s.

**Code:** `mpgs/engine/effects.py`.

## 7. End the Q ability on the server, and enforce its cooldown

**Server:** when Q is used:
1. spawn the raptor pack (see 9)
2. end the ability straight away by sending `ClientEndAbility`
3. block Q for its cooldown, 60 s

**Why:** the client's Q stops early. It checks for the buff `alluring_raptor_manager_buff` before it would start
the cooldown and end itself. Without an end from the server, Q stays active and never works again.

**Evidence:**
- **Read:** `dp_y_ability_spawn_raptor_ab`. Its server instance ends in the same activation, and
  `bReplicateEndAbility` sends the end to the client.
- **Live, 2026-10-27:** the client's instance stopped at that buff check.

**Code:** `mpgs/engine/abilities.py` (`SERVER_ENDED`).

## 8. Give the player the raptor manager buff

**Server:** add `alluring_raptor_manager_buff` to the player. A buff is its own replicated actor that names the
player's buffable component.

**Why:** Q looks this buff up on the client (see 7).

**Evidence:**
- **Read:** `ArchonBuff` / `ArchonBuffableComponent` (2026-10-27).
- **Stand-in:** the buff is sent once at hunt start, while the game adds it on the first Q. It was built from the
  class layouts with no captured example, so it's the piece most likely to need a fix in game.

**Code:** `mpgs/engine/buffs.py`.

## 9. Run the Q raptors

**Server:** spawn the pack and run each raptor's AI against the behemoth:
- 2 raptors, or 3 with a talent, each a random pick of three raptor types
- spawned at the right hand, facing the camera direction
- each lives 90 s, and a new Q removes the old pack

Deal their damage from their own damage table. Replicate them like the behemoth: their movement, montages and
despawn.

**Why:** the ability spawns them only on the server. They are AI creatures, and our server runs all AI.

**Evidence:**
- **Read:** the ability's class defaults, the raptor behavior trees and attack montages (2026-10-26).
- **Live:** the raptors needed three fixes, all now in the code:
  - movement uses the game's own character movement packing
  - the root motion of their attack montages is played
  - they and the behemoth block each other

**Stand-ins:**
- how close counts as a hit (the raptors' own hitboxes weren't exported)
- the damage row of the slam area effect
- level 1 creatures
- straight-line walking with no navmesh

**Code:** `mpgs/creatures/raptors.py`, `mpgs/engine/allies.py`. **Test:** `tests/test_ally_raptors.py`.

## 10. The legendary ability: the ultra raptor

**Server:**
1. On equip, make the legendary ability available: the tag `Combat.LegendaryAbilityAvailable`.
2. When the player does the legendary move, spawn one ultra raptor at the right hand. Before that, clear the old one.
3. Take the tag away for the cooldown, 120 s, then give it back.

**Why:**
- The legendary part does all of this on the server.
- The client only enters the legendary move while the tag is present.
- The move's montage event at 0.845 s is what activates the part.

**Evidence:**
- **Read:** `legendary_weapon_part_base_bpc` and `_spawn_raptor_bpc` (2026-10-26).
- **Stand-in:** the tag is sent as a loose tag on the player's ability system, not as the game's effect. The client's
  check sees either.

**Code:** `mpgs/engine/legendary.py`. **Test:** `tests/test_ally_raptors.py` (legendary tag, move, ultra raptor,
cooldown).

## 11. Talents

**Server:**
- **In town:** handle unlocking and equipping talent perks.
  - Unlocking costs the recipe for that perk index.
  - The number unlocked per talent is capped by the item level: item level ÷ 10, at most 5.
  - Up to 3 perks per row can be equipped.
  - Save the state, and send it in the weapon's inventory data, in the client's own JSON format.
- **In a hunt:** apply the equipped perks' effects to the player.
  - Each D24 perk grants one `weapon.talent.d24.*` tag, which the abilities read.
  - `sp05` makes X's cooldown 10 s; `sp03` makes Q's 50 s.

**Why:**
- The client's own check (`0x28b87a0`) only lets a perk be equipped if it is unlocked, not already equipped, and
  fewer than 3 are equipped in its row.
- A click only sends the request. The client doesn't change its own copy.
- `ClientSetWeaponTalentPerk*State` only queues a pending change.
- The client reads its item data from the backend when it joins, so a change shows after a rejoin.
- `ClientItemCustomDataReplicated` is an empty stub in this client.

**Evidence:**
- **Read:** the talent tables, and the decompiled `FromCustomData` / `ToCustomData` (2026-10-28).
- **Live, 2026-10-28:** saved talent changes show after rejoining.
- **Known gap:** the talent screen doesn't redraw live. This is parked; the findings so far are in `mpgs/talents.py`.

**Stand-ins:**
- the item level is the weapon type's experience level
- escalation kill-count ingredients aren't checked
- perk buffs (might, defense) aren't applied yet

**Code:** `mpgs/talents.py`, `mpgs/engine/effects.py` (`apply_talents`). **Tests:** `tests/test_talents.py`,
`tests/test_ally_raptors.py` (shorter cooldowns with talents).

## 12. Stamina

**Server:** hold the player's stamina:
- spend each move's cost when the move starts
- drain it while sprinting (3.75/s)
- regenerate it (9/s) after a 1.4 s pause

**Why:** stamina is decided by the server and sent to the client.

**Evidence:**
- **Read:** the moves' stamina notifies, and `BP_PlayerCharacter`'s stamina tick.
- **Stand-ins:** when regeneration may start; the dodge's cost (the dodge is detected in native code) isn't covered.

**Code:** `mpgs/engine/stamina.py`. **Test:** `tests/test_stamina.py`.

## Checklist: what a weapon type needs on the server

Use this for each new weapon type. Swords are next; `weapons/sword.txt` has its data.

- [ ] **Part components:** for each item, its parts from the gameplay catalog's part slots; one component per part
      from `weapon_part_catalog`, added on equip and sent with the weapon (section 1)
- [ ] **Abilities:** every ability the parts and the power table grant (`RefAbilities`, `RefSpecialAbilities1`);
      check each one's activation type (LocalPredicted, ServerInitiated) and answer `ServerTryActivateAbility`
      accordingly (section 2)
- [ ] **Cooldowns:** each ability's cooldown effect, on the right ability system (weapon or player), with its
      duration, its tag and any talent overrides; and whether the client predicts it or the server must end the
      ability itself (sections 6, 7)
- [ ] **Damage:**
      - every attack montage's damage windows and rows (`weapons/<type>.txt`, Montages)
      - rows the game completes at runtime (charge or combo level, marked `*`)
      - rows set when something is spawned (sections 4, 5)
- [ ] **Server-spawned things:** projectiles, area effects, pets; spawned by the server, their hits scored by the
      server (sections 5, 9)
- [ ] **Buffs** the abilities look up or grant, sent as buff actors (section 8)
- [ ] **Legendary part:** its availability tag, its move, what it spawns, its cooldown (section 10)
- [ ] **Meter, ammo or charges:** whatever the type keeps on the server and the client only reads (section 3)
      - repeaters: ammo
      - sword: the weapon meter, held as the weapon's `Determination` (`mpgs/engine/weapon_meter.py`)
      - axe: charge level and determination
      - war pike: ammo and its meter
      - hammer: clips
      - chain blades: their meter
- [ ] **Talents:** the type's talent rows and what each perk changes in its abilities (section 11)
- [ ] **Area rules:** what the type may do in town (`area_rules.txt`)
- [ ] **Stand-ins:** list every value you had to choose, and label it in the code
- [ ] **Tests** in `GameServer/tests`, modelled on the repeater ones
- [ ] **Live test** with the unmodified client, then record what was confirmed in `verified.json`
