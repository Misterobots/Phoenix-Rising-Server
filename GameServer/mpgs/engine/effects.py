"""Gameplay effects on the equipped weapon's ASC (ActiveGameplayEffects), so the HUD shows ability cooldowns.

What the game does (atlas dp_weapon_bp StartXCooldown / StartYCooldown, GetXCooldown / GetYCooldown; AssetDump of the GEs; read
2026-10-27): the repeaters' X and Y abilities call StartXCooldown / StartYCooldown(AbilityCooldown), which applies
dp_x_ability_cooldown_ge / dp_y_ability_cooldown_ge (HasDuration, SetByCaller 'Duration', granting Combat.Pistols.XCooldown /
YCooldown) to GetAbilitySystemComponent(self): the weapon actor's own ASC, not the player's (the first build used the pawn's;
live 2026-10-27 no timer showed and X re-fired inside 15 s). The HUD (weapon_dp_bpw) asks the weapon's GetXCooldown / GetYCooldown, which is
ArchonAbilitySystemComponent.GetTimePercentionOfActiveEffectsWithTags(that tag): only the replicated effect matters, not the
weapon's handle.

Wire: one item of the ASC's ActiveGameplayEffects fast array, fields as in the recorded Ramsgate server's own effect
(world/ramsgate_template.json: Def = the GE CDO, Duration, Period 0, StackCount 1, EffectContext instigator/causer,
PredictionKey, StartServerWorldTime). The client turns StartServerWorldTime into its own start time with the game state's
server clock (FActiveGameplayEffect::RecomputeStartWorldTime), so every effect goes out together with a GameState
ReplicatedWorldTimeSeconds update on the same clock. Removed with a fast-array delete when it runs out, as the server's
duration timer does.
"""
from ..repl import model
from ..repl.guid import GuidInfo

ASC_CLS = 'ArchonAbilitySystemComponent'
FIELD = 'ActiveGameplayEffects'
P = 'ActiveGameplayEffects.GameplayEffects_Internal[].'
EXPORT_FLAGS = 5

EFFECTS = {   # GE class -> package
    'dp_x_ability_cooldown_ge_C': '/Game/game/abilities/weapons/DP/x_abilities/dp_x_ability_cooldown_ge',
    'dp_y_ability_cooldown_ge_C': '/Game/game/abilities/weapons/DP/y_abilities/dp_y_ability_cooldown_ge',
}
# ability class -> (cooldown GE, AbilityCooldown CDO seconds, the client predicts it). Talents that shorten them (X SP05 10 s,
# Y SP03 50 s) are not modelled. X runs StartXCooldown on the client too (LocalPredicted), so the server's effect carries the
# client's prediction key and replaces the predicted one; Y's client instance stops before StartYCooldown (see abilities.py).
ABILITY_COOLDOWNS = {
    'dp_x_ability_piercing_ab_C': ('dp_x_ability_cooldown_ge_C', 15.0, True),
    'dp_y_ability_spawn_raptor_ab_C': ('dp_y_ability_cooldown_ge_C', 60.0, False),
}


def _log(msg):
    print('EFFECT ' + msg, flush=True)


def _info(guid, name, outer=None):
    i = GuidInfo()
    i.guid, i.flags, i.outer_info, i.name, i.checksum = guid, EXPORT_FLAGS, outer, name, 0
    return i


