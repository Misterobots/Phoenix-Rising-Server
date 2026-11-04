# PRE-FIGHT  2026-11-04  Adventure_IslandP_D24  (Ostian Borderlands > Hades Reach)

Template: [tools/atlas/PREFIGHT_CHECKLIST.md](../../../../tools/atlas/PREFIGHT_CHECKLIST.md)

## 1. Composition (from the Atlas)
- [x] `python -m tools.atlas plan Adventure_IslandP_D24 --behemoth sally_normal --weapon repeaters --item QI_FROSTBITE_PYLON`
- [x] Test items map to plan checks: pylon activation (actor/effect, charge decrement, no traceback); weapon parts granted and swap without missing-GUID; behemoth turns, navigates, attacks.
- [x] Blocking findings: `preview_missing_guid_allocator` (source fix, client-unvalidated); `island_p_behemoth_engine_disabled` (launcher fixed, client-unvalidated).

## 2. Server config
- [x] previewserver pid 37736, udp 8792, started 06:45 after the last code change; env: test unlocks 1, inventory V2, dev reload; engine default on.
- [ ] "ENCOUNTER engine on" for sally_normal: logged only on arrival, so check it on arrival.
- [x] Engine manifest `Adventure_IslandP_D24.engine_data.json` present; tracer melee/kite 0 unhandled (57 / 25 attacks).

## 3. Character (U5GKKE7DOJWYTE2NIIFCESP3SC, backend)
- [ ] **Equipped weapon is the plan's type: NO.** `WP_IH_SHOCK_01` is a war hammer; the plan was composed for repeaters. The Atlas lists hammers as shared-runtime-only, so weapon-specific checks are not testable with this loadout.
- [ ] **Second weapon for the swap: NO.** `subweapon` is empty, so V has nothing to swap to. The swap test is **not testable** in this config.
- [x] Level gate: the hunt has `MinimumAnyWeaponLevel -1` and requires quest D24_A_MAIN_03B redeemed; session-only test weapon levels of 20 apply (log: PROGRESSION applied).
- [x] Quick items owned: QI_FROSTBITE_PYLON 8, QI_GRENADE_ICE 20. Active loadout quick_items = [QI_GRENADE_ICE, QI_FROSTBITE_PYLON], so the pylon is slot index 1 = key 2.
- [x] The change was made through the backend: transaction `test-livecheck:quickitems:20261104`, DB backup `phoenix.sqlite.bak-quickitems-20261104`. It was made **after** the client had logged in.

## 4. Client loaded it
- [ ] The Ramsgate re-fetch logged `QUICKITEMS applied 5 active loadout slots`; 2 items are configured, so the count needs explaining before this counts as loaded. The user reports no pylon on the character, so this is **not verified**.
- [ ] On arrival: `QUICKITEMS granted slot=1 item=QI_FROSTBITE_PYLON` not yet seen.
- [ ] Relaunch the client (or re-log) after the loadout change, then re-check the lines above.

## 5. Route and inputs
- [x] Esc > Hunt > Hunting Grounds > Ostian Borderlands > Hades Reach > Private Hunt > Confirm (low weapon warning) > Go Now.
- [x] Keys from Input.ini: Primary Attack LMB, Weapon Swap V, Quickslot 2 = 2, Sprint LeftShift, Dodge Space.
- [x] User drives; no input automation.

## 6. Test items
| Item | Action (key) | Proof | Blocking finding | Testable now? |
|---|---|---|---|---|
| Skraev engages, attacks, hits accepted | fight in melee (LMB) | `ENCOUNTER AI ... attack`, `hit accepted` | engine-disabled finding (re-check engine on) | **Y** |
| Roar cut short takes over root motion | stay in melee about 1 min | roar montage followed by an attack inside its duration; zero `already moving` | none | **Y** |
| Frostbite Pylon spawn + charge | 2 | `QUICKITEMS granted ... FROSTBITE`, `QUICKITEM spawned authored actor`, stack 8 > 7 | missing-GUID allocator | **N until 4 passes** (client has not shown the pylon) |
| Weapon parts + swap, no missing GUID | V | second `EQUIP weapon`, `WEAPON PARTS granted`, no `failed`/GUID error | missing-GUID allocator | **N**: no subweapon in the loadout |
| Repeater-specific checks from the plan | n/a | n/a | n/a | **N**: hammer equipped |

## 7. After
- [ ] Run `livecheck.py` on the newest previewserver log; record the results in `evidence_gap_model.json`.

**Verdict: not ready to hand off.** Fix 3 and 4 first: set a subweapon, decide whether the test weapon is repeaters (as planned) or the hammer (re-plan with `--weapon hammer`), then relaunch the client and confirm the QUICKITEMS and EQUIP lines.
