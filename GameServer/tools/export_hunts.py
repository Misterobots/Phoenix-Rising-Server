"""Export authored 2.1.1 hunt definitions, preserving unresolved references.

python tools/export_hunts.py <all-datatables-211 directory>
This exports game data, not playable-world templates or runtime support claims.
"""
import argparse
import hashlib
import json
from pathlib import Path

TABLE = 'Archon_Content_Gameplay_hunts_matchmaker_hunts_table.json'
SPECS = 'Archon_Content_gamemodes_exploration_data_tables_exploration_island_spec_table.json'
DEFAULT_OUT = Path(__file__).resolve().parents[1] / 'data/2.1.1/hunts.json'


def rows(path):
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if isinstance(data, list):
        if len(data) != 1:
            raise ValueError(f'{path.name}: expected one DataTable export')
        data = data[0]
    result = data.get('Rows')
    if not isinstance(result, dict):
        raise ValueError(f'{path.name}: missing Rows')
    return result


def export(source):
    source = Path(source)
    hunts, specs = rows(source / TABLE), rows(source / SPECS)
    result = {}
    for hunt_id, row in hunts.items():
        maps = []
        for entry in row.get('MapList', []):
            asset = (entry.get('MapAsset') or {}).get('AssetPathName')
            if not isinstance(asset, str) or not asset.startswith('/Game/'):
                raise ValueError(f'{hunt_id}: missing map asset')
            maps.append({'name': entry['MapName'], 'asset': asset,
                         'package': asset.split('.', 1)[0],
                         'weight': entry.get('Weighting', 0), 'definition': entry})
        ref = row.get('GameModeSpecificData') or {}
        table = (ref.get('DataTable') or {}).get('ObjectPath', '')
        spec_id = ref.get('RowName')
        spec = None
        if table.endswith('/exploration_island_spec_table.0') and spec_id not in (None, '', 'None'):
            if spec_id not in specs:
                raise ValueError(f'{hunt_id}: missing exploration spec {spec_id}')
            spec = specs[spec_id]
        result[hunt_id] = {'maps': maps, 'gameModeOverride': row.get('GameModeOverride') or None,
                           'islandSpec': spec, 'definition': row}
    return {'format': 'phoenix-hunts', 'version': 1, 'build': '2.1.1',
            'sources': [{'file': name, 'sha256': hashlib.sha256((source / name).read_bytes()).hexdigest()}
                        for name in (TABLE, SPECS)], 'hunts': result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    data = export(args.source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(f"Exported {len(data['hunts'])} authored hunts to {args.output}")


if __name__ == '__main__':
    main()
