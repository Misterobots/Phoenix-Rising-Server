"""Write world/behemoths/<variant>_bodies.json: the physics-asset bodies of a behemoth in actor space, reference pose.

  dotnet <AssetDump.dll> dump <abs dir> Archon/Content/Monsters/aq/lerawr/physics/lerawr_phat Archon/Content/Monsters/aq/lerawr/lerawr_skel
  python -m tools.export_behemoth_bodies <dir>/lerawr_skel.json <dir>/lerawr_phat.json --variant lerawr_beta --mesh-z -149 --mesh-scale 0.8

Damage windows (archon_damage_window_C) name the bones whose bodies deal damage. Each body is a capsule/sphere/box on a bone; its
centre is the bone's component-space reference pose applied to the body's local centre, then the CharacterMesh0 offset and scale
(the Blueprint's RelativeLocation.Z and RelativeScale3D) take it to actor space. Output: {bone: [{'pos': [x, y, z], 'radius': r,
'half_length': h}]} in cm, x forward.
"""
import argparse
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def _qmul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw, aw * bw - ax * bx - ay * by - az * bz)


def _qrot(q, v):
    x, y, z, w = q
    vx, vy, vz = v
    t = (2 * (y * vz - z * vy), 2 * (z * vx - x * vz), 2 * (x * vy - y * vx))
    return (vx + w * t[0] + y * t[2] - z * t[1], vy + w * t[1] + z * t[0] - x * t[2], vz + w * t[2] + x * t[1] - y * t[0])


def component_pose(skeleton):
    """FinalRefBonePose (parent-relative) -> {bone: (rotation quat, translation)} in component space."""
    ref = next(e for e in skeleton if isinstance(e, dict) and e.get('Type') == 'Skeleton')['ReferenceSkeleton']
    out, cs = {}, []
    for info, pose in zip(ref['FinalRefBoneInfo'], ref['FinalRefBonePose']):
        r, t = pose['Rotation'], pose['Translation']
        q, tr = (r['X'], r['Y'], r['Z'], r['W']), (t['X'], t['Y'], t['Z'])
        if info['ParentIndex'] >= 0:
            pq, pt = cs[info['ParentIndex']]
            rt = _qrot(pq, tr)
            q, tr = _qmul(pq, q), (pt[0] + rt[0], pt[1] + rt[1], pt[2] + rt[2])
        cs.append((q, tr))
        out[info['Name']] = (q, tr)
    return out


def bodies(skeleton, phat, mesh_z=0.0, mesh_scale=1.0):
    pose = component_pose(skeleton)
    out = {}
    for e in phat:
        if not isinstance(e, dict) or e.get('Type') != 'SkeletalBodySetup':
            continue
        p = e.get('Properties') or {}
        bone = p.get('BoneName')
        if bone not in pose:
            continue
        q, t = pose[bone]
        geom = p.get('AggGeom') or {}
        for kind in ('SphylElems', 'SphereElems', 'BoxElems'):
            for g in geom.get(kind, []):
                c = g.get('Center') or {'X': 0.0, 'Y': 0.0, 'Z': 0.0}
                r = _qrot(q, (c['X'], c['Y'], c['Z']))
                if kind == 'BoxElems':
                    radius, half = max(g.get('X', 0.0), g.get('Y', 0.0)) / 2.0, g.get('Z', 0.0) / 2.0
                else:
                    radius, half = g.get('Radius', 0.0), g.get('Length', 0.0) / 2.0
                pos = [(t[i] + r[i]) * mesh_scale for i in range(3)]
                pos[2] += mesh_z
                out.setdefault(bone, []).append({'pos': [round(v, 1) for v in pos], 'radius': round(radius * mesh_scale, 1),
                                                 'half_length': round(half * mesh_scale, 1)})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('skeleton')
    ap.add_argument('phat')
    ap.add_argument('--variant', required=True)
    ap.add_argument('--mesh-z', type=float, default=0.0)
    ap.add_argument('--mesh-scale', type=float, default=1.0)
    args = ap.parse_args()
    with open(args.skeleton, encoding='utf-8') as f:
        skeleton = json.load(f)
    with open(args.phat, encoding='utf-8') as f:
        phat = json.load(f)
    out = {'source': [os.path.basename(args.skeleton), os.path.basename(args.phat)], 'mesh_z': args.mesh_z,
           'mesh_scale': args.mesh_scale, 'bodies': bodies(skeleton, phat, args.mesh_z, args.mesh_scale)}
    path = os.path.join(HERE, '..', 'world', 'behemoths', args.variant + '_bodies.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(out, f, indent=1)
    print('%s: %d bones' % (os.path.normpath(path), len(out['bodies'])))


if __name__ == '__main__':
    main()
