"""Local-frame hit-area tests. Unreal units; X forward, Y right; yaw in degrees, positive turning X toward Y."""
import math


def to_local(origin, yaw, point):
    dx, dy = point[0] - origin[0], point[1] - origin[1]
    c, s = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    return dx * c + dy * s, -dx * s + dy * c


def norm180(a):
    a = (a + 180.0) % 360.0 - 180.0
    return 180.0 if a == -180.0 else a


def in_arc(origin, yaw, point, arc, scale=1.0):
    """Cone test: within range of the arc's local offset point and within half the arc angle of its centre angle."""
    lx, ly = to_local(origin, yaw, point)
    lx -= arc['offset'][0] * scale
    ly -= arc['offset'][1] * scale
    if math.hypot(lx, ly) > arc['range'] * scale:
        return False
    if arc['angle'] >= 360.0:
        return True
    if lx == 0.0 and ly == 0.0:
        return True
    return abs(norm180(math.degrees(math.atan2(ly, lx)) - arc['center_angle'])) <= arc['angle'] / 2.0


def in_box(origin, yaw, point, box, scale=1.0):
    """Box test; the box is centred on `offset` and rotated by its own yaw about that centre (extent = half size)."""
    lx, ly = to_local(origin, yaw, point)
    c, s = math.cos(math.radians(box['yaw'])), math.sin(math.radians(box['yaw']))
    dx, dy = lx - box['offset'][0] * scale, ly - box['offset'][1] * scale
    bx, by = dx * c + dy * s, -dx * s + dy * c
    return abs(bx) <= box['extent'][0] * scale and abs(by) <= box['extent'][1] * scale
