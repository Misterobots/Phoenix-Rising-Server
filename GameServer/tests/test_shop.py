"""Vendor purchases (ServerPurchaseItemFromCatalog): price and quest gate from the 2.1.1 catalog export."""
import unittest

from mpgs import crafting

RECIPES = crafting.load_recipes()


class ShopData(unittest.TestCase):
    def test_every_purchasable_has_a_gate_from_purchase_unlock_criteria(self):
        buy = {k: v for k, v in RECIPES.items() if 'purchase' in v}
        self.assertEqual(len(buy), 19)
        self.assertTrue(all(v.get('purchase_unlock') for v in buy.values()))
        # purchase_unlock_criteria ALCHEMY_STAMINA_PURCHASE: D24_B_SIDE_SLAYERSPATH Redeemed
        self.assertEqual(RECIPES['QI_STAMINA_POTION_00']['purchase_unlock'], [['D24_B_SIDE_SLAYERSPATH', [3]]])
        # GREN_CLUSTER_PURCHASE: D24_B_SIDE_GRANNY_01 Redeemed
        self.assertEqual(RECIPES['QI_GRENADE_CLUSTER']['purchase_unlock'], [['D24_B_SIDE_GRANNY_01', [3]]])
        self.assertEqual(RECIPES['QI_ATTACK_SPEED_POTION']['purchase'], [['CURRENCY_NOTES', 500]])


class ShopPlan(unittest.TestCase):
    def test_locked_until_quest_redeemed(self):
        owned = {'CURRENCY_NOTES': 10000}
        ok, why, _, _ = crafting.plan(RECIPES, 'QI_GRENADE_CLUSTER', 'purchase', 1, owned, lambda q: 1)
        self.assertFalse(ok)
        self.assertIn('D24_B_SIDE_GRANNY_01', why)
        ok, _, spends, grants = crafting.plan(RECIPES, 'QI_GRENADE_CLUSTER', 'purchase', 2, owned,
                                              lambda q: 3 if q == 'D24_B_SIDE_GRANNY_01' else 0)
        self.assertTrue(ok)
        price = RECIPES['QI_GRENADE_CLUSTER']['purchase'][0][1]
        self.assertEqual(spends, [('CURRENCY_NOTES', price * 2)])
        self.assertEqual(grants, [('QI_GRENADE_CLUSTER', 2 * RECIPES['QI_GRENADE_CLUSTER'].get('num', 1))])

    def test_not_enough_notes(self):
        ok, why, _, _ = crafting.plan(RECIPES, 'QI_ATTACK_SPEED_POTION', 'purchase', 1, {'CURRENCY_NOTES': 499},
                                      lambda q: 3)
        self.assertFalse(ok)
        self.assertIn('insufficient', why)


if __name__ == '__main__':
    unittest.main()
