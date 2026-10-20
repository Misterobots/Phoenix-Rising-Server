# Phoenix Rising hunt implementation

Working checkout: this repository (`_handoff/Dauntless-Phoenix-Rising-Server`).

## Completed: authored selection and game-server destination

- `GameServer/data/2.1.1/hunts.json` contains all 67 rows of the primary
  `Gameplay/hunts/matchmaker_hunts_table` export. Generated arena tables are not
  included. Source filenames and SHA-256 hashes are embedded in the catalog.
- Full source rows preserve unlock requirements, behemoth references, modifiers,
  player limits, atmosphere references and game-mode-specific data. Referenced
  exploration spec-table rows are resolved; the underlying IslandSpecAsset
  Blueprints are still references, not extracted encounter implementations.
- `mpgs.hunts.select` resolves an exact hunt ID and authored map. Multi-map hunts
  require an explicit selection. Missing default game modes are reported as
  unresolved, rather than inferred from a hunt's name.
- GameServer instances now own their Welcome map and game-mode paths. The
  existing Ramsgate defaults are preserved.
- `run.py --hunt` requires a world template containing the selected map and mode
  references. This is a consistency check, not proof of a playable world.
  The shipped Ramsgate template fails this check for an island before UDP opens.
- Fourteen automated tests cover catalog export, selection failures, actual
  Welcome wire decoding, the Ramsgate regression, command-line startup
  rejection, authored island contract extraction, full island world build/encode
  (`Adventure_IslandA_D24`), and backend matchmaking/instance routing.

Commands from the repository root:

```powershell
python -m unittest discover -s GameServer/tests -v
python GameServer/run.py --list-hunts
python GameServer/run.py --hunt CR19_MatchmakerHunt_ShatteredIsles_TrainingDojo --inspect-hunt
python GameServer/tools/export_hunts.py <all-datatables-211-directory>
python GameServer/tools/build_island_world.py --hunt Adventure_IslandA_D24
```

## Completed: Step 1 (authored island world) and Step 2 (backend matchmaking routing)

1. **Step 1 — Authored island world build and offline wire encoding (`Adventure_IslandA_D24`)**:
   - `GameServer/mpgs/island_world.py` and `GameServer/tools/build_island_world.py` extract the verified island contract (`GameServer/world/islands/Adventure_IslandA_D24.contract.json`) and build `GameServer/world/islands/Adventure_IslandA_D24.json` from the exported 2.1.1 map packages in `GameServer/world/hunt_source/`.
   - Preserves the supported player initialization while replacing Ramsgate exports, streaming-level RPCs (the authored `_design`, `_sound` and `_tutorial` sublevels), game state (`BPGS_ArchonExploration_GameState_C`), placed actor references, and PlayerStart spawn transform.
   - `GenWorld.from_template` supports both string `source` metadata and dict `source` metadata carrying `islandContract`, and `mpgs.hunts.validate_world` verifies the embedded `islandContract`.
2. **Step 2 — Backend matchmaking connected to available island instances**:
   - `Backend/phoenix_backend/__main__.py` shares a single `Routes` and `Gameplay` instance across all three listeners (`3001`, `3000`, `443`).
   - `Backend/phoenix_backend/gameplay.py` registers/unregisters live `CITY` and `ISLAND` instances (`GET /instances`, `POST /instances/register`, `POST /instances/unregister`), validates `welcomeMap` and `welcomeGameMode` against the authored catalog (`GameServer/data/2.1.1/hunts.json`), and verifies `serverInfo.map` (`<welcomeMap>?bUseIPSockets`) matches `NMT_Welcome`.
   - `Gameplay.join`, `Gameplay.status`, `Gameplay.party`, and `Gameplay.key_generate` retain FTUE compatibility (`gameType == 'FTUE'` without an explicit hunt routes to `CITY` when `PHOENIX_SKIP_FTUE=1`), preserve `huntId` and `playerHuntId`, reject unknown hunts (`404`) and unresolved/ambiguous hunts (`400`) without falling back to `CITY`, and keep island candidates in `MATCHING` (`NO_AVAILABLE_INSTANCE`, `/key/generate` -> `409`) unless a verified island `GameServer` instance is registered.
   - `GameServer/mpgs/server.py`, `GameServer/mpgs/backend.py`, and `GameServer/run.py` automatically register/unregister the running `GameServer` instance with the backend and reject starting an island template without `--hunt`.

## Remaining execution order

