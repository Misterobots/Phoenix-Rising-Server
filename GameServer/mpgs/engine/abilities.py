"""Engine layer E3: Gameplay Ability System on the server side, for the player's own ability system component (ASC).

What a real Dauntless server does and this module rebuilds (UE 5.1.1 GameplayAbilities, AbilitySystemComponent_Abilities.cpp):

* Grant (GiveAbility): a new FGameplayAbilitySpec in the ASC's ActivatableAbilities fast array (a custom-delta field on the ASC
  sub-object), and, for InstancedPerActor abilities with ReplicationPolicy ReplicateYes, the ability instance as a replicated
  sub-object of the pawn that the spec's ReplicatedInstances points at. The client never creates those instances itself; it waits
  for the server's (OnGiveAbility). The spec fields copy the recorded server's specs (tools/dump_ability_specs.py).
* Activation: ServerTryActivateAbility(Handle, InputPressed, PredictionKey) is answered with ClientActivateAbilitySucceed(Handle,
  key) or ClientActivateAbilityFailed(Handle, key.Current) (InternalServerTryActivateAbility). For ServerInitiated and ServerOnly
  abilities the server mints a new key: FPredictionKey::CreateNewServerInitiatedKey (Current from a counter starting at 1,
  bIsServerInitiated set), which the client adopts before running its local instance (ClientActivateAbilitySucceedWithEventData).
* What the client actually sends is in the logs (2026-10-16): ServerTryActivateAbility only for granted abilities. The weapon's
  abilities (weapon_power_table RefAbilities / RefSpecialAbilities1) are granted on equip by native code (AArchonWeapon), which the
  Ramsgate join template never ran, so they are granted here.

Env:
  MPGS_GRANT_WEAPON_ABILITIES=1   grant the equipped weapon's abilities after join (default on when the Rock is on)
  MPGS_ABILITY_DELAY=2            seconds after the world is delivered and the player has moved
"""
import os

from ..repl import model
from ..repl.decode import OrderedFields
from ..repl.guid import GuidInfo

ASC_CLS = 'ArchonAbilitySystemComponent'
FIELD = 'ActivatableAbilities'
P = 'ActivatableAbilities.Items[].'
EXPORT_FLAGS = 5                 # path + checksum flag, checksum 0: as the recorded server exported ability classes and CDOs

# Weapon ability rows (Gameplay/weapon_power_table, FWeaponPowerTableData). Only the starter sword so far; the row of the equipped
# weapon should come from the item data when more weapons are handled.
# Part abilities whose server instance ends at once and that the server cools down itself. dp_y_ability_spawn_raptor_ab: the
# server spawns the pack and calls K2_EndAbility in the same activation, so UGameplayAbility::EndAbility (bReplicateEndAbility)
# sends ClientEndAbility; the client's own instance never ends on its own here (it stops at FindBuff(alluring_raptor_manager_buff)
# before StartYCooldown / K2_EndAbility, live 2026-10-27). Its cooldown is the CDO AbilityCooldown, enforced here because the
# client never applies its dp_y_ability_cooldown_ge; the server applies that effect instead (effects.py) for the HUD.
SERVER_ENDED = {'dp_y_ability_spawn_raptor_ab_C': 60.0}

WEAPON_ABILITIES = {
    'eblade_beginner_00_bp_C': [
        # (package, class, what it is)
        ('/Game/game/abilities/weapons/eb_charge_ab', 'eb_charge_ab_C', 'RefAbilities: sword charge (Event.OnDamageGiven, ServerInitiated)'),
        ('/Game/game/abilities/weapons/Sword/eb_q_projectile_raw_ab', 'eb_q_projectile_raw_ab_C', 'RefSpecialAbilities1: special (ServerInitiated)'),
    ],
}


def log(msg):
    print('ABILITY ' + msg, flush=True)


def _info(guid, name, outer=None, flags=EXPORT_FLAGS, checksum=0):
    i = GuidInfo()
    i.guid, i.flags, i.outer_info, i.name, i.checksum = guid, flags, outer, name, checksum
    return i



