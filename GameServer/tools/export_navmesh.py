"""Preserve Detour polygons and detail geometry from an AssetDump navmesh export."""
import argparse
import json
from pathlib import Path


def export_navmesh(source, destination, vertical_compensation=100.0, query_extent=(300.0, 300.0, 600.0)):
    raw = json.loads(Path(source).read_text(encoding='utf-8-sig'))
    tiles = []
    for tile in raw['tiles']:
        if not tile.get('valid', True):
            continue
        data = tile['detourTile']
        header = data['Header']
        tiles.append({
            'index': tile['index'], 'x': header['X'], 'y': header['Y'],
            'layer': header['Layer'], 'vertices': data['Vertices'],
            'polygons': data['Polys'], 'detailMeshes': data['DetailMeshes'],
            'detailVertices': data['DetailVertices'], 'detailTris': data['DetailTris'],
        })
    result = {
        'format': 'phoenix-detour-navmesh', 'version': 1,
        'source': raw['package'], 'sourceActor': raw['actor'],
        'navMeshVersion': raw['navMeshVersion'], 'parameters': raw['parameters'],
        'tileCount': len(tiles),
        'polygonCount': sum(len(tile['polygons']) for tile in tiles), 'tiles': tiles,
    }
    result['parameters']['VerticalDeviationFromGroundCompensation'] = vertical_compensation
    result['parameters']['DefaultQueryExtent'] = list(query_extent)
    destination = Path(destination)
    temporary = destination.with_suffix(destination.suffix + '.tmp')
    temporary.write_text(json.dumps(result, separators=(',', ':')) + '\n', encoding='utf-8')
    temporary.replace(destination)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source')
    parser.add_argument('destination')
    parser.add_argument('--vertical-compensation', type=float, default=100.0,
                        help='Native Recast actor query compensation, verified as 100 for Hades Reach')
    parser.add_argument('--query-extent', type=float, nargs=3, default=(300.0, 300.0, 600.0),
                        help='Agent DefaultQueryExtent from DefaultEngine.ini SupportedAgents (Behemoth: 300 300 600)')
    args = parser.parse_args()
    result = export_navmesh(args.source, args.destination, args.vertical_compensation, args.query_extent)
    print(f"Exported {result['tileCount']} tiles and {result['polygonCount']} polygons with detail geometry")
