"""Server-spawned ability projectiles (repeater heavy: dp_x_ability_piercing_ab).

The ability runs on the client, which computes the muzzle point and aim and calls the server RPC SpawnProjectile(Loc, Rot) on its
ability instance. The server (authority) spawns dp_x_ability_piercing_proj via GameplayTask_SpawnActor(bSpawnOnlyOnAuthority=true)
with Instigator = the pawn; the projectile's overlap on the server calls ranged_weapon_function_library.AuthOnHitRangedTarget
with the damage row 'DP.PiercingShot' (atlas pseudo/dp_x_ability_piercing_ab_C.txt, dp_x_ability_piercing_proj_C.txt).

Here the flight is not simulated: the hit is a straight ray against the behemoth's physics bodies (reference pose, carried by the
actor's transform) up to RANGE. RANGE is a STAND-IN (the projectile's AirSpeed and lifetime were not read).
"""
import math

RANGE = 6000.0          # cm, stand-in
PIERCING_ROW = 'DP.PiercingShot'


def direction(rot):
    """FRotator (pitch, yaw, roll) degrees -> unit forward vector."""
    p, y = math.radians(rot[0]), math.radians(rot[1])
    return (math.cos(p) * math.cos(y), math.cos(p) * math.sin(y), math.sin(p))


def ray_hit(bodies, actor_loc, actor_yaw, start, rot, max_range=RANGE):
    """-> (bone, point, distance) of the first body the ray from `start` along `rot` passes through, or None.
    bodies: {bone: [{'pos', 'radius', 'half_length'}]} in actor space (x forward)."""
    d = direction(rot)
    c, s = math.cos(math.radians(actor_yaw)), math.sin(math.radians(actor_yaw))
    best = None
    for bone, shapes in (bodies or {}).items():
        if bone == 'joints' or not isinstance(shapes, list):
            continue
        for sh in shapes:
            lx, ly, lz = sh['pos']
            w = (actor_loc[0] + lx * c - ly * s, actor_loc[1] + lx * s + ly * c, actor_loc[2] + lz)
            rel = [w[i] - start[i] for i in range(3)]
            t = sum(rel[i] * d[i] for i in range(3))
            if t < 0 or t > max_range:
                continue
            closest = [start[i] + d[i] * t for i in range(3)]
            if math.dist(closest, w) <= sh['radius'] + sh.get('half_length', 0.0) and (best is None or t < best[2]):
                best = (bone, closest, t)
    return best


def area_hit(bodies, actor_loc, actor_yaw, origin, yaw, radius, wedge=None):
    """aoe_base_bp's shape overlap for one actor -> (bone, point, distance) of the nearest body the area reaches, or None.
    The sphere of `radius` around `origin` must reach the body (its radius plus half length); with `wedge` (start, end
    degrees about `yaw`, as FilterHitsByWedgeCheck's WedgeStartAngle / WedgeEndAngle) the body centre's horizontal
    bearing from the origin must also lie inside it. Bodies are those of ray_hit. The wedge's vertical extent
    (WedgeWidth) is not applied."""
    c, s = math.cos(math.radians(actor_yaw)), math.sin(math.radians(actor_yaw))
    best = None
    for bone, shapes in (bodies or {}).items():
        if bone == 'joints' or not isinstance(shapes, list):
            continue
        for sh in shapes:
            lx, ly, lz = sh['pos']
            w = (actor_loc[0] + lx * c - ly * s, actor_loc[1] + lx * s + ly * c, actor_loc[2] + lz)
            dist = math.dist(origin, w)
            if dist > radius + sh['radius'] + sh.get('half_length', 0.0):
                continue
            if wedge is not None:
                bearing = math.degrees(math.atan2(w[1] - origin[1], w[0] - origin[0])) - yaw
                bearing = (bearing + 180.0) % 360.0 - 180.0
                if not wedge[0] <= bearing <= wedge[1]:
                    continue
            if best is None or dist < best[2]:
                best = (bone, w, dist)
    return best
