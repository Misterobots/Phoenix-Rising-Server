"""Environment queries ported from UE 5.1.1 AIModule/Private/EnvironmentQuery and their cooked assets."""
import math


def on_circle(center, radius, num_points=8, raycast=None):
    """UEnvQueryGenerator_OnCircle, ByNumberOfPoints, full circle, no defined arc: the start direction is
    ForwardVector rotated by -ArcAngle/2 about Up, and the points step by ArcAngle / NumberOfPoints. TraceData defaults
    to Navigation (Keep): each point becomes the navmesh raycast hit from the centre, or stays put without a hit."""
    if radius <= 0.0 or num_points <= 0:
        return []
    step = 360.0 / num_points
    points = []
    for i in range(num_points):
        a = math.radians(-180.0 + step * i)
        p = (center[0] + radius * math.cos(a), center[1] + radius * math.sin(a), center[2])
        points.append(raycast(center, p)[0] if raycast is not None else p)
    return points


def single_result(items):
    """SingleResult FinalizeQuery without tests: every item scores 0, SortScores orders by TGreater over
    FEnvQueryItem, whose final tie-break is DataOffset, so item 0 after sorting is the last item generated."""
    return items[-1] if items else None


# eqs_aetherconsume_position: one OnCircle option around EQContext_CurrentPatrolCenterPoint_C, ByNumberOfPoints with
# the class default 8 points, CircleRadius 300 bound to the OnCircle.CircleRadius query param; no tests.
QUERIES = {'eqs_aetherconsume_position': {'center': 'CurrentPatrolCenterPoint', 'radius': 300.0,
                                          'radius_param': 'OnCircle.CircleRadius', 'points': 8}}


def run(name, cfg, center, raycast):
    q = QUERIES.get(name)
    if q is None or center is None:
        return None
    radius = float(cfg.get(q['radius_param'], q['radius']))
    return single_result(on_circle(center, radius, q['points'], raycast))
