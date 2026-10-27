"""Ally creatures in the island encounter: spawn, tick, replicate and despawn the repeater Q raptors (mpgs.creatures.raptors).

Replication, as the encounter does it for the behemoth: an actor channel per raptor (spawn.spawn_entry, Instigator = the pawn,
IsAlly = true: af_raptor_archetype_bp's replicated IsAlly / HasSpawnedIn), MulticastPlayMontage on its NetworkedMontage component,
unreliable ReplicatedMovement updates, and a channel close when its lifespan ends.
"""
import math
import random
import time

from ..repl import compose, model

SEND_INTERVAL = 1.0 / 15.0
MONTAGE_COMPONENT = ('NetworkedMontage', 'NetworkedMontageComponent')


def _log(msg):
    print('ALLY ' + msg, flush=True)


def _data(session):
    cached = getattr(session.world, '_raptor_data', None)
    if cached is None:
        from ..creatures import raptors
        cached = session.world._raptor_data = raptors.load() or {}
    return cached or None


def spawn_raptors(session, enc, now):
    """dp_y_ability_spawn_raptor_ab on the server: clear the previous pack, spawn PACK_SIZE random raptors at the player. -> entries."""
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
    out = despawn_all(session, enc)
    schema = session.world.schema
    rng = enc.setdefault('ally_rng', random.Random(enc['guid'] + 7))
    pack = enc['allies'] = []
    beh = raptors.Behemoth(lambda: (tuple(director.loc), director.yaw, enc['health'] > 0.0), director.species.bodies,
                           getattr(director.species, 'capsule_radius', 0.0))
    yaw = float((st.get('ctrl_rot') or [0.0, 0.0, 0.0])[1])   # the control rotation: GetThrowPosition uses the camera yaw
    for i in range(raptors.PACK_SIZE):
        cls = rng.choice(raptors.PACK)
        if not register_atlas_class_full(schema, cls):
            _log('%s has no layout; skipped' % cls)
            continue
        r = math.radians(yaw)
        loc = [st['loc'][0] + 150.0 * math.cos(r) - (i - 0.5) * 120.0 * math.sin(r),
               st['loc'][1] + 150.0 * math.sin(r) + (i - 0.5) * 120.0 * math.cos(r), st['loc'][2]]
        raptor = raptors.Raptor(data, cls, director.terrain, loc, yaw, lambda: getattr(session, 'pawn_state', None), beh,
                                lambda: enc.get('allies') or [], now, rng=random.Random(rng.random()),
                                log=lambda m, c=cls: _log('%s %s' % (c, m)))
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
        _log('spawned %s ch%d at %s (lifespan %.0f s)' % (cls, raptor.ch, [round(v) for v in loc], raptors.LIFESPAN))
    return out


def despawn_all(session, enc):
    from ..behemoth.spawn import close_entry
    out = [close_entry(r.ch, r.cls) for r in enc.get('allies') or () if r.ch is not None]
    enc['allies'] = []
    return out


def step(session, enc, now):
    """-> (reliable entries, unreliable bunches) for this tick."""
    from ..behemoth import spawn
    from ..behemoth.spawn import close_entry
    from ..island_encounter import _entry, _block, handle_damage
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
            elif ev[0] == 'hit':
                _, row, bone, dmg = ev
                _log('%s %s hit %s for %.0f' % (r.cls, row, bone, dmg))
                handle_damage(session, None, None, {'Damage': dmg, 'Payload.Hit': {'bone_name': [bone]}})
        if r.ctx.bb.get('HasSpawnedIn') and not spawned:
            reliable.append(_entry(session.world, r.ch, r.cls, [_block(r.cls, {'HasSpawnedIn': True})]))
        if now - r.sent_at >= SEND_INTERVAL and r.mover.pop_changed():
            r.sent_at = now
            m = r.mover
            vel = (m.walk_speed * math.cos(math.radians(m.yaw)), m.walk_speed * math.sin(math.radians(m.yaw)), 0.0) \
                if m.path else (0.0, 0.0, 0.0)
            upd = spawn.movement_entry(r.ch, r.cls, m.loc, m.yaw, vel=vel, mode=1, server_time=now)
            w = model.encode(upd.parsed, session.ctx, r.cls)
            bunches.extend(compose.compose(r.ch, w.n, w.nbits, upd.exports, open=False, close=False, reliable=False))
    enc['allies'] = keep
    return reliable, bunches