3. **Completed live:** the user confirmed arrival animation, visible island and
   free movement after the cinematic fade correction on October 17, 2026.
   No client patching was required.
4. Implement encounter spawning, movement and combat using the chosen island's
   authored content. The first authored spawn layer is implemented; movement
   and combat remain open. Local
   workbench asset exports and prior experimental runtime may be references,
   but any reused code must live here, use this package's build data and pass
   compatibility tests. Do not replace this repository with the experimental
   checkout or assume its prior live results validate this package.
5. Implement defeat/death, loot, XP, quest objectives and return travel, with
   idempotent backend transactions and reconnect behavior. Verify one complete
   encounter before adding more species or modes.

## Acceptance boundary

The selection/Welcome layer, `Adventure_IslandA_D24` world build/encoding, and
backend matchmaking-to-instance routing are tested offline. The expanded suite
has 26 tests, including real HTTP requests across separate ephemeral listeners
and arrival readiness/codec coverage.
The user has confirmed live stock-client island entry and free movement.
A behemoth fight, reward delivery and return trip remain unverified. The launcher work is outside the
active gameplay scope.

## Island bootstrap verification and current live boundary

- The extracted first-island contract is preserved at
  `GameServer/data/2.1.1/islands/Adventure_IslandA_D24.contract.json`, with source
  hashes, four PlayerStarts, framework classes and streaming-level references.
- Three authored always-loaded sublevels are sent (design, sound, tutorial).
  The optional storm vista remains unactivated; its authored transform is
  preserved in the contract for subsequent dynamic streaming work.
- All 36 bootstrap entries compose into wire bunches, reassemble, decode and
  re-encode bit-exactly. An isolated GenSession emits 63 bunches. Character data,
  weapon progression and recovery use the adventure framework classes.
  Ramsgate atmosphere injection is excluded for island contracts.
- Single-map exploration hunts have authored Weighting=0. Explicit selection
  now accepts them; the weight is not treated as an unlock/availability flag.
- A real HTTP test uses two ephemeral listeners sharing the same backend state:
  join waits without an instance, authenticated registration enables the exact
  island URL/port, a key generated on one listener is consumed once on the other,
  and unregister returns the candidate to matching. Test services are closed.
- Before the live preparation below, the old backend responded HTTP 404 to authenticated
  `GET /instances`; it has not loaded this implementation. Existing TCP ports
  3000/3001/443 and UDP 8790 were occupied.
  A stock-client island acceptance run requires the updated backend and an
  island instance, then a manual client join. No live success is claimed.

Rebuild from the checked-in contract without requiring raw exports:

```powershell
python GameServer/tools/build_island_world.py --hunt Adventure_IslandA_D24 --contract GameServer/data/2.1.1/islands/Adventure_IslandA_D24.contract.json --output GameServer/world/islands/Adventure_IslandA_D24.json
```

With the updated backend running, start a separate island instance (the city
server remains on 8790):

```powershell
python GameServer/run.py --port 8791 --hunt Adventure_IslandA_D24 --template GameServer/world/islands/Adventure_IslandA_D24.json
```

## Live preparation: October 17, 2026, 10:19 AM America/Chicago

- User authorized the backend restart and island launch. The legacy backend was
  verified by executable, module, local port ownership and start time matching
  this package's original backend log before being stopped.
- Updated backend PID 38692 serves TCP 3001, 3000 and 443. The existing city
  process PID 17580 remains on UDP 8790. Island PID 33900 serves UDP 8791.
- Authenticated `GET /instances` confirms `Adventure_IslandA_D24` with the exact
  authored map and exploration-tutorial game mode. Its registration returned 200.
- Local hostname routing and CA trust were installed with the package setup
  script. A verified HTTPS request to `https://api.epicgames.dev/` resolves to
  127.0.0.1 and returns HTTP 200 / `PhoenixBackend`.
- The configured client executable matched the expected stock 2.1.1 SHA-256.
  Launched `Misterobots` through `start_protected_game.exe`; the shipping game
  process was observed running. Manual menu interaction and island entry remain
  pending; process launch is not island acceptance.
- Exact logs: `logs/backend_island_20261017_101906.err.log`,
  `logs/island_20261017_101906.log`, `logs/island_20261017_101906.err.log`.
  Preparation state/PIDs are in `logs/island-live-preparation.json`.
- Windows currently reports null/incorrect TCP state labels for some listeners.
  Port ownership plus successful direct connections and HTTP/HTTPS responses
  were used to verify readiness.

