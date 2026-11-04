# Pre-fight checklist

Fill this in before asking anyone to run a live test. Every test item must be **testable in the current configuration**:
the scenario comes from the Atlas, and each condition is verified on the running servers and on the character the
client actually loaded, not assumed from source or from a database edit. A box left unchecked means the item is not
handed off. Copy the template below for each live session and keep the filled copy with the test results.

## Template

```
PRE-FIGHT  <date>  <hunt id>  (<in-game route>)

1. COMPOSITION (from the Atlas)
[ ] python -m tools.atlas plan <hunt> --behemoth <variant> --weapon <type> --item <QI_...>   output saved
[ ] Every test item below maps to a "check:" or "Live checks recorded as needed" line in that plan
[ ] Known findings in the plan that can block a test item are listed under 6 with their status

2. SERVER CONFIG
[ ] The server process for this hunt (port, pid) started after the last code change, or DEV RELOAD logged it
[ ] Its environment matches the plan: behemoth engine on (log: "ENCOUNTER engine on"), test unlocks as needed
[ ] Engine manifest and template exist for the hunt; offline tracer for this composition has 0 unhandled nodes

3. CHARACTER (backend, read-only check first)
[ ] Account / character id the client logs in as
[ ] Equipped weapon is the plan's weapon type            (loadout.weapon = ...)
[ ] Second weapon present if a swap is tested             (loadout.subweapon = ...)
[ ] Weapon level meets the hunt gate, or the session-only test levels apply   (log: PROGRESSION applied)
[ ] Each quick item tested is OWNED (quantity > 0) and in the active loadout at slot N   (key from Input.ini)
[ ] Any change made here is made before the client loads the character, through the backend, and recorded

4. CLIENT LOADED IT (server log, not the database)
[ ] Login / arrival log shows the loadout:  PLAYERDATA fetched, QUICKITEMS applied N, EQUIP weapon ..., WEAPON PARTS ...
[ ] On arrival: QUICKITEMS granted slot=N item=<QI_...> for each tested item
[ ] If the character changed after login: client relaunched or re-zoned, and the lines above re-checked

5. ROUTE AND INPUTS
[ ] Exact menu path (plan "In-game location"), private hunt if level-gated, warning dialogs named
[ ] Each action's key from Saved/Config/WindowsClient/Input.ini (Primary Attack LMB, Weapon Swap V, Quickslot N = N ...)
[ ] The person driving has the list; no input automation of the client

6. TEST ITEMS (one row each)
   item | player action (key) | proving log line / checker row | blocking finding | testable now? (Y/N + why)

7. AFTER
[ ] Checker run on the newest log for that hunt; results and the filled checklist recorded in the gap model
```

## Where each fact comes from

| Condition | Source |
|---|---|
| Composition, checks, known findings | `python -m tools.atlas plan ...` |
| Hunt level gate, region, route | Atlas islands sheet / matchmaker hunts table (`MinimumAnyWeaponLevel`, `Region`, plan "In-game location") |
| Loadout, owned items | Phoenix backend `Backend/data/phoenix.sqlite` tables `loadouts`, `stacks`, `items` (read-only unless a change is part of the setup) |
| What the client loaded | Phoenix server logs: `PLAYERDATA`, `QUICKITEMS`, `EQUIP`, `WEAPON PARTS`, `PROGRESSION` lines |
| Key bindings | `%LOCALAPPDATA%\Archon\Saved\Config\WindowsClient\Input.ini` |
| Server running config | `logs/pids.json`, the process command line and environment, the hunt server's startup log |
