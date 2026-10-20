"""ENGINE_PORT_PLAN.md step 3: the locally generated Embermane and island data is present, matches the recorded manifest, and loads
through the ported engine modules. Skipped where the game data has not been generated (it is not distributed)."""
import hashlib
import json
import os
import sys
import unittest

SERVER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SERVER)

from mpgs.behemoth import bt  # noqa: E402
from mpgs.behemoth.mover import montage_curve  # noqa: E402
from mpgs.behemoth.rock_ai import DEFAULT_BT_DIR  # noqa: E402
from mpgs.world.terrain import Terrain  # noqa: E402

ISLANDS = os.path.join(SERVER, 'data', '2.1.1', 'islands')
MANIFEST = os.path.join(ISLANDS, 'Adventure_IslandA_D24.engine_data.json')
CONTRACT = os.path.join(ISLANDS, 'Adventure_IslandA_D24.contract.json')
with open(MANIFEST, encoding='utf-8') as _f:
    M = json.load(_f)
HAVE_DATA = all(os.path.exists(os.path.join(SERVER, p)) for p in M['files'])


def sha256(path):
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


class ManifestTests(unittest.TestCase):
    def test_manifest_has_no_recorded_gaps(self):
        self.assertEqual(M['problems'], [])
        self.assertEqual(M['behaviourTree'], 'lerawr_beta_new_bt')
        self.assertEqual(len(M['actions']), 8)


@unittest.skipUnless(HAVE_DATA, 'Embermane/island engine data not generated locally (see regenerate in the manifest)')
class LocalDataTests(unittest.TestCase):
    def test_local_files_match_the_manifest(self):
        changed = [p for p, h in M['files'].items() if sha256(os.path.join(SERVER, p)) != h]
        self.assertEqual(changed, [])

    def test_behaviour_tree_and_every_sub_tree_resolve(self):
        ts = bt.TreeSet.load(DEFAULT_BT_DIR)
        self.assertEqual([t for t in M['trees'] if t not in ts.roots], [])

    def test_every_action_has_a_root_motion_curve(self):
        with open(os.path.join(SERVER, 'world', 'behemoths', M['variant'] + '.json'), encoding='utf-8') as f:
            actions = json.load(f)['actions']
        self.assertEqual([k for k, a in actions.items() if montage_curve(a['montage']) is None], [])

    def test_terrain_matches_where_the_player_stood(self):
        """Live log islandserver_20261019_195838: the player idled with root Z 127.5 at (-10681.4, 6836.1). The landscape there
        must be below the root by a plausible capsule half-height (80-110 cm)."""
        t = Terrain.load(os.path.join(SERVER, 'world', 'maps', M['terrain']))
        self.assertTrue(80.0 <= 127.5 - t.ground_z(-10681.4, 6836.1) <= 110.0)

    def test_patrol_center_vent_is_off_the_landscape(self):
        """Recorded finding, not a goal: BP_PatrolCenterPoint_263's aether vent sits 43 cm above the landscape (static-mesh floor
        the height grid cannot see). Step 4 must not assume landscape height equals walkable floor there."""
        t = Terrain.load(os.path.join(SERVER, 'world', 'maps', M['terrain']))
        with open(CONTRACT, encoding='utf-8') as f:
            center = next(c for c in json.load(f)['encounter']['centers'] if c['name'] == 'BP_PatrolCenterPoint_263')
        gap = center['groundZ'] - t.ground_z(*center['location'][:2])
        self.assertAlmostEqual(gap, 43.3, delta=1.0)


if __name__ == '__main__':
    unittest.main()
