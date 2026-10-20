"""Rock Blueprint class chain registered in the replication schema (needs world/behemoths/rock_schema.json)."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))  # GameServer/, for mpgs
import os
import unittest

from mpgs.behemoth import bp_schema
from mpgs.repl.schema import Schema

DATA = bp_schema.load() if os.path.exists(bp_schema.ROCK_SCHEMA) else None


@unittest.skipIf(DATA is None, 'world/behemoths/rock_schema.json not generated (python -m tools.export_behemoth_schema)')
class RockSchemaTests(unittest.TestCase):
    def setUp(self):
        self.schema = Schema()
        self.leaf = bp_schema.extend(self.schema, DATA)

    def test_method_reproduces_known_class(self):
        """The Blueprint-order rule gives exactly the live dump's Bp_Behemoth_C property list."""
        live = [p.name for p in sorted(self.schema.classes['Bp_Behemoth_C'][1], key=lambda p: p.ri)]
        self.assertEqual(DATA['base']['net_prop_names'], live)

    def test_rock_chain(self):
        self.assertEqual(self.leaf, 'sq_rock_normal_bp_C')
        self.assertEqual(self.schema.inherit['sq_rock_normal_bp_C'][:3],
                         ['sq_rock_archetype_bp_C', 'sq_genus_bp_C', 'Bp_Behemoth_C'])
        reps = self.schema.net_props(self.leaf)
        self.assertEqual(len(reps), 76)                                   # Bp_Behemoth_C 73 + Rock 3
        self.assertEqual([p.name for p, _ in reps[-3:]], ['ActiveVortexActor', 'TerraVentParticles', 'ParticleVentColor'])
        self.assertEqual([p.ri for p, _ in reps[-3:]], [73, 74, 75])

    def test_layout_builds(self):
        base = self.schema.layout('Bp_Behemoth_C')
        rock = self.schema.layout(self.leaf)
        self.assertGreater(len(rock.cmds), len(base.cmds))
        self.assertEqual(len(rock.parents), 76)

    def test_components_listed(self):
        classes = {c['class'] for c in DATA['base']['components']}
        self.assertIn('NetworkedMontageComponent', classes)
        self.assertIn('threat_tracking_bpc_C', classes)


if __name__ == '__main__':
    unittest.main()
