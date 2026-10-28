"""PROPOSAL, NOT GAME DATA: damage rows for the frost Gnasher (beaver_frost) that the 2.1.1 client lacks.

In 2.1.1, beaver_frost inherits DamageComp.DamageTable = beaver_damage_table (the normal Gnasher's physical/terra
table) and its frost attacks name rows that exist in no table (verified.json: frost-gnasher-missing-rows). This
script derives replacement rows from existing rows of the same family with two stated rules, so every number can
be traced back:

  swap   the model row's elemental amount (flame for the blaze Gnasher, terra for the normal one, flame for
         Hellion) becomes frost; physical damage, damage type, stagger and camera tags are kept.
  aether total damage x AETHER_TOTAL, the increase added as frost, physical unchanged. 1.2 is the median total
         increase of the game's base/aether row pairs (thunderbird LightSlam 1.11 and 1.17, mantis spin AoE 1.33
         and 1.2, wolf_frost JumpSlam 1.2); wolf_frost JumpSlam -> JumpSlam_Aether (200/50 -> 200/100) is the only
         frost pair and matches it exactly.

Run: python -m tools.atlas.proposals.beaver_frost_damage  ->  data/atlas/proposals/beaver_frost_damage_table.json
"""
import json
import os

from .. import paths
from ..behemoths import _table_rows

AETHER_TOTAL = 1.2
ELEMENTS = ('FlameDamage', 'FrostDamage', 'ShockDamage', 'RadiantDamage', 'UmbralDamage', 'TerraDamage')
OUT = os.path.join(paths.OUT, 'proposals', 'beaver_frost_damage_table.json')

# Missing rows: name -> (model table, model row, rule, where the frost Gnasher uses it, why this model)
MISSING = {
    'shoulderslam_body': ('lavaroller_damage_table', 'shoulderslam_body', 'swap',
                          'ShoulderDrop_L/R window (body, 1.88-2.33 s)',
                          'same row name in Hellion (same family); the blaze Gnasher has no such row'),
    'ShoulderDrop': ('beaver_blaze_damage_table', 'ShoulderDrop', 'swap',
                     'ShoulderDrop_L/R area effect (aoe_stomp_frost), not aether-charged',
                     'same row in the blaze Gnasher, the sister elemental variant'),
    'ShoulderDrop.Aether': ('beaver_blaze_damage_table', 'ShoulderDrop', 'aether',
                            'ShoulderDrop_L area effect when aether-charged (ShoulderDrop_R names plain ShoulderDrop '
                            'for its aether spawns in 2.1.1)', 'aether version of ShoulderDrop'),
    'BellyDive_Slam': ('beaver_blaze_damage_table', 'TailSlam_Tail', 'swap',
                       'BellyDive window (tail, pelvis, spine; 1.81-2.43 s)',
                       'the blaze Gnasher\'s heaviest tail/body slam window'),
    'BellyDive.Slam': ('beaver_blaze_damage_table', 'GroundStomp_AoE', 'swap',
                       'BellyDive area effect (aoe_base_slam, radius 800), not aether-charged',
                       'the Gnasher\'s own slam area effect row'),
    'BellyDive.Slam.Aether': ('beaver_blaze_damage_table', 'GroundStomp_AoE', 'aether',
                              'BellyDive area effect when aether-charged (aoe_frost_slam + chill)', 'aether version'),
    'BellyDrift.Body': ('beaver_blaze_damage_table', 'tackle', 'swap',
                        'BellyDrift window (body slide, 1.49-4.19 s)', 'the Gnasher\'s body-charge row'),
    'BellyDrift.Slam': ('beaver_blaze_damage_table', 'GroundStomp_AoE', 'swap',
                        'BellyDrift area effect (aoe_base_slam), not aether-charged', 'slam area effect row'),
    'BellyDrift.Slam.Aether': ('beaver_blaze_damage_table', 'GroundStomp_AoE', 'aether',
                               'BellyDrift area effect when aether-charged', 'aether version'),
    'Somersault.Tail': ('beaver_blaze_damage_table', 'TailSlam_Tail', 'swap',
                        'Somersault window (tail, pelvis, spine; 1.42-3.08 s)', 'tail slam window row'),
    'Somersault.Slam': ('beaver_blaze_damage_table', 'GroundStomp_AoE', 'swap',
                        'Somersault area effects (1.39 s with intact tail, 1.84 s, 2.76 s), not aether-charged',
                        'slam area effect row'),
    'Somersault.Slam.Aether': ('beaver_blaze_damage_table', 'GroundStomp_AoE', 'aether',
                               'Somersault area effects when aether-charged', 'aether version'),
    'IceSpike.Slam': ('beaver_blaze_damage_table', 'GroundStomp_AoE', 'swap',
                      'IceSpike first area effect (aoe_base_slam_frost, 1.19 s), not aether-charged',
                      'slam area effect row'),
    'IceSpike.Slam.Aether': ('beaver_blaze_damage_table', 'GroundStomp_AoE', 'aether',
                             'IceSpike first area effect when aether-charged', 'aether version'),
    'IceSpike.SecondSlam': ('beaver_blaze_damage_table', 'GroundStomp_AoE', 'swap',
                            'IceSpike second area effect (2.00 s), not aether-charged', 'slam area effect row'),
    'IceSpike.SecondSlam.Aether': ('beaver_blaze_damage_table', 'GroundStomp_AoE', 'aether',
                                   'IceSpike second area effect when aether-charged', 'aether version'),
    'IceToss.IceProjectile': ('mcrollin_alpha_damage_table', 'SpikeThrow_Projectile', 'swap',
                              'IceToss projectile (beaver_frost_icetoss_proj, 2.27 s) and the aether-charged '
                              'Somersault toss (1.44 s)',
                              'the frost Pangar\'s thrown projectile (same base class family); the Gnasher tables '
                              'have no projectile row'),
}
# Rows the frost Gnasher already finds in beaver_damage_table (terra). Optional frost versions from the blaze
# Gnasher's rows of the same name; using them changes 2.1.1 behaviour.
SHARED = ('tail_tantrum', 'tail_tantrum_body', 'TailSlam_Body', 'TailSlam_Tail', 'tailslash_tail', 'bite_lunge',
          'bite_combo', 'tackle', 'GroundStomp_LegImpact', 'GroundStomp_Tail', 'GroundStomp_AoE', 'rage_entry')


