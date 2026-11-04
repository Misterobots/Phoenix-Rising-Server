"""Compact point and connectivity queries over exported Detour navmesh polygons.

Coordinates in the export are Recast coordinates. Unreal vectors transform as
Recast=(-X, Z, -Y), matching UE's RecastHelpers conversion.
"""
import json
import base64
import math
import os
from collections import defaultdict, deque


class DetourNavMesh:
    def __init__(self, data):
        self.parameters = data.get('parameters') or {}
        self.polygons = []
        self._grid = defaultdict(list)
        self._links = defaultdict(set)
        self._portals = defaultdict(list)
        tile_polygons = []
        external_edges = defaultdict(list)
        for tile in data.get('tiles') or []:
            if tile.get('valid') is False:
                continue
            if tile.get('detourTile'):
                dt = tile['detourTile']
            else:
                dt = {'Vertices': tile.get('vertices') or [], 'Polys': tile.get('polygons') or [],
                      'DetailMeshes': tile.get('detailMeshes') or [],
                      'DetailVertices': tile.get('detailVertices') or [],
                      'DetailTris': tile.get('detailTris') or []}
            local_indices = {}
            tile_polygons.append((dt, local_indices))
            for local, p in enumerate(dt.get('Polys') or []):
                count = int(p.get('VertCount') or 0)
                if count < 3:
                    continue
                verts = [dt['Vertices'][i] for i in p['Verts'][:count]]
                idx = len(self.polygons)
                local_indices[local] = idx
                poly = {'verts': [(v['X'], v['Z']) for v in verts],
                        'vertices_3d': [(v['X'], v['Y'], v['Z']) for v in verts],
                        'z': sum(v['Y'] for v in verts) / count,
                        'flags': int(p.get('Flags') or 0),
                        'area': int(p.get('AreaAndType', 0)) & 0x3f}
                self.polygons.append(poly)
                poly['triangles'] = []
                if local < len(dt.get('DetailMeshes', [])):
                    detail = dt['DetailMeshes'][local]
                    extra = dt['DetailVertices'][detail['VertBase']:detail['VertBase'] + detail['VertCount']]
                    detail_vertices = poly['vertices_3d'] + [(v['X'], v['Y'], v['Z']) for v in extra]
                    for triangle in dt['DetailTris'][detail['TriBase']:detail['TriBase'] + detail['TriCount']]:
                        indices = base64.b64decode(triangle) if isinstance(triangle, str) else triangle
                        poly['triangles'].append(tuple(detail_vertices[i] for i in indices[:3]))
                for edge, neighbor in enumerate(p.get('Neis', [])[:count]):
                    side = int(neighbor) & 0xff
                    if int(neighbor) & 0x8000 and side in (0, 2, 4, 6):
                        a, b = poly['vertices_3d'][edge], poly['vertices_3d'][(edge + 1) % count]
                        axis = 0 if side in (0, 4) else 2
                        external_edges[(axis, round(a[axis], 2), side)].append((idx, edge, a, b))
                xs, zs = zip(*poly['verts'])
                poly['bounds'] = (min(xs), min(zs), max(xs), max(zs))
                # Tiles span ~988 UU; index every overlapped cell for point lookup.
                for gx in range(math.floor(poly['bounds'][0] / 988), math.floor(poly['bounds'][2] / 988) + 1):
                    for gz in range(math.floor(poly['bounds'][1] / 988), math.floor(poly['bounds'][3] / 988) + 1):
                        self._grid[(gx, gz)].append(idx)
        # Detour external-neighbor codes identify tile sides, but the AssetDump
        # projection omits runtime link arrays. Reconstruct shared-edge adjacency.
        edges = defaultdict(list)
        for i, p in enumerate(self.polygons):
            points = p['vertices_3d']
            for edge, (a, b) in enumerate(zip(points, points[1:] + points[:1])):
                key = tuple(sorted((tuple(round(v, 3) for v in a),
                                    tuple(round(v, 3) for v in b))))
                edges[key].append((i, edge))
        # Legacy hand-authored meshes can infer adjacency from shared edges.
        # Native exports use their encoded neighbors, including explicit walls.
        inferred_edges = () if data.get('format') in ('phoenix-detour-navmesh', 'assetdump-detour-navmesh') else edges.values()
        for polys in inferred_edges:
            if len(polys) > 1:
                for a, edge in polys:
                    for b, _ in polys:
                        if b != a:
                            self._links[a].add(b)
                            self._portals[a].append((edge, b, 0.0, 1.0))
        # Resolve Detour's tile-local polygon references after all tile offsets exist.
        for dt, local_indices in tile_polygons:
            for local, p in enumerate(dt.get('Polys') or []):
                src = local_indices.get(local)
                if src is None:
                    continue
                for edge, n in enumerate(p.get('Neis', [])[:int(p.get('VertCount') or 0)]):
                    n = int(n)
                    if n and not (n & 0x8000):
                        dst = local_indices.get((n & 0x3fff) - 1)
                        if dst is not None and dst != src:
                            self._links[src].add(dst)
                            self._portals[src].append((edge, dst, 0.0, 1.0))
        self._connect_external_edges(external_edges)

    def _connect_external_edges(self, edges):
        """Rebuild Detour tile portals, including partial edges and climb steps."""
        climb = float(self.parameters.get('WalkableClimb', 0))
        for (axis, plane, side), sources in edges.items():
            other_axis = 2 if axis == 0 else 0
            targets = edges.get((axis, plane, (side + 4) % 8), ())
            for src, edge, a, b in sources:
                amin, amax = sorted((a, b), key=lambda v: v[other_axis])
                for dst, _, c, d in targets:
                    bmin, bmax = sorted((c, d), key=lambda v: v[other_axis])
                    lo = max(amin[other_axis], bmin[other_axis])
                    hi = min(amax[other_axis], bmax[other_axis])
                    if hi - lo < 0.01:
                        continue
                    def height(v, w, coordinate):
                        return v[1] + (w[1] - v[1]) * (coordinate - v[other_axis]) / (w[other_axis] - v[other_axis])
                    dlo = height(bmin, bmax, lo) - height(amin, amax, lo)
                    dhi = height(bmin, bmax, hi) - height(amin, amax, hi)
                    if dlo * dhi >= 0 and min(abs(dlo), abs(dhi)) > climb * 2:
                        continue
                    if (abs(dlo) <= climb * 2) != (abs(dhi) <= climb * 2):
                        coordinate = lo if abs(dlo) <= climb * 2 else hi
                        point = [0.0, 0.0, 0.0]
                        point[axis], point[other_axis] = plane, coordinate
                        if abs(self._height(self.polygons[src], point[0], point[2]) -
                               self._height(self.polygons[dst], point[0], point[2])) > climb:
                            continue
                    lower, upper = sorted(((lo - a[other_axis]) / (b[other_axis] - a[other_axis]),
                                           (hi - a[other_axis]) / (b[other_axis] - a[other_axis])))
                    lower = int(max(0.0, min(1.0, lower)) * 255) / 255.0
                    upper = int(max(0.0, min(1.0, upper)) * 255) / 255.0
                    self._links[src].add(dst)
                    self._portals[src].append((edge, dst, lower, upper))

    def path_hits_navmesh(self, path, max_vertical=1024.0):
        """Native hit convention: an unprojectable start is not a boundary hit."""
        return self.path_query(path, max_vertical)['hit']

    def path_query(self, path, max_vertical=1024.0):
        """-> {hit, found, percentage} as PathHitsNavMesh reports them. The segment loop stops at the first hit, and
        OutPathPercentageBeforeHit is the distance travelled before the hit over the length of the segments walked,
        the hitting one included (native capture 2026-11-03: out-and-back, in-out and out-further paths agree)."""
        result = {'hit': False, 'found': False, 'percentage': 0.0}
        if len(path) < 2:
            return result
        compensation = max(0.0, float(self.parameters.get('VerticalDeviationFromGroundCompensation', 100.0)))
        projected = self.project_point(path[0], (100.0, 100.0, max_vertical + compensation))
        if projected is None:
            return result
        result['found'] = True
        current, start = projected
        before = walked = 0.0
        for end in path[1:]:
            length = math.dist(start, end)
            current, fraction = self._raycast(current, start, end)
            walked += length
            if current is None:
                before += length * fraction
                result['hit'] = True
                break
            before += length
            start = end
        result['percentage'] = before / walked if walked > 0.0 else 0.0
        return result

    @staticmethod
    def _height(poly, x, z):
        triangles = poly['triangles'] or [(poly['vertices_3d'][0], a, b)
                                         for a, b in zip(poly['vertices_3d'][1:], poly['vertices_3d'][2:])]
        for a, b, c in triangles:
            ux, uz, vx, vz = b[0] - a[0], b[2] - a[2], c[0] - a[0], c[2] - a[2]
            denominator = ux * vz - uz * vx
            if abs(denominator) < 1e-12:
                continue
            s = ((x - a[0]) * vz - (z - a[2]) * vx) / denominator
            t = (ux * (z - a[2]) - uz * (x - a[0])) / denominator
            if s >= -1e-6 and t >= -1e-6 and s + t <= 1.000001:
                return a[1] + s * (b[1] - a[1]) + t * (c[1] - a[1])
        return poly['z']

    def project_point(self, point, extent=(100.0, 100.0, 1024.0)):
        """Choose the nearest polygon in XY, with native vertical tie-breaking."""
        x, y, z = self.unreal_to_recast(point)
        candidates = set()
        for gx in range(math.floor((x - extent[0]) / 988), math.floor((x + extent[0]) / 988) + 1):
            for gz in range(math.floor((z - extent[1]) / 988), math.floor((z + extent[1]) / 988) + 1):
                candidates.update(self._grid.get((gx, gz), ()))
        best = None
        best_distance, best_height = float('inf'), float('inf')
        for index in sorted(candidates):
            poly = self.polygons[index]
            px, pz = x, z
            if not self._contains(poly, x, z):
                closest = []
                vertices = poly['verts']
                for a, b in zip(vertices, vertices[1:] + vertices[:1]):
                    dx, dz = b[0] - a[0], b[1] - a[1]
                    length = dx * dx + dz * dz
                    fraction = max(0.0, min(1.0, ((x - a[0]) * dx + (z - a[1]) * dz) / length)) if length else 0
                    qx, qz = a[0] + fraction * dx, a[1] + fraction * dz
                    closest.append(((qx - x) ** 2 + (qz - z) ** 2, qx, qz))
                _, px, pz = min(closest)
            height = self._height(poly, px, pz)
            vertical = abs(height - y)
            distance = (px - x) ** 2 + (pz - z) ** 2
            if abs(px - x) > extent[0] or abs(pz - z) > extent[1] or vertical > extent[2]:
                continue
            if distance < best_distance or (distance < best_distance + 1e-4 and vertical < best_height):
                best_distance, best_height = distance, vertical
                best = index, (-px, -pz, height)
        return best

    def _raycast(self, current, start, end):
        origin, target = self.unreal_to_recast(start), self.unreal_to_recast(end)
        visited = set()
        while current not in visited:
            visited.add(current)
            poly = self.polygons[current]
            clipped = self._clip_segment(poly, origin, target)
            if clipped is None:
                return None, 0.0
            _, upper, exit_edge = clipped
            if exit_edge is None:
                return current, 1.0
            a = poly['verts'][exit_edge]
            b = poly['verts'][(exit_edge + 1) % len(poly['verts'])]
            axis = 0 if abs(b[0] - a[0]) > abs(b[1] - a[1]) else 1
            coordinate = origin[axis * 2] + upper * (target[axis * 2] - origin[axis * 2])
            fraction = (coordinate - a[axis]) / (b[axis] - a[axis])
            neighbors = (dst for edge, dst, lo, hi in self._portals[current]
                         if edge == exit_edge and lo - 1e-9 <= fraction <= hi + 1e-9)
            nxt = next(neighbors, None)
            if nxt is None:
                return None, upper
            current = nxt
        return None, 0.0

    # ---- pathfinding (Detour findPath + findStraightPath, UE 5.1.1 Navmesh/Private/Detour/DetourNavMeshQuery.cpp) ----
    HEURISTIC_SCALE = 0.999          # ARecastNavMesh::HeuristicScale default
    MAX_SEARCH_NODES = 2048          # RECAST_MAX_SEARCH_NODES (filter GetMaxSearchNodes default)
    AREA_COST = {4: (10.0, 10.0)}    # area id -> (DefaultCost, FixedAreaEnteringCost); BP_Nav_BreakableTree_C; others (1, 0)

    def _area_cost(self, area):
        return self.AREA_COST.get(area, (1.0, 0.0))

    def _portal(self, src, dst):
        """getPortalPoints: the shared edge of src toward dst in recast space, clamped to the link's tile-edge limits."""
        for edge, other, lo, hi in self._portals[src]:
            if other == dst:
                verts = self.polygons[src]['vertices_3d']
                a, b = verts[edge], verts[(edge + 1) % len(verts)]
                if lo != 0.0 or hi != 1.0:
                    a, b = _lerp3(a, b, lo), _lerp3(a, b, hi)
                return a, b
        return None

    def _step_cost(self, pa, pb, cur, nxt):
        cost, _ = self._area_cost(self.polygons[cur]['area'])
        enter = 0.0
        if nxt is not None and self.polygons[nxt]['area'] != self.polygons[cur]['area']:
            enter = self._area_cost(self.polygons[nxt]['area'])[1]
        return math.dist(pa, pb) * cost + enter

    def find_path(self, start, end):
        """FPImplRecastNavMesh::FindPath -> {'points': [Unreal points], 'length', 'partial', 'cost'} or None when either end does not
        project. Ends project with the agent's DefaultQueryExtent (UE default 50, 50, 250), the vertical extent raised by
        VerticalDeviationFromGroundCompensation (GetModifiedQueryExtent)."""
        extent = self.parameters.get('DefaultQueryExtent') or (50.0, 50.0, 250.0)
        compensation = max(0.0, float(self.parameters.get('VerticalDeviationFromGroundCompensation', 100.0)))
        query = (extent[0], extent[1], extent[2] + compensation)
        a, b = self.project_point(start, query), self.project_point(end, query)
        if a is None or b is None:
            return None
        start_ref, start_pos = a[0], self.unreal_to_recast(a[1])
        end_ref, end_pos = b[0], self.unreal_to_recast(b[1])
        corridor, partial, cost = self._find_corridor(start_ref, end_ref, start_pos, end_pos)
        if len(corridor) == 1 and partial:
            points = [start_pos, self._closest_on_boundary(start_ref, end_pos)]
        else:
            points = self._straight_path(start_pos, end_pos, corridor)
        unreal = [(-q[0], -q[2], q[1]) for q in points]
        return {'points': unreal, 'length': sum(math.dist(u, v) for u, v in zip(unreal, unreal[1:])), 'partial': partial,
                'cost': cost}

    def raycast(self, start, end):
        """ARecastNavMesh::BatchRaycast for one ray -> (hit location, did hit). The start polygon comes from
        findNearestContainingPoly with the modified DefaultQueryExtent; no containing polygon leaves the work item at
        RayEnd with no hit. A hit lerps the given Unreal ray by the Detour hit time."""
        extent = self.parameters.get('DefaultQueryExtent') or (50.0, 50.0, 250.0)
        compensation = max(0.0, float(self.parameters.get('VerticalDeviationFromGroundCompensation', 100.0)))
        x, y, z = self.unreal_to_recast(start)
        candidates = set()
        for gx in range(math.floor((x - extent[0]) / 988), math.floor((x + extent[0]) / 988) + 1):
            for gz in range(math.floor((z - extent[1]) / 988), math.floor((z + extent[1]) / 988) + 1):
                candidates.update(self._grid.get((gx, gz), ()))
        best, best_vertical = None, extent[2] + compensation
        for index in sorted(candidates):
            poly = self.polygons[index]
            if self._contains(poly, x, z):
                vertical = abs(self._height(poly, x, z) - y)
                if vertical <= best_vertical:
                    best, best_vertical = index, vertical
        if best is None:
            return tuple(end), False
        current, fraction = self._raycast(best, start, end)
        if current is not None:
            return tuple(end), False
        return _lerp3(start, end, fraction), True

    def _find_corridor(self, start_ref, end_ref, start_pos, end_pos):
        if start_ref == end_ref:
            return [start_ref], False, 0.0
        nodes = {start_ref: {'pos': start_pos, 'parent': None, 'cost': 0.0,
                             'total': math.dist(start_pos, end_pos) * self.HEURISTIC_SCALE, 'open': True, 'closed': False}}
        heap = _NodeQueue()
        heap.push(start_ref, nodes[start_ref]['total'])
        best, best_heuristic = start_ref, nodes[start_ref]['total']
        while heap:
            ref = heap.pop()
            node = nodes[ref]
            node['open'], node['closed'] = False, True
            if ref == end_ref:
                best = ref
                break
            parent = node['parent']
            for _, neighbour, _, _ in self._portals[ref]:
                if neighbour == parent:
                    continue
                n = nodes.get(neighbour)
                if n is None:
                    if len(nodes) >= self.MAX_SEARCH_NODES:
                        continue
                    n = nodes[neighbour] = {'pos': None, 'parent': None, 'cost': 0.0, 'total': 0.0, 'open': False,
                                            'closed': False}
                if self.HEURISTIC_SCALE <= 1.0 or not (n['open'] or n['closed']):
                    left, right = self._portal(ref, neighbour)
                    position = _lerp3(left, right, 0.5)
                else:
                    position = n['pos']
                step = self._step_cost(node['pos'], position, ref, neighbour)
                if neighbour != end_ref:
                    cost = node['cost'] + step
                    heuristic = math.dist(position, end_pos) * self.HEURISTIC_SCALE
                else:
                    cost = node['cost'] + step + self._step_cost(position, end_pos, neighbour, None)
                    heuristic = 0.0
                total = cost + heuristic
                if (n['open'] or n['closed']) and total >= n['total']:
                    continue
                n.update(parent=ref, cost=cost, total=total, pos=position, closed=False)
                if n['open']:
                    heap.modify(neighbour, total)
                else:
                    n['open'] = True
                    heap.push(neighbour, total)
                if heuristic < best_heuristic:
                    best, best_heuristic = neighbour, heuristic
        cost = nodes[best]['cost']             # FNavMeshPath::GetCost: the corridor's summed A* cost to the last node
        corridor = []
        while best is not None:
            corridor.append(best)
            best = nodes[best]['parent']
        corridor.reverse()
        return corridor, corridor[-1] != end_ref, cost

    def _closest_on_boundary(self, ref, pos):
        """closestPointOnPolyBoundary: the point itself when inside in 2D, else the nearest edge point (2D distance)."""
        poly = self.polygons[ref]
        if self._contains(poly, pos[0], pos[2]):
            return pos
        verts = poly['vertices_3d']
        best, point = float('inf'), pos
        for a, b in zip(verts, verts[1:] + verts[:1]):
            dx, dz = b[0] - a[0], b[2] - a[2]
            length = dx * dx + dz * dz
            t = max(0.0, min(1.0, ((pos[0] - a[0]) * dx + (pos[2] - a[2]) * dz) / length)) if length else 0.0
            q = _lerp3(a, b, t)
            d = (q[0] - pos[0]) ** 2 + (q[2] - pos[2]) ** 2
            if d < best:
                best, point = d, q
        return point

    def _straight_path(self, start_pos, end_pos, corridor):
        """findStraightPath with DT_STRAIGHTPATH_AREA_CROSSINGS (FPImplRecastNavMesh::FindStraightPath)."""
        start = self._closest_on_boundary(corridor[0], start_pos)
        end = self._closest_on_boundary(corridor[-1], end_pos)
        out = [start]

        def append(point):
            if not _vequal(out[-1], point):
                out.append(point)

        def crossings(first, last, to):
            origin = out[-1]
            for k in range(first, last):
                src, dst = corridor[k], corridor[k + 1]
                if self.polygons[src]['area'] == self.polygons[dst]['area']:
                    continue
                left, right = self._portal(src, dst)
                t = _intersect_seg_seg_2d(origin, to, left, right)
                if t is not None:
                    append(_lerp3(left, right, t))

        apex = left_pt = right_pt = start
        apex_i = left_i = right_i = 0
        i = 0
        while i < len(corridor):
            if i + 1 < len(corridor):
                left, right = self._portal(corridor[i], corridor[i + 1])
                if i == 0 and _dist_pt_seg_sqr_2d(apex, left, right) < 0.001 ** 2:
                    i += 1
                    continue
            else:
                left = right = end
            if _tri_area_2d(apex, right_pt, right) <= 0.0:
                if _vequal(apex, right_pt) or _tri_area_2d(apex, left_pt, right) > 0.0:
                    right_pt, right_i = right, i
                else:
                    crossings(apex_i, left_i, left_pt)
                    apex, apex_i = left_pt, left_i
                    append(apex)
                    left_pt = right_pt = apex
                    left_i = right_i = apex_i
                    i = apex_i + 1
                    continue
            if _tri_area_2d(apex, left_pt, left) >= 0.0:
                if _vequal(apex, left_pt) or _tri_area_2d(apex, right_pt, left) < 0.0:
                    left_pt, left_i = left, i
                else:
                    crossings(apex_i, right_i, right_pt)
                    apex, apex_i = right_pt, right_i
                    append(apex)
                    left_pt = right_pt = apex
                    left_i = right_i = apex_i
                    i = apex_i + 1
                    continue
            i += 1
        crossings(apex_i, len(corridor) - 1, end)
        append(end)
        return out

    @classmethod
    def load(cls, path):
        with open(path, encoding='utf-8') as f:
            return cls(json.load(f))

    @staticmethod
    def unreal_to_recast(point):
        return (-float(point[0]), float(point[2]), -float(point[1]))

    @staticmethod
    def _contains(poly, x, z):
        inside = False
        pts = poly['verts']
        j = len(pts) - 1
        for i, (xi, zi) in enumerate(pts):
            xj, zj = pts[j]
            if ((zi > z) != (zj > z)) and x < (xj - xi) * (z - zi) / ((zj - zi) or 1e-20) + xi:
                inside = not inside
            j = i
        return inside

    def find_poly(self, point, max_vertical=1024.0):
        x, y, z = self.unreal_to_recast(point)
        candidates = self._grid.get((math.floor(x / 988), math.floor(z / 988)), ())
        best, best_score = None, float('inf')
        for idx in candidates:
            p = self.polygons[idx]
            if not self._contains(p, x, z):
                continue
            dz = abs(y - p['z'])
            if dz <= max_vertical and dz < best_score:
                best, best_score = idx, dz
        return best

    def segment_on_mesh(self, start, end, max_vertical=1024.0, step=125.0):
        """Whether the candidate straight path remains over walkable polygons.

        Clip against convex polygons and follow connectivity along the segment.
        The legacy step argument is accepted for compatibility. This geometric
        query still lacks native projection, filtering, and nav-link semantics.
        """
        a, b = self.find_poly(start, max_vertical), self.find_poly(end, max_vertical)
        if a is None or b is None:
            return False
        origin, target = self.unreal_to_recast(start), self.unreal_to_recast(end)
        intervals = {idx: self._segment_interval(poly, origin, target)
                     for idx, poly in enumerate(self.polygons)}
        if intervals[a] is None or intervals[b] is None:
            return False
        seen, todo = {a}, deque([a])
        while todo:
            current = todo.popleft()
            lower, upper = intervals[current]
            if current == b and upper >= 1.0 - 1e-9:
                return True
            for neighbor in self._links[current]:
                interval = intervals.get(neighbor)
                if neighbor in seen or interval is None:
                    continue
                entry, exit = interval
                if entry <= upper + 1e-9 and exit >= lower - 1e-9:
                    seen.add(neighbor)
                    todo.append(neighbor)
        return False

    @staticmethod
    def _segment_interval(poly, start, end):
        """Return the interval inside a convex polygon, in either winding."""
        clipped = DetourNavMesh._clip_segment(poly, start, end)
        return clipped[:2] if clipped is not None else None

    @staticmethod
    def _clip_segment(poly, start, end):
        points = poly['verts']
        edges = list(zip(points, points[1:] + points[:1]))
        area = sum(a[0] * b[1] - b[0] * a[1] for a, b in edges)
        if abs(area) < 1e-12:
            return None
        sign = 1.0 if area > 0 else -1.0
        lower, upper = 0.0, 1.0
        exit_edge = None
        dx, dz = end[0] - start[0], end[2] - start[2]
        for edge, (a, b) in enumerate(edges):
            ex, ez = b[0] - a[0], b[1] - a[1]
            distance = sign * (ex * (start[2] - a[1]) - ez * (start[0] - a[0]))
            slope = sign * (ex * dz - ez * dx)
            if abs(slope) < 1e-12:
                if distance < -1e-9:
                    return None
                continue
            crossing = -distance / slope
            if slope > 0:
                lower = max(lower, crossing)
            else:
                if crossing < upper:
                    upper, exit_edge = crossing, edge
            if lower > upper + 1e-9:
                return None
        return lower, upper, exit_edge

    def connected(self, start, end, max_vertical=1024.0):
        a, b = self.find_poly(start, max_vertical), self.find_poly(end, max_vertical)
        if a is None or b is None:
            return None
        if a == b:
            return True
        seen, todo = {a}, deque([a])
        while todo:
            cur = todo.popleft()
            for nxt in self._links[cur]:
                if nxt == b:
                    return True
                if nxt not in seen:
                    seen.add(nxt)
                    todo.append(nxt)
        return False


