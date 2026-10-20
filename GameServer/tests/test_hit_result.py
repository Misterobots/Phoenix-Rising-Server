"""FHitResult::NetSerialize (UE 5.1.1 Engine/Private/Engine/HitResult.cpp) as written by repl/structs.py, used by ClientTryHit."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mpgs.bitio import BitReader, BitWriter  # noqa: E402
from mpgs.repl.structs import STRUCT_READERS, STRUCT_WRITERS  # noqa: E402


def roundtrip(v):
    w = BitWriter()
    STRUCT_WRITERS['HitResult'](w, v)
    r = BitReader(w.n.to_bytes((w.nbits + 7) // 8, 'little'), w.nbits)
    out = STRUCT_READERS['HitResult'](r)
    again = BitWriter()
    STRUCT_WRITERS['HitResult'](again, out)
    return w, r, out, again


class HitResultTests(unittest.TestCase):
    def test_default_hit_matches_init(self):
        """FHitResult::Init: memzero then Time = 1. ImpactPoint == Location, ImpactNormal == Normal, no penetration -> flags 0x4C;
        Item/FaceIndex/ElementIndex are 0 (valid), so they are written."""
        w, r, out, again = roundtrip({})
        self.assertEqual(w.n & 0xFF, 0x4C)
        self.assertEqual(r.left(), 0)
        self.assertEqual(out['time'], 1.0)
        self.assertEqual((out['item'], out['face_index'], out['element_index']), (0, 0, 0))
        self.assertEqual((w.n, w.nbits), (again.n, again.nbits))

    def test_full_hit_is_bit_exact(self):
        v = {'blocking': 1, 'location': [120.0, -40.0, 15.0], 'normal': [0.0, 0.6, 0.8], 'impact_point': [121.0, -40.0, 15.0],
             'impact_normal': [1.0, 0.0, 0.0], 'trace_start': [0.0, 0.0, 0.0], 'trace_end': [300.0, 0.0, 0.0],
             'penetration': 2.5, 'item': -1, 'actor': 1234, 'component': 1236, 'instance_index': 3, 'face_index': -1,
             'element_index': 7}
        w, r, out, again = roundtrip(v)
        self.assertEqual(r.left(), 0)
        self.assertEqual((w.n, w.nbits), (again.n, again.nbits))
        flags = w.n & 0xFF
        self.assertEqual(flags & 0b1111_0111, 0b0011_0001)       # blocking, item and face index invalid; points differ
        self.assertEqual(out['impact_point'], [121.0, -40.0, 15.0])
        self.assertAlmostEqual(out['normal'][2], 0.8, places=4)  # SerializeFixedVector<1, 16>
        self.assertEqual((out['actor'], out['component'], out['instance_index']), (1234, 1236, 3))
        self.assertEqual(out['element_index'], 7)
        self.assertAlmostEqual(out['penetration'], 2.5)


if __name__ == '__main__':
    unittest.main()
