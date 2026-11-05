"""The player's combo moves on the server: plays each move's montage timeline and fires the notifies that do server work.

What the game does: CharCombatComp.ServerStartNextMove carries ComboInputBuffer.DestState, the combo state the client
entered ('<animstateset>:ComboAnimState_N'). The server plays that state's AnimMontage too. Its notifies with
bTriggerOnDedicatedServer run there at their times: notifies fire once, notify states begin at their time and end when
their duration is over or the montage is replaced (UE ends the active notify states of a montage that stops). A montage
plays its sections in order and follows NextSectionName; a section whose next is itself loops until the next move.
The move starts at the NextMontageSection of the transition the client took, when the previous state names one.

Data: world/player/combat_moves.json (tools/export_combat_moves.py): every weapon's combo states and every montage
notify with its time. The Atlas lists the same per weapon (python -m tools.atlas combat; weapons/<key>_combat.txt), and
its 'server' notifies are SERVER_NOTIFIES below: each one is handled in HANDLERS or logged once as unhandled.

Assumed: play rate 1 (transition PlayRate, the montage RateScale and attack speed attributes are not applied yet).
"""
import json
import os

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MOVES = os.path.join(HERE, 'world', 'player', 'combat_moves.json')

# Notify classes whose Blueprint code does server work (Atlas combat tables, 2026-11-04). Branch-only notifies
# (debris_event_bpn, footsteps_event_bpn, dp_play_muzzle_vfx_n) make no server call and are not listed.
SERVER_NOTIFIES = {
    'Notify_ConsumeStamina_C', 'Notify_ConsumeStamina_RepeatingElements_C', 'Notify_ConsumeStamina_BowComboChains_C',
    'reload_ammo_hammer_bpn_C', 'Notify_HammerPrimedShotWindow_C', 'Notify_Invincibility_C', 'linear_trajectory_warp_ns_C',
    'jump_ns_C', 'cb_consume_ammo_bpn_C', 'adjust_ammo_bpn_C', 'spawn_aoe_class_bpn_C', 'spawn_projectile_class_bpn_C',
    'synced_animation_damage_partner_n_C', 'ac_aetherdash_aoe_bpn_C', 'sword_rework_ignite_bpn_C',
    'decal_and_particle_linetrace_bpns_C', 'decal_and_particle_linetrace_bpn_C',
    'activate_weapon_part_gameplay_bpns_C', 'activate_weapon_part_gameplay_event_bpn_C',
}

_data = None


def load(path=MOVES):
    """-> (states keyed by lower-case DestState path, montages keyed by package path)."""
    global _data
    if _data is None or path != MOVES:
        if not os.path.exists(path):
            return {}, {}
        with open(path, encoding='utf-8') as f:
            d = json.load(f)
        data = ({k.lower(): v for k, v in d['states'].items()}, d['montages'])   # the client's path case differs
        if path != MOVES:
            return data
        _data = data
    return _data


def _log(msg):
    print('COMBAT %s' % msg, flush=True)


def _stamina(session, enc, n, phase, now, ctx):
    if phase != 'begin':
        return
    cost = float((n.get('properties') or {}).get('Stamina Cost') or 0.0)
    st = enc.get('stamina')
    if st is None or cost <= 0.0:
        return
    if st.spend(cost, now):
        _log('stamina -%.0f -> %.0f (%s)' % (cost, st.value, ctx['state']))
    else:
        out = ctx.get('out_of_stamina')
        if out:
            out()
        _log('stamina out (cost %.0f, %s) -> ClientHandleOutOfStamina' % (cost, ctx['state']))


def _hammer_reload(session, enc, n, phase, now, ctx):
    """reload_ammo_hammer_bpn (server): ammo_bpc.AuthReloadWeaponWithAmmoScale(NormalAmmoScale, false), a full clip."""
    from . import hammer
    hammer.on_reload_notify(session, enc, n.get('properties') or {})


def _hammer_primed_window(session, enc, n, phase, now, ctx):
    """Notify_HammerPrimedShotWindow.Received_NotifyEnd: an unused primed shell is removed when the strike's window ends."""
    if phase == 'end':
        from . import hammer
        hammer.on_primed_window_end(session, enc)


def _send(session, entries):
    for e in reversed(entries):
        session.entries.insert(session.pos, e)


