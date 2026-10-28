"""Ally creatures in the island encounter: spawn, tick, replicate and despawn the repeater Q raptors (mpgs.creatures.raptors).

Replication, as the encounter does it for the behemoth: an actor channel per raptor (spawn.spawn_entry, Instigator = the pawn,
IsAlly = true: af_raptor_archetype_bp's replicated IsAlly / HasSpawnedIn), MulticastPlayMontage on its NetworkedMontage component,
unreliable ReplicatedMovement updates, and a channel close when its lifespan ends.
"""
import math
import random

from ..repl import compose, model

SEND_INTERVAL = 1.0 / 15.0
MONTAGE_COMPONENT = ('NetworkedMontage', 'NetworkedMontageComponent')
# ReplicatedMovement quantization: the raptor blueprints (and base_creature_bp / af_raptor_archetype_bp) set none, so they inherit the
# native ArchonAbilityCharacter values the player pawn uses (BP_PlayerCharacter sets none either; x100 / x1 / byte confirmed live).
# Bp_Behemoth's own override (x10 / x10 / short) made the client fail the bunch (ObjectReplicatorReceivedBunchFail, 2026-10-27).
RAPTOR_MOVE_QUANT = {'loc': 100, 'vel': 1, 'rot': 'byte'}


def _log(msg):
    print('ALLY ' + msg, flush=True)


def _data(session):
    cached = getattr(session.world, '_raptor_data', None)
    if cached is None:
        from ..creatures import raptors
        cached = session.world._raptor_data = raptors.load() or {}
    return cached or None


def spawn_raptors(session, enc, now, group='q', classes=None, lifespan=None, yaw_key='ctrl_rot'):
    """Spawn ally raptors at the player, first clearing the previous ones of the same group. -> entries.
    group 'q': dp_y_ability_spawn_raptor_ab (PACK_SIZE random picks of PACK, camera yaw, LIFESPAN; KillPrevSpawnedRaptors).
    group 'legend': legendary_weapon_part_spawn_raptor_bpc (one uraptor, the character's yaw, ULTRA_LIFESPAN; ClearRaptorsLegend)."""
    from ..creatures import raptors
    from ..behemoth import spawn
    from ..loadout import register_atlas_class_full
    from ..island_encounter import _pawn_guid
    data = _data(session)
    director = enc.get('director')
    st = getattr(session, 'pawn_state', None)
    if data is None or director is None or st is None:
        _log('raptors not spawned (%s)' % ('no world/creatures/raptor; python -m tools.export_raptors' if data is None
                                            else 'no behemoth engine' if director is None else 'no player position'))
        return []
    out = despawn_all(session, enc, group)
    director.extra_blockers = lambda: [(r.mover.x, r.mover.y, _radius(r)) for r in enc.get('allies') or ()]
    schema = session.world.schema
    rng = enc.setdefault('ally_rng', random.Random(enc['guid'] + 7))
    pack = enc.setdefault('allies', [])
    beh = raptors.Behemoth(lambda: (tuple(director.loc), director.yaw, enc['health'] > 0.0), director.species.bodies,
                           getattr(director.species, 'capsule_radius', 0.0))
    yaw = float((st.get(yaw_key) or st.get('ctrl_rot') or [0.0, 0.0, 0.0])[1])
    tags = getattr(session, 'talent_tags', None) or set()
    size = 3 if 'weapon.talent.d24.sp01' in tags else raptors.PACK_SIZE      # Q: SP01 sets RaptorPackSpawnQuantity 3
    picks = classes or [rng.choice(raptors.PACK) for _ in range(size)]
    lifespan = raptors.LIFESPAN if lifespan is None else lifespan
    for i, cls in enumerate(picks):
        if not register_atlas_class_full(schema, cls):
            _log('%s has no layout; skipped' % cls)
            continue
        r = math.radians(yaw)
        loc = [st['loc'][0] + 150.0 * math.cos(r) - (i - 0.5) * 120.0 * math.sin(r),
               st['loc'][1] + 150.0 * math.sin(r) + (i - 0.5) * 120.0 * math.cos(r), st['loc'][2]]
        raptor = raptors.Raptor(data, cls, director.terrain, loc, yaw, lambda: getattr(session, 'pawn_state', None), beh,
                                lambda: enc.get('allies') or [], now, rng=random.Random(rng.random()),
                                log=lambda m, c=cls: _log('%s %s' % (c, m)), lifespan=lifespan)
        raptor.group = group
        raptor.mover.blockers = (lambda me=raptor: _raptor_blockers(enc, director, me))
        session._live_ch = getattr(session, '_live_ch', 900)
        raptor.ch = session._live_ch
        session._live_ch += 1
        package = raptors.RAPTOR_PACKAGES[cls]
        entry, ids = spawn.spawn_entry(schema, enc['guids'], ch=raptor.ch, cls_name=cls, package=package,
                                       loc=[round(v, 1) for v in raptor.loc], rot=[0.0, round(yaw, 2), 0.0],
                                       values={'Instigator': ('obj', _pawn_guid(session)), 'IsAlly': True, 'IsAlive': True,
                                               'HasSpawnedIn': False},
                                       components=[(MONTAGE_COMPONENT[0], MONTAGE_COMPONENT[1], None)])
        session.chan_cls[raptor.ch] = cls
        session.ctx._names[ids['package']] = (0, package)
        session.ctx._names[ids['archetype']] = (ids['package'], 'Default__' + cls)
        session.ctx._names[ids[MONTAGE_COMPONENT[0]]] = (ids['actor'], MONTAGE_COMPONENT[0])
        raptor.montage_comp = ids[MONTAGE_COMPONENT[0]]
        raptor.sent_at = 0.0
        pack.append(raptor)
        out.append(entry)
        _log('spawned %s ch%d at %s (lifespan %.0f s)' % (cls, raptor.ch, [round(v) for v in loc], lifespan))
    return out


