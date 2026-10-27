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