def _axe_clear_charge(session, enc, n, phase, now, ctx):
    """Notify_GAClearChargeLevel (Y3 and open-attack charge montages, 0 s): BP_Axe_Weapon.ResetCurrentChargeLevel."""
    if phase == 'begin':
        from . import axe
        _send(session, axe.clear_charge(session, enc))


def _part_event(session, enc, n, phase, now, ctx):
    """The forwarding notifies: the weapon's part of WeaponPartToActivate (GetWeaponPartByType) gets
    activate_weapon_part_gameplay_bpns: ActivateWeaponPartGameplay at the begin, DeactivateWeaponPartGameplay at the end;
    activate_weapon_part_gameplay_event_bpn: ActivateGameplayEvent(EventName). The part's code runs its server work."""
    p = n.get('properties') or {}
    if n['class'] == 'activate_weapon_part_gameplay_bpns_C':
        event = 'ActivateWeaponPartGameplay' if phase == 'begin' else 'DeactivateWeaponPartGameplay'
    elif phase == 'begin':
        event = p.get('EventName')
    else:
        return
    part = (enc.get('parts_by_type') or {}).get(p.get('WeaponPartToActivate'))
    if part is None:
        return                                   # GetWeaponPartByType finds no part: the notify does nothing
    h = PART_EVENTS.get((part, event))
    if h is not None:
        h(session, enc, now)
    elif (part, event) not in ctx.setdefault('unhandled_parts', set()):
        ctx['unhandled_parts'].add((part, event))
        _log('unhandled part event %s.%s in %s' % (part, event, ctx['montage']))


def on_charge(session, enc, func, params, now):
    """CharCombatComp.ServerStartCharge / ServerProcessCharge(PreviousChargeLevel, NewChargeLevel) / ServerEndCharge.
    The weapon's OnChargeStarted / OnChargeLevelChanged (BP_BaseArchonWeapon) forward to every part component's
    WeaponPartChargeStarted / WeaponPartChargeLevelChanged. Assumed from the Blueprint side: the native charge functions
    call those events on the server too (not decompiled)."""
    p = params or {}
    charge = enc.setdefault('charge', {'level': 0, 'charging': False})
    if func == 'ServerStartCharge':
        charge.update(level=0, charging=True)
        event, args = 'WeaponPartChargeStarted', ()
    elif func == 'ServerProcessCharge':
        charge['level'] = int(p.get('NewChargeLevel') or 0)
        event, args = 'WeaponPartChargeLevelChanged', (int(p.get('PreviousChargeLevel') or 0), charge['level'])
        from . import axe                                  # the weapon's own OnChargeLevelChanged (BP_Axe_Weapon)
        _send(session, axe.on_charge_level(session, enc, *args))
    else:
        charge['charging'] = False
        _log('charge ended at level %d' % charge['level'])
        return
    _part_notify(session, enc, event, now, *args)


def _hammer_part(name):
    def call(*args):
        from . import hammer
        return getattr(hammer, name)(*args)
    return call


# player_state_machine_bpc OnDefaultStateChanged with PreviousPlayerState Attack (EPlayerStatesDefault 10): parts that
# bound it (ih_special_default_bpc on WeaponPartChargeStarted) react. The server takes the end of a move chain (a montage
# that finishes with no next move) as leaving Attack; dodges and staggers outside the combo graph are not seen yet.
ATTACK_STATE_LEFT = 'OnDefaultStateChanged(Attack ->)'


def _part_notify(session, enc, event, now, *args):
    for part in (enc.get('parts_by_type') or {}).values():
        h = PART_EVENTS.get((part, event))
        if h is not None:
            h(session, enc, now, *args)


# (part component class, event) -> handler(session, enc, now, *event args): the part code's server work.
PART_EVENTS = {
    ('ih_special_default_bpc_C', 'WeaponPartChargeStarted'): _hammer_part('on_charge_started'),
    ('ih_special_default_bpc_C', 'WeaponPartChargeLevelChanged'): _hammer_part('on_charge_level'),
    ('ih_special_default_bpc_C', 'Shoot'): _hammer_part('on_salvo_shoot'),
    ('ih_special_default_bpc_C', ATTACK_STATE_LEFT): _hammer_part('on_attack_state_left'),
}