## Failed live join and arrival repair: October 17, 2026

The user reported that the hunt timed out and never became playable. The exact
first-attempt log `logs/island_20261017_101906.log` shows the server accepting the
handshake at 10:22:00, NMT_Join at 10:22:01, possession acknowledgement, and movement
at the authored island spawn. It delivered the bootstrap and continued exchanging
packets with zero loss/undecodable packets until the client closed at 10:24:01.
This establishes a live connection and actor initialization, not playable entry.

Cooked `BPPC_Adventure_PlayerController.HasFinishedLoading` returns its parent
result AND `(ArrivalCineReplicated OR NOT AdventureModeArrivalsFeature)`.
`OnRep_ArrivalCinematicTrigger` sets `ArrivalCineReplicated`; the previous world
never sent that reference. The arrival actor itself also requires its animation
and dynamic cinematic component to finish preloading. This missing server
contract is corrected; a second live attempt is needed to establish whether it
fully resolves the user-visible timeout.

- `mpgs/island_arrival.py` extracts the authored intro actor, fallback arrival
  animation, landing transforms and proxy assets from cooked exports. It adds
  the owner arrival actor and component preload state, then sends the controller
  references. The rebuilt world has 38 entries.
- Cinematic completion RPCs are handled for the owning controller only, with
  idempotent completion acknowledgement and gameplay-start updates. No timeout
  is used to manufacture a client completion event.
- Quaternion transform serialization follows `TQuat<double>::NetSerialize` in
  UE 5.1.1 `Core/Private/Math/UnrealMath.cpp`: XYZ doubles, positive W reconstructed
  on receipt. Known-byte tests cover the wire size and sign convention.
- All 26 tests passed, including the full template round trip and an encoded
  client cinematic completion request through the actual session parser.
- Restarted only the island instance after testing. Current island PID 43112,
  registered on 8791; backend and Ramsgate remain running. Retry logs are
  `logs/island_arrival_20261017_105044.log` and the corresponding `.err.log`.
  The new startup log confirms 38 entries and successful instance registration.

Live status: ready for another manual stock-client island join; no successful
playable island entry, behemoth combat or rewards are claimed yet.

## Black island scene and authored daylight: October 17, 2026

The second live attempt joined at 11:00:35/36. Its arrival cinematic reported
state 1 at 11:00:46 and state 2 at 11:01:02, followed by gameplay-start
acknowledgement and movement at the landing position. The user confirmed the
island loaded but the scene was black. Log: `logs/island_arrival_20261017_105044.log`.

The island map places `atmosphere_spawner_bp2_2`, whose replicated requested
atmosphere index invokes client atmosphere creation. The previous bootstrap did
not send this actor. `mpgs/island_atmosphere.py` now extracts and replicates the
map's authored day atmosphere, index 0, including sun brightness 8, skylight
brightness 4, authored colors and fog settings. The contract uses JSON-native
null object references and survives disk serialization. The rebuilt world has
39 entries. All 26 tests passed; the seven island tests passed again after adding
explicit daylight assertions, including full wire and template round trips.

Only the island was restarted: PID 43248 on UDP 8791, registered successfully.
Backend and Ramsgate remain running. New logs:
`logs/island_lighting_20261017_110716.log` and corresponding `.err.log`.
Visual lighting acceptance remains pending a fresh client island join.

## Arrival-to-gameplay fade correction: October 17, 2026

The user clarified that the drop-in animation is visible, then the world becomes
black with only the HUD. The lighting-instance attempts at 11:10 and 11:13 both
reached cinematic state 2 and gameplay acknowledgement without packet loss.
Lighting activation alone did not resolve the visible failure.

Cooked dynamic cinematic completion code at offsets 27746-28749 selects the
fade behavior from ForceFadeFromBlack: 0 sets LShouldFadeFromBlack true and
returns the camera immediately; 1 blends the camera but explicitly sets that
flag false; 2 also skips the fade. Offsets 26545-26949 play the fade-from-black
sequence only when the flag is true. Our bootstrap override was still 1 when
AllClientsCompleteOwnerOnly ran. The completion update now resets it to 0 before
that reliable RPC. Startup animation settings remain unchanged.

All 26 tests passed, including a completion request through the session parser,
wire-decoded fade reset, reliable ordering before completion, and idempotence.
Only island PID 43248 was replaced; new PID 9892 is registered on UDP 8791.
Logs: `logs/island_camera_20261017_112218.log` and corresponding `.err.log`.
A fresh client join is needed to validate that gameplay becomes visible.


