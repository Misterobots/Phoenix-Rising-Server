"""Turn an account's character into a test character: the beginner quests done, weapons at level 10, talent materials.

    python Backend/tools/make_test_character.py <player name> [--level 10] [--series D24_A_MAIN_QUESTS,...]

Run with the game closed (the server writes these too). It edits Backend/data/phoenix.sqlite in place after copying it to
phoenix.sqlite.bak-<name>. What it sets:
- quests: every quest of the D24 (Awakening) series Redeemed (EQuestStatus 3) in the character's SERIE_* data, which the
  game server merges over data/2.1.1/starting_quests.json and replicates. The quests' rewards are not granted.
- weapon levels: every ExperienceTrack_Weapon_* progression raised to the XP total of --level (curves from the game's
  progression config), never lowered. Weapon talents open at weapon level 10 (weapon_talent_catalog WeaponUnlockLevel).
- stacks: talent essences, notes, gauntlet coins and marks for every talent perk recipe (crafting_recipes TALENT_SHARED_1-5).
- weapon talents: every perk of every owned weapon's talents unlocked in the game server's /talents store (restored on join with
  ClientSetWeaponTalentPerkUnlockState). The talent crafting screen's "requires weapon level" check is native and not matched
  yet (live 2026-10-28: still locked at class level 10), so the perks are unlocked here; equip them in game.
Test data only: none of this is how a player earns it.
"""
import argparse
import json
import os
import shutil
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
DB = os.path.join(ROOT, 'Backend', 'data', 'phoenix.sqlite')
sys.path.insert(0, os.path.join(ROOT, 'Backend'))
sys.path.insert(0, os.path.join(ROOT, 'GameServer'))

SERIES = ('D24_A_MAIN_QUESTS', 'D24_B_SIDE_QUESTS', 'D24_C_BEHEMOTHS', 'D24_D_TUTORIALS')
STACKS = {'CURRENCY_TALENT_ESSENCE_PRIMARY': 200, 'CURRENCY_TALENT_ESSENCE_SECONDARY': 200, 'CURRENCY_NOTES': 250000,
          'CURRENCY_GAUNTLET_COIN': 50, 'CURRENCY_MARKS_STEEL': 2000, 'CURRENCY_MARKS_GILDED': 2000}
REDEEMED = 3


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('name')
    ap.add_argument('--level', type=int, default=10)
    ap.add_argument('--series', default=','.join(SERIES))
    a = ap.parse_args()
    from phoenix_backend.db import DB as Store
    from mpgs import progression
    con = sqlite3.connect(DB)
    row = con.execute('SELECT account_id FROM accounts WHERE display_name = ?', (a.name,)).fetchone()
    if row is None:
        sys.exit('no account named %s (launch once with .\\Play.bat -Name %s first)' % (a.name, a.name))
    acct = row[0]
    chars = [r[0] for r in con.execute('SELECT character_id FROM characters WHERE account_id = ?', (acct,))]
    if not chars:
        sys.exit('account %s has no character yet: create one in game first' % a.name)
    con.close()
    shutil.copyfile(DB, DB + '.bak-' + a.name)
    db = Store(DB)

    catalog = {s['id']: s for s in json.load(open(os.path.join(ROOT, 'GameServer', 'data', '2.1.1', 'quests.json')))['series']}
    con = sqlite3.connect(DB)
    for ch in chars:
        raw = con.execute('SELECT data FROM characters WHERE character_id = ?', (ch,)).fetchone()[0]
        data = json.loads(raw)
        wrapped = isinstance(data, str)
        if wrapped:
            data = json.loads(data)
        n = 0
        for sid in a.series.split(','):
            s = catalog.get(sid)
            if s is None:
                print('  series %s not in the quest catalog; skipped' % sid)
                continue
            key = 'SERIE_' + sid.lower()
            serie = json.loads(data.get(key) or '{}')
            serie['ID'] = sid
            for q in s['quests']:
                serie[q['guid']] = dict(serie.get(q['guid']) or {}, Status=REDEEMED)
                n += 1
            data[key] = json.dumps(serie)
        text = json.dumps(data)
        con.execute('UPDATE characters SET data = ? WHERE character_id = ?', (json.dumps(text) if wrapped else text, ch))
        con.commit()
        print('%s: %d quests redeemed' % (ch, n))
        db.apply_transaction(ch, 'test-character:%s:stacks' % ch,
                             add_stacked=[{'catalogId': c, 'quantity': q} for c, q in STACKS.items()])
        print('%s: stacks %s' % (ch, STACKS))
    con.close()

    from mpgs import talents
    cat = talents.catalog()
    for ch in chars:
        state = db.get(ch, 'weapon_talents', {}) or {}
        n = 0
        for it in db.items(ch):
            for t in cat.weapons.get(it['catalog_id'], []):
                for i in range(len(cat.talents[t]['perks'])):
                    state = talents.set_flag(state, it['instance_id'], t, i, 'unlocked', True)
                    n += 1
        db.put(ch, 'weapon_talents', state)
        print('%s: %d talent perks unlocked' % (ch, n))

    curves = progression.load_curves()
    for track, curve in sorted(curves.items()):
        if not track.startswith('ExperienceTrack_Weapon_') or not isinstance(curve, list):
            continue
        want = sum(curve[:a.level])
        have = db.progression(acct).get(track, 0)
        if want > have:
            db.add_progress(acct, track, want - have)
        print('%s: %d -> %d XP (level %s)' % (track, have, max(have, want), progression.level(curve, max(have, want))[0]))


if __name__ == '__main__':
    main()
