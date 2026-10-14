"""Quest state replication: QuestSystemComponent -> Serie -> Quest -> Objective sub-objects.

The client keeps no quest state of its own. The server creates one USerie per tracked series
(SerieAssetIdx = index into the client's QuestSerieDataAssets), one UQuest per quest of that
series (QuestDataIdx = index into the series asset's QuestsObject, Status = EQuestStatus) and
one UObjective per top-level objective (ObjectiveDataIdx, Status = EObjectiveStatus,
CurrentAmount). All are dynamic sub-objects of the controller's QuestSystemComponent.

Data:
- world/quest_catalog.json (optional, generated locally): series / quest / objective order and TrackingId
  GUIDs. The GUIDs are the keys of the backend's SERIE_* JSON.
- data/2.1.1/starting_quests.json: per-GUID status of a new character that has arrived in Ramsgate.
  Backend character SERIE_* data for the player overrides it per quest.
"""
import json
import os

from .repl import model
from .repl.decode import OrderedFields
from .repl.guid import GuidInfo

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOG = os.path.join(HERE, 'world', 'quest_catalog.json')
BASELINE = os.path.join(HERE, 'data', '2.1.1', 'starting_quests.json')

# Static (odd) NetGUIDs for the three /Script/Archon classes; the client resolves them by path.
CLASS_GUIDS = {'Serie': 0x7FF001, 'Quest': 0x7FF003, 'Objective': 0x7FF005}


def load_catalog(path=CATALOG):
    with open(path) as f:
        return json.load(f)['series']


def load_baseline(path=BASELINE):
    with open(path) as f:
        return json.load(f)['series']


def merge_progress(baseline, character_data):
    """character_data: the backend character's data dict (SERIE_* values are JSON strings). Per-quest override."""
    out = {k: dict(v) for k, v in baseline.items()}
    for k, v in (character_data or {}).items():
        if not k.startswith('SERIE_') or not isinstance(v, str):
            continue
        try:
            s = json.loads(v)
        except ValueError:
            continue
        sid = s.get('ID')
        if not sid:
            continue
        quests = {g: q for g, q in s.items() if g != 'ID' and isinstance(q, dict)}
        if quests:
            out.setdefault(sid, {}).update(quests)
    return out


def build_objects(catalog, progress):
    """-> list of series: (asset idx, [(quest idx, status, [(objective idx, status, amount)], quest guid)], series id)."""
    by_id = {s['id']: s for s in catalog}
    out = []
    for sid, quests in progress.items():
        s = by_id.get(sid)
        if s is None:
            continue
        qs = []
        for q in s['quests']:
            st = quests.get(q['guid'], {})
            objs = []
            for o in q['objectives']:
                os_ = st.get(o['guid'], {}) if isinstance(st.get(o['guid']), dict) else {}
                objs.append((o['idx'], int(os_.get('Status', 0)), int(os_.get('CurrentAmount', 0))))
            qs.append((q['idx'], int(st.get('Status', 0)), objs, q['guid']))
        out.append((s['idx'], qs, sid))
    return out


def _arr(key, guids):
    elems = {}
    for i, g in enumerate(guids):
        e = OrderedFields()
        e[key + '[]'] = ('obj', g)
        elems[i] = e
    return {'count': len(guids), 'elems': elems}


def _block(guid, cls_guid, outer, cls_name, values, actor_outer=False):
    b = model.Block()
    b.actor, b.rep, b.guid, b.stable, b.destroy = False, True, guid, False, False
    b.cls, b.outer = cls_guid, (None if actor_outer else outer)
    b.ref = b.ref_name = None
    b.cls_name = cls_name
    b.values, b.tail, b.raw, b.fields = values, None, None, None
    b.partial_properties = False
    return b


def class_exports(package_info):
    out = []
    for name, g in CLASS_GUIDS.items():
        i = GuidInfo()
        i.guid, i.flags, i.name, i.checksum, i.outer_info = g, 1, name, None, package_info
        out.append(i)
    return out