def _frost(row, rule):
    out = dict(row)
    element = sum(out.get(e, 0) or 0 for e in ELEMENTS)
    for e in ELEMENTS:
        out[e] = 0
    phys = out.get('PhysicalDamage', 0) or 0
    if rule == 'aether':
        element = round((phys + element) * AETHER_TOTAL) - phys
    out['FrostDamage'] = element
    return out


def build():
    rows = {}
    for name, (table, model, rule, used, why) in MISSING.items():
        src = _table_rows(table)[model]
        r = _frost(src, rule)
        r['_proposal'] = {'model': '%s[%s]' % (table, model), 'rule': rule, 'used_by': used, 'why': why,
                          'model_values': {k: v for k, v in src.items() if k in ('PhysicalDamage',) + ELEMENTS and v}}
        rows[name] = r
    optional = {}
    for name in SHARED:
        blaze = _table_rows('beaver_blaze_damage_table').get(name)
        if blaze:
            r = _frost(blaze, 'swap')
            r['_proposal'] = {'model': 'beaver_blaze_damage_table[%s]' % name, 'rule': 'swap',
                              'replaces': 'beaver_damage_table[%s] (terra), which beaver_frost uses in 2.1.1' % name}
            optional[name] = r
    doc = {
        '_about': 'PROPOSAL BY THE PHOENIX SERVER PROJECT, NOT GAME DATA. Damage rows for beaver_frost that the 2.1.1 '
                  'client lacks, derived from existing rows by tools/atlas/proposals/beaver_frost_damage.py (rules in '
                  'its docstring and in each row\'s _proposal). Values are table inputs before threat/tuning scaling, '
                  'like any DamageTableData row. Unreviewed: tune in play before relying on them.',
        'row_struct': 'DamageTableData',
        'aether_total_multiplier': AETHER_TOTAL,
        'missing_rows': rows,
        'optional_frost_versions_of_shared_rows': optional,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(doc, f, indent=1, ensure_ascii=False)
    return doc


if __name__ == '__main__':
    d = build()
    for part in ('missing_rows', 'optional_frost_versions_of_shared_rows'):
        print(part)
        for k, r in d[part].items():
            print('  %-28s phys %4s frost %4s  %-22s %s' % (k, r.get('PhysicalDamage'), r['FrostDamage'],
                                                            ','.join(t['TagName'] for t in r.get('AssetTags', [])),
                                                            r['_proposal']['model']))
