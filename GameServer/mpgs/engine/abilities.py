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
  abilities (its power row's RefAbilities / RefSpecialAbilities1-5, and its parts' abilities) are granted on equip by native code
  (AArchonWeapon) on the server, onto the pawn's ASC; engine/weapon_parts.py collects them and grant_entry sends them.
"""
from ..repl import model
from ..repl.guid import GuidInfo

ASC_CLS = 'ArchonAbilitySystemComponent'
FIELD = 'ActivatableAbilities'
P = 'ActivatableAbilities.Items[].'
EXPORT_FLAGS = 5                 # path + checksum flag, checksum 0: as the recorded server exported ability classes and CDOs

# Part abilities whose server instance ends at once and that the server cools down itself. dp_y_ability_spawn_raptor_ab: the
# server spawns the pack and calls K2_EndAbility in the same activation, so UGameplayAbility::EndAbility (bReplicateEndAbility)
# sends ClientEndAbility; the client's own instance never ends on its own here (it stops at FindBuff(alluring_raptor_manager_buff)
# before StartYCooldown / K2_EndAbility, live 2026-10-27). Its cooldown is the CDO AbilityCooldown, enforced here because the
# client never applies its dp_y_ability_cooldown_ge; the server applies that effect instead (effects.py) for the HUD.
SERVER_ENDED = {'dp_y_ability_spawn_raptor_ab_C': 60.0}


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
    """Ability state for one connection's own pawn: the weapon's abilities granted at hunt start (weapon_parts.setup)."""

    def __init__(self, session):
        self.s = session
        self.granted = {}            # handle -> {'cls', 'package', 'instance', 'cdo'}
        self.server_key = 0          # FPredictionKey::GenerateNewPredictionKey counter (server-initiated keys)
        self.meter = None            # WeaponMeter, for the special's full-meter check
        self.ch = self.hdr = None
        self.fa_key = self.next_rid = self.next_handle = None

    # ---- state recorded in the join world ----------------------------------------------------------------------------
    def _load_recorded(self):
        """ASC header (channel, block) and the fast-array state the client already holds from the join world."""
        ch, hdr = self.s._server_header(ASC_CLS)
        if hdr is None:
            raise LookupError('no %s in the sent world' % ASC_CLS)
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
        log('pawn ASC ch%d guid %s: fast array key %d, %d recorded specs (handles up to %d)'
            % (ch, hdr.guid, key, len(rids), self.next_handle - 1))

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
