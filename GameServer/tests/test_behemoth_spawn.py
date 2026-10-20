"""Behemoth spawn / movement / montage entries compose to wire bunches that parse back to what was composed (offline only)."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))  # GameServer/, for mpgs
import os
import unittest
from types import SimpleNamespace

from mpgs.behemoth import bp_schema, spawn
from mpgs.repl import compose, model
from mpgs.repl.chan import Reassembler
from mpgs.repl.guid import flatten
from mpgs.repl.schema import Schema

DATA = bp_schema.load() if os.path.exists(bp_schema.ROCK_SCHEMA) else None
CLS = 'sq_rock_normal_bp_C'
PACKAGE = '/Game/Monsters/sq/rock/sq_rock_normal_bp'


class Ctx:
    """Stand-in for the world's name table: filled from the export headers of what was just composed."""

    def __init__(self, schema):
        self.schema, self._names = schema, {}

    def learn(self, infos):
        for root in infos:
            for i in flatten(root):
                self._names[i.guid] = (i.outer, i.name)

    def known(self):
        return self._names

    def names(self, g):
        return self._names[g]

    def comp_class(self, actor_cls, g):
        n = self._names.get(g, (None, None))[1]
        return self.schema.member_class(actor_cls, n) if n else None


@unittest.skipIf(DATA is None, 'world/behemoths/rock_schema.json not generated (python -m tools.export_behemoth_schema)')
class SpawnTests(unittest.TestCase):
    def setUp(self):
        self.schema = Schema()
        bp_schema.extend(self.schema, DATA)
        self.n = [1000]
        self.guids = spawn.GuidSource(self._next)
        self.ctx = Ctx(self.schema)

    def _next(self):
        self.n[0] += 2
        return self.n[0]

    def roundtrip(self, entry):
        """entry -> wire bunches -> reassembled -> parsed; returns (parsed, bunches)."""
        w = model.encode(entry.parsed, self.ctx, entry.cls)
        bunches = compose.compose(entry.ch, w.n, w.nbits, entry.exports, open=entry.open, close=entry.close,
                                  reliable=entry.reliable, must_map=entry.must_map)
        ra, full = Reassembler(), None
        for b in bunches:
            full = ra.feed(b) or full
        self.assertIsNotNone(full, 'bunches did not reassemble')
        self.ctx.learn(full.exports)
        parsed, _ = model.parse(SimpleNamespace(bits=full.bits, nbits=full.nbits, must_map=full.must_map, open=full.open,
                                                exports=full.exports), self.ctx, entry.cls)
        return parsed, bunches, full

    def make(self, **kw):
        values = kw.pop('values', None)
        return spawn.spawn_entry(self.schema, self.guids, ch=900, cls_name=CLS, package=PACKAGE, loc=(1200.5, -300.25, 90.0),
                                 rot=(0.0, 90.0, 0.0), values=values, **kw)

    def test_guid_parity(self):
        entry, ids = self.make()
        self.assertEqual(ids['actor'] % 2, 0)                       # dynamic = even
        self.assertEqual(ids['archetype'] % 2, 1)                   # static path = odd
        self.assertEqual(ids['package'] % 2, 1)
        self.assertEqual(len({ids['actor'], ids['archetype'], ids['package']}), 3)

    def test_spawn_roundtrip(self):
        values = {'InRageState': 0, 'InCombat': 1, 'ActiveVortexActor': ('obj', 0), 'TerraVentParticles': 1,
                  'ParticleVentColor': [1.0, 0.5, 0.25]}
        entry, ids = self.make(values=values)
        parsed, bunches, full = self.roundtrip(entry)
        o = parsed.open
        self.assertEqual((o['guid'], o['archetype'], o['level']), (ids['actor'], ids['archetype'], 5))
        self.assertAlmostEqual(o['loc'][0], 1200.5, places=1)
        self.assertAlmostEqual(o['rot'][1], 90.0, places=1)
        actor = parsed.blocks[0]
        self.assertTrue(actor.actor)
        self.assertEqual(actor.cls_name, CLS)
        self.assertEqual(actor.values['RemoteRole'], 1)
        self.assertEqual(actor.values['Role'], 3)
        self.assertEqual(actor.values['InCombat'], 1)
        self.assertEqual(actor.values['TerraVentParticles'], 1)
        self.assertEqual(list(actor.values['ParticleVentColor']), [1.0, 0.5, 0.25])
        self.assertTrue(bunches[0].open)
        # the archetype export names the Blueprint's Default object under its package
        arch = next(r for r in full.exports if r.guid == ids['archetype'])
        self.assertEqual(arch.name, 'Default__' + CLS)
        self.assertEqual(arch.outer_info.name, PACKAGE)

    def test_rock_properties_use_reps_73_to_75(self):
        entry, _ = self.make(values={'ActiveVortexActor': ('obj', 0), 'TerraVentParticles': 1, 'ParticleVentColor': [1.0, 2.0, 3.0]})
        lay = self.schema.layout(CLS)
        names = [p.name for p, _ in lay.parents]
        self.assertEqual(names[73:76], ['ActiveVortexActor', 'TerraVentParticles', 'ParticleVentColor'])
        w = model.encode(entry.parsed, self.ctx, CLS)
        plain = spawn.spawn_entry(self.schema, spawn.GuidSource(self._next), ch=901, cls_name=CLS, package=PACKAGE, loc=(0, 0, 0))[0]
        self.assertGreater(w.nbits, model.encode(plain.parsed, self.ctx, CLS).nbits)

    def test_component_block_roundtrip(self):
        entry, ids = self.make(components=[('NetworkedMontage', 'NetworkedMontageComponent', {'bReplicates': 1, 'bIsActive': 1})])
        parsed, bunches, full = self.roundtrip(entry)
        comp = parsed.blocks[1]
        self.assertFalse(comp.actor)
        self.assertEqual(comp.guid, ids['NetworkedMontage'])
        self.assertEqual(comp.cls_name, 'NetworkedMontageComponent')
        self.assertEqual(comp.values['bIsActive'], 1)
        root = next(r for r in full.exports if r.guid == ids['NetworkedMontage'])
        self.assertEqual(root.name, 'NetworkedMontage')
        self.assertEqual(root.outer_info.guid, ids['actor'])

    def test_movement_uses_the_behemoth_quantization(self):
        """Bp_Behemoth's CDO: location/velocity RoundOneDecimal (x10), rotation ShortComponents; decoded here with those levels."""
        from mpgs.bitio import BitReader
        from mpgs.repl.quant import read_quantized_vector, read_rotator
        upd = spawn.movement_entry(900, CLS, loc=(1500.5, -310.25, 90.0), yaw=135.0, vel=(25.5, 0.0, 0.0))
        cmd = next(c for c in self.schema.layout(CLS).cmds if c.name == 'ReplicatedMovement')
        from mpgs.repl.structs import STRUCT_WRITERS
        from mpgs.bitio import BitWriter
        w = BitWriter()
        STRUCT_WRITERS['RepMovement'](w, upd.parsed.blocks[0].values['ReplicatedMovement'])
        r = BitReader(w.n.to_bytes((w.nbits + 7) // 8, 'little'), w.nbits)
        self.assertEqual(r.read(4), 0)
        loc = read_quantized_vector(r, 10)
        rot = read_rotator(r)
        vel = read_quantized_vector(r, 10)
        self.assertEqual(r.left(), 0)
        self.assertAlmostEqual(loc[0], 1500.5, places=1)
        self.assertAlmostEqual(loc[1], -310.2, delta=0.11)
        self.assertAlmostEqual(rot[1], 135.0, delta=0.01)
        self.assertAlmostEqual(vel[0], 25.5, places=1)
        # the pawn default (x100 location, byte rotation) is a different, incompatible bit layout
        default = BitWriter()
        STRUCT_WRITERS['RepMovement'](default, {'loc': [1500.5, -310.25, 90.0], 'rot': [0.0, 135.0, 0.0], 'vel': [25.5, 0, 0]})
        self.assertNotEqual(default.nbits, w.nbits)
        # Phoenix routes ArchonBehemoth classes to RepMovement_Behemoth in the schema; the ported quant hint must match it bit for bit.
        self.assertEqual(cmd.type, 'NetSerialize:RepMovement_Behemoth')
        routed = BitWriter()
        STRUCT_WRITERS['RepMovement_Behemoth'](routed, upd.parsed.blocks[0].values['ReplicatedMovement'])
        self.assertEqual((routed.n, routed.nbits), (w.n, w.nbits))

    def test_root_motion_and_timestamp_roundtrip(self):
        """Engine layer E1: Character.RepRootMotion (flattened struct, handles 34-45) and ReplicatedServerLastTransformUpdateTimeStamp
        go through the real layout and decode back."""
        entry, ids = self.make()
        self.roundtrip(entry)
        rm = spawn.root_motion_values(4321, 1.25, (1500.0, -310.5, 90.0), 45.0, vel=(250.0, 0.0, 0.0))
        upd = spawn.movement_entry(900, CLS, loc=(1500.0, -310.5, 90.0), yaw=45.0, vel=(250.0, 0.0, 0.0),
                                   quant={'loc': 100, 'vel': 1, 'rot': 'byte'}, server_time=37.5, root_motion=rm)
        parsed, _, _ = self.roundtrip(upd)
        v = parsed.blocks[0].values
        self.assertAlmostEqual(v['ReplicatedServerLastTransformUpdateTimeStamp'], 37.5, places=3)
        self.assertEqual(v['RepRootMotion.bIsActive'], 1)
        self.assertEqual(v['RepRootMotion.AnimMontage'], ('obj', 4321))
        self.assertAlmostEqual(v['RepRootMotion.Position'], 1.25, places=4)
        self.assertAlmostEqual(v['RepRootMotion.Location'][0], 1500.0, places=1)
        self.assertEqual(v['RepRootMotion.AuthoritativeRootMotion']['sources'], [])
        self.assertAlmostEqual(v['RepRootMotion.LinearVelocity'][0], 250.0, places=0)
        cleared = spawn.movement_entry(900, CLS, loc=(1500.0, -310.5, 90.0), yaw=45.0, quant={'loc': 100, 'vel': 1, 'rot': 'byte'},
                                       server_time=41.0, root_motion=spawn.root_motion_values(None, 0, (0, 0, 0), 0))
        v2 = self.roundtrip(cleared)[0].blocks[0].values
        self.assertEqual(v2['RepRootMotion.bIsActive'], 0)
        self.assertEqual(v2['RepRootMotion.AnimMontage'], ('obj', 0))

    def test_empty_root_motion_source_group_bits(self):
        """FRootMotionSourceGroup::NetSerialize (UE 5.1.1 source) for an empty group: 3 flags as 32-bit UBOOLs (Ar << bool), zero
        NetQuantize10 vector, one more 32-bit UBOOL, 8-bit flags, then two 8-bit counts (RootMotionSources, PendingAddRootMotionSources)."""
        from mpgs.bitio import BitWriter, BitReader
        from mpgs.repl.structs import STRUCT_WRITERS, STRUCT_READERS
        from mpgs.repl.quant import write_quantized_vector
        w = BitWriter()
        STRUCT_WRITERS['RootMotionSourceGroup'](w, {})
        zero = BitWriter()
        write_quantized_vector(zero, 10, [0.0, 0.0, 0.0])
        self.assertEqual(w.nbits, 3 * 32 + zero.nbits + 32 + 8 + 8 + 8)
        r = BitReader(w.n.to_bytes((w.nbits + 7) // 8, 'little'), w.nbits)
        self.assertEqual(STRUCT_READERS['RootMotionSourceGroup'](r)['sources'], [])

    def test_movement_roundtrip(self):
        entry, ids = self.make()
        self.roundtrip(entry)                                         # teach the name table
        # the generic reader decodes the pawn's quantization (x100 / x1 / byte), so the layout round trip uses it; the behemoth layout is tested above
        upd = spawn.movement_entry(900, CLS, loc=(1500.0, -310.5, 90.0), yaw=45.0, vel=(250.0, 0.0, 0.0),
                                   quant={'loc': 100, 'vel': 1, 'rot': 'byte'})
        parsed, bunches, _ = self.roundtrip(upd)
        mv = parsed.blocks[0].values['ReplicatedMovement']
        self.assertAlmostEqual(mv['loc'][0], 1500.0, places=1)
        self.assertAlmostEqual(mv['vel'][0], 250.0, places=0)
        self.assertFalse(bunches[0].reliable)
        self.assertFalse(bunches[0].open)

    def test_montage_play_rpc_roundtrip(self):
        entry, ids = self.make(components=[('NetworkedMontage', 'NetworkedMontageComponent', None)])
        self.roundtrip(entry)
        cache = {}
        play = spawn.montage_play_entry(self.schema, self.guids, ch=900, actor_cls=CLS, montage_guid_cache=cache,
                                        component_guid=ids['NetworkedMontage'], component_cls='NetworkedMontageComponent',
                                        montage_package='/Game/Monsters/sq/rock/montages/b_sq_rok_00_atk_sidetackle_left_00_mot',
                                        montage_name='b_sq_rok_00_atk_sidetackle_left_00_mot', play_rate=1.0, blend_time=0.25)
        parsed, bunches, full = self.roundtrip(play)
        blk = parsed.blocks[0]
        idx, name, kind, params = blk.fields[0]
        self.assertEqual((name, kind), ('MulticastPlayMontage', 'rpc'))
        (mg, _), = cache.values()
        self.assertEqual(params['NewAnimMontage'], ('obj', mg))
        self.assertAlmostEqual(params['BlendTime'], 0.25)
        self.assertEqual(params['bPlayBit'], 1)
        mont = next(r for r in full.exports if r.guid == mg)
        self.assertEqual(mont.name, 'b_sq_rok_00_atk_sidetackle_left_00_mot')
        self.assertTrue(bunches[0].reliable)
        # a second play of the same montage reuses its GUID
        play2 = spawn.montage_play_entry(self.schema, self.guids, ch=900, actor_cls=CLS, montage_guid_cache=cache,
                                         component_guid=ids['NetworkedMontage'], component_cls='NetworkedMontageComponent',
                                         montage_package='/Game/Monsters/sq/rock/montages/b_sq_rok_00_atk_sidetackle_left_00_mot',
                                         montage_name='b_sq_rok_00_atk_sidetackle_left_00_mot')
        self.assertEqual(len(cache), 1)
        self.assertEqual(play2.exports[0].guid, mg)

    def test_montage_prop_roundtrip(self):
        entry, ids = self.make(components=[('NetworkedMontage', 'NetworkedMontageComponent', None)])
        self.roundtrip(entry)
        cache = {}
        pkg = '/Game/Monsters/sq/rock/montages/b_sq_rok_00_atk_shellslam_mot'
        upd = spawn.montage_prop_entry(self.schema, self.guids, ch=900, actor_cls=CLS, montage_guid_cache=cache,
                                       component_guid=ids['NetworkedMontage'], component_cls='NetworkedMontageComponent',
                                       montage_package=pkg, montage_name='b_sq_rok_00_atk_shellslam_mot', play_rate=1.0,
                                       blend_time=0.25, force_play_bit=1)
        parsed, bunches, full = self.roundtrip(upd)
        v = parsed.blocks[0].values
        (mg, _), = cache.values()
        self.assertEqual(v['RepAnimMontageInfo.AnimMontage'], ('obj', mg))
        self.assertAlmostEqual(v['RepAnimMontageInfo.BlendTime'], 0.25)
        self.assertEqual(v['RepAnimMontageInfo.ForcePlayBit'], 1)
        self.assertEqual(parsed.blocks[0].guid, ids['NetworkedMontage'])

    def test_close_entry(self):
        e = spawn.close_entry(900, CLS)
        w = model.encode(e.parsed, self.ctx, CLS)
        bunches = compose.compose(900, w.n, w.nbits, [], close=True)
        self.assertTrue(bunches[-1].close)

    def test_unregistered_class_refused(self):
        with self.assertRaises(KeyError):
            spawn.spawn_entry(Schema(), self.guids, ch=1, cls_name=CLS, package=PACKAGE, loc=(0, 0, 0))


if __name__ == '__main__':
    unittest.main()
