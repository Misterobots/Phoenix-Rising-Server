"""Damage model, hit-name decoding, Rock health and the hit reply (offline)."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))  # GameServer/, for mpgs
import os
import tempfile
import unittest
from types import SimpleNamespace

from mpgs.behemoth import damage
from mpgs.behemoth.session import RockDirector

READY = os.path.exists(damage.DATA)
TEMPLATE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'world', 'ramsgate_template.json')

ROWS = {'EBRework.X1': {'BaseDamage': 100}, 'EBRework.X1_Dash': {'BaseDamage': 375}, 'EBRework.Y3': {'BaseDamage': 310}}
MODEL = {'curves': {damage.PLAYER_CURVE: [[-125, 0.25], [-124, 0.65], [0, 1.0], [124, 1.3], [125, 2.0]]}, 'rows': ROWS,
         'rock': {'normal': {'power': 120.0}, 'heroic': {'power': 425.0}}}


def raw_with(text, shift, pad=11):
    """A bit string holding `text` NUL-terminated at a bit offset (what an FString inside a packed RPC looks like)."""
    data = b'\x15' * pad + text.encode() + b'\x00' + b'\x07' * 5
    return (int.from_bytes(data, 'little') << shift) | 0b101, len(data) * 8 + shift


class CurveTests(unittest.TestCase):
    def test_interpolation_and_clamps(self):
        k = MODEL['curves'][damage.PLAYER_CURVE]
        self.assertEqual(damage.eval_curve(k, 0), 1.0)
        self.assertAlmostEqual(damage.eval_curve(k, 62), 1.15)
        self.assertEqual(damage.eval_curve(k, -500), 0.25)
        self.assertEqual(damage.eval_curve(k, 500), 2.0)

    def test_attack_name_at_any_bit_offset(self):
        for shift in range(8):
            self.assertEqual(damage.find_attack_name(raw_with('EBRework.Y3', shift), ROWS), 'EBRework.Y3', shift)

    def test_longest_known_name_wins(self):
        self.assertEqual(damage.find_attack_name(raw_with('EBRework.X1_Dash', 3), ROWS), 'EBRework.X1_Dash')
        self.assertEqual(damage.find_attack_name(raw_with('EBRework.X1', 3), ROWS), 'EBRework.X1')

    def test_unknown_name(self):
        self.assertIsNone(damage.find_attack_name(raw_with('Other.Thing', 2), ROWS))


class PredictionKeyTests(unittest.TestCase):
    def report(self, counter, extra_bits=700):
        """An arbitrary body followed by the key as live reports end: valid=1, has_base=0, server=0, 16-bit counter."""
        body = (0x5A5A5A5A5A5A5A5A5A5A * 0x10001) & ((1 << extra_bits) - 1)
        tail = 1 | (0 << 1) | (0 << 2) | (counter << 3)
        return (body | (tail << extra_bits)), extra_bits + 19

    def test_reads_the_counter_from_the_tail(self):
        for c in (23, 24, 25, 65535):
            self.assertEqual(damage.tail_prediction_key(self.report(c)), {'server': 0, 'current': c})

    def test_invalid_key_is_none(self):
        bits, n = self.report(7)
        self.assertIsNone(damage.tail_prediction_key((bits & ~(1 << (n - 19)), n)))

    def test_live_report_tail(self):
        # last 24 bits of three real reports (LSB-first as printed during analysis): counters 23, 24, 25
        for window, want in (('000011001110100000000000', 23), ('000011000001100000000000', 24), ('000011001001100000000000', 25)):
            v = int(window[::-1], 2)
            self.assertEqual(damage.tail_prediction_key((v, 24)), {'server': 0, 'current': want})


class HealthTests(unittest.TestCase):
    def health(self, **env):
        return damage.RockHealth(MODEL, 'normal', env=env)

    def test_damage_uses_row_and_power_delta(self):
        self.assertEqual(self.health().damage_for('EBRework.X1'), 100.0)
        self.assertAlmostEqual(self.health(MPGS_PLAYER_POWER='182').damage_for('EBRework.X1'), 100 * 1.15)
        self.assertIsNone(self.health().damage_for('Nope.X'))

    def test_dies_and_then_ignores_hits(self):
        h = self.health(MPGS_ROCK_HEALTH='250')
        self.assertEqual(h.apply('EBRework.X1'), (100.0, False))
        self.assertEqual(h.apply('EBRework.X1'), (100.0, False))
        self.assertEqual(h.apply('EBRework.X1'), (50.0, True))          # capped at the remaining health
        self.assertEqual(h.apply('EBRework.X1'), (0.0, False))
        self.assertTrue(h.dead)

    def test_unknown_attack_deals_nothing(self):
        h = self.health()
        self.assertEqual(h.apply('Nope.X'), (0.0, False))
        self.assertEqual(h.current, h.max)


class FakeSession:
    def __init__(self):
        self.pawn_state = {'loc': [0.0, 0.0, 0.0], 'vel': [0.0, 0.0, 0.0], 'ctrl_rot': [0.0, 0.0, 0.0]}


def active_director(tmpdir, **env):
    base = {'MPGS_HIT_LOG': os.path.join(tmpdir, 'hits.jsonl'), 'MPGS_ROCK_HEALTH': '250'}
    base.update(env)
    d = RockDirector(FakeSession(), env=base)
    d.state, d.loc = 'active', [500.0, 0.0, 0.0]
    d.health = damage.RockHealth(MODEL, 'normal', env=base)
    d.brain = SimpleNamespace(run='x')
    return d


class DirectorHitTests(unittest.TestCase):
    def test_hit_in_range_counts_and_is_logged(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = active_director(tmp)
            raw = raw_with('EBRework.X1', 5)
            self.assertTrue(d.on_hit(raw, 12.0))
            self.assertEqual(d.health.current, 150.0)
            line = open(os.path.join(tmp, 'hits.jsonl')).read()
            self.assertIn('"nbits": %d' % raw[1], line)
            self.assertEqual(int.from_bytes(bytes.fromhex(__import__('json').loads(line)['hex']), 'little'), raw[0])

    def test_out_of_range_hit_does_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = active_director(tmp, MPGS_HIT_RANGE='100')
            self.assertFalse(d.on_hit(raw_with('EBRework.X1', 0), 1.0))
            self.assertEqual(d.health.current, d.health.max)

    def test_death_stops_the_current_attack(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = active_director(tmp, MPGS_ROCK_HEALTH='100')
            self.assertTrue(d.on_hit(raw_with('EBRework.X1', 0), 1.0))
            self.assertTrue(d.health.dead)
            self.assertIsNone(d.brain.run)

    def test_before_spawn_only_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = active_director(tmp)
            d.state = 'waiting'
            self.assertFalse(d.on_hit(raw_with('EBRework.X1', 0), 1.0))
            self.assertTrue(os.path.exists(os.path.join(tmp, 'hits.jsonl')))


@unittest.skip('Mystic GenSession._player_hit (Rock session glue) is not ported; Phoenix replies in '
               'island_encounter.handle_damage. Re-target in ENGINE_PORT_PLAN.md step 5.')
class ReplyTests(unittest.TestCase):
    def test_counted_hit_queues_a_succeed_rpc_on_the_same_channel(self):
        from mpgs.genworld import GenWorld
        from mpgs.repl import model
        world = GenWorld.from_template(TEMPLATE)
        s = world.new_session({'name': 'T', 'char': 'C', 'acct': 'A', 'session': 'S'})
        s._rock = SimpleNamespace(on_hit=lambda raw, now: True)
        ch = next(c for c, cls in s.chan_cls.items() if cls == 'BP_PlayerCharacter_C')
        req = SimpleNamespace(cls_name='DamageComponent', guid=1234, ref=None, ref_name=None)
        n = len(s.entries)
        s._player_hit(ch, req, (0, 8))
        self.assertEqual(len(s.entries), n + 1)
        e = s.entries[s.pos]
        self.assertEqual((e.ch, e.parsed.blocks[0].fields[0][1]), (ch, 'ClientDoDamageSucceed'))
        model.encode(e.parsed, s.ctx, e.cls)                       # the codec can write it

    def test_reply_can_be_turned_off_and_uncounted_hits_get_none(self):
        from mpgs.genworld import GenWorld
        world = GenWorld.from_template(TEMPLATE)
        s = world.new_session({'name': 'T', 'char': 'C', 'acct': 'A', 'session': 'S'})
        req = SimpleNamespace(cls_name='DamageComponent', guid=1234, ref=None, ref_name=None)
        n = len(s.entries)
        s._rock = SimpleNamespace(on_hit=lambda raw, now: False)
        s._player_hit(4, req, (0, 8))
        os.environ['MPGS_HIT_REPLY'] = '0'
        try:
            s._rock = SimpleNamespace(on_hit=lambda raw, now: True)
            s._player_hit(4, req, (0, 8))
        finally:
            os.environ.pop('MPGS_HIT_REPLY', None)
        self.assertEqual(len(s.entries), n)


if __name__ == '__main__':
    unittest.main()
