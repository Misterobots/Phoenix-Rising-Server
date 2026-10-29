"""Engine layer E3: granting the weapon's abilities and answering their activation (mpgs/engine/abilities.py)."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))  # GameServer/, for mpgs
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, '..', 'world', 'ramsgate_template.json')


def session():
    from mpgs.genworld import GenWorld
    world = GenWorld.from_template(TEMPLATE)
    return world.new_session({'name': 'T', 'char': 'C', 'acct': 'A', 'session': 'S'})


def beginner_sword():
    """WP_EB_BEGINNER's power-row abilities: eb_charge_ab, then the special eb_q_projectile_raw_ab."""
    from mpgs.engine import weapon_parts
    return weapon_parts.power_abilities(weapon_parts.load_power(), 'WP_EB_BEGINNER')


@unittest.skipUnless(os.path.exists(TEMPLATE), 'world template missing')
class GrantTests(unittest.TestCase):
    def setUp(self):
        from mpgs.engine.abilities import PlayerAbilities
        self.s = session()
        self.ab = PlayerAbilities(self.s)                    # the player's ASC (recorded specs 1-11)
        self.sword = beginner_sword()

    def test_recorded_state_is_read_from_the_join_world(self):
        self.ab._load_recorded()
        self.assertEqual(self.ab.fa_key, 12)                 # recorded ArrayReplicationKey
        self.assertEqual(self.ab.next_handle, 12)            # recorded handles 1-11
        self.assertEqual(self.ab.next_rid, 12)               # recorded ReplicationIDs 1-9, 11

    def test_grant_encodes_and_decodes_back(self):
        from mpgs.repl import model
        from mpgs.repl.chan import Full
        e, handles = self.ab.grant_entry(self.sword)
        self.assertEqual(handles, [12, 13])
        w = model.encode(e.parsed, self.s.ctx, e.cls)
        full = Full()
        full.bits, full.nbits, full.must_map, full.open, full.exports = w.n, w.nbits, True, False, e.exports
        for info in e.exports:                              # teach the decoder the exported names (as the client learns them)
            self.s.ctx._names[info.guid] = (info.outer_info.guid if info.outer_info else None, info.name)
        p, _ = model.parse(full, self.s.ctx, e.cls)
        self.assertEqual(p.must_map, e.parsed.must_map)
        inst = [b for b in p.blocks if not b.stable]
        self.assertEqual([b.cls_name for b in inst], ['eb_charge_ab_C', 'eb_q_projectile_raw_ab_C'])
        asc = p.blocks[-1]
        fa = next(f[3] for f in asc.fields if f[1] == 'ActivatableAbilities')
        self.assertEqual((fa['key'], fa['base']), (13, 12))
        got = {dict(v)['ActivatableAbilities.Items[].Handle.Handle']: dict(v) for _, v in fa['changed']}
        self.assertEqual(sorted(got), [12, 13])
        ref = got[12]['ActivatableAbilities.Items[].ReplicatedInstances'][0][0][1]
        self.assertEqual(ref, ('obj', inst[0].guid))       # the spec points at the instance sent in the same bunch

    def test_power_row_abilities(self):
        from mpgs.engine import weapon_parts
        self.assertEqual([(c, w) for _, c, w in self.sword], [('eb_charge_ab_C', 'power RefAbilities'),
                                                              ('eb_q_projectile_raw_ab_C', 'power RefSpecialAbilities1')])
        power = weapon_parts.load_power()
        self.assertEqual(weapon_parts.power_abilities(power, 'WP_EB_CROWBEAR_EXOTIC'), [])   # The Hunger: no abilities
        self.assertEqual(weapon_parts.power_abilities(power, 'WP_NO_SUCH_ROW'), [])           # no row: nothing, no fallback

    def test_activation_of_a_granted_ability_succeeds_with_a_server_key(self):
        from mpgs.repl import model
        e, handles = self.ab.grant_entry(self.sword)
        req = self.ab.hdr
        n = len(self.s.entries)
        self.ab.on_try_activate(self.ab.ch, req, {'AbilityToActivate.Handle': 13, 'InputPressed': 0, 'PredictionKey': {'server': 0}})
        self.assertEqual(len(self.s.entries), n + 1)
        reply = self.s.entries[self.s.pos]
        name, params = reply.parsed.blocks[0].fields[0][1], reply.parsed.blocks[0].fields[0][3]
        self.assertEqual(name, 'ClientActivateAbilitySucceed')
        self.assertEqual(params['PredictionKey'], {'server': 1, 'current': 1})
        model.encode(reply.parsed, self.s.ctx, reply.cls)
        self.ab.on_try_activate(self.ab.ch, req, {'AbilityToActivate.Handle': 13, 'PredictionKey': {}})
        self.assertEqual(self.s.entries[self.s.pos].parsed.blocks[0].fields[0][3]['PredictionKey']['current'], 2)

    def test_recorded_abilities_get_no_reply(self):
        self.ab._load_recorded()
        n = len(self.s.entries)
        self.ab.on_try_activate(self.ab.ch, self.ab.hdr, {'AbilityToActivate.Handle': 11, 'PredictionKey': {'server': 0, 'current': 1}})
        self.assertEqual(len(self.s.entries), n)