def activation_prefix(raw):
    """ServerTryActivateAbilityWithEventData raw params -> {Handle, InputPressed, PredictionKey}, the parameters before
    TriggerEventData (not decoded). RPC layout: a send bit before every non-bool parameter. Checked against four live
    captures (2026-10-26): handles 12/13 and keys matching the client's ServerEndAbility. None when it does not fit."""
    from ..bitio import BitReader
    from ..repl.structs import STRUCT_READERS
    bits, nbits = raw
    try:
        r = BitReader(bits.to_bytes((nbits + 7) // 8, 'little'), nbits)
        h = r.read(32) if r.bit() else 0
        pressed = r.bit()
        key = STRUCT_READERS['PredictionKey'](r) if r.bit() else {}
    except Exception:
        return None
    return {'AbilityToActivate.Handle': h, 'InputPressed': pressed, 'PredictionKey': key}

class PlayerAbilities:
    """Ability state for one connection's own pawn. Created by the session; step() grants once the world is delivered."""

    def __init__(self, session, env=None):
        env = os.environ if env is None else env
        self.s = session
        self.enabled = env.get('MPGS_GRANT_WEAPON_ABILITIES', '1') == '1'
        self.delay = float(env.get('MPGS_ABILITY_DELAY', '2'))
        # pawn | weapon: which ASC gets the weapon abilities. Live 2026-10-16: 'weapon' crashed the client right after the grant;
        # 'pawn' was accepted. Default pawn.
        self.target = env.get('MPGS_ABILITY_ASC', 'pawn')
        self.t_ready = None
        self.granted = {}            # handle -> {'cls', 'package', 'instance', 'cdo'}
        self.server_key = 0          # FPredictionKey::GenerateNewPredictionKey counter (server-initiated keys)
        self.done = False
        self.meter = None            # WeaponMeter, started with the grant
        self.ch = self.hdr = None
        self.fa_key = self.next_rid = self.next_handle = None

    # ---- state recorded in the join world ----------------------------------------------------------------------------
    def _target_asc(self):
        """(channel, header) of the ASC the weapon abilities go to. Default 'weapon': the equipped weapon actor's own ASC (owner and
        avatar = the weapon). Evidence (2026-10-16): the weapon meter is that ASC's ArchonDamageAttributeSet.Determination,
        AArchonWeapon keeps its own Abilities list, and with the abilities on the pawn's ASC and a full meter the client still never
        asked to activate the special. MPGS_ABILITY_ASC=pawn targets the player's ASC instead (the first attempt)."""
        from .attributes import find_block
        if self.target == 'pawn':
            return self.s._server_header(ASC_CLS)
        ch = next((e.ch for e in self.s.entries for b in e.parsed.blocks if b.actor and b.cls_name in WEAPON_ABILITIES), None)
        return (ch, find_block(self.s, ch, ASC_CLS)) if ch is not None else (None, None)

    def _load_recorded(self):
        """ASC header (channel, block) and the fast-array state the client already holds from the join world."""
        ch, hdr = self._target_asc()
        if hdr is None:
            raise LookupError('no %s (%s) in the sent world' % (ASC_CLS, self.target))
        key, rids, handles = 0, [], []
        for e in self.s.entries:
            if e.ch != ch:
                continue
            for blk in e.parsed.blocks:
                if blk.cls_name != ASC_CLS:
                    continue
                for fld in blk.fields or []:
                    if fld[1] == FIELD and fld[2] == 'fast':
                        fa = fld[3]
                        key = max(key, fa['key'])
                        for rid, vals in fa['changed']:
                            rids.append(rid)
                            handles += [v for k, v in vals if k == P + 'Handle.Handle']
        self.ch, self.hdr = ch, hdr
        self.fa_key = key
        self.next_rid = max(rids, default=0) + 1
        self.next_handle = max(handles, default=0) + 1
        log('target ASC (%s) ch%d guid %s: fast array key %d, %d recorded specs (handles up to %d)'
            % (self.target, ch, hdr.guid, key, len(rids), self.next_handle - 1))

    # ---- grant -----------------------------------------------------------------------------------------------------
    def grant_entry(self, abilities, level=1):
        """One reliable pawn-channel entry: the ability classes exported, one instance sub-object per ability, then the ASC's
        ActivatableAbilities delta adding a spec per ability. -> (Entry, [handles])."""
        if self.hdr is None:
            self._load_recorded()
        g = self.s.next_live_guid
        exports, must_map, inst_blocks, changed, handles = [], [], [], [], []
        for package, cls, what in abilities:
            pkg = _info(g() + 1, package)
            cls_info = _info(g() + 1, cls, outer=pkg)
            cdo = _info(g() + 1, 'Default__' + cls, outer=pkg)
            inst = g()                                         # dynamic: created by its content block, never exported by path
            from ..loadout import register_atlas_class_full
            register_atlas_class_full(self.s.world.schema, cls)   # its own RPCs (e.g. SpawnProjectile) then decode
            ctx = self.s.ctx
            if not hasattr(ctx, 'dynamic_classes'):
                ctx.dynamic_classes = {}
            ctx.dynamic_classes[inst] = cls
            exports += [cls_info, cdo]
            must_map += [cls_info.guid, cdo.guid]
            b = model.Block()
            b.actor, b.rep, b.guid, b.stable, b.destroy = False, False, inst, False, False
            b.cls, b.outer = cls_info.guid, None                # outer None: the actor is the outer (bActorIsOuter)
            b.cls_name = cls
            b.values = b.tail = b.raw = None
            b.fields = []
            b.ref = b.ref_name = None
            b.partial_properties = False
            inst_blocks.append(b)
            h = self.next_handle
            self.next_handle += 1
            rid = self.next_rid
            self.next_rid += 1
            vals = [(P + 'Handle.Handle', h), (P + 'Ability', ('obj', cdo.guid)), (P + 'Level', level), (P + 'InputID', -1),
                    (P + 'SourceObject', ('obj', 0)), (P + 'DynamicAbilityTags', []),
                    (P + 'ReplicatedInstances', [[(P + 'ReplicatedInstances[]', ('obj', inst))]])]
            changed.append((rid, vals))
            handles.append(h)
            self.granted[h] = {'cls': cls, 'package': package, 'instance': inst, 'cdo': cdo.guid, 'what': what}
        fa = {'delta_flag': 0, 'key': self.fa_key + 1, 'base': self.fa_key, 'deleted': [], 'changed': changed}
        self.fa_key += 1
        asc = model.Block()
        asc.actor, asc.rep, asc.guid, asc.stable, asc.destroy = False, False, self.hdr.guid, self.hdr.stable, False
        asc.cls, asc.outer = self.hdr.cls, self.hdr.outer
        asc.ref, asc.ref_name = getattr(self.hdr, 'ref', None), getattr(self.hdr, 'ref_name', None)
        asc.cls_name = ASC_CLS
        asc.values = asc.tail = asc.raw = None
        asc.partial_properties = False
        fi = self.s.world.schema.field_index(ASC_CLS)
        asc.fields = [(fi.index(('prop', FIELD)), FIELD, 'fast', fa)]
        from ..genworld import Entry
        e = Entry()
        e.t, e.ch, e.cls, e.exports = 0, self.ch, self.s.chan_cls.get(self.ch), exports
        e.open = e.close = False
        e.must_map = True
        e.reliable = True
        e.patch_pairs = ()
        e.parsed = model.Parsed()
        e.parsed.must_map = must_map
        e.parsed.blocks = inst_blocks + [asc]
        return e, handles

    def step(self, now):
        if not self.enabled or self.done:
            return
        s = self.s
        if s.pawn_state is None or s.pos < len(s.entries):
            self.t_ready = None
            return
        if self.t_ready is None:
            self.t_ready = now + self.delay
            return
        if now < self.t_ready:
            return
        self.done = True
        weapon = next((b.cls_name for e in s.entries for b in e.parsed.blocks if b.actor and b.cls_name in WEAPON_ABILITIES), None)
        if weapon is None:
            log('no known weapon in the world; nothing granted')
            return
        e, handles = self.grant_entry(WEAPON_ABILITIES[weapon])
        s.entries.insert(s.pos, e)
        for h in handles:
            log('t=%.2f granted handle %d: %s (%s)' % (now, h, self.granted[h]['cls'], self.granted[h]['what']))
        try:
            from .weapon_meter import WeaponMeter
            from ..behemoth.damage import load_model
            self.meter = WeaponMeter(s, load_model()['rows'], log)
            self.meter.start()
        except Exception as exc:
            log('weapon meter unavailable (%s: %s)' % (type(exc).__name__, exc))

    # ---- damage given ----------------------------------------------------------------------------------------------
    def on_damage_given(self, attack):
        """A player hit that the server counted (the attack's damage-table row name, e.g. EBRework.Y3)."""
        if self.meter is not None and attack:
            self.meter.on_hit(attack)

    # ---- activation ------------------------------------------------------------------------------------------------
    def new_server_key(self):
        """FPredictionKey::CreateNewServerInitiatedKey: GenerateNewPredictionKey (int16 counter from 1) + bIsServerInitiated."""
        self.server_key = self.server_key + 1 if self.server_key < 0x7FFF else 1
        return {'server': 1, 'current': self.server_key}

    def on_try_activate(self, ch, req, params):
        """ServerTryActivateAbility -> ClientActivateAbilitySucceed (granted here) or ClientActivateAbilityFailed (unknown handle).
        -> True when the activation was accepted."""
        h = params.get('AbilityToActivate.Handle')
        key = params.get('PredictionKey') or {}
        if ch != self.ch:
            log('ServerTryActivateAbility handle %s on ch%d (a different ASC than ch%s): no reply' % (h, ch, self.ch))
            return
        spec = self.granted.get(h)
        if spec is None:
            # a recorded spec (quick items, handles 1-11): not run by this server yet. No reply, as before, so the client's
            # current behaviour for them does not change; answering Failed would cancel its prediction.
            log('handle %s: not granted by this server; no reply (client key %s)' % (h, key))
            return
        special = 'RefSpecialAbilities' in spec['what']
        if special and self.meter is not None and not self.meter.full:
            # the client asks only when its meter reads full (SpecialAttackReady); the server's own meter decides
            self.s._queue_rpc(ch, req, 'ClientActivateAbilityFailed',
                              {'AbilityToActivate.Handle': h, 'PredictionKey': key.get('current', 0)})
            log('handle %d (%s): meter %.0f / %.0f not full -> ClientActivateAbilityFailed'
                % (h, spec['cls'], self.meter.value, self.meter.max))
            return
        cooldown = SERVER_ENDED.get(spec['cls'])
        if cooldown is not None:
            from .effects import cooldown as talent_cooldown
            cooldown = talent_cooldown(self.s, spec['cls']) or cooldown       # SP03 shortens Q to 50 s
        now = self.s.clock() if hasattr(self.s, 'clock') else __import__('time').monotonic()
        if cooldown is not None and now < spec.get('ready_at', 0.0):
            self.s._queue_rpc(ch, req, 'ClientActivateAbilityFailed',
                              {'AbilityToActivate.Handle': h, 'PredictionKey': key.get('current', 0)})
            log('handle %d (%s): on cooldown (%.0f s left) -> ClientActivateAbilityFailed' % (h, spec['cls'], spec['ready_at'] - now))
            return
        if 'part ' in spec['what'] and key:
            # weapon part abilities are LocalPredicted (dp_weapon_ability_base_ab): confirm the client's own prediction key,
            # as the potion reply does (live 2026-10-22); a new server key would not match the client's pending activation
            sk = key
        else:
            sk = self.new_server_key()
        self.s._queue_rpc(ch, req, 'ClientActivateAbilitySucceed', {'AbilityToActivate.Handle': h, 'PredictionKey': sk})
        log('handle %d (%s): ServerTryActivateAbility (client key %s) -> ClientActivateAbilitySucceed server key %d'
            % (h, spec['cls'], key, sk['current']))
        from . import effects
        fx = effects.on_ability_activated(self.s, spec['cls'], key, now)     # the cooldown effect the HUD reads
        self.s.entries[self.s.pos:self.s.pos] = fx
        if cooldown is not None:
            spec['ready_at'] = now + cooldown
            self.s._queue_rpc(ch, req, 'ClientEndAbility', {
                'AbilityToEnd.Handle': h, 'ActivationInfo.ActivationMode': 0,        # the server's instance: Authority
                'ActivationInfo.bCanBeEndedByOtherInstance': 0, 'ActivationInfo.PredictionKeyWhenActivated': sk})
            log('handle %d (%s): server instance ended -> ClientEndAbility (cooldown %.0f s)' % (h, spec['cls'], cooldown))
        if special and self.meter is not None:
            self.meter.spend_all('special %s activated' % spec['cls'])
        return True
