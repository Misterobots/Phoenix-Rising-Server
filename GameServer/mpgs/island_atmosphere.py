"""Activate the selected map's placed atmosphere spawner from cooked data."""
import copy

from .repl import model
from .repl.guid import GuidInfo, flatten

ACTOR = 'atmosphere_spawner_bp_C'


def extract(exports, read):
    spawners = [r for r in exports if r['Type'] == ACTOR]
    if len(spawners) != 1:
        raise ValueError('Expected one authored island atmosphere spawner')
    placed = spawners[0]
    class_path = placed['Class'].split("'", 1)[1].rstrip("'")
    rows = read(class_path.rsplit('.', 1)[0])
    defaults = next(r['Properties'] for r in rows if r['Name'] == 'Default__' + ACTOR)
    props = placed.get('Properties', {})
    valid = props.get('ValidAtmosphere', defaults['ValidAtmosphere'])
    requested = props.get('CurrentAtmosphere', defaults['CurrentAtmosphere'])['AssetPathName']
    matches = [i for i, ref in enumerate(valid) if ref['AssetPathName'] == requested]
    if len(matches) != 1:
        raise ValueError('Authored atmosphere is not uniquely present in this map spawner')
    atmosphere_rows = read(requested.rsplit('.', 1)[0])
    atmosphere = next(r['Properties'] for r in atmosphere_rows
                      if r['Name'] == 'Default__' + requested.rsplit('.', 1)[1])
    driven = defaults['TargetDrivenValues']

    def timing(name):
        return next(value for key, value in driven.items() if key.startswith(name + '_'))

    values = {'ServerAtmosphereSettings.ChangeAtmosphereCount': 1,
              'ServerAtmosphereSettings.ChangeValues.Duration': timing('Duration'),
              'ServerAtmosphereSettings.ChangeValues.FadeInTime': timing('FadeInTime'),
              'ServerAtmosphereSettings.ChangeValues.FadeOutTime': timing('FadeOutTime'),
              'ServerAtmosphereSettings.ChangeValues.InterpolationCurve': 0,
              'ServerAtmosphereSettings.ChangeValues.AffectLighting': timing('Lighting'),
              'ServerAtmosphereSettings.ChangeValues.AffectFog': timing('HeightFog'),
              'RequestedAtmosphereIndex': matches[0]}
    prefix = 'ServerAtmosphereSettings.ChangeValues.'
    scalar = {'SunBrightness': 'sun_brightness', 'SkylightBrightness': 'skylight_brightness',
              'HeightFogDensity': 'Height Fog Density', 'HeightFogOpacity': 'Height Fog Opacity',
              'HeightFogFalloff': 'Height Fog Falloff'}
    colors = {'DirectionalLightColor': 'directional_light_color', 'SkylightColor': 'skylight_color',
              'HeightFogColor': 'Height Fog Colour', 'HeightFogSunGlowColor': 'Height Fog Sun Glow Colour'}
    for field, source in scalar.items():
        values[prefix + field + '.Mode'] = 1  # Archon::EAtmosphereDrivenValueMode::Target
        values[prefix + field + '.Value'] = atmosphere[source]
    for field, source in colors.items():
        values[prefix + field + '.Mode'] = 1
        for channel in 'RGBA':
            values[prefix + field + '.Color.' + channel] = atmosphere[source][channel]
    return {'actor': placed['Name'], 'class': ACTOR, 'atmosphere': requested, 'values': values}


def append_bootstrap(world, contract):
    settings = contract['atmosphere']
    level = next(copy.deepcopy(i) for e in world.entries for root in e.exports for i in flatten(root)
                 if i.name == 'PersistentLevel' and i.guid == 5)
    guid = 42001  # static placed actor; no dynamic actor/archetype spawn
    if guid in world.ctx._names:
        raise ValueError('Atmosphere GUID collides with the island bootstrap')
    actor = GuidInfo()
    actor.guid, actor.flags, actor.outer_info = guid, 1, level
    actor.name, actor.checksum = settings['actor'], None
    world.ctx._names[guid] = (5, actor.name)
    block = model.Block()
    block.actor = block.rep = True
    block.guid, block.stable, block.destroy = 0, False, False
    block.cls = block.outer = block.ref = block.ref_name = None
    block.cls_name = ACTOR
    block.values = copy.deepcopy(settings['values'])
    block.tail = block.raw = block.fields = None
    entry = type(world.entries[0])()
    entry.t, entry.ch, entry.cls = 32, 18, ACTOR
    entry.open, entry.close, entry.must_map, entry.reliable = True, False, False, True
    entry.exports, entry.patch_pairs = [actor], ()
    entry.parsed = model.Parsed()
    entry.parsed.open, entry.parsed.blocks = {'guid': guid}, [block]
    world.entries.append(entry)
    world.entries.sort(key=lambda e: e.t)