HANDLERS = {
    'activate_weapon_part_gameplay_bpns_C': _part_event,
    'activate_weapon_part_gameplay_event_bpn_C': _part_event,
    'Notify_ConsumeStamina_C': _stamina,
    'Notify_GAClearChargeLevel_C': _axe_clear_charge,
    'Notify_ConsumeStamina_RepeatingElements_C': _stamina,
    'Notify_ConsumeStamina_BowComboChains_C': _stamina,
    'reload_ammo_hammer_bpn_C': _hammer_reload,
    'Notify_HammerPrimedShotWindow_C': _hammer_primed_window,
}


class Moves:
    """One player's current combo move."""

    def __init__(self, data=None):
        self.states, self.montages = data if data is not None else load()
        self.state = None          # lower-case DestState of the current move
        self.ctx = None
        self.unhandled = set()

    def start(self, session, enc, dest, now, out_of_stamina=None):
        """ServerStartNextMove(DestState) at encounter time `now`."""
        self.step(session, enc, now)
        self._stop(session, enc, now)
        key = dest.lower() if isinstance(dest, str) else None
        st = self.states.get(key)
        prev = self.states.get(self.state) if self.state else None
        self.state = key
        if st is None or not st.get('montage') or st['montage'] not in self.montages:
            if dest:
                _log('move %s has no montage data' % dest.rsplit('.', 1)[-1])
            return
        m = self.montages[st['montage']]
        sections = [dict(s, time=s.get('time') or 0.0) for s in m.get('sections') or [{'name': 'Default', 'time': 0.0}]]
        names = {t.get('NextMontageSection') for t in (prev or {}).get('transitions') or []
                 if (t.get('DestState') or '').lower() == key and t.get('NextMontageSection') not in (None, 'None')}
        section = next((s['name'] for s in sections if len(names) == 1 and s['name'] in names), sections[0]['name'])
        events = [n for n in m.get('notifies') or [] if n.get('class') in SERVER_NOTIFIES]
        self.ctx = {'state': st.get('state'), 'montage': st['montage'].rsplit('/', 1)[-1], 'length': m.get('length') or 0.0,
                    'sections': sections, 'section': section, 'wall': now, 'pos': None, 'events': events,
                    'active': [], 'out_of_stamina': out_of_stamina}

    def _bounds(self, name):
        secs = self.ctx['sections']
        i = next(i for i, s in enumerate(secs) if s['name'] == name)
        end = min([s['time'] for s in secs if s['time'] > secs[i]['time']] + [self.ctx['length']])
        return secs[i], secs[i]['time'], end

    def _fire(self, session, enc, n, phase, now):
        h = HANDLERS.get(n['class'])
        if h is None:
            if n['class'] not in self.unhandled:
                self.unhandled.add(n['class'])
                _log('unhandled server notify %s in %s' % (n['class'], self.ctx['montage']))
            return
        h(session, enc, n, phase, now, self.ctx)

    def _stop(self, session, enc, now):
        """The montage stops: its active notify states end."""
        if self.ctx is None:
            return
        for n in self.ctx['active']:
            self._fire(session, enc, n, 'end', now)
        self.ctx = None

    def step(self, session, enc, now):
        """Fire the current move's notifies that came due by `now`."""
        c = self.ctx
        while c is not None:
            sec, start, end = self._bounds(c['section'])
            pos = start + (now - c['wall'])
            frm = start if c['pos'] is None else c['pos']
            for n in sorted(c['active'], key=lambda n: n['time'] + (n.get('duration') or 0.0)):
                if frm < n['time'] + (n.get('duration') or 0.0) <= min(pos, end):
                    c['active'].remove(n)
                    self._fire(session, enc, n, 'end', now)
            for n in sorted(c['events'], key=lambda n: n['time']):
                if (frm < n['time'] or (c['pos'] is None and n['time'] == frm)) and n['time'] <= min(pos, end) and n['time'] < end:
                    self._fire(session, enc, n, 'begin', now)
                    if (n.get('duration') or 0.0) > 0.0:
                        c['active'].append(n)
            if pos < end:
                c['pos'] = pos
                return
            nxt = sec.get('next')
            if nxt in (None, 'None', '') or not any(s['name'] == nxt for s in c['sections']):
                self._stop(session, enc, now)
                _part_notify(session, enc, ATTACK_STATE_LEFT, now)
                return
            c['wall'] += end - start
            c['section'], c['pos'] = nxt, None


def for_encounter(enc):
    m = enc.get('moves')
    if m is None:
        m = enc['moves'] = Moves()
    return m
