"""Build the ground-height grid for a landscape map (cora_poodle) from AssetDump output.

  AssetDump dump <json dir> Archon/Content/Maps/islands/1702/cora_poodle.umap
  AssetDump texture <tex dir> Archon/Content/Maps/islands/1702/cora_poodle.umap
  python -m tools.export_terrain <map json> <tex dir> [--name cora_poodle] [--out world/maps]

Writes, next to each other:
  <name>_height.bin   uint16 LE, row-major, width x height quad samples; 0xFFFF = no landscape there
  <name>_height.json  header: origin (world cm), quad step (cm), z mapping, byte order picked, checks

World position of sample (qx, qy) is origin + (qx*quad_x, qy*quad_y); z = origin_z + z_scale * (h - 32768) / 128
(the usual UE landscape mapping). The byte order of the RGBA heightmap texel is chosen from the data: each candidate is
scored by how smooth the decoded grid is (mean step between neighbouring samples); the smoothest wins.

Generated data only (like rock.json); it is not distributed.
"""
import argparse
import json
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.normpath(os.path.join(HERE, '..'))
VERTS = 32  # samples per component side (ComponentSizeQuads 31 + 1)
NODATA = 0xFFFF
# texel memory order is B, G, R, A (PF_B8G8R8A8); candidates are (hi channel, lo channel) pairs
CANDIDATES = {'R_hi_G_lo': (2, 1), 'G_hi_R_lo': (1, 2), 'B_hi_G_lo': (0, 1),
              'G_hi_B_lo': (1, 0), 'R_hi_B_lo': (2, 0), 'B_hi_R_lo': (0, 2)}


def components(map_json):
    """(qx0, qy0, texture number) for each LandscapeComponent, plus landscape root transform."""
    d = json.load(open(map_json, encoding='utf-8'))
    comps = []
    for e in d:
        if e.get('Type') != 'LandscapeComponent':
            continue
        p = e['Properties']
        loc = p.get('RelativeLocation') or {}
        m = re.search(r'Texture2D_(\d+)', (p.get('HeightmapTexture') or {}).get('ObjectName', ''))
        if not m:
            continue
        comps.append((int(round(loc.get('X', 0.0))), int(round(loc.get('Y', 0.0))), int(m.group(1))))
    land = next(e for e in d if e.get('Type') == 'Landscape')
    root = next(e for e in d if e.get('Type') == 'SceneComponent' and e.get('Outer', {}).get('ObjectName', '').startswith("Landscape'"))
    rp = root['Properties']
    return comps, rp['RelativeLocation'], rp['RelativeScale3D']


def texel_heights(tex_path, hi, lo):
    raw = open(tex_path, 'rb').read()
    out = []
    for i in range(0, len(raw), 4):
        out.append((raw[i + hi] << 8) | raw[i + lo])
    return out  # VERTS*VERTS, row-major (y, x)


def build(comps, tex_dir, name, hi, lo):
    grid = {}
    samples = {}  # (qx, qy) -> list of heights from each component covering that quad
    for qx0, qy0, tn in comps:
        path = None
        for f in os.listdir(tex_dir):
            if f.startswith(f'{name}__Texture2D_{tn}.') and f.endswith('.bin'):
                path = os.path.join(tex_dir, f)
                break
        if not path:
            raise FileNotFoundError(f'texture Texture2D_{tn} not in {tex_dir}')
        h = texel_heights(path, hi, lo)
        for y in range(VERTS):
            for x in range(VERTS):
                k = (qx0 + x, qy0 + y)
                v = h[y * VERTS + x]
                samples.setdefault(k, []).append(v)
    return samples


def roughness(samples):
    """Mean |height step| between neighbouring samples. Wrong byte order gives noisy steps; the right one is smooth.

    Shared edges cannot score byte order: neighbouring components store identical texels there, so every candidate
    agrees on them.
    """
    grid = {k: sum(v) / len(v) for k, v in samples.items()}
    steps = []
    for (x, y), v in grid.items():
        for n in ((x + 1, y), (x, y + 1)):
            if n in grid:
                steps.append(abs(grid[n] - v))
    return (sum(steps) / len(steps)) if steps else float('inf'), len(steps)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('map_json')
    ap.add_argument('tex_dir')
    ap.add_argument('--name', default='cora_poodle')
    ap.add_argument('--out', default=os.path.join(SERVER, 'world', 'maps'))
    a = ap.parse_args()

    comps, loc, scale = components(a.map_json)
    if not comps:
        sys.exit('no LandscapeComponent with a heightmap texture found')

    scored = {}
    for cname, (hi, lo) in CANDIDATES.items():
        samples = build(comps, a.tex_dir, a.name, hi, lo)
        scored[cname] = (roughness(samples), samples)
    best = min(scored, key=lambda c: scored[c][0][0])
    (mean_diff, n_pairs), samples = scored[best]

    xs = [k[0] for k in samples]
    ys = [k[1] for k in samples]
    x0, y0 = min(xs), min(ys)
    w, h = max(xs) - x0 + 1, max(ys) - y0 + 1
    grid = [NODATA] * (w * h)
    for (qx, qy), vals in samples.items():
        grid[(qy - y0) * w + (qx - x0)] = int(round(sum(vals) / len(vals)))

    os.makedirs(a.out, exist_ok=True)
    bin_path = os.path.join(a.out, f'{a.name}_height.bin')
    with open(bin_path, 'wb') as f:
        f.write(struct.pack(f'<{len(grid)}H', *grid))

    header = {
        'source': 'Archon/Content/Maps/islands/1702 landscape (2.1.1 paks) via tools/AssetDump',
        'width': w, 'height': h,
        'quad_origin': [x0, y0],
        'origin_world': [loc['X'] + scale['X'] * x0, loc['Y'] + scale['Y'] * y0, loc['Z']],
        'quad_world': [scale['X'], scale['Y']],
        'z_world': {'base': loc['Z'], 'scale': scale['Z'], 'formula': 'z = base + scale * (h - 32768) / 128'},
        'landscape_location': [loc['X'], loc['Y'], loc['Z']],
        'landscape_scale': [scale['X'], scale['Y'], scale['Z']],
        'byte_order': best, 'byte_order_scores': {c: scored[c][0][0] for c in scored},
        'components': len(comps), 'mean_neighbour_step_raw': mean_diff, 'neighbour_pairs': n_pairs,
        'nodata': NODATA,
        'format': 'uint16 little-endian, row-major (y then x); sample (qx,qy) at ((qy-quad_origin[1])*width + (qx-quad_origin[0]))',
    }
    with open(os.path.join(a.out, f'{a.name}_height.json'), 'w', encoding='utf-8') as f:
        json.dump(header, f, indent=1)

    filled = sum(1 for v in grid if v != NODATA)
    print(f'byte order {best} (mean step {mean_diff:.1f} over {n_pairs} neighbour pairs); '
          f'grid {w}x{h}, filled {filled}/{w * h}; components {len(comps)}')
    print('scores', {c: round(scored[c][0][0], 1) for c in scored})


if __name__ == '__main__':
    main()