def navmesh_path(server, name):
    """Resolve a navmesh resource from the island engine manifest."""
    manifest_path = os.path.join(server, 'data', '2.1.1', 'islands', name + '.engine_data.json')
    try:
        with open(manifest_path, encoding='utf-8') as f:
            entry = json.load(f).get('navmesh')
        if not entry:
            return None
        path = os.path.join(server, entry)
        return path if os.path.isfile(path) else None
    except (OSError, ValueError):
        return None


def _lerp3(a, b, t):
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)


def _vequal(a, b):
    """dtVequal: squared 3D distance below (1/16384)^2."""
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2 < (1.0 / 16384.0) ** 2


def _tri_area_2d(a, b, c):
    return (c[0] - a[0]) * (b[2] - a[2]) - (b[0] - a[0]) * (c[2] - a[2])


def _dist_pt_seg_sqr_2d(p, a, b):
    dx, dz = b[0] - a[0], b[2] - a[2]
    length = dx * dx + dz * dz
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[2] - a[2]) * dz) / length)) if length else 0.0
    return (a[0] + t * dx - p[0]) ** 2 + (a[2] + t * dz - p[2]) ** 2


def _intersect_seg_seg_2d(ap, aq, bp, bq):
    """dtIntersectSegSeg2D -> t along bp..bq, or None when parallel."""
    u = (aq[0] - ap[0], aq[2] - ap[2])
    v = (bq[0] - bp[0], bq[2] - bp[2])
    w = (ap[0] - bp[0], ap[2] - bp[2])
    d = u[0] * v[1] - u[1] * v[0]
    if abs(d) < 1e-6:
        return None
    return (u[0] * w[1] - u[1] * w[0]) / d


class _NodeQueue:
    """dtNodeQueue: binary min-heap on node total with Detour's bubbleUp/trickleDown, for identical tie-breaking."""

    def __init__(self):
        self.heap = []

    def __bool__(self):
        return bool(self.heap)

    def push(self, ref, total):
        self.heap.append((ref, total))
        self._bubble_up(len(self.heap) - 1, (ref, total))

    def pop(self):
        top = self.heap[0][0]
        last = self.heap.pop()
        if self.heap:
            self._trickle_down(0, last)
        return top

    def modify(self, ref, total):
        for i, (r, _) in enumerate(self.heap):
            if r == ref:
                self._bubble_up(i, (ref, total))
                return

    def _bubble_up(self, i, node):
        parent = (i - 1) // 2
        while i > 0 and self.heap[parent][1] > node[1]:
            self.heap[i] = self.heap[parent]
            i, parent = parent, (parent - 1) // 2
        self.heap[i] = node

    def _trickle_down(self, i, node):
        size = len(self.heap)
        child = i * 2 + 1
        while child < size:
            if child + 1 < size and self.heap[child][1] > self.heap[child + 1][1]:
                child += 1
            self.heap[i] = self.heap[child]
            i, child = child, child * 2 + 1
        self._bubble_up(i, node)