## Authored first encounter spawn: October 17, 2026

Live island entry is accepted: the user reported "fixed. I'm on the island and
running around" after the fade correction. The fresh camera-instance log also
shows continuing movement without packet loss.

The selected island spec contains two identically weighted entries for the same
level-1 `lerawr_beta` (Lesser Embermane), with one maximum active primary content.
The encounter extractor resolves its actual metadata row, cooked class defaults
and Blueprint inheritance to ArchonBehemoth. It retains both map-authored patrol
centers and source hashes in the portable contract. Ambiguous species/content
choices and unsupported schema types fail extraction instead of guessing.

`mpgs/island_encounter.py` installs the missing cooked net properties and class
ancestry, retaining RPC names for correct field ordering. RPC payload signatures
not implemented by this layer explicitly fail as unsupported. Both source-built
and disk-loaded worlds install the same definitions. Arrival completion queues
one dynamic Lesser Embermane at the nearest authored center
`BP_PatrolCenterPoint_263` (-10033.564, 4907.358, 261.01343), then publishes its
reference in GameState.SpawnedBehemoths. There is no timer-based early spawn, and
repeated completion callbacks cannot duplicate the encounter.

All 30 tests passed. Coverage includes early gameplay acknowledgement arriving
before cinematic completion, and the completion path through the actual
session parser, bit-exact spawn packet decoding with all requested fields
present, separate dynamic IDs per session, duplicate suppression, the arrival
gate and a disk template round trip. The four encounter tests also passed independently after adding explicit
unsupported-RPC handling and arrival-order coverage.

Acceptance boundary: this is owner-session spawn replication. AI, authoritative
health/damage, shared encounter simulation, intro cinematic, defeat, loot and
rewards are not yet implemented. A visible live Embermane spawn is still pending.
The client remains stock.

Deployment is waiting for the Windows UAC prompt (`consent.exe` PID 43224).
The existing island PID 9892 remains running; no new preparation state exists
yet. The pending verified restart helper is `logs/restart-island-encounter.ps1`;
its result will be written to `logs/island-encounter-preparation.json`.

## Grounded posture, spotting gate, patrol/combat movement and damage: October 18, 2026

The user reported three observations from the live island test (`logs/islandserver_20261018_071503.log`):
1. **Body positioning**: The Lesser Embermane (`lerawr_beta_bp_C`) spawned hunched/distorted with hind legs stretched downward and front paws floating.
2. **No movement**: The Behemoth only played its idle standing animation with no patrol or combat movement.
3. **Premature compass reveal**: The player could see the Behemoth's location and revealed icon on the compass immediately upon landing, whereas in retail Dauntless it was not shown until spotted.

Root causes and fixes implemented in `GameServer/mpgs/island_encounter.py`, `GameServer/mpgs/genworld.py`, and `GameServer/mpgs/server.py`:
- **Grounded Arena Elevation & Full-Body IK**: `BP_PatrolCenterPoint_263` is an airborne design marker at `Z = 261.01343`, while the arena floor (at the linked `ConnectedAetherVent`, `aether_vent_bp_355`) is at `Z = 18.325516` and `CharacterMesh0.RelativeLocation.Z = -149.0`. Spawning at `Z = 261.01` left the mesh soles ~94 cm above the ground with `ReplicatedMovementMode = 0`, causing full-body ground IK to stretch the hind legs downward. `island_encounter.extract` now extracts each patrol center's `groundZ` from its `ConnectedAetherVent` and `capsuleOffset = 149.0` from `CharacterMesh0`, spawning the root at `groundZ + capsuleOffset = 167.325516` with `ReplicatedMovementMode = 1` (`MOVE_Walking`), `Curving = 0`, and `DisableIkinema = 1`.
- **Spotting Gate (Compass Concealment Until Spotted)**: `island_encounter.start` no longer populates `GameState.SpawnedBehemoths` or sets `bImageRevealed = 1` when the player finishes arrival 14,000 units away at the landing pad. Instead, `island_encounter.reveal` is triggered when the player approaches within the authored `aggro_bpc_C.EnterCombatRange` (`4000.0` units) or lands a damage hit, populating `GameState.SpawnedBehemoths`, `BehemothState = 1`, `bImageRevealed = True`, `IsAlerted = True`, `InCombat = True`, `BehemothMusicState = 1`, `ChosenTarget`, `LockedTarget`, and queuing `MulticastRegisterStartOfCombat`.
- **15 Hz Patrol & Combat Movement (`step_movement`)**: Driven from `server.py`'s 15 Hz movement loop, the Behemoth walks a calm circular patrol (`radius = 450.0`, `speed = 220.0 u/s`) while unspotted, then smoothly turns toward and stalks/approaches the player at `walkSpeed = 400.0 u/s` in combat, emitting unreliable `ReplicatedMovement` + `ReplicatedMovementMode = 1` bunches on channel 19.
- **Authoritative Health, Floating Combat Text & Defeat (`handle_damage`)**: `GenSession.on_client_bunch` dispatches `DamageComponent.ServerTryDoDamage` to `island_encounter.handle_damage`, which reveals the Behemoth if unspotted, decrements `health` from `BaseHealth` (`18900.0`), replicates `HealthDEPRECATED_DO_NOT_USE`, invokes `UpdateHealth`, and sends `MulticastSendMultitypeCombatText` on the exported `floating_combat_text_bpc` component (`guid 44002`). When health reaches `0`, it sets `IsDead = True`, `bCanBeDamaged = False`, invokes `MulticastSetBehemothCollision(CollisionOn=0)` and `MultiDissolve`, and clears `GameState.SpawnedBehemoths`.

