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
        from mpgs.repl import model
        from mpgs.repl.chan import Full
        ends = [e for e in s.entries[s.pos:] if any(f[1] == 'ClientEndAbility' for b in e.parsed.blocks for f in b.fields or ())]
        self.assertEqual(len(ends), 1)                                      # the server ends the client's instance
        w = model.encode(ends[0].parsed, s.ctx, ends[0].cls)
        full = Full()
        full.bits, full.nbits, full.must_map, full.open, full.exports = w.n, w.nbits, False, False, []
        p, _ = model.parse(full, s.ctx, ends[0].cls)
        got = dict(next(f[3] for b in p.blocks for f in b.fields or () if f[1] == 'ClientEndAbility'))
        self.assertEqual(got['AbilityToEnd.Handle'], h)
        self.assertEqual(got['ActivationInfo.PredictionKeyWhenActivated']['current'], 8)
        fx = [e for e in s.entries[s.pos:] for b in e.parsed.blocks for f in b.fields or () if f[1] == 'ActiveGameplayEffects']
        self.assertEqual(len(fx), 1)                                        # the Y cooldown effect the HUD reads
        wch = next(e.ch for e in s.entries for b in e.parsed.blocks if b.actor and b.cls_name == 'dp_umbral_01_bp_C')
        self.assertEqual(fx[0].ch, wch)                                     # on the weapon's own ASC, as StartYCooldown
        w = model.encode(fx[0].parsed, s.ctx, fx[0].cls)
        full = Full()
        full.bits, full.nbits, full.must_map, full.open, full.exports = w.n, w.nbits, True, False, []
        p, _ = model.parse(full, s.ctx, fx[0].cls)
        fa = next(f[3] for b in p.blocks for f in b.fields or () if f[1] == 'ActiveGameplayEffects')
        item = dict(fa['changed'][0][1])
        pre = 'ActiveGameplayEffects.GameplayEffects_Internal[].'
        self.assertEqual(item[pre + 'Spec.Duration'], 60.0)
        self.assertEqual(item[pre + 'Spec.Def'], ('obj', s._effects.cdo['dp_y_ability_cooldown_ge_C']))
        self.assertTrue(any('ReplicatedWorldTimeSeconds' in (b.values or {}) for e in s.entries[s.pos:] for b in e.parsed.blocks))
        rid = fa['changed'][0][0]
        self.assertEqual(s._effects.step(s._effects.active[rid]['ends'] - 1.0), [])
        gone = s._effects.step(s._effects.active[rid]['ends'])
        self.assertEqual(gone[0].parsed.blocks[0].fields[0][3]['deleted'], [rid])   # removed when it runs out
        n = len(s.entries)
        island_encounter.handle_ability(s, asc_ch, pa.hdr, {'AbilityToActivate.Handle': h, 'InputPressed': 0,
                                                            'PredictionKey': {'server': 0, 'current': 10, 'base': 9}})
        self.assertTrue(any(f[1] == 'ClientActivateAbilityFailed' for e in s.entries[s.pos:] for b in e.parsed.blocks
                            for f in b.fields or ()))                       # within the 60 s cooldown
        self.assertEqual(len(enc['allies']), raptors.PACK_SIZE)
        self.assertNotIn('uraptor_00_bp_C', raptors.PACK)                    # the ultra raptor is the legendary's only
        start_health = enc['health']
        out = case.run_until(s, 30.0, player=lambda t: player)
        self.assertLess(enc['health'], start_health)                       # their hits landed
        for _, _, queued in out:                                            # everything sent encodes
            pass
        self.assertTrue(all(r.ctx.bb.get('HasSpawnedIn') for r in enc['allies']))
        case.run_until(s, raptors.LIFESPAN + 1.0, start=30.0, player=lambda t: player)
        self.assertEqual(enc.get('allies'), [])

    def test_equipped_talents_shorten_the_cooldowns(self):
        """SP03 (Q 50 s) and SP05 (X 10 s) equipped: their Infinite effects on the player's ASC, and the shorter cooldowns."""
        import test_island_encounter_engine as T
        from mpgs import talents
        from mpgs.engine import effects
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
        st = {}
        for perk in (2, 4):
            st = talents.set_flag(st, 'W1', 'WTP_DPISTOLS_ALLURING_TALENT2', perk, 'unlocked', True)
            st = talents.set_flag(st, 'W1', 'WTP_DPISTOLS_ALLURING_TALENT2', perk, 'equipped', True)
        s.talent_state = st
        s.player_records = {'active_loadout': {'weapon': {'item_id': 'WP_DP_UMBRAL_01', 'instance_id': 'W1'}}}
        out = island_encounter._talent_setup(s)
        self.assertEqual(s.talent_tags, {'weapon.talent.d24.sp03', 'weapon.talent.d24.sp05'})
        pawn_ch = next(e.ch for e in s.entries if e.open and e.cls == 'BP_PlayerCharacter_C')
        fx = [e for e in out for b in e.parsed.blocks for f in b.fields or () if f[1] == 'ActiveGameplayEffects']
        self.assertEqual(len(fx), 2)
        self.assertTrue(all(e.ch == pawn_ch for e in fx))                   # the player's ASC, not the weapon's
        from mpgs.repl import model
        for e in out:
            model.encode(e.parsed, s.ctx, e.cls)
        self.assertEqual(effects.cooldown(s, 'dp_y_ability_spawn_raptor_ab_C'), 50.0)
        self.assertEqual(effects.cooldown(s, 'dp_x_ability_piercing_ab_C'), 10.0)
        s.talent_tags = set()
        self.assertEqual(effects.cooldown(s, 'dp_y_ability_spawn_raptor_ab_C'), 60.0)
        s.talent_tags = {'weapon.talent.d24.sp01'}                          # SP01: RaptorPackSpawnQuantity 3
        enc, pa = s._encounter, s._encounter['part_abilities']
        h = next(k for k, v in pa.granted.items() if v['cls'] == 'dp_y_ability_spawn_raptor_ab_C')
        bl = enc['loc']
        s.pawn_state = {'loc': [bl[0] - 1200.0, bl[1], bl[2]], 'rot': [0.0, 0.0, 0.0], 'ctrl_rot': [0.0, 0.0, 0.0],
                        'vel': [0.0, 0.0, 0.0], 'mode': 1}
        island_encounter.handle_ability(s, pa.ch, pa.hdr, {'AbilityToActivate.Handle': h, 'InputPressed': 0,
                                                           'PredictionKey': {'server': 0, 'current': 8, 'base': 7}})
        self.assertEqual(len(enc['allies']), 3)

    def test_legendary_tag_move_ultra_raptor_and_cooldown(self):
        import test_island_encounter_engine as T
        from mpgs.engine import legendary
        from mpgs.repl import model
        from mpgs.repl.structs import tag_index
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
        self.assertTrue(enc['legendary']['ready'])
        sent = [b.values for e in s.entries for b in e.parsed.blocks if b.values and 'ReplicatedLooseTags' in b.values]
        self.assertEqual(sent[-1]['ReplicatedLooseTags'], [tag_index('Combat.LegendaryAbilityAvailable')])
        self.assertEqual(tag_index('Combat.LegendaryAbilityAvailable'), 1047)
        bl = enc['loc']
        player = (bl[0] - 1200.0, bl[1], bl[2])
        s.pawn_state = {'loc': list(player), 'rot': [0.0, 0.0, 0.0], 'ctrl_rot': [0.0, 0.0, 0.0], 'vel': [0.0, 0.0, 0.0], 'mode': 1}
        dest = ('soft', '/Game/Blueprints/Weapons/_shared/legendary/mint/active_weapon_part_DP_umbral_1_animstateset.'
                         'active_weapon_part_DP_umbral_1_animstateset:ComboAnimState_0')
        legendary.on_move(s, enc, {'ComboInputBuffer.DestState': dest}, 1.0)
        self.assertFalse(enc['legendary']['ready'])
        case.run_until(s, 3.0, start=1.0, player=lambda t: player)
        self.assertEqual([(r.cls, r.group) for r in enc['allies']], [('uraptor_00_bp_C', 'legend')])
        legendary.on_move(s, enc, {'ComboInputBuffer.DestState': dest}, 3.0)      # on cooldown: nothing more
        case.run_until(s, 122.0, start=3.0, player=lambda t: player)
        self.assertTrue(enc['legendary']['ready'])
        refreshed = [e for e in s.entries for b in e.parsed.blocks for f in b.fields or ()
                     if f[1] == 'Multicast_OnLegendaryAbilityRefreshed']
        self.assertEqual(len(refreshed), 1)                                 # the client clears its own cooldown timer
        model.encode(refreshed[0].parsed, s.ctx, refreshed[0].cls)
        self.assertEqual(len([r for r in enc['allies'] if r.group == 'legend']), 1)


if __name__ == '__main__':
    unittest.main()
