"""Generate world/recipes.json (catalogId -> craft / purchase cost, items per craft and the quest unlock gate)
from the 2.1.1 catalog exports (game data: generated locally, not distributed).
python -m tools.export_recipes [all-datatables-211 directory]
"""
import glob
import json
import os
import sys

DEFAULT = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'debug', 'exports', 'all-datatables-211')
OUT = os.path.join(os.path.dirname(__file__), '..', 'world', 'recipes.json')
QUEST_STATUS = {'EQuestStatus::Accepted': 1, 'EQuestStatus::Redeemable': 2, 'EQuestStatus::Redeemed': 3}


def _rows(path):
    d = json.load(open(path, encoding='utf-8-sig'))
    rows = d[0].get('Rows') if isinstance(d, list) else d.get('Rows')
    return rows if isinstance(rows, dict) else {}


def _level0(entries, recipes):
    """Ingredients [[catalogId, amount]] of the Level_00 recipe, or None."""
    for e in entries or []:
        if e.get('ItemLevel') == 'EUpgradeableItemLevel::Level_00':
            row = recipes.get((e.get('CraftingRecipe') or {}).get('RowName'))
            if row is not None:
                return [[i['Item']['RowName'], int(i['Amount'])] for i in row.get('Ingredients') or []
                        if (i.get('Item') or {}).get('RowName') and int(i.get('Amount') or 0) > 0]
    return None


def _unlock(refs, criteria):
    """Quest gate of the crafting unlock criteria: [[quest, [allowed statuses]]] (any listed quest state passes)."""
    gate = {}
    for ref in refs or []:
        row = criteria.get(ref.get('RowName')) or {}
        if row.get('QuestRequirementsOperation', '').endswith('Disabled'):
            continue
        for q in row.get('QuestRequirements') or []:
            st = QUEST_STATUS.get(q.get('QuestStatusRequired'))
            if q.get('QuestName') and st:
                gate.setdefault(q['QuestName'], set()).add(st)
    return [[q, sorted(s)] for q, s in sorted(gate.items())]


def main(src=DEFAULT):
    find = lambda name: _rows(glob.glob(os.path.join(src, '*_%s.json' % name))[0])
    recipes, criteria = find('crafting_recipes'), find('crafting_unlock_criteria')
    out = {}
    for path in sorted(glob.glob(os.path.join(src, '*Catalogs_*catalog*.json'))):
        for cid, row in _rows(path).items():
            if not isinstance(row, dict):
                continue
            craft = _level0(row.get('CraftingRecipesPerLevel'), recipes)
            buy = _level0(row.get('PurchaseRecipesPerLevel'), recipes)
            if craft is None and buy is None:
                continue
            rec = {'num': int(row.get('NumToCraft') or 1)}
            if craft is not None:
                rec['craft'] = craft
                rec['unlock'] = _unlock(row.get('CraftingUnlockCriteria'), criteria)
            if buy is not None:
                rec['purchase'] = buy
            out[cid] = rec
    json.dump({'source': 'catalog CraftingRecipesPerLevel / PurchaseRecipesPerLevel Level_00 (2.1.1 export)',
               'recipes': out}, open(OUT, 'w'), indent=1, sort_keys=True)
    print('recipes', len(out), 'craftable', sum('craft' in r for r in out.values()),
          'purchasable', sum('purchase' in r for r in out.values()))


if __name__ == '__main__':
    main(*sys.argv[1:])
