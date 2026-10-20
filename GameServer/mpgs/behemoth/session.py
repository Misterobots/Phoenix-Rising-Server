"""Session glue: one Rock in the player's world, driven by Brain. Enabled with MPGS_ROCK_SPAWN=1 (see docs/ROCK_LIVE_WIRING_PLAN_2026-10-14.md).

Environment:
  MPGS_ROCK_SPAWN=1          enable
  MPGS_ROCK_DELAY=10         seconds after the world has been delivered and the player has moved before the Rock appears
  MPGS_ROCK_DISTANCE=1800    units in front of the player's camera yaw (ignored with MPGS_ROCK_AT)
  MPGS_ROCK_AT=x,y,z         fixed spawn point
  MPGS_ROCK_Z_OFFSET=0       units added to the player's height at the spawn point (island ground is uneven; the server has no terrain)
  MPGS_ROCK_VARIANT=normal   normal | alpha
  MPGS_ROCK_MOVE_EVERY=2     seconds between repeated position updates (0 = never repeat; a facing change still sends one). Live 2026-10-15:
                             an idle Rock replayed a ~1 s stretch pose about every 2 s, which looks like these refreshes
  MPGS_ROCK_STAGE=attack     spawn (only the open bunch) | move (+ position updates) | attack (+ montage calls); used to find which bunch a
                             client rejects (2026-10-14: the client sent ObjectReplicatorReceivedBunchFail about a second after the first attack)
  MPGS_ROCK_ATTACK_DELAY=0   seconds after the spawn before the first attack (lets position updates run alone first)
  MPGS_ROCK_MONTAGE_MODE=rpc rpc (MulticastPlayMontage) | prop (RepAnimMontageInfo property) | both
  MPGS_ROCK_TRACE=1          log the size and first bytes of every Rock bunch body sent
  MPGS_ROCK_ACTIONS=a,b      only these attacks (names from rock.json, e.g. StoneSpire,FrontTackle). Live 2026-10-15: the Rock fell through the
                             ground and vanished when it attacked; the first attack was WhirlingFortress, whose montage changes part collision
                             (ModifyPartCollisionChannelNotify) and calls behemoth functions; use this to test attacks one family at a time
  MPGS_ROCK_SEND_HZ=30       at most this many Rock movement updates per second while the mover runs (0 = every tick;
                             live 2026-10-15 an uncapped root-motion attack sent ~120/s)
  MPGS_ROCK_YAW_SWEEP=30     attack choice also tries the Rock turned by up to this many degrees (10-degree steps) and turns to the
                             facing that hits when the attack starts (see brain.choose; 0 = only the current facing)
  MPGS_ROCK_RM_SEND=stream   while a montage's root motion moves the Rock: 'stream' = movement updates as it moves, each carrying
                             Character.RepRootMotion (montage, track position, location) the way the engine does; 'end' = hold
                             updates until it ends (the 2026-10-15 workaround, kept for comparison). Without RepRootMotion the
                             client snapped back after attacks: it ignores ReplicatedMovement during root motion and applies the
                             last one it got when the montage ends
  MPGS_HIT_LOG=path         append every player ServerTryDoDamage report (full bits, hex) as jsonl
  MPGS_HIT_RANGE=1500        a hit counts when the Rock is within this many units of the player (the report's hit result is not decoded)
  MPGS_ROCK_HEALTH=32000     Rock health (36000 for alpha); MPGS_PLAYER_POWER (default: the Rock's power) and MPGS_DAMAGE_SCALE tune damage
  MPGS_HIT_REPLY=1           answer counted hits with DamageComponent.ClientDoDamageSucceed (0 = log only)
Everything is logged with a ROCK prefix. Any exception disables the Rock for the session instead of breaking the join.
"""
import json
import math
import os
import random
import traceback

from . import bp_schema, damage, spawn
from .brain import Brain, Target, load_rock

PLAYER_TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'world', 'ramsgate_template.json')

