"""Behemoth part health, part breaks and the stagger meter, from world/behemoths/<variant>_parts.json.

Read from the game (Blueprint bytecode, see `python -m tools.atlas show staggerable_bpc`):
- parts: health = RatioOfBehemothHealth x max health; the body never breaks (body_standard_ptpreset, PercentOfMaxHealth 999).
- stagger (monsterpart_bpc.HandleStaggerDamage -> staggerable_bpc.TryAddStaggerDamage): per hit
      total = BaseDamage x (1.333 if DamageType.Blunt) x (1 if Combat.Weapon.WeaponImpact and not Piercing/CantStagger else 0)
      dealt = (max(total x BaseDamageStaggerMultiplier - max(stage.per_hit_armor x (1 - penetration), 0), 0)
               + StaggerPerHitBonus + ExplicitStagger) x StaggerPowerCurve(power delta) x PartStaggerMultiplier
  temporary armor soaks `dealt` first (ReduceArmor); the rest lowers the meter; at 0 the stage advances (meter = next stage's
  required x player scaling), the behemoth staggers and gains that stage's armor, which decays to 0 over armor_duration
  (AddStaggerArmor / DegenStaggerArmor). The meter regenerates stage.regen per second, capped at its max (ReceiveTick).
Assumed: the player's BaseDamageStaggerMultiplier 1, StaggerPerHitBonus 0, penetration 0 (no Blueprint sets them; native or
gameplay-effect values), power delta 0 (curve value 1), PartStaggerMultiplier 1, the last stage repeats, and the part hit is
the part whose root joint is the nearest ancestor of the hit bone (the game uses ArchonPartCollisionComponent).
"""
import json
import os

SERVER = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BLUNT_STAGGER = 1.3329999446868896     # HandleStaggerDamage: DamageType.Blunt
BASE_STAGGER_MULT = float(os.environ.get('MPGS_STAGGER_MULT', '1'))   # the player's BaseDamageStaggerMultiplier (assumed)


def load(variant, server=SERVER):
    path = os.path.join(server, 'world', 'behemoths', variant + '_parts.json')
    if not os.path.exists(path):
        return None
    with open(path, encoding='utf-8') as f:
        return json.load(f)


class PartTracker:
    def __init__(self, data, joints, max_health, players=1):
        self.data = data
        self.parent = {b: (j.get('parent') if isinstance(j, dict) else None) for b, j in (joints or {}).items()}
        self.root_part = {r: name for name, p in data['parts'].items() for r in p['roots']}
        self.health = {name: p['ratio'] * max_health for name, p in data['parts'].items()}
        self.broken = set()
        st = data.get('stagger') or {}
        self.stages = st.get('stages') or [{'required': 500.0, 'regen': 0.0, 'per_hit_armor': 0.0, 'armor_on_stagger': 0.0}]
        self.scaling = 1.0 + float(st.get('per_player_scaling', 0.0)) * (players - 1)
        self.armor_duration = float(st.get('armor_duration', 30.0))
        self.stage = 0
        self.stagger_max = self.stages[0]['required'] * self.scaling
        self.stagger = self.stagger_max          # CurrentStaggerHealth: counts down
        self.armor = []                          # [remaining, degen per second]
        self.last_t = None

    def part_of(self, bone):
        seen = 0
        while bone and seen < 64:
            if bone in self.root_part:
                return self.root_part[bone]
            bone, seen = self.parent.get(bone), seen + 1
        return self.data['default_part']

    def tick(self, now):
        """Meter regen and armor decay up to `now` (seconds)."""
        if self.last_t is not None and now > self.last_t:
            dt = now - self.last_t
            self.stagger = min(self.stagger_max, self.stagger + self.stages[self.stage]['regen'] * dt)
            for a in self.armor:
                a[0] -= a[1] * dt
            self.armor = [a for a in self.armor if a[0] > 0.0]
        self.last_t = now

    def stagger_damage(self, row):
        """HandleStaggerDamage + TryAddStaggerDamage for one hit of damage-table `row` (before armor)."""
        tags = set((row or {}).get('tags') or ())
        impact = 'Combat.Weapon.WeaponImpact' in tags and not tags & {'DamageType.Piercing', 'DamageType.CantStagger'}
        total = float((row or {}).get('BaseDamage', 0)) * (BLUNT_STAGGER if 'DamageType.Blunt' in tags else 1.0) * (1.0 if impact else 0.0)
        mult = BASE_STAGGER_MULT if 'Combat.Weapon.WeaponImpact' in tags else 1.0
        armor = max(self.stages[self.stage]['per_hit_armor'], 0.0)
        return max(total * mult - armor, 0.0) + float((row or {}).get('ExplicitStagger', 0))

    def hit(self, bone, damage, side, row=None, now=None):
        """Apply one hit. -> (part, [(kind, montage, seconds)]) with kind 'break' or 'stagger'; at most one reaction per hit,
        a break first. `row` is the attack's damage-table row (stagger), `now` the encounter time (regen/armor decay)."""
        if now is not None:
            self.tick(now)
        part = self.part_of(bone)
        out = []
        if part not in self.broken and self.data['parts'][part].get('breakable', True):
            self.health[part] = max(0.0, self.health[part] - damage - float((row or {}).get('ExplicitPartBreak', 0)))
            if self.health[part] <= 0.0:
                self.broken.add(part)
                m = self.data['parts'][part].get('break_montage') or self.data['break_montages'][side]
                out.append(('break', m, self.data['montage_seconds'].get(m, 2.5)))
        dealt = self.stagger_damage(row) if row is not None else 0.0
        for a in self.armor:                     # ReduceArmor: newest armor first
            take = min(a[0], dealt)
            a[0] -= take
            dealt -= take
        self.armor = [a for a in self.armor if a[0] > 0.0]
        self.stagger -= dealt
        if self.stagger <= 0.0:
            self.stage = min(self.stage + 1, len(self.stages) - 1)
            self.stagger_max = self.stages[self.stage]['required'] * self.scaling
            self.stagger = self.stagger_max
            amount = self.stages[self.stage]['armor_on_stagger'] * self.scaling   # ChangeStaggerStage: the new stage's armor
            if amount > 0:
                self.armor.insert(0, [amount, amount / self.armor_duration])
            if not out:
                m = self.data['stagger_montages'][side]
                out.append(('stagger', m, self.data['montage_seconds'].get(m, 2.5)))
        return part, out