class PlayerEffects:
    """The ActiveGameplayEffects the server keeps on the weapon's ASC for one session."""

    def __init__(self, session, clock_base=None):
        self.s = session
        self.ch = self.hdr = None
        self.key = self.next_id = None
        self.cdo = {}                   # GE class -> CDO guid (exported once)
        self.active = {}                # item id -> {'cls', 'ends'}
        self.clock_base = clock_base    # server world time = clock_base + now (now: the abilities' clock)

    def _weapon(self):
        """(channel, actor guid) of the equipped weapon: the open actor whose channel carries its own ASC and damage set."""
        for e in self.s.entries:
            if e.open and e.cls != 'BP_PlayerCharacter_C':
                names = {b.cls_name for x in self.s.entries if x.ch == e.ch for b in x.parsed.blocks}
                if ASC_CLS in names and 'ArchonDamageAttributeSet' in names:
                    return e.ch, (e.parsed.open or {}).get('guid', 0)
        return None, None

    def _load(self, now):
        from .attributes import find_block
        ch, self.owner = self._weapon()
        hdr = find_block(self.s, ch, ASC_CLS) if ch is not None else None
        if hdr is None:
            raise LookupError('no weapon %s in the sent world' % ASC_CLS)
        key, ids = 0, []
        for e in self.s.entries:
            if e.ch != ch:
                continue
            for blk in e.parsed.blocks:
                if blk.cls_name == ASC_CLS:
                    for fld in blk.fields or []:
                        if fld[1] == FIELD and fld[2] == 'fast':
                            key = max(key, fld[3]['key'])
                            ids += [rid for rid, _ in fld[3]['changed']]
        self.ch, self.hdr, self.key, self.next_id = ch, hdr, key, max(ids, default=0) + 1
        if self.clock_base is None:
            gs = self._game_state()
            # the client's server clock reads the sent value now; continue from there (keeps float32 precision)
            self.clock_base = (float(gs[1].values.get('ReplicatedWorldTimeSeconds', 0.0)) if gs else 0.0) - now

    def _game_state(self):
        """(entry, actor block) of the game state the client holds."""
        for e in self.s.entries:
            if e.open:
                for blk in e.parsed.blocks:
                    if blk.actor and blk.values and 'ReplicatedWorldTimeSeconds' in blk.values:
                        return e, blk
        return None

    def world_time(self, now):
        return self.clock_base + now

    def _asc_entry(self, fa, exports=(), must_map=()):
        from ..genworld import Entry
        b = model.Block()
        b.actor, b.rep, b.guid, b.stable, b.destroy = False, False, self.hdr.guid, self.hdr.stable, False
        b.cls, b.outer = self.hdr.cls, self.hdr.outer
        b.ref, b.ref_name = getattr(self.hdr, 'ref', None), getattr(self.hdr, 'ref_name', None)
        b.cls_name = ASC_CLS
        b.values = b.tail = b.raw = None
        b.partial_properties = False
        fi = self.s.world.schema.field_index(ASC_CLS)
        b.fields = [(fi.index(('prop', FIELD)), FIELD, 'fast', fa)]
        e = Entry()
        e.t, e.ch, e.cls, e.exports = 0, self.ch, self.s.chan_cls.get(self.ch), list(exports)
        e.open = e.close = False
        e.must_map = bool(must_map)
        e.reliable = True
        e.patch_pairs = ()
        e.parsed = model.Parsed()
        e.parsed.must_map = list(must_map)
        e.parsed.blocks = [b]
        return e

    def _clock_entry(self, now):
        from ..island_arrival import _block, _entry
        gs = self._game_state()
        if gs is None:
            return None
        gs[1].values['ReplicatedWorldTimeSeconds'] = self.world_time(now)
        return _entry(self.s.world, gs[0].ch, gs[0].cls, [_block(gs[0].cls, {'ReplicatedWorldTimeSeconds': self.world_time(now)})])

    def apply(self, cls, duration, now, prediction_key=None):
        """Apply GE `cls` for `duration` s. -> reliable entries (game-state clock first, then the ASC delta)."""
        if self.hdr is None:
            self._load(now)
        exports, must_map = [], []
        if cls not in self.cdo:
            g = self.s.next_live_guid
            pkg = _info(g() + 1, EFFECTS[cls])
            cls_info = _info(g() + 1, cls, outer=pkg)
            cdo = _info(g() + 1, 'Default__' + cls, outer=pkg)
            exports, must_map = [cls_info, cdo], [cls_info.guid, cdo.guid]
            self.cdo[cls] = cdo.guid
        rid = self.next_id
        self.next_id += 1
        vals = [(P + 'Spec.Def', ('obj', self.cdo[cls])), (P + 'Spec.ModifiedAttributes', []), (P + 'Spec.Duration', float(duration)),
                (P + 'Spec.Period', 0.0), (P + 'Spec.ChanceToApplyToTarget', 1.0), (P + 'Spec.DynamicGrantedTags', []),
                (P + 'Spec.DynamicAssetTags', []), (P + 'Spec.Modifiers', []), (P + 'Spec.StackCount', 1),
                (P + 'Spec.GrantedAbilitySpecs', []), (P + 'Spec.EffectContext', {'rep': 3, 'instigator': self.owner, 'causer': self.owner}),
                (P + 'Spec.Level', 0.0), (P + 'PredictionKey', prediction_key or {'server': 0}),
                (P + 'StartServerWorldTime', self.world_time(now))]
        fa = {'delta_flag': 0, 'key': self.key + 1, 'base': self.key, 'deleted': [], 'changed': [(rid, vals)]}
        self.key += 1
        self.active[rid] = {'cls': cls, 'ends': now + duration}
        clock = self._clock_entry(now)
        _log('%s item %d for %.0f s (server time %.2f)' % (cls, rid, duration, self.world_time(now)))
        return ([clock] if clock is not None else []) + [self._asc_entry(fa, exports, must_map)]

    def step(self, now):
        """-> reliable entries: a delete for every effect whose duration ran out."""
        done = [rid for rid, a in self.active.items() if now >= a['ends']]
        if not done:
            return []
        for rid in done:
            _log('%s item %d expired' % (self.active.pop(rid)['cls'], rid))
        fa = {'delta_flag': 0, 'key': self.key + 1, 'base': self.key, 'deleted': done, 'changed': []}
        self.key += 1
        return [self._asc_entry(fa)]


def for_session(session):
    pe = getattr(session, '_effects', None)
    if pe is None:
        pe = session._effects = PlayerEffects(session)
    return pe


def on_ability_activated(session, cls, client_key, now):
    """An accepted activation of a weapon ability: its cooldown effect. -> reliable entries."""
    cd = ABILITY_COOLDOWNS.get(cls)
    if cd is None:
        return []
    ge, duration, predicted = cd
    try:
        return for_session(session).apply(ge, duration, now, prediction_key=client_key if predicted and client_key else None)
    except LookupError as exc:
        _log('%s not applied (%s)' % (ge, exc))
        return []
