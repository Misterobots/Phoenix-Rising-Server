import unittest
from types import SimpleNamespace

from mpgs.world.navmesh import DetourNavMesh


def mesh(rectangles):
    vertices, polygons = [], []
    for left, bottom, right, top, height in rectangles:
        offset = len(vertices)
        vertices.extend({'X': x, 'Y': height, 'Z': z} for x, z in
                        [(left, bottom), (right, bottom), (right, top), (left, top)])
        polygons.append({'VertCount': 4, 'Verts': list(range(offset, offset + 4)),
                         'Neis': [0] * 4})
    return DetourNavMesh({'tiles': [{'vertices': vertices, 'polygons': polygons}]})


class NavMeshTests(unittest.TestCase):
    def test_decorator_preserves_full_scaled_path(self):
        from mpgs.behemoth.bt import _d_navtest
        queries = []
        ctx = SimpleNamespace(
            behemoth_scale=lambda: {'X': 2, 'Y': 3, 'Z': 4},
            path_hits_navmesh=lambda path, name: queries.append((path, name)) or False)
        points = [{'X': 1, 'Y': 2, 'Z': 3}, {'X': 4}, {'Y': 5}]
        self.assertTrue(_d_navtest(ctx, {'Displacement': points}, {}, False))
        self.assertEqual(queries[0][0], [(2, 6, 12), (8, 0, 0), (0, 15, 0)])

    def test_context_transforms_each_local_segment(self):
        from mpgs.behemoth.director import BehemothContext
        ctx = object.__new__(BehemothContext)
        ctx.d = SimpleNamespace(loc=(100, 200, 300), yaw=90, log_ai=lambda msg: None)
        queries = []
        ctx.navmesh = SimpleNamespace(path_hits_navmesh=lambda path, **kw:
                                      queries.append(path) or False)
        self.assertFalse(ctx.path_hits_navmesh([(0, 0, 0), (10, 0, 0), (10, 20, 0)],
                                              'NavigationQueryFilter_NoDynamic'))
        self.assertEqual(len(queries[0]), 3)
        for actual, expected in zip(queries[0][2], (80, 210, 300)):
            self.assertAlmostEqual(actual, expected)

    def test_failed_start_projection_returns_no_hit(self):
        nav = mesh([(0, 0, 10, 10, 0)])
        self.assertFalse(nav.path_hits_navmesh([(-5, -5, 1500), (-50, -5, 1500)]))

    def test_native_vertical_compensation(self):
        nav = mesh([(0, 0, 10, 10, 0)])
        self.assertTrue(nav.path_hits_navmesh([(-5, -5, 1100), (-50, -5, 1100)]))

    def test_path_projects_start_within_query_extent(self):
        nav = mesh([(0, 0, 10, 10, 0)])
        self.assertFalse(nav.path_hits_navmesh([(5, -5, 0), (-5, -5, 0)]))
        self.assertTrue(nav.path_hits_navmesh([(-5, -5, 0), (-50, -5, 0)]))

    def test_path_carries_polygon_across_segments(self):
        nav = mesh([(0, 0, 10, 10, 0)])
        self.assertTrue(nav.path_hits_navmesh([(-5, -5, 0), (-6, -5, 2000), (-50, -5, 2000)]))

    def test_partial_external_portal(self):
        vertices = [{'X': x, 'Y': 0, 'Z': z} for x, z in
                    [(0, 0), (10, 0), (10, 10), (0, 10),
                     (10, 2), (20, 2), (20, 8), (10, 8)]]
        nav = DetourNavMesh({'tiles': [{'vertices': vertices, 'polygons': [
            {'VertCount': 4, 'Verts': [0, 1, 2, 3], 'Neis': [0, 32768, 0, 0]},
            {'VertCount': 4, 'Verts': [4, 5, 6, 7], 'Neis': [0, 0, 0, 32772]}]}]})
        self.assertFalse(nav.path_hits_navmesh([(-5, -5, 0), (-15, -5, 0)]))
        self.assertTrue(nav.path_hits_navmesh([(-5, -1, 0), (-15, -1, 0)]))

    def test_short_native_paths_return_no_hit(self):
        from mpgs.behemoth.director import BehemothContext
        ctx = object.__new__(BehemothContext)
        for path in ([], [(1, 2, 3)]):
            self.assertFalse(ctx.path_hits_navmesh(path, 'NavigationQueryFilter_NoDynamic'))

    def test_percentage_before_hit(self):
        nav = mesh([(0, 0, 10, 10, 0)])
        self.assertEqual(nav.path_query([(-5, -5, 0), (-15, -5, 0)]),
                         {'hit': True, 'found': True, 'percentage': 0.5})
        self.assertEqual(nav.path_query([(-2, -5, 0), (-8, -5, 0)])['percentage'], 1.0)

    def test_percentage_stops_at_hitting_segment(self):
        nav = mesh([(0, 0, 10, 10, 0)])
        # The segment after the hit is not walked: 5 before the hit over the 10 walked.
        q = nav.path_query([(-5, -5, 0), (-15, -5, 0), (-5, -5, 0)])
        self.assertTrue(q['hit'])
        self.assertAlmostEqual(q['percentage'], 0.5)
        q = nav.path_query([(-5, -5, 0), (-7, -5, 0), (-15, -5, 0)])
        self.assertAlmostEqual(q['percentage'], 0.5)

    def test_simplify_vectors_keeps_shipped_split(self):
        from mpgs.behemoth.brain import simplify_vectors
        straight = [(0, 0, 0), (100, 10, 0), (200, 0, 0)]
        self.assertEqual(simplify_vectors(straight), [(0, 0, 0), (200, 0, 0)])
        # Both recursion halves are points[0..split]: the tail after (100, 500) is dropped and the start repeats.
        bent = [(0, 0, 0), (100, 500, 0), (200, 0, 0)]
        self.assertEqual(simplify_vectors(bent), [(0, 0, 0), (0, 0, 0), (100, 500, 0)])

    def test_evaluate_rejects_path_hitting_before_ninety_percent(self):
        from mpgs.behemoth.brain import Brain
        attack = {'attack_vectors1': [(0, 0, 0), (500, 0, 0)], 'attack_vectors2': [], 'require_states': [],
                  'excluded_states': []}
        brain = object.__new__(Brain)
        brain.actions = {'Tackle': {'attack': attack}}
        brain._simple = {'Tackle': [[(0, 0, 0), (500, 0, 0)], []]}
        brain.pos, brain.yaw, brain.z, brain.behemoth_scale = (100.0, 200.0), 90.0, 50.0, (2.0, 2.0, 2.0)
        queries = []
        for percentage, rejected in ((0.5, True), (0.9, False)):
            brain.path_query = lambda path, p=percentage: queries.append(path) or {'hit': True, 'percentage': p}
            self.assertEqual(brain.nav_rejected('Tackle'), rejected)
        for actual, expected in zip(queries[0][1], (100.0, 1200.0, 50.0)):
            self.assertAlmostEqual(actual, expected)

    def test_adjacent_segment(self):
        nav = mesh([(0, 0, 10, 10, 0), (10, 0, 20, 10, 0)])
        self.assertTrue(nav.segment_on_mesh((-5, -5, 0), (-15, -5, 0)))

    def test_small_gap_with_connected_detour(self):
        nav = mesh([(0, 0, 10, 10, 0), (11, 0, 20, 10, 0),
                    (0, 10, 10, 20, 0), (10, 10, 11, 20, 0),
                    (11, 10, 20, 20, 0)])
        # Explicit detour links isolate segment coverage from tile-link rebuilding.
        for a, b in [(0, 2), (2, 3), (3, 4), (4, 1)]:
            nav._links[a].add(b)
            nav._links[b].add(a)
        self.assertTrue(nav.connected((-5, -5, 0), (-15, -5, 0)))
        self.assertFalse(nav.segment_on_mesh((-5, -5, 0), (-15, -5, 0)))

    def test_stacked_surfaces_are_disconnected(self):
        nav = mesh([(0, 0, 10, 10, 0), (0, 0, 10, 10, 100)])
        self.assertFalse(nav.connected((-5, -5, 0), (-5, -5, 100)))

    def test_clip_both_windings(self):
        points = [(0, 0), (10, 0), (10, 10), (0, 10)]
        for vertices in (points, points[::-1]):
            interval = DetourNavMesh._segment_interval(
                {'verts': vertices}, (-5, 0, 5), (15, 0, 5))
            self.assertEqual(interval, (0.25, 0.75))