def _radius(r):
    from ..creatures import raptors
    return raptors.CAPSULE_RADIUS * (1.5 if r.cls == 'uraptor_00_bp_C' else 1.0)   # uraptor_00_bp scales itself 1.5x


def _raptor_blockers(enc, director, me):
    """Capsules a raptor may not walk into: the behemoth's and the other raptors' (both radii). Without it a sprinting raptor
    (~160 cm per 15 Hz tick) ended inside the behemoth's capsule and the client pushed the overlapping pawns apart, so all of them
    snapped back on the next update (live 2026-10-27)."""
    own = _radius(me)
    out = [(director.mover.x, director.mover.y, own + getattr(director.species, 'capsule_radius', 0.0))]
    return out + [(r.mover.x, r.mover.y, own + _radius(r)) for r in enc.get('allies') or () if r is not me]


def despawn_all(session, enc, group=None):
    """Close the raptors of `group` (all when None)."""
    from ..behemoth.spawn import close_entry
    gone = [r for r in enc.get('allies') or () if group is None or getattr(r, 'group', 'q') == group]
    enc['allies'] = [r for r in enc.get('allies') or () if r not in gone]
    return [close_entry(r.ch, r.cls) for r in gone if r.ch is not None]


def step(session, enc, now):
    """-> (reliable entries, unreliable bunches) for this tick."""
    from ..behemoth import spawn
    from ..behemoth.spawn import close_entry
    from ..island_encounter import _entry, _block, handle_damage, _montage_info
    allies = enc.get('allies')
    if not allies:
        return [], []
    reliable, bunches, keep = [], [], []
    for r in allies:
        if now >= r.expires:
            reliable.append(close_entry(r.ch, r.cls))
            _log('%s ch%d lifespan over' % (r.cls, r.ch))
            continue
        keep.append(r)
        spawned = r.ctx.bb.get('HasSpawnedIn')
        for ev in r.tick(now):
            if ev[0] == 'montage':
                m = r.data['montages'].get(ev[1]) or {}
                if not m.get('package'):
                    continue
                reliable.append(spawn.montage_play_entry(
                    session.world.schema, enc['guids'], ch=r.ch, actor_cls=r.cls, montage_guid_cache=enc['montage_guids'],
                    component_guid=r.montage_comp, component_cls=MONTAGE_COMPONENT[1], montage_package=m['package'],
                    montage_name=ev[1]))
                _log('%s ch%d send montage %s' % (r.cls, r.ch, ev[1]))
            elif ev[0] == 'hit':
                _, row, bone, dmg = ev
                dmg *= getattr(r, 'damage_mult', 1.0)
                _log('%s %s hit %s for %.0f' % (r.cls, row, bone, dmg))
                handle_damage(session, None, None, {'Damage': dmg, 'Payload.Hit': {'bone_name': [bone]}})
        if r.ctx.bb.get('HasSpawnedIn') and not spawned:
            reliable.append(_entry(session.world, r.ch, r.cls, [_block(r.cls, {'HasSpawnedIn': True})]))
            _log('%s ch%d send HasSpawnedIn' % (r.cls, r.ch))
        ending = getattr(r, 'root_motion', None) is not None and r.mover.montage is None   # send the RepRootMotion clear
        if now - r.sent_at >= SEND_INTERVAL and (r.mover.pop_changed() or ending):
            r.sent_at = now
            m = r.mover
            vel = (m.walk_speed * math.cos(math.radians(m.yaw)), m.walk_speed * math.sin(math.radians(m.yaw)), 0.0) \
                if m.path else (0.0, 0.0, 0.0)
            rm, exports = None, []
            if getattr(r, 'root_motion', None) is not None:        # Character.RepRootMotion, as for the behemoth
                name, package, t0 = r.root_motion
                if m.montage is not None and (package, name) in enc['montage_guids']:
                    rm = spawn.root_motion_values(enc['montage_guids'][(package, name)][0], now - t0, m.loc, m.yaw, vel)
                    exports = [_montage_info(enc, name, package)]
                else:
                    rm = spawn.root_motion_values(None, 0.0, (0.0, 0.0, 0.0), 0.0)
                    r.root_motion = None
            upd = spawn.movement_entry(r.ch, r.cls, m.loc, m.yaw, vel=vel, mode=1, server_time=now, quant=RAPTOR_MOVE_QUANT,
                                       root_motion=rm, exports=exports)
            w = model.encode(upd.parsed, session.ctx, r.cls)
            bunches.extend(compose.compose(r.ch, w.n, w.nbits, upd.exports, open=False, close=False, reliable=False))
    enc['allies'] = keep
    return reliable, bunches