@unittest.skipUnless(os.path.exists(TEMPLATE), 'world template missing')
class MeterTests(unittest.TestCase):
    ROWS = {'EBRework.Y3': {'tags': ['Combat.AttackWeight.Heavy', 'Combat.Sword.TokenGranterTwo']},
            'EBRework.X3': {'tags': ['Combat.Sword.TokenGranter']},
            'EBRework.X1': {'tags': ['Combat.AttackWeight.Light']}}

    def setUp(self):
        from mpgs.engine.weapon_meter import WeaponMeter
        self.s = session()
        self.logs = []
        self.m = WeaponMeter(self.s, self.ROWS, self.logs.append)

    def test_the_meter_is_the_weapons_damage_attribute_set(self):
        self.assertEqual(self.m.weapon, 'eblade_beginner_00_bp_C')
        self.assertEqual(self.m.header.cls_name, 'ArchonDamageAttributeSet')
        self.assertNotEqual(self.m.ch, next(c for c, k in self.s.chan_cls.items() if k == 'BP_PlayerCharacter_C'))

    def test_start_and_gains_encode(self):
        from mpgs.repl import model
        e = self.m.start_entry()
        self.assertEqual(dict(e.parsed.blocks[0].values), {'Determination': 0.0, 'DeterminationMax': 100.0})
        model.encode(e.parsed, self.s.ctx, e.cls)
        self.assertEqual(self.m.on_hit('EBRework.X1'), 0.0)
        self.assertEqual(self.m.on_hit('EBRework.Y3'), 12.0)
        self.assertEqual(self.m.on_hit('EBRework.X3'), 6.0)
        self.assertEqual(self.m.value, 18.0)
        model.encode(self.s.entries[self.s.pos].parsed, self.s.ctx, e.cls)
        for _ in range(10):
            self.m.on_hit('EBRework.Y3')
        self.assertEqual(self.m.value, 100.0)                       # clamped to the max
        self.assertTrue(self.m.full)

    def test_increase_meter_request(self):
        """weapon_meter_bpc.IncreaseMeter as the Crowbear exotic sends it (live 2026-10-28: 81 bits, 3.0 scaled)."""
        from mpgs.engine.weapon_meter import increase_params, is_meter_weapon
        live = '0100000000001080068001'                       # a captured request (logs/rpc_capture.jsonl), 81 bits
        raw = (int.from_bytes(bytes.fromhex(live), 'little'), 81)
        self.assertEqual(increase_params(raw), (3.0, True))
        self.assertEqual(self.m.on_increase(3.0, True), 3.0)
        self.assertEqual(self.m.on_increase(-1.0, True), 0.0)       # Auth_IncreaseMeter refuses negative amounts
        self.assertTrue(is_meter_weapon('eblade_crowbear_exotic_00_bp_C'))
        self.assertFalse(is_meter_weapon('dp_weapon_bp_C'))

    def test_special_needs_a_full_meter_and_spends_it(self):
        from mpgs.engine.abilities import PlayerAbilities
        ab = PlayerAbilities(self.s)
        ab.grant_entry(beginner_sword())
        ab.meter = self.m
        ask = {'AbilityToActivate.Handle': 13, 'PredictionKey': {'server': 0, 'current': 4}}     # the special, on the pawn's ASC
        ab.on_try_activate(ab.ch, ab.hdr, ask)
        self.assertEqual(self.s.entries[self.s.pos].parsed.blocks[0].fields[0][1], 'ClientActivateAbilityFailed')
        self.m.value = 100.0
        ab.on_try_activate(ab.ch, ab.hdr, ask)
        names = [b.fields[0][1] if b.fields else 'props' for e in self.s.entries[self.s.pos:self.s.pos + 2] for b in e.parsed.blocks]
        self.assertIn('ClientActivateAbilitySucceed', names)
        self.assertEqual(self.m.value, 0.0)


if __name__ == '__main__':
    unittest.main()