## Encounter defeat rewards, progression XP, quest completion and return travel: October 18, 2026

Implemented Step 5 across the encounter, arrival, progression, quest, and backend layers:
- **Cooked Kill Reward Extraction (`island_encounter.extract`)**: Extracts `killRewardItem = metadata['Exploration_GenericKillRewardType']['RowName']` (`'ORB_FLAME'`), `huntStartedRewards = metadata['HuntStartedItemRewards']` (`['TOKEN_HUNT_COUNT_LERAWR_BETA']`), and `eomLootId = defaults['EoM_LootID']` (`'BUNDLE_KILL_LERAWR_BETA_00'`) into `Adventure_IslandA_D24.contract.json`.
- **Defeat Banner, Loot Notifications & Deterministic Inventory Grant (`island_encounter.grant_defeat_rewards`)**: On fatal damage, sends `BehemothSlain_Notification`, `Client_ShowBehemothSlainChatMessage`, `ClientSetEncounteredState`, and `NotifyOwningClientAboutConditionalLoot` on `BPPC_Adventure_PlayerController_C`. Builds a deterministic transaction (`rewards.build_transaction`) for `ORB_FLAME` (x10), `CURRENCY_NOTES` (x250), `CURRENCY_WEAPONXP_SMALL` (x1), and `TOKEN_HUNT_COUNT_LERAWR_BETA` (x1), persists it to `/inventory`, and queues V2 item state updates (`_main_queue`).
- **Player & Weapon XP Replication + Idempotent Backend Persistence**: Grants `+150` XP to `ExperienceTrack_PlayerLevel` and the equipped weapon track, replicating `LastExperienceGranted.*` on `BPPC_Adventure_PlayerController_C`, `PlayerProgress` on `progression_bpc_C`, and `WeaponExperienceDetails.*` on `adventure_player_state_bp_C`. `DB.add_progress` and `POST /progression/{account}/{track}/{amount}` now accept `x-transaction-id` and deduplicate against the SQLite `transactions` table so retries are idempotent.
- **FTUE First-Hunt Quest Completion (`CR20_PJM_FTUE_01`)**: Advances `CR20_PJM_FTUE_01` from `Accepted` (`1`) to `Redeemable` (`2`) and its objective (`BB60F69042F1165642414C81ADFC0E5C`) to `Completed` (`2`, `CurrentAmount = 1`), replicating both sub-objects on `quest_channel` and persisting to `/character` via `quests.persist_status(..., objectives=...)`.
- **Return to Arrival Area & Return to Ramsgate**: `island_arrival.handle_rpc` handles `Server_MovePlayersBackToArrivalArea` on `BPPC_Adventure_PlayerController_C` (`ClientSetLocation` + `TeleportOnOwningClient` to `starts[0]`) and `ServerSetUserWantsToLeaveMatch` / `ServerSetUserWantsToRetryMatch` on `adventure_player_state_bp_C`. `Gameplay.join` routes explicit `gameMode == 'CITY'` / `gameType == 'CITY'` requests back to Ramsgate even when `PHOENIX_HUNT_ID` is set.
- **Verification**: All 34 unit tests in `test_hunts.py`, `test_island_world.py`, `test_island_arrival.py`, and `test_island_encounter.py` pass.
