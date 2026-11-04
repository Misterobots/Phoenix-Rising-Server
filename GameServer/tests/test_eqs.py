import math
import os
import unittest

from mpgs.behemoth import bt, eqs

HERE = os.path.dirname(os.path.abspath(__file__))
NAVMESH = os.path.join(HERE, '..', 'world', 'maps', 'adventure_arid_rose_behemoth_navmesh.json')


class OnCircleTests(unittest.TestCase):
    def test_points_start_behind_and_step_counter_clockwise(self):
        points = eqs.on_circle((0.0, 0.0, 5.0), 100.0, 8)
        self.assertEqual(len(points), 8)
        self.assertAlmostEqual(points[0][0], -100.0)
        self.assertAlmostEqual(points[0][1], 0.0)
        self.assertAlmostEqual(points[2][1], -100.0)
        self.assertTrue(all(p[2] == 5.0 for p in points))

    def test_single_result_without_tests_is_last_item(self):
        p = eqs.run('eqs_aetherconsume_position', {'OnCircle.CircleRadius': 150.0}, (0.0, 0.0, 0.0), None)
        self.assertAlmostEqual(p[0], 150.0 * math.cos(math.radians(135.0)))
        self.assertAlmostEqual(p[1], 150.0 * math.sin(math.radians(135.0)))

    def test_raycast_hit_replaces_point(self):
        def wall(start, end):
            return ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2, start[2]), True
        p = eqs.run('eqs_aetherconsume_position', {}, (0.0, 0.0, 0.0), wall)
        self.assertAlmostEqual(math.hypot(p[0], p[1]), 150.0)

    def test_run_eqs_task_writes_move_destination(self):
        class Ctx(bt.Context):
            bb = {}

            def patrol_center_point(self):
                return (10.0, 20.0, 0.0)
        ctx = Ctx()
        task = bt.EQSQuery.__new__(bt.EQSQuery)
        task.p = {'EQSRequest': {'QueryTemplate': {'ObjectName': "EnvQuery'eqs_aetherconsume_position'"},
                                 'QueryConfig': [{'ParamName': 'OnCircle.CircleRadius', 'Value': 150.0}]},
                  'BlackboardKey': 'bb.MoveDestination'}
        self.assertEqual(task.start(ctx), bt.SUCCEEDED)
        self.assertAlmostEqual(math.dist(ctx.bb['MoveDestination'][:2], (10.0, 20.0)), 150.0)


class DeaggroNodeTests(unittest.TestCase):
    def test_remove_all_targets_clears_attack_target(self):
        ctx = bt.Context()
        ctx.bb = {'AttackTarget': 'player'}
        self.assertEqual(bt.RemoveAllTargets({}).start(ctx), bt.SUCCEEDED)
        self.assertIsNone(ctx.bb['AttackTarget'])

    def test_has_generic_action_needs_a_montage(self):
        class Ctx(bt.Context):
            def generic_action_montage(self, action_type):
                return 'roar_Montage' if action_type == 'EGenericActionType::Roar' else None
        deco = bt.DECORATORS['has_generic_action_btdeco_C']
        self.assertTrue(deco(Ctx(), {'ActionType': 'EGenericActionType::Roar'}, {}, False))
        self.assertFalse(deco(Ctx(), {'ActionType': 'EGenericActionType::DeAggroAnnounce'}, {}, False))


@unittest.skipUnless(os.path.exists(NAVMESH), 'Island P behemoth navmesh not exported')
class NavRaycastTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from mpgs.world.navmesh import DetourNavMesh
        cls.nav = DetourNavMesh.load(NAVMESH)

    def test_open_ray_keeps_end(self):
        end = (-13146.11, 3640.64, -1193.28)
        self.assertEqual(self.nav.raycast((-13040.04, 3534.57, -1193.28), end), (end, False))

    def test_start_off_mesh_keeps_end(self):
        end = (-16100.0, 3611.0, -1119.0)
        self.assertEqual(self.nav.raycast((-16058.0, 3611.0, -1119.0), end), (end, False))


if __name__ == '__main__':
    unittest.main()