def build_entry(series, comp_header, comp_ch, comp_cls, package_info, next_guid, t):
    """One reliable bunch: every Serie/Quest/Objective sub-object, then the component's QuestSeries + NumExpectedQuests."""
    blocks, serie_guids, nquests, meta = [], [], 0, {}
    comp_guid = comp_header.guid
    for asset_idx, quests, sid in series:
        sg = next_guid(); serie_guids.append(sg)
        qguids = []
        for qidx, status, objs, qguid in quests:
            qg = next_guid(); qguids.append(qg); nquests += 1
            meta[qg] = (sid, qguid)
            oguids = []
            for oidx, ostatus, amount in objs:
                og = next_guid(); oguids.append(og)
                v = OrderedFields()
                v['Status'] = ostatus
                v['CurrentAmount'] = amount
                v['ObjectiveDataIdx'] = oidx
                blocks.append(_block(og, CLASS_GUIDS['Objective'], comp_guid, 'Objective', v))
            v = OrderedFields()
            v['Status'] = status
            if oguids:
                v['Objectives'] = _arr('Objectives', oguids)
            v['QuestDataIdx'] = qidx
            v['OwningSerie'] = ('obj', sg)
            blocks.append(_block(qg, CLASS_GUIDS['Quest'], comp_guid, 'Quest', v))
        v = OrderedFields()
        v['SerieAssetIdx'] = asset_idx
        v['Quests'] = _arr('Quests', qguids)
        blocks.append(_block(sg, CLASS_GUIDS['Serie'], comp_guid, 'Serie', v))
    v = OrderedFields()
    v['QuestSeries'] = _arr('QuestSeries', serie_guids)
    v['NumExpectedQuests'] = nquests
    comp = model.Block()
    comp.actor, comp.rep, comp.guid, comp.stable, comp.destroy = False, True, comp_guid, comp_header.stable, False
    comp.cls, comp.outer = comp_header.cls, comp_header.outer
    comp.ref = comp.ref_name = None
    comp.cls_name = 'QuestSystemComponent'
    comp.values, comp.tail, comp.raw, comp.fields = v, None, None, None
    comp.partial_properties = False
    blocks.append(comp)
    from .genworld import Entry
    e = Entry()
    e.t, e.ch, e.cls = t, comp_ch, comp_cls
    e.exports = class_exports(package_info)
    e.open = e.close = False
    e.must_map = True
    e.reliable = True
    e.patch_pairs = ()
    e.parsed = model.Parsed()
    e.parsed.must_map = list(CLASS_GUIDS.values())
    e.parsed.blocks = blocks
    return e, {'series': len(serie_guids), 'quests': nquests, 'objects': len(blocks) - 1, 'meta': meta}


def persist_status(account, character, sid, quest_guid, status, auth_headers, base='http://127.0.0.1:3001'):
    """Write one quest status into the backend character's SERIE_<id> JSON (read-modify-write;
    POST /character replaces the whole data blob)."""
    from urllib.parse import quote
    from urllib.request import Request, urlopen
    path = '/character/' + quote(account, safe='') + '/' + quote(character, safe='')
    with urlopen(Request(base + path, headers=auth_headers), timeout=3) as r:
        prof = json.load(r)
    prof = prof.get('payload', prof) if isinstance(prof, dict) else prof
    data = prof.get('data')
    data = json.loads(data) if isinstance(data, str) else (data or {})
    key = 'SERIE_' + sid.lower()
    serie = json.loads(data.get(key) or '{}') if isinstance(data.get(key, '{}'), str) else {}
    serie['ID'] = sid
    q = serie.get(quest_guid) if isinstance(serie.get(quest_guid), dict) else {}
    q['Status'] = status
    serie[quest_guid] = q
    data[key] = json.dumps(serie, separators=(',', ':'))
    body = json.dumps({'characterId': character, 'accountId': account, 'data': json.dumps(data),
                       'updateVersion': int(prof.get('updateVersion') or 0) + 1}).encode()
    hdr = dict(auth_headers); hdr['Content-Type'] = 'application/json'
    with urlopen(Request(base + '/character', data=body, headers=hdr, method='POST'), timeout=3) as r:
        return r.status