CLS = 'sq_rock_normal_bp_C'
MONTAGE_DIR = '/Game/Monsters/sq/rock/montages/'
# turn-in-place montages by angle: (left, right). Left is a negative yaw (90L's root turns -90.0 degrees).
TURN_MONTAGES = {45: ('b_sq_rok_00_loco_turninplace_45L_mot', 'b_sq_rok_00_loco_turninplace_45R_mot'),
                 90: ('b_sq_rok_00_loco_turninplace_90L_mot', 'b_sq_rok_00_loco_turninplace_90R_mot'),
                 135: ('b_sq_rok_00_loco_turninplace_135L_00_mot', 'b_sq_rok_00_loco_turninplace_135R_00_mot')}
TURN_SNAP_DEG = 25.0             # turns up to this are set directly (no montage covers less than 45)
MONTAGE_COMPONENT = ('NetworkedMontage', 'NetworkedMontageComponent')
DEFAULT_MONTAGE_SECONDS = 2.5


def log(*a):
    print('ROCK', *a, flush=True)


class RockDirector:
    def __init__(self, session, env=None):
        env = os.environ if env is None else env
        self.s = session
        self.delay = float(env.get('MPGS_ROCK_DELAY', '10'))
        self.distance = float(env.get('MPGS_ROCK_DISTANCE', '1800'))
        self.fixed = [float(v) for v in env['MPGS_ROCK_AT'].split(',')] if env.get('MPGS_ROCK_AT') else None
        self.env = env
        self.only_actions = {a.strip() for a in env.get('MPGS_ROCK_ACTIONS', '').split(',') if a.strip()}
        self.health = None
        self.last_key = None            # prediction key of the last counted hit (echoed in ClientDoDamageSucceed)
        self.hit_log = env.get('MPGS_HIT_LOG') or None     # every ServerTryDoDamage report, full bits as hex (jsonl)
        self.hit_range = float(env.get('MPGS_HIT_RANGE', '1500'))
        self.z_offset =float(env.get('MPGS_ROCK_Z_OFFSET', '0'))     # the server has no terrain: raise the Rock where the ground is higher than the player's
        self.terrain = None
        self.half_height = float(env.get('MPGS_ROCK_HALF_HEIGHT', '250'))
        self.mover_on = env.get('MPGS_MOVER') == '1'
        hz = float(env.get('MPGS_ROCK_SEND_HZ', '30'))         # cap on movement updates while the mover runs (0 = no cap)
        self.send_interval = 1.0 / hz if hz > 0 else 0.0
        self.yaw_sweep = float(env.get('MPGS_ROCK_YAW_SWEEP', '30'))
        self.rm_hold = env.get('MPGS_ROCK_RM_SEND', 'stream') == 'end'
        self._rm = None                 # (montage name, start time) while a root-motion montage moves the Rock
        self.rm_probe = ({'i': 0, 'next': None, 'gap': float(env.get('MPGS_RM_PROBE_GAP', '3'))}
                         if env.get('MPGS_RM_PROBE') == '1' else None)
        self.player_hits = 0
        self.player_stagger = env.get('MPGS_PLAYER_STAGGER') == '1'
        # live turn test: MPGS_TURN_TEST=montage,montage,... played one after another, yaw logged before and after
        self.turn_plan = [m.strip() for m in env.get('MPGS_TURN_TEST', '').split(',') if m.strip()] or None
        self.turn_next = None
        self.turn_gap = float(env.get('MPGS_TURN_GAP', '5'))     # seconds between turns, so each one is easy to find in a recording
        self.turn_name = None
        self.turn_yaw0 = None
        self._window_open = False
        self.mover = None
        self._last_sent = None
        self.ai = None                  # RockAI (behaviour tree) when MPGS_ROCK_BT=1
        self.bt_on = env.get('MPGS_ROCK_BT') == '1'
        self._stop_logged = False
        if env.get('MPGS_TERRAIN') == '1' or self.mover_on:
            from ..world.terrain import Terrain, default_base
            try:
                self.terrain = Terrain.load(env.get('MPGS_TERRAIN_BASE') or default_base())
                log('terrain loaded (half height %.0f, mover %s)' % (self.half_height, 'on' if self.mover_on else 'off'))
            except (OSError, ValueError) as e:
                if self.mover_on:
                    raise RuntimeError('MPGS_MOVER=1 needs the height grid (tools.export_terrain): %s' % e)
                log('no terrain (%s): spawn height stays at the player height' % e)
        self.variant = env.get('MPGS_ROCK_VARIANT', 'normal')
        self.move_every = float(env.get('MPGS_ROCK_MOVE_EVERY', '2'))
        self.stage = env.get('MPGS_ROCK_STAGE', 'attack')
        self.trace = env.get('MPGS_ROCK_TRACE', '1') == '1'
        self.attack_delay = float(env.get('MPGS_ROCK_ATTACK_DELAY', '0'))
        self.montage_mode = env.get('MPGS_ROCK_MONTAGE_MODE', 'rpc')
        self.spawned_at = None
        self._attacks_logged = False
        self._force_bit = 0
        self.state = 'waiting'         # waiting -> active -> (failed | closed)
        self.t_ready = None
        self.brain = None
        self.ch = self.ids = None
        self.pos = self.loc = None
        self.yaw = 0.0
        self.montages = {}
        self.last_move = 0.0
        self.actions_started = 0
        self.sent = {'spawn': 0, 'move': 0, 'montage': 0}

    @property
    def alive(self):
        return self.state in ('waiting', 'active')

    # ------------------------------------------------------------------------------------------------------------
    def step(self, now):
        """Called from GenSession.due with the session clock in seconds."""
        if not self.alive:
            return
        try:
            if self.state == 'waiting':
                self._maybe_spawn(now)
            elif self.state == 'active':
                self._drive(now)
        except Exception:
            log('disabled after an error:\n' + traceback.format_exc())
            self.state = 'failed'

    def _insert(self, entries):
        """Queue entries to go out next, in the given order (the session inserts at its send position, so reverse)."""
        for e in reversed(entries):
            self.s.entries.insert(self.s.pos, e)
        if self.trace:
            from ..repl import model
            for e in entries:
                w = model.encode(e.parsed, self.s.world.ctx, e.cls)
                raw = w.n.to_bytes((w.nbits + 7) // 8, 'little')
                log('send ch%d %s%s%s %d bits exports=%s body=%s%s' % (
                    e.ch, 'open ' if e.open else '', 'close ' if e.close else '', 'reliable' if e.reliable else 'unreliable',
                    w.nbits, [(i.guid, i.name) for r in e.exports for i in _flat(r)], raw[:48].hex(), '...' if len(raw) > 48 else ''))

    def _maybe_spawn(self, now):
        s = self.s
        st = s.pawn_state
        if st is None or s.pos < len(s.entries):          # the player has not moved yet / the world is still being delivered
            self.t_ready = None
            return
        if self.t_ready is None:
            self.t_ready = now + self.delay
            log('world delivered; spawning in %.0fs' % self.delay)
            return
        if now < self.t_ready:
            return
        schema = s.world.schema
        bp_schema.extend(schema)
        data = load_rock()
        self.brain = Brain(data, self.variant, rng=random.Random())
        self.brain.yaw_sweep = self.yaw_sweep
        if self.only_actions:
            keep = [n for n in self.brain.actions if n in self.only_actions]
            for n in list(self.brain.actions):
                if n not in keep:
                    del self.brain.actions[n]
                    self.brain._cd.pop(n, None)
            log('attacks limited to %s (MPGS_ROCK_ACTIONS)' % keep)
        try:
            self.health = damage.RockHealth(damage.load_model(), self.variant, env=self.env)
        except (OSError, KeyError, ValueError) as e:
            log('no damage model (%s): hits are only logged; run tools.export_damage' % e)
        if self.fixed:
            loc = self.fixed
        else:
            yaw = math.radians(st['ctrl_rot'][1])
            x = st['loc'][0] + self.distance * math.cos(yaw)
            y = st['loc'][1] + self.distance * math.sin(yaw)
            z = st['loc'][2] + self.z_offset
            if self.terrain is not None:
                # ground under the spawn point + half height of the Rock's capsule (inferred 250 from Bp_Behemoth)
                g = self.terrain.ground_z(x, y)
                if g is not None:
                    z = g + self.half_height
                else:
                    log('no terrain under spawn (%.0f, %.0f): keeping player height' % (x, y))
            loc = [x, y, z]
        self.loc = loc
        face = math.degrees(math.atan2(st['loc'][1] - loc[1], st['loc'][0] - loc[0]))
        self.yaw = face
        self.brain.pos, self.brain.yaw = (loc[0], loc[1]), face
        if self.mover_on and self.terrain is not None:
            from .mover import Mover
            self.mover = Mover(self.terrain, (loc[0], loc[1]), face, half_height=self.half_height)
            log('mover on: root motion plays on attacks, walking %s' % ('on (behaviour tree)' if self.bt_on else 'off'))
        if self.bt_on:
            if self.mover is None:
                raise RuntimeError('MPGS_ROCK_BT=1 needs MPGS_MOVER=1 (walking and root motion)')
            from .rock_ai import RockAI
            self.ai = RockAI(self, self.env)
            log('behaviour tree on: rock_bt + %d sub-trees' % (len(self.ai.trees.roots) - 1))
        self.ch = getattr(s, '_live_ch', 900)
        s._live_ch = self.ch + 1
        guids = spawn.GuidSource(s.next_live_guid)
        entry, self.ids = spawn.spawn_entry(
            schema, guids, ch=self.ch, cls_name=CLS, package='/Game/Monsters/sq/rock/sq_rock_normal_bp',
            loc=loc, rot=(0.0, face, 0.0), values={'InCombat': 1},
            components=[(MONTAGE_COMPONENT[0], MONTAGE_COMPONENT[1], None)])
        self.guids = guids
        s.chan_cls[self.ch] = CLS
        self._insert([entry])
        self.sent['spawn'] += 1
        self.state = 'active'
        self.last_move = now
        self.spawned_at = now
        log('spawned ch%d %s at (%.0f, %.0f, %.0f) yaw %.0f; guids %s; player at (%.0f, %.0f, %.0f)'
            % (self.ch, CLS, loc[0], loc[1], loc[2], face, self.ids, st['loc'][0], st['loc'][1], st['loc'][2]))

    # ------------------------------------------------------------------------------------------------------------
    def on_hit(self, raw, now):
        """A player's ServerTryDoDamage report (raw = (bits, nbits)). -> True when it counted as a hit on this Rock (reply Succeed)."""
        if self.hit_log:
            try:
                nbits = raw[1]
                with open(self.hit_log, 'a', encoding='utf8') as f:
                    f.write(json.dumps({'t': round(now, 2), 'nbits': nbits, 'hex': raw[0].to_bytes((nbits + 7) // 8, 'little').hex()}) + '\n')
            except OSError:
                pass
        if self.state != 'active' or self.health is None:
            return False
        name = damage.find_attack_name(raw, self.health.model['rows'])
        st = self.s.pawn_state
        if name is None:
            log('t=%.2f HIT report with no known attack name (%d bits)' % (now, raw[1]))
            return False
        dist = math.hypot(st['loc'][0] - self.loc[0], st['loc'][1] - self.loc[1]) if st else 1e9
        if dist > self.hit_range:
            log('t=%.2f HIT %s ignored: Rock %.0f units away (range %.0f)' % (now, name, dist, self.hit_range))
            return False
        dealt, died = self.health.apply(name)
        log('t=%.2f HIT %s -> %.0f damage, Rock health %.0f/%.0f%s' % (now, name, dealt, self.health.current, self.health.max,
                                                                        ' DEAD' if died else ''))
        if died:
            self.brain.run = None
            if self.ai is not None:
                self.ai.on_death()
                log('Rock is dead: the behaviour tree plays the death montage (no replicated health yet)')
            else:
                log('Rock is dead: attacks stop (no death animation or replicated health yet)')
        self.last_key = damage.tail_prediction_key(raw)
        self.last_attack = name                 # damage-table row of the last counted hit (weapon meter, engine/weapon_meter.py)
        return dealt > 0

    def _target(self):
        st = self.s.pawn_state
        return Target('player', (st['loc'][0], st['loc'][1]), (st['vel'][0], st['vel'][1]), 0.0), st

    def _on_event(self, kind, payload, t):
        """Brain timeline events. A damage window that opens while the player is inside the running attack's hit area
        is a player hit: the damage is computed and logged. It is not sent to the client yet (no player damage RPC)."""
        if kind == 'damage_window_close':
            self._window_open = False
            return
        if kind != 'damage_window_open' or self.health is None or self.s.pawn_state is None:
            return
        run = self.brain.run
        name = getattr(run, 'name', None)
        if name is None or name not in self.brain.actions:
            return
        player = (self.s.pawn_state['loc'][0], self.s.pawn_state['loc'][1])
        if not self.brain.in_area(name, player):
            log('t=%.2f %s window: player outside the hit area' % (t, name))
            return
        rock_power = self.health.power
        player_power = float(self.env.get('MPGS_PLAYER_POWER', rock_power))
        row = self.brain.damage.get(name) or {}
        dmg = damage.behemoth_to_player(self.health.model, row.get('elements'), rock_power, player_power,
                                        float(self.env.get('MPGS_PLAYER_DAMAGE_SCALE', '1')))
        if dmg is None:
            log('t=%.2f %s window: no damage row for this attack' % (t, name))
            return
        self.player_hits += 1
        if self.player_stagger:
            self._send_stagger(dmg, t, name)
        else:
            log('t=%.2f PLAYER-HIT %s -> %.0f damage (computed, not sent to the client: MPGS_PLAYER_STAGGER is off)' % (t, name, dmg))

    def _send_stagger(self, dmg, t, name):
        """ClientStartStaggerAction on the player's hit reaction component (channel and GUID from the join template)."""
        ch, guid = spawn.player_pawn(PLAYER_TEMPLATE, 'hit_reaction_bpc')
        entry = spawn.stagger_entry(self.s.world.schema, ch=ch, actor_cls='BP_PlayerCharacter_C', component_guid=guid,
                                    component_cls='hit_reaction_bpc_C', damage_magnitude=dmg)
        self._insert([entry])
        self.sent['stagger'] = self.sent.get('stagger', 0) + 1
        log('t=%.2f PLAYER-HIT %s -> %.0f damage, ClientStartStaggerAction sent (ch%d guid %d)' % (t, name, dmg, ch, guid))

    def _move_entry(self, now=None):
        """Movement update. While walking, the velocity since the last update goes with it: the client's animation reads speed
        from the replicated velocity (a zero velocity plays the idle pose while the actor slides)."""
        vel = (0.0, 0.0, 0.0)
        if now is not None and self.mover is not None and self.mover.path and self._last_sent is not None:
            (lx, ly, lz), lt = self._last_sent
            dt = now - lt
            if 0.0 < dt < 1.0:
                vel = ((self.loc[0] - lx) / dt, (self.loc[1] - ly) / dt, 0.0)
        if now is not None:
            self._last_sent = (tuple(self.loc), now)
        rm, exports = self._root_motion_values(now)
        return spawn.movement_entry(self.ch, CLS, self.loc, self.yaw, vel=vel, server_time=now, root_motion=rm, exports=exports)

    # RepRootMotion members in handle order; the probe adds one per step (MPGS_RM_PROBE=1)
    RM_PROBE_ORDER = ['bIsActive', 'AnimMontage', 'Position', 'Location', 'Rotation', 'MovementBase', 'MovementBaseBoneName',
                      'bRelativePosition', 'bRelativeRotation', 'AuthoritativeRootMotion', 'Acceleration', 'LinearVelocity']

    def _rm_probe_step(self, now, out):
        """Diagnostic for the client's RepRootMotion rejection (2026-10-16): while the Rock stands still, every
        MPGS_RM_PROBE_GAP seconds send one movement update whose RepRootMotion carries one more member than the last
        (handle order). The client closes the connection on the first one it cannot read; the last 'accepted' line before
        ObjectReplicatorReceivedBunchFail in the log names the member that broke it. Values are inactive / zero except
        AnimMontage (a real montage reference, exported with the update)."""
        p = self.rm_probe
        if p['next'] is None:
            p['next'] = now + 3.0
        if now < p['next'] or p['i'] > len(self.RM_PROBE_ORDER):
            return
        p['next'] = now + p['gap']
        mname = 'b_sq_rok_00_atk_fronttackle_fwd_00_mot'
        key = (MONTAGE_DIR + mname, mname)
        if key not in self.montages:
            self.montages[key] = (self.guids.static(), self.guids.static())
        mg, pg = self.montages[key]
        full = spawn.root_motion_values(mg, 0.5, self.loc, self.yaw)
        full['RepRootMotion.bIsActive'] = 0
        members = self.RM_PROBE_ORDER[:p['i']]
        vals = {'RepRootMotion.' + m: full['RepRootMotion.' + m] for m in members}
        exports = (spawn._info(mg, mname, outer=spawn._info(pg, MONTAGE_DIR + mname)),) if 'AnimMontage' in members else ()
        out.append(spawn.movement_entry(self.ch, CLS, self.loc, self.yaw, server_time=now, root_motion=vals, exports=exports))
        log('t=%.2f RM-PROBE %d sent: %s (if the client survives %.0fs, these members were accepted)'
            % (now, p['i'], '+'.join(members) or '(timestamp only)', p['gap']))
        p['i'] += 1

    def _root_motion_values(self, now):
        """(RepRootMotion values, exports) for this update, as ACharacter::PreReplication does it: filled while a root-motion montage
        moves the Rock, cleared once on the first update after it ends, absent otherwise. The client ignores ReplicatedMovement while
        it plays a root-motion montage and corrects its local root motion to these instead (engine layer E1, ENGINE_LAYER_PLAN)."""
        if self._rm is None or now is None or self.rm_probe is not None:
            return None, ()
        mname, t0 = self._rm
        if self.mover is None or self.mover.montage is None:
            self._rm = None
            return spawn.root_motion_values(None, 0.0, self.loc, self.yaw), ()
        key = (MONTAGE_DIR + mname, mname)
        if key not in self.montages:
            return None, ()
        mg, pg = self.montages[key]
        info = spawn._info(mg, mname, outer=spawn._info(pg, MONTAGE_DIR + mname))
        return spawn.root_motion_values(mg, max(0.0, now - t0), self.loc, self.yaw), (info,)

    def _drive(self, now):
        if self.stage == 'spawn':
            return
        b, out = self.brain, []
        target, st = self._target()
        for t, kind, payload in b.tick(now):
            log('t=%.2f %s %s' % (t, kind, _short(payload)))
            self._on_event(kind, payload, t)
        if self.mover is not None:
            self.mover.tick(now)
            self.loc = self.mover.loc
            self.yaw = self.mover.yaw
            b.pos = (self.loc[0], self.loc[1])
            b.yaw = self.yaw
            if self.mover.stopped_reason and not self._stop_logged:
                self._stop_logged = True
                log('t=%.2f mover stopped: %s at (%.0f, %.0f)' % (now, self.mover.stopped_reason, self.loc[0], self.loc[1]))
            held = self.rm_hold and self.mover.montage is not None      # the client plays this root motion itself
            rm_ended = self._rm is not None and self.mover.montage is None   # send the cleared RepRootMotion even if nothing moved
            if (not held and self.mover.pop_changed(now, self.send_interval)) or rm_ended:
                out.append(self._move_entry(now))
                self.sent['move'] += 1
                self.last_move = now
        if self.rm_probe is not None:
            self._rm_probe_step(now, out)
        if self.turn_plan is not None:
            self._turn_test(now, out)
        # face the player before choosing the next attack (not during a turn test)
        if not b.busy and self.turn_plan is None and self.ai is None:
            face = math.degrees(math.atan2(target.pos[1] - self.loc[1], target.pos[0] - self.loc[0]))
            if abs(((face - self.yaw + 180) % 360) - 180) > 5.0:
                self.yaw = face
                b.yaw = face
                if self.mover is not None:
                    self.mover.yaw = face       # sent with the next change
                else:
                    out.append(self._move_entry())
                    self.sent['move'] += 1
                    self.last_move = now
        if self.move_every > 0 and now - self.last_move >= self.move_every:
            out.append(self._move_entry())
            self.sent['move'] += 1
            self.last_move = now
        attacks_on = self.stage == 'attack' and now - self.spawned_at >= self.attack_delay and not (self.health and self.health.dead)
        if attacks_on and not self._attacks_logged:
            self._attacks_logged = True
            log('t=%.2f attacks enabled (montage mode %s)' % (now, self.montage_mode))
        if self.ai is not None:
            self.ai.tick(now)
        elif not b.busy and attacks_on:
            choice = b.choose(now, [target])
            if choice is None:
                if int(now) % 10 == 0 and getattr(self, '_idle_logged', -1) != int(now):
                    self._idle_logged = int(now)
                    log('t=%.1f no available attack covers the player at (%.0f, %.0f)' % (now, target.pos[0], target.pos[1]))
            else:
                self.start_attack(choice[0], now, score=choice[1], out=out, yaw=b.chosen_yaw)
        if out:
            self._insert(out)

    # ---- actions used by the hand-written loop and by the behaviour tree (rock_ai.py) -------------------------------
    def attacks_allowed(self, now):
        return self.stage == 'attack' and now - self.spawned_at >= self.attack_delay and not (self.health and self.health.dead)

    def _montage_entries(self, mname, play_rate=1.0):
        kw = dict(ch=self.ch, actor_cls=CLS, montage_guid_cache=self.montages, component_guid=self.ids[MONTAGE_COMPONENT[0]],
                  component_cls=MONTAGE_COMPONENT[1], montage_package=MONTAGE_DIR + mname, montage_name=mname, play_rate=play_rate)
        schema = self.s.world.schema
        self._force_bit ^= 1
        sends = []
        if self.montage_mode in ('rpc', 'both'):
            sends.append(spawn.montage_play_entry(schema, self.guids, **kw))
        if self.montage_mode in ('prop', 'both'):
            sends.append(spawn.montage_prop_entry(schema, self.guids, force_play_bit=self._force_bit, **kw))
        self.sent['montage'] += len(sends)
        return sends

    def _root_motion(self, label, mname, now):
        """Start the montage's root motion on the mover. -> the curve (or None)."""
        if self.mover is None:
            return None
        from .mover import montage_curve
        curve = montage_curve(mname + '_mot' if not mname.endswith('_mot') else mname)
        if curve is None:
            log('t=%.2f %s: no root motion curve, staying in place' % (now, label))
        elif not self.mover.play(curve, now):
            log('t=%.2f %s: a montage is already moving the Rock' % (now, label))
        else:
            log('t=%.2f %s: root motion %.2fs' % (now, label, curve.duration))
            self._rm = (mname, now)
        return curve

    def start_attack(self, name, now, score=1.0, out=None, yaw=None):
        """Start one attack from the action table: brain timeline + montage RPC + root motion. -> True if it started.

        `yaw`: the facing the choice was scored at (brain.chosen_yaw); the Rock turns to it before the root motion starts.
        """
        b = self.brain
        if name not in b.actions or b.busy:
            return False
        if yaw is not None and abs(((yaw - self.yaw + 180.0) % 360.0) - 180.0) > 0.5:
            log('t=%.2f %s: turning %.0f degrees to the facing it was chosen at' % (now, name, ((yaw - self.yaw + 180.0) % 360.0) - 180.0))
            self.yaw = b.yaw = ((yaw + 180.0) % 360.0) - 180.0
            if self.mover is not None:
                self.mover.yaw = self.yaw       # root motion starts from this facing; sent with the next movement update
        target, _ = self._target()
        run = b.start(name, now, target)
        a = b.actions[name]
        mname = a['montage']
        sends = []
        if self.mover is not None and self.mover.pop_changed():
            sends.append(self._move_entry(now))    # the turned facing goes out before the montage starts from it
        sends += self._montage_entries(mname, a['play_rate'] or 1.0)
        if self.mover is not None:
            self.mover.path = []                # an attack stops any walk
        self._root_motion(name, mname, now)
        self.actions_started += 1
        log('t=%.2f attack %s score %.2f montage %s length %.2fs (%d timeline events)'
            % (now, name, score, mname, run.end - run.start, len(run.events)))
        if out is None:
            self._insert(sends)
        else:
            out.extend(sends)
        return True

    def play_montage(self, mname, now):
        """A non-attack montage (patrol, rage, death). -> the time it ends, or None if it was skipped."""
        if not mname or ('rok' not in mname and 'rock' not in mname):
            log('t=%.2f montage %s skipped (not a Rock montage)' % (now, mname))
            return None
        curve = self._root_motion(mname, mname, now)
        self._insert(self._montage_entries(mname))
        log('t=%.2f montage %s' % (now, mname))
        return now + (curve.duration if curve is not None else DEFAULT_MONTAGE_SECONDS)

    def face(self, point, now=None, animate=False):
        """Turn toward a point. -> the time a turn montage ends, or None when the yaw was set directly.

        With `animate` (the behaviour tree's pivots and walk starts) a turn of more than TURN_SNAP_DEG plays the Rock's
        turn-in-place montage nearest the angle (45/90/135, L or R); its root motion turns the Rock, so the client sees a stepping
        turn. Live 2026-10-15: setting the yaw directly looked like the Rock spinning in place. Smaller turns are set directly.
        """
        face = math.degrees(math.atan2(point[1] - self.loc[1], point[0] - self.loc[0]))
        delta = ((face - self.yaw + 180.0) % 360.0) - 180.0
        if (animate and now is not None and self.mover is not None and self.mover.montage is None and not self.brain.busy
                and abs(delta) > TURN_SNAP_DEG):
            mag = min(TURN_MONTAGES, key=lambda m: abs(abs(delta) - m))
            mname = TURN_MONTAGES[mag][0 if delta < 0 else 1]
            log('t=%.2f turn %+.0f degrees: %s' % (now, delta, mname))
            end = self.play_montage(mname, now)
            if end is not None and self.mover.montage is not None:
                return end
        self.yaw = face
        self.brain.yaw = face
        if self.mover is not None:
            self.mover.yaw = face               # goes out with the next movement update
        return None

    def log_ai(self, msg):
        log('AI ' + msg)

    def player_state(self):
        """The context's view of the player (director.BehemothContext reads positions through this)."""
        return self.s.pawn_state

    def _turn_test(self, now, out):
        """Play the listed turn montages in order (no attacks). Each one starts when the previous one has ended."""
        from .mover import montage_curve
        if self.turn_name is not None:
            if self.mover.montage is None:
                log('TURN %s done at %s: yaw %.1f -> %.1f (%.1f deg), position (%.0f, %.0f)' % (
                    self.turn_name, _clock(), self.turn_yaw0, self.mover.yaw, self.mover.yaw - self.turn_yaw0, self.mover.x, self.mover.y))
                self.turn_name = None
                self.turn_next = now + self.turn_gap
            return
        if self.turn_next is not None and now < self.turn_next:
            return
        if not self.turn_plan:
            if self.turn_next is not None:
                log('TURN test finished')
                self.turn_plan = None
            return
        name = self.turn_plan.pop(0)
        curve = montage_curve(name)
        if curve is None:
            log('TURN %s: no root motion curve' % name)
            self.turn_next = now + 1.0
            return
        self.turn_name, self.turn_yaw0 = name, self.mover.yaw
        self.mover.play(curve, now)
        kw = dict(ch=self.ch, actor_cls=CLS, montage_guid_cache=self.montages, component_guid=self.ids[MONTAGE_COMPONENT[0]],
                  component_cls=MONTAGE_COMPONENT[1], montage_package=MONTAGE_DIR + name, montage_name=name, play_rate=1.0)
        out.append(spawn.montage_play_entry(self.s.world.schema, self.guids, **kw))
        self.sent['montage'] += 1
        log('TURN %s start at %s: yaw %.1f, duration %.2fs' % (name, _clock(), self.mover.yaw, curve.duration))

    def close(self, now=None):
        if self.state == 'active':
            self._insert([spawn.close_entry(self.ch, CLS)])
        log('closed; sent %s, attacks started %d' % (self.sent, self.actions_started))
        self.state = 'closed'


def _clock():
    """Wall-clock time for log lines that have to be matched to a screen recording."""
    import time
    return time.strftime('%H:%M:%S')


def _flat(info):
    while info is not None:
        yield info
        info = info.outer_info


def _short(payload, limit=240):
    t = str(payload)
    return t if len(t) <= limit else t[:limit] + '...'
