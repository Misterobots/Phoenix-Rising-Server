"""Hunt Pass: premium is open to everyone (no premium lock), rewards from progression.json."""
import unittest

from mpgs import huntpass


class Premium(unittest.TestCase):
    def setUp(self):
        self.path = huntpass.load_path('season19')

    def test_record_has_premium(self):
        rec = huntpass.record('season19', self.path, 0, 0)
        self.assertTrue(rec['ActiveHuntPassData[].bHasPremium'])

    def test_premium_rewards_from_config(self):
        items, _ = huntpass.claim_plan(self.path, 0, 2, 'premium_rewards')
        self.assertEqual(items, [('CURRENCY_PJM_WEAPON', 125), ('CURRENCY_NOTES', 10000)])
        free, _ = huntpass.claim_plan(self.path, 0, 1)
        self.assertEqual(free, [('CURRENCY_NOTES', 1000)])

    def test_nothing_twice(self):
        self.assertEqual(huntpass.claim_plan(self.path, 2, 2, 'premium_rewards'), ([], []))


if __name__ == '__main__':
    unittest.main()
