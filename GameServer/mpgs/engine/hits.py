"""Engine layer: server-owned attacker -> player hits (STAGE_B_DAMAGE_PLAN B3 in Mystic-Paradox; ENGINE_PORT_PLAN.md step 6).

Protocol, from the net functions (signatures from FunctionsInfo; the order of events is INFERRED, the trigger is native):
  server -> victim's client   ArchonHittableComponent.ClientTryHit(HitID, Damager, Params_0, ResultDamageTags,
                              bHitWouldHaveSucceededOnServer)
  victim's client -> server   ServerAcceptHit(HitID)  |  ServerRefuseHit(HitID, InvulnerabilityTags)   (dodges, i-frames)
  on accept the server applies the damage: the victim's ArchonHealthAttributeSet.CurrentHealth (replicated property) and
  ArchonCharacter.ClientHandlePostMitigationDamage (the numbers the victim's HUD shows; inferred use).

Damage amount: behemoth_to_player = the row's element damage x behemoth_to_player_exploration_dmgcurve(power difference). Behemoth
and player power are a STAND-IN (equal, difference 0) until power is read from the encounter's level and the player's gear.
"""
from ..repl.decode import OrderedFields

PENDING_LIMIT = 64          # hits awaiting the victim's answer; older ones are dropped (a client that never answers)


def hit_id(rng):
    """A fresh FGuid as the four int32 members ClientTryHit carries."""
    return {k: rng.randint(-2 ** 31, 2 ** 31 - 1) for k in 'ABCD'}


def key_of(values, prefix='HitID'):
    """(A, B, C, D) from decoded RPC values ({'HitID.A': ..} flattened), for matching an answer to its ClientTryHit."""
    return tuple(int(values.get('%s.%s' % (prefix, k), 0)) for k in 'ABCD')


# FDamageTableData members in declaration order (ClientTryHit layout); a struct parameter is sent whole, so every member has a value.
DAMAGE_ROW_FIELDS = ('BaseDamage', 'PhysicalTypeOverride', 'ExplicitPartBreak', 'ExplicitStagger', 'ExplicitExpose', 'DamageTrue',
                     'PhysicalDamage', 'FlameDamage', 'FrostDamage', 'ShockDamage', 'RadiantDamage', 'UmbralDamage', 'TerraDamage',
                     'RawElementalDamage', 'ElementalHealthDamageMod', 'Meter')
SUMMARY_FIELDS = ('MitigatedAttackDamage', 'VsBehemoth_CoreDamage', 'VsBehemoth_PartDamage', 'VsBehemoth_StaggerDamage',
                  'VsBehemoth_WoundDamage', 'ElementalDamage', 'RawBaseDamage', 'BaseDamageWithMultipliersApplied')


def client_try_hit_params(hid, damager_guid, row, direction=(1.0, 0.0, 0.0), scale=1.0, would_succeed=True, hit=None):
    """OrderedFields for ClientTryHit. `row` is the authored FDamageTableData (export_behemoth_damage 'row'). Params_0 is sent whole:
    tag containers empty, HitInfo a default FHitResult unless `hit` is given (structs.py HitResult fields), DamageTypeClass/Effect
    null, RemoveBuffStacksList and AssetTags empty (the row's tags, e.g. Stagger.Medium, are not sent yet). ResultDamageTags: default."""
    row = row or {}
    p = OrderedFields()
    for k in 'ABCD':
        p['HitID.' + k] = hid[k]
    p['Damager'] = ('obj', damager_guid)
    p['Params_0.InstigatorTags'] = []
    p['Params_0.SourceActor'] = ('obj', damager_guid)
    p['Params_0.HitDirection'] = [float(v) for v in direction]
    p['Params_0.HitInfo'] = dict(hit or {})
    for field in DAMAGE_ROW_FIELDS:
        default = 1.0 if field == 'ElementalHealthDamageMod' else 0
        p['Params_0.DamageRow.' + field] = row.get(field, default)
    p['Params_0.DamageRow.DamageTypeClass'] = ('obj', 0)
    p['Params_0.DamageRow.Effect'] = ('obj', 0)
    p['Params_0.DamageRow.RemoveBuffStacksList'] = []
    p['Params_0.DamageRow.AssetTags'] = []
    p['Params_0.DamageScale'] = float(scale)
    p['Params_0.bApplyBuffsOnHit'] = 0
    p['bHitWouldHaveSucceededOnServer'] = 1 if would_succeed else 0
    return p


def post_mitigation_params(taker_guid, damage, elemental, raw_base):
    p = OrderedFields()
    p['DamageTaker'] = ('obj', taker_guid)
    values = {'MitigatedAttackDamage': damage, 'ElementalDamage': elemental, 'RawBaseDamage': raw_base,
              'BaseDamageWithMultipliersApplied': damage}
    for field in SUMMARY_FIELDS:
        p['DamageEventData.' + field] = float(values.get(field, 0.0))
    return p


def amount(model, elements, behemoth_power=0.0, player_power=0.0, scale=1.0):
    """-> (damage, elemental part) for one hit, or (0, 0) for a row without element damage."""
    from ..behemoth.damage import behemoth_to_player
    total = behemoth_to_player(model, elements, behemoth_power, player_power, scale)
    if not total:
        return 0.0, 0.0
    phys = float((elements or {}).get('PhysicalDamage', 0.0))
    raw = sum(float(v) for v in elements.values())
    return total, total * (raw - phys) / raw if raw else 0.0


class PendingHits:
    """ClientTryHit sent, answer not yet received: HitID key -> what to apply on ServerAcceptHit."""

    def __init__(self):
        self._hits = {}

    def add(self, hid, info):
        self._hits[tuple(hid[k] for k in 'ABCD')] = info
        while len(self._hits) > PENDING_LIMIT:
            self._hits.pop(next(iter(self._hits)))

    def take(self, key):
        return self._hits.pop(key, None)

    def __len__(self):
        return len(self._hits)
