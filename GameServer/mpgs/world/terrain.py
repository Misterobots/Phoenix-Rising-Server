"""Ground height and walkability for a landscape map (the server has no engine, so it reads the exported grid).

Data: world/maps/<name>_height.{json,bin} from tools/export_terrain.py (generated locally, not distributed).

  t = Terrain.load(path_without_extension)
  t.ground_z(x, y)     bilinear ground height in world cm, None outside the landscape or in a no-data hole
  t.inside(x, y)       True where the landscape has data
  t.slope_deg(x, y)    slope from the four neighbouring samples
  t.walkable(x, y)     inside and slope below MAX_SLOPE_DEG
  t.path(a, b)         A* over walkable quads; returns [(x, y), ...] or None

Pure python, no numpy. Positions are world cm (the same units as the UE actor locations on the wire).
"""
import array
import heapq
import json
import math
import os
import sys

NODATA = 0xFFFF
MAX_SLOPE_DEG = 45.0


class Terrain:
    def __init__(self, header, samples):
        self.w = header['width']
        self.h = header['height']
        self.x0, self.y0 = header['quad_origin']
        self.lx, self.ly, self.lz = header['landscape_location']
        self.sx, self.sy = header['quad_world']
        self.z_base = header['z_world']['base']
        self.z_scale = header['z_world']['scale']
        self.grid = samples  # array('H'), row-major, index = j * w + i
        self.hunt = None

    @classmethod
    def load(cls, base):
        """base = path without extension, e.g. <ParadoxGameServer>/world/maps/cora_poodle_height"""
        with open(base + '.json', encoding='utf-8') as f:
            header = json.load(f)
        samples = array.array('H')
        with open(base + '.bin', 'rb') as f:
            samples.frombytes(f.read())
        if sys.byteorder == 'big':
            samples.byteswap()
        if len(samples) != header['width'] * header['height']:
            raise ValueError('height grid size does not match its header')
        return cls(header, samples)

    # ---- coordinates --------------------------------------------------------------------------------------------
    def _to_quad(self, x, y):
        return (x - self.lx) / self.sx - self.x0, (y - self.ly) / self.sy - self.y0

    def _h(self, i, j):
        if 0 <= i < self.w and 0 <= j < self.h:
            v = self.grid[j * self.w + i]
            return None if v == NODATA else v
        return None

    def _z(self, i, j):
        v = self._h(i, j)
        return None if v is None else self.lz + self.z_scale * (v - 32768) / 128.0

    # ---- queries ------------------------------------------------------------------------------------------------
    def ground_z(self, x, y):
        qx, qy = self._to_quad(x, y)
        i, j = math.floor(qx), math.floor(qy)
        fx, fy = qx - i, qy - j
        corners = [self._z(i, j), self._z(i + 1, j), self._z(i, j + 1), self._z(i + 1, j + 1)]
        if any(c is None for c in corners):
            return None
        z0 = corners[0] + (corners[1] - corners[0]) * fx
        z1 = corners[2] + (corners[3] - corners[2]) * fx
        return z0 + (z1 - z0) * fy

    def inside(self, x, y):
        return self.ground_z(x, y) is not None

    def slope_deg(self, x, y):
        qx, qy = self._to_quad(x, y)
        i, j = round(qx), round(qy)
        zl, zr = self._z(i - 1, j), self._z(i + 1, j)
        zd, zu = self._z(i, j - 1), self._z(i, j + 1)
        if None in (zl, zr, zd, zu):
            return None
        dzdx = (zr - zl) / (2 * self.sx)
        dzdy = (zu - zd) / (2 * self.sy)
        return math.degrees(math.atan(math.hypot(dzdx, dzdy)))

    def walkable(self, x, y):
        s = self.slope_deg(x, y)
        return s is not None and s <= MAX_SLOPE_DEG

    def path(self, a, b, max_nodes=200000):
        """A* over quads that are walkable. a and b are world (x, y). Returns world points or None."""
        start = tuple(round(v) for v in self._to_quad(*a))
        goal = tuple(round(v) for v in self._to_quad(*b))
        if not (self._walk_q(*start) and self._walk_q(*goal)):
            return None
        open_heap = [(0.0, start)]
        came = {start: None}
        cost = {start: 0.0}
        seen = 0
        while open_heap:
            _, cur = heapq.heappop(open_heap)
            seen += 1
            if seen > max_nodes:
                return None
            if cur == goal:
                pts = self._to_world_path(self._unwind(came, cur))
                pts[0] = (a[0], a[1])
                if len(pts) > 1 and self.walkable(b[0], b[1]):
                    pts[-1] = (b[0], b[1])
                return self.smooth(pts)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    nxt = (cur[0] + dx, cur[1] + dy)
                    if not self._walk_q(*nxt):
                        continue
                    step = math.hypot(dx, dy)
                    nc = cost[cur] + step
                    if nc < cost.get(nxt, float('inf')):
                        cost[nxt] = nc
                        came[nxt] = cur
                        h = math.hypot(goal[0] - nxt[0], goal[1] - nxt[1])
                        heapq.heappush(open_heap, (nc + h, nxt))
        return None

    def clear_line(self, a, b):
        """True when the segment a-b is walkable everywhere (sampled every half quad)."""
        d = math.hypot(b[0] - a[0], b[1] - a[1])
        n = max(1, int(d / (0.5 * min(abs(self.sx), abs(self.sy)))))
        return all(self.walkable(a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n) for k in range(n + 1))

    def smooth(self, pts):
        """String-pull a grid path: keep a point only where the straight line past it would leave walkable ground.
        Live 2026-10-15: the raw 8-direction path made the Rock snap between 0 and 45 degree headings every quad while it
        followed the player (it looked like sliding and spinning)."""
        if len(pts) <= 2:
            return list(pts)
        out, i = [pts[0]], 0
        while i < len(pts) - 1:
            j = len(pts) - 1
            while j > i + 1 and not self.clear_line(pts[i], pts[j]):
                j -= 1
            out.append(pts[j])
            i = j
        return out

    # ---- internals ----------------------------------------------------------------------------------------------
    def _walk_q(self, i, j):
        x = (i + self.x0) * self.sx + self.lx
        y = (j + self.y0) * self.sy + self.ly
        return self.walkable(x, y)

    @staticmethod
    def _unwind(came, cur):
        out = []
        while cur is not None:
            out.append(cur)
            cur = came[cur]
        return out[::-1]

    def _to_world_path(self, quads):
        return [((i + self.x0) * self.sx + self.lx, (j + self.y0) * self.sy + self.ly) for i, j in quads]


def default_base():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, '..', '..', 'world', 'maps', 'cora_poodle_height'))
