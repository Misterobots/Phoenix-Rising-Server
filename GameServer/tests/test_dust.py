"""Salvage (ServerDustItem*): results from catalog DustItemResult -> bundles_dusting_results (2.1.1 export)."""
import unittest

from mpgs import dust

TABLE = dust.load()


class Dust(unittest.TestCase):
    def test_table_from_bundles(self):
        self.assertGreater(len(TABLE), 900)
        self.assertEqual(TABLE['GATHER_PYLON_01'], [['CURRENCY_NOTES', 10]])          # BUNDLE_DUSTING_RESULT_GATHER_01
        self.assertEqual(TABLE['BREAK_AMBER_EPIC'], [['CURRENCY_NOTES', 40]])

    def test_plan(self):
        ok, _, spends, grants = dust.plan(TABLE, 'GATHER_PYLON_01', 3, {'GATHER_PYLON_01': 5})
        self.assertTrue(ok)
        self.assertEqual((spends, grants), ([('GATHER_PYLON_01', 3)], [('CURRENCY_NOTES', 30)]))
        self.assertFalse(dust.plan(TABLE, 'GATHER_PYLON_01', 6, {'GATHER_PYLON_01': 5})[0])
        self.assertFalse(dust.plan(TABLE, 'CURRENCY_NOTES', 1, {'CURRENCY_NOTES': 5})[0])


if __name__ == '__main__':
    unittest.main()
