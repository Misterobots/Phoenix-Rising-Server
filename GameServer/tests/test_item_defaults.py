"""The custom data the client gives an item that arrives without any (mpgs/item_defaults.py)."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))  # GameServer/, for mpgs
import unittest

from mpgs.inventory_v2 import unreal_tchar_crc32
from mpgs.item_defaults import default_custom_data


class ClientDefaultsTests(unittest.TestCase):
    # catalog id -> the CRC the client's inventory state held for it (live 2026-10-28, EAC-off client memory)
    LIVE = {
        'WP_EB_BEGINNER': 0x9b0f0c5c,          # tier 1 weapon: CurrentLevel 0
        'WP_EB_CROWBEAR_EXOTIC': 0xcb670d9a,   # legendary weapon: CurrentLevel 10
        'AR_UNEQUIPPED_HELM': 0x643b8594,
        'LT_BASIC': 0xd79f8a98,
        'PART_DP_GRIP_DEFAULT': 0x81e71270,
        'FL_HEALING_DEFAULT': 0x81e71270,
        'PR_DARKNESS': 0x35eefb39,
        'BN_BEGINNER_00': 0x79499073,          # the banner catalog's CustomData customization
    }

    def test_matches_the_clients_state_crcs(self):
        for catalog, crc in self.LIVE.items():
            self.assertEqual(unreal_tchar_crc32(default_custom_data(catalog)), crc, catalog)

    def test_stacks_and_gliders_stay_empty(self):
        for catalog in ('CURRENCY_NOTES', 'QI_HEALING_PYLON_BIG', 'GD_DEFAULT'):
            self.assertEqual(default_custom_data(catalog), '')


if __name__ == '__main__':
    unittest.main()
