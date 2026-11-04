"""Compose an implementation plan from island, behemoth, weapon and item references."""
import json
import os
import sys

from . import paths


def _read(relative, default=None):
    path = os.path.join(paths.OUT, relative)
    if not os.path.exists(path):
        return default
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def _die(message):
    sys.exit(message)


def _print_incident(incident):
    print('  [%s] %s' % (incident.get('status', 'unknown'), incident.get('title', incident.get('id', 'finding'))))
    for label in ('observed', 'cause', 'correction', 'validation'):
        if incident.get(label):
            print('    %s: %s' % (label.capitalize(), incident[label]))
    for source in incident.get('evidence', []):
        print('    evidence: %s' % source)
    for step in incident.get('next_checks', []):
        print('    check: %s' % step)


def plan(a):
    contract_data = _read('topics/adventure_island_map_contracts.json', {})
    contracts = contract_data.get('contracts', [])
    contract = next((x for x in contracts if x.get('huntId', '').lower() == a.hunt.lower()), None)
    if contract is None:
        matches = [x.get('huntId') for x in contracts if a.hunt.lower() in x.get('huntId', '').lower()]
        _die('no island contract matches %r%s' % (a.hunt, (': ' + ', '.join(matches)) if matches else ''))

    chosen = contract.get('reproducibleEncounter')
    if chosen is None:
        if a.behemoth:
            _die('this is a map-only preview; it has no authored encounter to override')
        choices = []
        behemoth_row = None
    else:
        choices = chosen.get('selection', {}).get('behemothChoices', [])
        requested = a.behemoth or chosen.get('behemothRow')
        candidate = next((x for x in choices if x.get('row', '').lower() == requested.lower()), None)
        behemoth_row = candidate.get('row') if candidate else requested

    print('IMPLEMENTATION PLAN  %s' % contract.get('huntId'))
    location = contract.get('clientFacingLocation') or {}
    if location.get('destination'):
        print('In-game location: %s / %s' % (location.get('region', '?'), location['destination']))
    elif location.get('region'):
        print('In-game location: %s (destination not mapped)' % location['region'])
    print('Map: %s' % contract.get('map', '?'))
    print('Game mode: %s' % contract.get('gameMode', '?'))
    arrival = contract.get('arrival') or {}
    print('Arrival: %s; landing transforms=%s' % (arrival.get('actorClass', '?'), arrival.get('landingTransformCount', '?')))
    print('Map evidence: %s; map export SHA256=%s' % (
        contract.get('evidenceStatus', 'unrecorded'), contract.get('mapExportSha256', 'not recorded')))

    if chosen:
        print('\nENCOUNTER COMPONENT')
        if a.behemoth and not candidate:
            print('Selected behemoth: %s (outside this authored StandardBehemoth candidate pool; compatibility unknown)' % behemoth_row)
        else:
            weight = candidate.get('weight') if candidate else '?'
            print('Selected behemoth: %s (authored pool weight=%s)' % (behemoth_row, weight))
        behemoths = _read('behemoths.json', {})
        record = behemoths.get(behemoth_row) if isinstance(behemoths, dict) else None
        if candidate and candidate.get('actorClass'):
            actor_label = candidate['actorClass']
        elif behemoth_row == chosen.get('behemothRow'):
            actor_label = chosen.get('actorClass', '?')
        elif record:
            actor_label = '%s (%s; full package path not mapped in this candidate row)' % (
                record.get('class', '?'), record.get('package', '?'))
        else:
            actor_label = 'not mapped'
        is_default = behemoth_row == chosen.get('behemothRow')
        level = (candidate or {}).get('level', chosen.get('level') if is_default else 'see behemoth sheet')
        health = chosen.get('baseHealth', '?') if is_default else 'see behemoth sheet'
        speed = chosen.get('walkSpeed', '?') if is_default else 'see behemoth sheet'
        print('Actor: %s; level=%s; base health=%s; walk speed=%s' % (actor_label, level, health, speed))
        print('Placement candidates: %s patrol centers; deterministic preview tests one encounter.' % len(chosen.get('centers', [])))
        if len(choices) > 1:
            print('Other authored candidates: %s' % ', '.join('%s (weight %s)' % (x.get('row'), x.get('weight'))
                                                               for x in choices if x.get('row', '').lower() != behemoth_row.lower()))
        if record:
            print('Atlas behemoth coverage: %s; action set=%s; damage table=%s; behavior trees=%d; spawned classes=%d.' % (
                'missing=' + (', '.join(record.get('missing', [])) if record.get('missing') else 'none recorded'),
                record.get('links', {}).get('ActionSetTable', '?'), record.get('links', {}).get('DamageTable', '?'),
                len(record.get('behavior_trees', [])), len(record.get('spawned_classes', []))))
            print('Important: source coverage does not establish that Phoenix implements or the client accepts these actions.')
        else:
            print('Atlas behemoth coverage: no generated behemoth row for %s.' % behemoth_row)

    readiness = _read('topics/weapon_item_server_readiness.json', {})
    if a.weapon:
        weapons = readiness.get('weapons', [])
        weapon = next((x for x in weapons if x.get('type', '').lower() == a.weapon.lower()), None)
        if not weapon:
            _die('no weapon readiness row matches %r; choices: %s' % (a.weapon, ', '.join(x.get('type', '?') for x in weapons)))
        print('\nWEAPON COMPONENT')
        print('%s (%s): %s' % (weapon['type'], weapon.get('actor', '?'), weapon.get('type_specific_runtime', 'implementation status not recorded')))
        print('Implementation: %s; client validation: %s' % (
            weapon.get('implementation_status', 'not recorded'), weapon.get('validation_status', 'not recorded')))
        print('Live checks recorded as needed: %s' % weapon.get('live_test', 'not listed'))
        for path_name, path in readiness.get('shared_server_paths', {}).items():
            print('  shared %s: %s' % (path_name.replace('_', ' '), path))
    if a.item:
        item_data = _read('items/quick_item_catalog_ability_map.json', {})
        item = next((x for x in item_data.get('rows', []) if x.get('catalog_id', '').lower() == a.item.lower()), None)
        if item is None:
            _die('no exact quick-item catalog mapping for %r' % a.item)
        print('\nQUICK-ITEM COMPONENT')
        print('%s -> %s' % (item['catalog_id'], item.get('ability_class', {}).get('name', '?')))
        actor_refs = [x.get('object_name') for x in item.get('ability_class', {}).get('blueprint_default_object_refs', [])
                      if 'GeneratedClass' in x.get('object_name', '')]
        if actor_refs:
            print('Authored ability dependencies: %s' % ', '.join(actor_refs))
        print('Catalog mapping proves identity/dependency only; runtime support and client-visible effect need separate evidence.')
        if readiness.get('quick_items'):
            runtime_ids = readiness['quick_items'].get('runtime_named_catalog_ids', [])
            named = any(x.lower() == a.item.lower() for x in runtime_ids)
            print('Phoenix quick-item handler mapping: %s.' % ('named for this catalog ID' if named else 'not named for this catalog ID'))
            print('Implementation: %s; client validation: %s' % (
                readiness['quick_items'].get('implementation_status', 'not recorded'),
                readiness['quick_items'].get('validation_status', 'not recorded')))
            print('Other runtime-named item IDs: %s' % ', '.join(x for x in runtime_ids if x.lower() != a.item.lower()))

    print('\nKNOWN IMPLEMENTATION FINDINGS')
    findings = readiness.get('known_runtime_findings', [])
    selected_ids = {contract.get('huntId', '').lower()}
    if behemoth_row:
        selected_ids.add(behemoth_row.lower())
    if a.item:
        selected_ids.add(a.item.lower())
    if a.weapon:
        selected_ids.add(a.weapon.lower())
    relevant = [x for x in findings if not x.get('applies_to') or any(
        s.lower() in selected_ids or (s.lower() == 'weapons' and bool(a.weapon)) for s in x['applies_to'])]
    if relevant:
        for finding in relevant:
            _print_incident(finding)
    else:
        print('  No matching implementation incident is recorded. This is not a pass: no per-composition client acceptance is established.')
    print('\nEVIDENCE BOUNDARY')
    print('  %s' % contract_data.get('evidenceBoundary', 'Source/encoding coverage only; live client acceptance not implied.'))
    if a.item or a.weapon:
        print('  Selected components are a planning composition only; compatibility between independently selected components is not implied.')

