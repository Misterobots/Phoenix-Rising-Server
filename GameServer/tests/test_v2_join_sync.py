"""V2 item-state RPCs the client can read, and the server's copy of the client's item state."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))  # GameServer/, for mpgs
import unittest

from mpgs.bitio import BitReader
from mpgs.inventory_v2 import (OPERATION_BITS, encode_client_item_state_updates_field, operation_state_bytes,
                               post_operation_hash)
from mpgs.item_defaults import CLIENT_ITEMS, client_items


def op(operation):
    return {'mismatchCount': 7, 'operation': operation, 'postOperationHash': 0x1122334455667788, 'instanceId': 'A',
            'catalogIdCrc': 0xAABBCCDD, 'quantity': 5, 'customData': ''}


class EncodingTests(unittest.TestCase):
    def test_operation_is_a_three_bit_enum(self):
        # FEnumProperty::GetMaxNetSerializeBits: CeilLogTwo64(EInventoryItemOperation max 4 + 1)
        self.assertEqual(OPERATION_BITS, 3)
        _, _, _, (bits, n) = encode_client_item_state_updates_field([op('UpdateItem')])
        r = BitReader(bits.to_bytes((n + 7) // 8, 'little'), n)
        self.assertEqual((r.read(1), r.read(16), r.read(32), r.read(3), r.read(64)), (1, 1, 7, 3, 0x1122334455667788))

    def test_payload_shrinks_by_five_bits_per_op(self):
        one = encode_client_item_state_updates_field([op('AddItem')])[3][1]
        two = encode_client_item_state_updates_field([op('AddItem'), op('AddItem')])[3][1]
        self.assertEqual(two - one, one - 17)          # 17 = send bit + uint16 count


class ClientStateTests(unittest.TestCase):
    def test_client_items_are_added_once(self):
        recs = client_items({'QI_FLASK_HEAL_00'})
        self.assertEqual(len(recs), len(CLIENT_ITEMS) - 1)
        self.assertTrue(all(r['instanceId'] == r['catalogId'] and r['quantity'] == 1 for r in recs))

    def test_custom_data_kinds(self):
        by = {r['instanceId']: r['customData'] for r in client_items()}
        self.assertEqual(by['PART_WT_KILLS'], '{"CurrentLevel":0,"EquippedCells":[],"EquippedCellsv2":[]}')
        for c in ('PART_LANTERN_CAPACITOR', 'QI_FLASK_REVIVE_00', 'EM_INTRO_RANDOM'):
            self.assertEqual(by[c], '')

    def test_hash_covers_them(self):
        base = [{'instanceId': 'CURRENCY_NOTES', 'catalogIdCrc': 1, 'quantity': 3, 'customData': ''}]
        self.assertNotEqual(post_operation_hash(base), post_operation_hash(base + client_items()))
        self.assertEqual(len(operation_state_bytes(base + client_items())), 16 * (1 + len(CLIENT_ITEMS)))


if __name__ == '__main__':
    unittest.main()
