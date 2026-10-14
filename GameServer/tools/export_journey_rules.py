"""Generate world/journey_rules.json from the 2.1.1 player_journey_table export (game data: generated locally, not distributed).

python -m tools.export_journey_rules [path to Archon_Content_Gameplay_PlayerJourney_player_journey_table.json]
Per node: parents (reverse of ChildNodes), currency costs, required quests (ObjectiveData_CompleteQuest), auto-unlock flag.
"""
import json
import os
import sys

DEFAULT = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'debug', 'exports', 'all-datatables-211',
                       'Archon_Content_Gameplay_PlayerJourney_player_journey_table.json')
OUT = os.path.join(os.path.dirname(__file__), '..', 'world', 'journey_rules.json')


def build(rows):
    nodes = {}
    for nid, r in rows.items():
        quests, other = [], []
        for o in r.get('Objectives') or []:
            od = o.get('Objective') or {}
            cls = ((od.get('ObjectiveDataClass') or {}).get('ObjectName') or '')
            if 'CompleteQuest' in cls and od.get('QuestId'):
                quests.append(od['QuestId'])
            else:
                other.append(cls.split("'")[1] if "'" in cls else cls)
        nodes[nid] = {
            'children': [c['RowName'] for c in r.get('ChildNodes') or []],
            'parents': [],
            'costs': [{'currency': c['Currency']['RowName'], 'amount': c['Amount']} for c in r.get('CurrencyCosts') or []],
            'quests': quests,
            'other_objectives': other,
            'auto_unlock': bool(r.get('bAutoUnlockIfParentUnlocked')),
        }
    for nid, n in nodes.items():
        for c in n['children']:
            if c in nodes:
                nodes[c]['parents'].append(nid)
    return nodes


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else DEFAULT
    rows = json.load(open(src))[0]['Rows']
    nodes = build(rows)
    json.dump({'source': 'player_journey_table (2.1.1 export)', 'nodes': nodes}, open(OUT, 'w'), indent=1)
    print('nodes', len(nodes), 'with costs', sum(1 for n in nodes.values() if n['costs']),
          'with quest requirements', sum(1 for n in nodes.values() if n['quests']),
          'other objective kinds', sorted({o for n in nodes.values() for o in n['other_objectives']}))


if __name__ == '__main__':
    main()
