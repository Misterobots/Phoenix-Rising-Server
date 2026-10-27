"""Write data/2.1.1/gameplay_tags.json: the gameplay tag net index table (UGameplayTagsManager::ConstructNetIndex).

  python -m tools.export_gameplay_tags <DefaultGameplayTags.ini>

(AssetDump raw <dir> Archon/Config/DefaultGameplayTags.ini extracts it from the paks.)

Every tag in the ini and every parent node of it, names compared case-insensitively (FName: 'Behemoth.Lerawr' and
'Behemoth.lerawr' are one node), sorted case-insensitively (FCompareFGameplayTagNodeByTag -> FName::Compare); the position is the
net index. No CommonlyReplicatedTags in the ini; no native tags were found to add. Checked against live traffic (2026-10-26):
ServerTryActivateAbilityWithEventData EventTag Combat.Shoot.X = 1073 and Combat.Shoot.Y = 1074 come out at exactly those indices.
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, '..', 'data', '2.1.1', 'gameplay_tags.json')


def build(ini_text):
    nodes = {}
    for tag in re.findall(r'GameplayTagList=\(Tag="([^"]+)"', ini_text):
        parts = tag.split('.')
        for i in range(1, len(parts) + 1):
            name = '.'.join(parts[:i])
            nodes.setdefault(name.lower(), name)
    return sorted(nodes.values(), key=str.lower)


def main():
    with open(sys.argv[1], encoding='utf-8', errors='replace') as f:
        table = build(f.read())
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(table, f, indent=0)
    print('wrote %s: %d nodes; Combat.Shoot.X = %d' % (OUT, len(table), table.index('Combat.Shoot.X')))


if __name__ == '__main__':
    main()
