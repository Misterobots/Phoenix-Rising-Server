"""Behemoth part health, part breaks and the stagger meter, from world/behemoths/<variant>_parts.json.

Exact (asset data): the parts, each part's health as RatioOfBehemothHealth of max health, the stagger table montages.
The body's preset (body_standard_ptpreset, PercentOfMaxHealth 999) never breaks: 'breakable': false.
Assumed until the native rules are traced: the part hit is the part whose root joint is the nearest ancestor of the hit bone
(the game uses ArchonPartCollisionComponent), the stagger meter fills with STAGGER_RATIO of each hit's damage and empties at
STAGGER_THRESHOLD_RATIO of max health, and a part break plays the short startle stagger (the tail its tailcut montage).
"""
import json
import os

SERVER = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STAGGER_RATIO = 0.75               # matches the StaggerDamage the combat text already shows
STAGGER_THRESHOLD_RATIO = 0.08     # of max health; placeholder for the native stagger threshold
STAGGER_THRESHOLD_GROWTH = 1.5     # each stagger makes the next one harder (placeholder)


def load(variant, server=SERVER):
    path = os.path.join(server, 'world', 'behemoths', variant + '_parts.json')
    if not os.path.exists(path):
        return None
    with open(path, encoding='utf-8') as f:
        return json.load(f)


class PartTracker:
    def __init__(self, data, joints, max_health):
        self.data = data
        self.parent = {b: (j.get('parent') if isinstance(j, dict) else None) for b, j in (joints or {}).items()}
        self.root_part = {r: name for name, p in data['parts'].items() for r in p['roots']}
        self.health = {name: p['ratio'] * max_health for name, p in data['parts'].items()}
        self.broken = set()
        self.stagger = 0.0
        self.stagger_threshold = STAGGER_THRESHOLD_RATIO * max_health

    def part_of(self, bone):
        seen = 0
        while bone and seen < 64:
            if bone in self.root_part:
                return self.root_part[bone]
            bone, seen = self.parent.get(bone), seen + 1
        return self.data['default_part']

    def hit(self, bone, damage, side):
        """Apply one hit. -> (part, [(kind, montage, seconds)]) with kind 'break' or 'stagger'; at most one reaction per hit,
        a break first."""
        part = self.part_of(bone)
        out = []
        if part not in self.broken and self.data['parts'][part].get('breakable', True):
            self.health[part] = max(0.0, self.health[part] - damage)
            if self.health[part] <= 0.0:
                self.broken.add(part)
                m = self.data['parts'][part].get('break_montage') or self.data['break_montages'][side]
                out.append(('break', m, self.data['montage_seconds'].get(m, 2.5)))
        self.stagger += damage * STAGGER_RATIO
        if self.stagger >= self.stagger_threshold:
            self.stagger = 0.0
            self.stagger_threshold *= STAGGER_THRESHOLD_GROWTH
            if not out:
                m = self.data['stagger_montages'][side]
                out.append(('stagger', m, self.data['montage_seconds'].get(m, 2.5)))
        return part, out
