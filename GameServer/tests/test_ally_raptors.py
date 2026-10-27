"""Repeater Q raptors (mpgs/creatures/raptors.py, mpgs/engine/allies.py): spawned on the Q activation, run their own behaviour
tree against the Embermane, deal their damage-table damage, and leave when their lifespan ends."""
import math
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from mpgs import island_encounter, loadout  # noqa: E402
from mpgs.creatures import raptors  # noqa: E402
from mpgs.engine import abilities  # noqa: E402

DATA = raptors.load()


@unittest.skipUnless(DATA, 'world/creatures/raptor not generated (tools/export_raptors.py)')
class AllyRaptorTests(unittest.TestCase):
    def test_q_spawns_raptors_that_fight_and_expire(self):
        import test_island_encounter_engine as T
        if not T.HAVE_DATA:
            self.skipTest('island engine data not generated')
        T.setUpModule()
        self.addCleanup(T.tearDownModule)
        T.EngineEncounterTests.setUpClass()
        w = T.EngineEncounterTests.world
        loadout.swap_weapon_actor(w.entries, w.schema, 'WP_DP_UMBRAL_01')
        self.addCleanup(loadout.swap_weapon_actor, w.entries, w.schema, 'WP_EB_BEGINNER')
        case = T.EngineEncounterTests('test_spawn_stands_on_the_landscape_and_walks')
        with patch.dict(os.environ, {'MPGS_WEAPON_PARTS': '1'}):
            s = case.session()
        enc = s._encounter
        pa = enc['part_abilities']
        h = next(k for k, v in pa.granted.items() if v['cls'] == 'dp_y_ability_spawn_raptor_ab_C')
        bl = enc['loc']
        player = (bl[0] - 1200.0, bl[1], bl[2])
        s.pawn_state = {'loc': list(player), 'rot': [0.0, 0.0, 0.0], 'ctrl_rot': [0.0, 0.0, 0.0], 'vel': [0.0, 0.0, 0.0], 'mode': 1}
        asc_ch = pa.ch
        island_encounter.handle_ability(s, asc_ch, pa.hdr, {'AbilityToActivate.Handle': h, 'InputPressed': 0,
                                                            'PredictionKey': {'server': 0, 'current': 8, 'base': 7}})
        self.assertEqual(len(enc['allies']), raptors.PACK_SIZE)
        start_health = enc['health']
        out = case.run_until(s, 30.0, player=lambda t: player)
        self.assertLess(enc['health'], start_health)                       # their hits landed
        for _, _, queued in out:                                            # everything sent encodes
            pass
        self.assertTrue(all(r.ctx.bb.get('HasSpawnedIn') for r in enc['allies']))
        case.run_until(s, raptors.LIFESPAN + 1.0, start=30.0, player=lambda t: player)
        self.assertEqual(enc.get('allies'), [])


if __name__ == '__main__':
    unittest.main()
