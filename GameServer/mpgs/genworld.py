"""World source built on the replication model: the template's content is parsed into actors/blocks (mpgs.repl.model) and every
bunch is re-composed from that model (exports regenerated, partial splitting by compose.py).

Each connection gets its own instantiated copy of the world (GenSession): dynamic GUIDs remapped to a fresh range, identity strings edited
structurally. Blocks the model cannot decode (raw) still receive the same-length byte patch."""
import copy
import json
import math
import os
import time

from .replayworld import ReplayWorld, ReplaySession, patch_bits
from .repl import model, compose, remap, views, moves
from .wire import Bunch, CHNAME_ACTOR
from .repl.chan import Reassembler
from .repl.guid import GuidInfo, flatten
from .repl.schema import Schema
from .repl.decode import OrderedFields
from .initialization import apply_backend_weapon_reference, build_v2_weapon_add_update
from .inventory_v2 import encode_client_item_state_updates_field


class _Ctx:
    def __init__(self, schema):
        self.schema, self._names = schema, {}

    def known(self):
        return self._names

    def names(self, g):
        return self._names[g]

    def comp_class(self, actor_cls, g):
        n = self._names.get(g, (None, None))[1]
        if not n:
            return None
        found = self.schema.member_class(actor_cls, n)
        if found:
            return found
        # Subobjects the class dump does not list as members (e.g. the
        # controller's TimeTranslation): try the conventional component class
        # names. model.parse only keeps the decode if it is bit-exact.
        for candidate in ('Archon%sComponent' % n, '%sComponent' % n, n):
            if candidate in self.schema.inherit:
                return candidate
        return None


def _should_clear_unresolved_replay_banner(environ=None):
    """Clear the template's foreign banner reference (MPGS_PRESERVE_REPLAY_BANNER=1 keeps it)."""
    env = os.environ if environ is None else environ
    return env.get('MPGS_PRESERVE_REPLAY_BANNER') != '1'


class Entry:
    __slots__ = ('t', 'ch', 'parsed', 'cls', 'exports', 'open', 'close', 'reliable', 'must_map', 'patch_pairs')


def _defer_inventory_probe(entries, delay_ms):
    """Publish a staged diagnostic row after world setup, as a normal actor update."""
    if type(delay_ms) is not int or not 0 < delay_ms <= 30000:
        raise ValueError('inventory probe delay must be 1..30000 ms')
    targets = [(e, b) for e in entries for b in e.parsed.blocks
               if b.cls_name == 'bp_archon_inventory_C' and b.values is not None
               and 'ReplicatedItems' in b.values]
    if len(targets) != 1:
        raise ValueError('expected one staged inventory row block')
    source, block = targets[0]
    if not block.actor:
        raise ValueError('inventory update requires the owning actor block')
    update = copy.copy(source)
    update.t = max(e.t for e in entries) + delay_ms
    update.open = update.close = update.must_map = False
    update.reliable = True
    update.exports = []
    update.parsed = model.Parsed()
    late_block = copy.deepcopy(block)
    late_block.values = {'ReplicatedItems': copy.deepcopy(block.values['ReplicatedItems']),
                         'ExpectedNumItems': block.values['ExpectedNumItems']}
    late_block.tail = (0, 0)
    late_block.fields = []
    update.parsed.blocks = [late_block]
    # Keep the accepted empty initial inventory; the later update changes its
    # rows, so OnRep is eligible to run again after the local loader settles.
    del block.values['ReplicatedItems']
    block.values['ExpectedNumItems'] = 0
    entries.append(update)
    entries.sort(key=lambda e: e.t)
    return update.t


def _stage_v2_weapon_add_rpc(entries, schema, update, delay_ms):
    """Append one delayed owner RPC after the recorded inventory actor opens."""
    return _stage_v2_inventory_rpcs(entries, schema, [update], delay_ms)


def _stage_v2_inventory_rpcs(entries, schema, updates, delay_ms):
    """Append one reliable owner RPC per operation, 1 ms apart, in order.

    One operation per bunch keeps each RPC the size the live weapon test proved
    and avoids partial bunches; reliable delivery on one channel keeps order.
    """
    if type(delay_ms) is not int or not 0 < delay_ms <= 30000:
        raise ValueError('V2 inventory RPC delay must be 1..30000 ms')
    field_index = next((i for i, item in enumerate(schema.field_index('bp_archon_inventory_C'))
                        if item == ('func', 'ClientHandleItemStateUpdates')), None)
    if not updates:
        raise ValueError('no V2 inventory operations to stage')
    fields = [encode_client_item_state_updates_field([update]) for update in updates]
    if field_index is None or field_index != fields[0][0]:
        raise ValueError('SDK RPC field index does not match ClientHandleItemStateUpdates encoder')
    inventory_entries = [(entry, block) for entry in entries for block in entry.parsed.blocks
                         if entry.ch >= 0 and block.actor and block.cls_name == 'bp_archon_inventory_C']
    if not inventory_entries:
        raise ValueError('recording has no ArchonInventory actor channel')
    source_entry, _ = inventory_entries[0]
    base = max(entry.t for entry in entries) + delay_ms
    for i, field in enumerate(fields):
        _append_inventory_rpc(entries, source_entry, field, base + i)
    entries.sort(key=lambda entry: entry.t)
    return base


def _append_inventory_rpc(entries, source_entry, field, t):
    rpc = Entry()
    rpc.t = t
    rpc.ch, rpc.cls, rpc.exports = source_entry.ch, 'bp_archon_inventory_C', []
    rpc.open = rpc.close = rpc.must_map = False
    rpc.reliable = True
    rpc.patch_pairs = ()
    rpc.parsed = model.Parsed()
    block = model.Block()
    block.actor, block.rep, block.guid = True, False, 0
    block.stable = block.destroy = False
    block.cls = block.outer = None
    block.cls_name = 'bp_archon_inventory_C'
    block.values = block.tail = block.raw = None
    block.fields = [field]
    rpc.parsed.blocks = [block]
    entries.append(rpc)


class GenWorld:
    _next_guid_base = int(os.environ.get('MPGS_GUID_START', '1000'))   # dynamic GUIDs of each connection are allocated from here (even), 256 apart

    def __init__(self, recording, recorded_key_hex, schema=None):
        src = ReplayWorld(recording, recorded_key_hex)
        self.old_ids = src.old_ids
        self.schema = schema or Schema()
        self.ctx = _Ctx(self.schema)
        self.entries = []
        chans, cls_of = {}, {}
        for t, b in src.events:
            full = chans.setdefault(b.ch, Reassembler()).feed(b)
            if not full:
                continue
            for root in full.exports:
                for i in flatten(root):
                    if i.name is not None:
                        self.ctx._names[i.guid] = (i.outer, i.name)
            p, cls = model.parse(full, self.ctx, cls_of.get(b.ch))
            cls_of[b.ch] = cls
            e = Entry()
            e.t, e.ch, e.parsed, e.cls, e.exports = t, b.ch, p, cls, full.exports
            e.open, e.close, e.reliable, e.must_map = full.open, full.close, full.reliable, full.must_map
            e.patch_pairs = ()
            self.entries.append(e)

    @classmethod
    def from_template(cls, path, schema=None):
        """Build the world from a readable template file (mpgs.template)."""
        from . import template as tpl
        data = tpl.read_template(path)
        self = cls.__new__(cls)
        self.old_ids = dict(data['identities'])
        self.schema = schema or Schema()
        self.ctx = _Ctx(self.schema)
        for g, o, n in data['names']:
            self.ctx._names[g] = (o, n)
        self.entries = tpl.load_entries(data, Entry)
        self.template_path = path
        return self

    def new_session(self, live_ids):
        return GenSession(self, live_ids)

    @property
    def events(self):
        return self.entries


class GenSession(ReplaySession):
    """One player's copy of the world. MPGS_GUID_SHIFT=<even int> forces a pure GUID offset (testing aid: proves the remap is complete)."""

    def __init__(self, world, live_ids):
        self.world = world
        self.pos = 0
        self.patch_hits = 0
        self.pairs = []
        text_pairs = []
        for k in ('name', 'char', 'acct', 'session'):
            old, new = world.old_ids.get(k), live_ids.get(k)
            if old and new and old != new:
                text_pairs.append((old, new))
                if len(old) == len(new):
                    self.pairs.append((old.encode(), new.encode()))
        shift = os.environ.get('MPGS_GUID_SHIFT')
        if shift:
            m = remap.GuidMap(shift=int(shift))
        else:
            m = remap.GuidMap(start=GenWorld._next_guid_base)
            self.guid_base = GenWorld._next_guid_base
            GenWorld._next_guid_base += 256
        self.entries, self.stats = remap.instantiate(world.entries, m, text_pairs)
        if os.environ.get('MPGS_WEAPON_SPAWN_AT_ORIGIN', '1') == '1':
            from .initialization import spawn_weapons_at_origin
            moved = spawn_weapons_at_origin(self.entries, world.schema)
            print('WEAPON spawn transform zeroed=%s' % (','.join(moved) or 'none'), flush=True)
        if os.environ.get('MPGS_INITIAL_DATA') == '1':
            from .initialization import load_snapshots, apply_snapshots
            try:
                snapshots = load_snapshots(live_ids.get('acct'), auth_token=live_ids.get('auth_token'))
                count = apply_snapshots(self.entries, world.schema, snapshots)
                print('INIT applied %d backend component snapshots' % count, flush=True)
            except Exception as exc:
                # Do not claim data arrived or erase player data after a failed fetch.
                print('INIT unavailable (%s); recorded world preserved' % type(exc).__name__, flush=True)
        self.player_records = None
        dev_items = os.environ.get('MPGS_DEV_ITEMS', '')
        if dev_items and live_ids.get('acct') and live_ids.get('char'):
            # dev test characters: one-time starter stacks (deterministic transaction id per account -> idempotent)
            from . import rewards
            from .initialization import _backend_headers
            items = [(k, int(v)) for k, v in (x.split('=') for x in dev_items.split(',') if '=' in x)]
            body = rewards.build_transaction(live_ids['acct'], live_ids['char'], '%s:dev-items:%s' % (live_ids['acct'], dev_items), items)
            ok, detail = rewards.post_transaction(body, _backend_headers(live_ids.get('auth_token')))
            print('DEV items %s -> %s' % (items, detail), flush=True)
        if os.environ.get('MPGS_PLAYER_DATA') == '1':
            from .initialization import load_player_records, load_character_profile, apply_character_profile
            try:
                self.player_records = load_player_records(
                    live_ids.get('acct'), live_ids.get('char'), auth_token=live_ids.get('auth_token'))
                inv = self.player_records['inventory']
                print('PLAYERDATA fetched instanced=%d stacked=%d loadouts=%d active=%s' % (
                    len(inv['instancedItems']), len(inv['stackedItems']), len(self.player_records['loadouts']),
                    self.player_records['active_index'] if self.player_records['active_loadout'] is not None else 'none'), flush=True)
                row_only = os.environ.get('MPGS_TEST_INVENTORY_ROW_ONLY') == '1'
                equip_all = os.environ.get('MPGS_TEST_INVENTORY_EQUIPMENT') == '1'
                v2_weapon = os.environ.get('MPGS_TEST_INVENTORY_V2_WEAPON') == '1'
                if os.environ.get('MPGS_TEST_INVENTORY_V2_ALL') == '1':
                    from .initialization import apply_backend_equipment_references, build_v2_inventory_add_updates
                    updates = build_v2_inventory_add_updates(
                        self.player_records, include_stacks=os.environ.get('MPGS_TEST_INVENTORY_V2_STACKS', '1') == '1')
                    refs = apply_backend_equipment_references(self.entries, self.player_records)
                    self.inventory_v2_updates = updates
                    stacks = sum(1 for u in updates if u['instanceId'] not in
                                 {it['instanceId'] for it in self.player_records['inventory']['instancedItems']})
                    print('INVENTORY V2 full sync prepared items=%d (stacks=%d) equipped_slots=%s legacy_rows=0' % (
                        len(updates), stacks, ','.join(sorted(refs))), flush=True)
                    if os.environ.get('MPGS_EQUIP_WEAPON_CLASS', '1') == '1':
                        from . import loadout
                        active = self.player_records.get('active_loadout') or {}
                        for slot, name in sorted(loadout.SLOT_NAMES.items()):
                            item = (active.get(name) or {}).get('item_id')
                            if not item:
                                continue
                            swapped = loadout.swap_equipment_actor(self.entries, world.schema, slot, item)
                            print('EQUIP %s actor %s' % (name, '%s -> %s (%s)' % (swapped[0], swapped[1], item)
                                                         if swapped else 'unchanged (%s)' % item), flush=True)
                    if os.environ.get('MPGS_EQUIPMENT_ACTOR_IDENTITY', '1') == '1':
                        from .initialization import apply_equipment_actor_identity
                        actors = apply_equipment_actor_identity(self.entries, world.schema, self.player_records)
                        print('EQUIPMENT actor identity applied=%s' % (
                            ','.join('%s:%d' % a for a in actors) or 'none'), flush=True)
                elif v2_weapon or os.environ.get('MPGS_TEST_INVENTORY_BACKEND_IDS') == '1':
                    identity = apply_backend_weapon_reference(self.entries, self.player_records)
                    if v2_weapon:
                        self.inventory_v2_weapon_update = build_v2_weapon_add_update(
                            self.player_records, quantity=1, custom_data='')
                        print('INVENTORY V2 one-weapon operation prepared catalog=%s weapon_instance_crc=%08X quantity=1 legacy_rows=0' % (
                            identity['catalog'], identity['instance_crc']), flush=True)
                    else:
                        print('INVENTORY V2 reference-only catalog=%s weapon_instance_crc=%08X legacy_rows=0' % (
                            identity['catalog'], identity['instance_crc']), flush=True)
                elif equip_all or row_only or os.environ.get('MPGS_TEST_INVENTORY_WEAPON') == '1':
                    from .initialization import apply_equipment_inventory_test
                    fixture = apply_equipment_inventory_test(
                        self.entries, world.schema, self.player_records,
                        slots=None if equip_all else ['weapon'], update_slot=not row_only)
                    self.inventory_test_aliases = fixture['aliases']
                    self.inventory_test_reverse = fixture['reverse']
                    stage = 'row_only' if row_only else 'row_and_slot'
                    print('INVENTORY TEST staged rows=%d slots=%s quantity=1 mode=%s (experimental)' % (
                        len(fixture['rows']), ','.join(fixture['published_slots']), stage), flush=True)
            except Exception as exc:
                # Profile state is account-specific; never substitute another
                # profile or alter the known-good recorded equipment on failure.
                # The exception text contains only the transport status or our
                # fixed validation diagnostic; request headers (including the
                # bearer) are never included. Keeping the reason visible makes
                # backend/auth/shape failures actionable without dumping data.
                reason = str(exc).replace('\n', ' ')[:180]
                print('PLAYERDATA unavailable (%s: %s); recorded equipment preserved' % (
                    type(exc).__name__, reason), flush=True)
            try:
                profile = load_character_profile(
                    live_ids.get('acct'), live_ids.get('char'), auth_token=live_ids.get('auth_token'))
                self.character_profile = profile
                count = apply_character_profile(self.entries, world.schema, profile, live_ids.get('char'))
                print('PLAYERPROFILE applied=%d appearance=account' % count, flush=True)
            except Exception as exc:
                # Appearance is account-specific too. If lookup or validation
                # fails, keep the captured PlayerState byte-for-byte intact.
                print('PLAYERPROFILE unavailable (%s); recorded appearance preserved' % type(exc).__name__, flush=True)
            if os.environ.get('MPGS_SERVER_VALUES', '1') == '1' and self.player_records is not None:
                from . import server_values
                from .initialization import _backend_headers
                try:
                    ents = server_values.load_entitlements(headers=_backend_headers(live_ids.get('auth_token')))
                except Exception as exc:
                    ents = None
                    print('SERVERVALUES entitlements unavailable (%s); template entitlements kept' % type(exc).__name__, flush=True)
                try:
                    report = server_values.apply_all(self.entries, self.player_records, ents, time.time())
                    print('SERVERVALUES applied %s' % report, flush=True)
                except Exception as exc:
                    print('SERVERVALUES failed (%s: %s)' % (type(exc).__name__, str(exc)[:160]), flush=True)
        if os.environ.get('MPGS_PROGRESSION', '1') == '1':
            try:
                self._stage_progression(live_ids)
            except Exception as exc:
                print('PROGRESSION failed (%s: %s)' % (type(exc).__name__, str(exc)[:160]), flush=True)
        if os.environ.get('MPGS_JOURNEY_PERSIST', '1') == '1':
            try:
                self._stage_journey(live_ids)
            except Exception as exc:
                print('JOURNEY restore failed (%s: %s)' % (type(exc).__name__, str(exc)[:160]), flush=True)
        if os.environ.get('MPGS_QUESTS', '1') == '1':
            try:
                self._stage_quests(base_guid=getattr(self, 'guid_base', 1000) * 32)
            except Exception as exc:
                print('QUESTS failed (%s: %s)' % (type(exc).__name__, str(exc)[:160]), flush=True)
        # The captured banner item belongs to another account and has no
        # matching inventory row here. The stock client crashes when menus
        # dereference its unresolved CachedBanner. Clear the stale replay
        # reference by default; keep an opt-out for controlled comparisons.
        if _should_clear_unresolved_replay_banner():
            from .initialization import suppress_replay_banner
            count = suppress_replay_banner(self.entries)
            print('REPLAY unresolved banner references cleared=%d' % count, flush=True)
        for e in self.entries:
            e.patch_pairs = self.pairs                # raw (undecodable) blocks carry THIS player's identity, wherever they are shown
        self.guid_map = m
        # names of this session's (remapped) GUIDs, for decoding what the client sends back
        self.ctx = _Ctx(world.schema)
        for g, (o, n) in world.ctx._names.items():
            self.ctx._names[m(g)] = (m(o) if o else o, n)
        self.chan_cls = {e.ch: e.cls for e in self.entries if e.ch < 20 and e.cls}
        if os.environ.get('MPGS_RAMSGATE_ATMOSPHERE') == '1':
            entry = _ramsgate_atmosphere_entry(world, self.entries)
            self.entries.append(entry)
            self.entries.sort(key=lambda e: e.t)
            self.chan_cls[entry.ch] = entry.cls
            self.ctx._names[entry.parsed.open['guid']] = (5, 'atmosphere_spawner_bp_3')
        if hasattr(self, 'inventory_v2_updates'):
            delay = int(os.environ.get('MPGS_TEST_INVENTORY_DELAY_MS', '5000'))
            at = _stage_v2_inventory_rpcs(self.entries, world.schema, self.inventory_v2_updates, delay)
            print('INVENTORY V2 full sync: %d AddItem RPCs staged reliably from %d ms after join' % (
                len(self.inventory_v2_updates), at), flush=True)
        if hasattr(self, 'inventory_v2_weapon_update'):
            delay = int(os.environ.get('MPGS_TEST_INVENTORY_DELAY_MS', '5000'))
            at = _stage_v2_weapon_add_rpc(self.entries, world.schema,
                                          self.inventory_v2_weapon_update, delay)
            print('INVENTORY V2 one-weapon AddItem RPC staged reliably at %d ms after join' % at, flush=True)
        if hasattr(self, 'inventory_test_aliases'):
            delay = int(os.environ.get('MPGS_TEST_INVENTORY_DELAY_MS', '0'))
            if delay:
                at = _defer_inventory_probe(self.entries, delay)
                print('INVENTORY TEST deferred row update to %d ms after join' % at, flush=True)
        self._rx = {}
        self.pawn_state = None              # latest authoritative movement from this player's client
        self._prev_move = None
        self.remote_pawn_ch = {}            # id(other session) -> channel that shows other's pawn on THIS connection
        self.remote = {}                    # id(other session) -> list of channels used for that player on THIS connection
        self.remote_comp = {}               # id(other session) -> header (guid, stable, cls, outer) of that pawn's player_state_machine component
        self._loco_sent = {}                # id(other session) -> sent state-machine state (see remote_state)
        self._facing = {}                   # id(other session) -> last facing yaw shown for that pawn
        self.name = live_ids.get('name')
        self.live_ids = live_ids
        self._main_queue = []


    def _stage_journey(self, live_ids):
        """Saved Slayer's Path nodes: unlocked set for the rules, and one ClientHandlePlayerJourneyUpdates right
        after the journey component's first bunch."""
        from . import journey
        from .initialization import _backend_headers
        if not live_ids.get('acct'):
            return
        saved = journey.load_unlocked(live_ids['acct'], _backend_headers(live_ids.get('auth_token')))
        self.journey_unlocked = {journey.ROOT} | saved
        restore = saved - {journey.ROOT}
        sch, hdr = self._server_header('player_journey_bpc_C')
        if not restore or hdr is None:
            print('JOURNEY restored %d nodes' % len(restore), flush=True)
            return
        at = next(k for k, e in enumerate(self.entries) if hdr in e.parsed.blocks)
        pos, self.pos = self.pos, at + 1
        try:
            self._queue_rpc(sch, hdr, 'ClientHandlePlayerJourneyUpdates', {'PlayerJourneyUpdates': journey.update_elems(restore)})
            self.entries[at + 1].t = self.entries[at].t
        finally:
            self.pos = pos + (1 if pos > at else 0)
        print('JOURNEY restored %d nodes: %s' % (len(restore), ','.join(sorted(restore))), flush=True)

    def _stage_progression(self, live_ids):
        """Player / weapon levels from the backend's stored XP (mpgs/progression.py)."""
        from . import progression
        from .initialization import _backend_headers
        if not live_ids.get('acct'):
            return
        curves = progression.load_curves()
        hdrs = _backend_headers(live_ids.get('auth_token'))
        progress = progression.load_progress(live_ids['acct'], hdrs)
        dev = int(os.environ.get('MPGS_DEV_LEVEL', '0') or 0)
        if dev > 1:                      # dev test character: raise player + weapon tracks to this level
            granted = progression.grant_to_level(live_ids['acct'], curves, progress, dev, hdrs)
            if granted:
                print('PROGRESSION dev level %d granted %s' % (dev, granted), flush=True)
                progress = progression.load_progress(live_ids['acct'], hdrs)
        weapon = (((self.player_records or {}).get('active_loadout') or {}).get('weapon') or {}).get('item_id')
        pp, report = progression.apply(self.entries, curves, progress, weapon)
        self.prog_curves, self.prog_progress = curves, dict(progress)
        hdr = ch = None
        idx = 0
        for k, e in enumerate(self.entries):
            for b in e.parsed.blocks:
                if b.cls_name == 'progression_bpc_C' and not b.actor and hdr is None:
                    hdr, ch, idx = b, e.ch, k
        if hdr is not None and pp['count']:
            blk = model.Block()
            blk.actor, blk.rep, blk.guid, blk.stable, blk.destroy = False, True, hdr.guid, hdr.stable, False
            blk.cls, blk.outer, blk.ref, blk.ref_name = hdr.cls, hdr.outer, None, None
            blk.cls_name = 'progression_bpc_C'
            v = OrderedFields()
            v['PlayerProgress'] = pp
            blk.values, blk.tail, blk.raw, blk.fields = v, None, None, None
            blk.partial_properties = False
            e = Entry()
            e.t, e.ch, e.cls, e.exports = self.entries[idx].t + 1, ch, self.entries[idx].cls, []
            e.open = e.close = e.must_map = False
            e.reliable = True
            e.patch_pairs = ()
            e.parsed = model.Parsed()
            e.parsed.blocks = [blk]
            pos = next((k for k, x in enumerate(self.entries) if x.t > e.t), len(self.entries))
            self.entries.insert(pos, e)
        print('PROGRESSION applied %s (weapon %s)' % (report, weapon), flush=True)

    def _stage_quests(self, base_guid):
        """Replicate quest state (mpgs/quests.py) right after the controller's QuestSystemComponent exists."""
        from . import quests
        hdr = ch = cls = None
        idx = 0
        for k, e in enumerate(self.entries):
            for b in e.parsed.blocks:
                if b.cls_name == 'QuestSystemComponent' and not b.actor and hdr is None:
                    hdr, ch, cls, idx = b, e.ch, e.cls, k
        pkg = None
        for e in self.entries:
            for r in e.exports:
                for i in flatten(r):
                    if i.name == '/Script/Archon' and pkg is None:
                        pkg = i
        if hdr is None or pkg is None:
            print('QUESTS no QuestSystemComponent / package export; skipped', flush=True)
            return
        data = None
        prof = getattr(self, 'character_profile', None)
        if isinstance(prof, dict):
            data = prof.get('data')
            if isinstance(data, str):
                data = json.loads(data)
        progress = quests.merge_progress(quests.load_baseline(), data)
        series = quests.build_objects(quests.load_catalog(), progress)
        start = base_guid & ~1                      # 32x the connection's base: 8192 GUIDs per connection, clear of remapped ones
        counter = [start]

        def nxt():
            counter[0] += 2
            return counter[0]
        t = max(self.entries[idx].t, 0) + 1
        entry, report = quests.build_entry(series, hdr, ch, cls, pkg, nxt, t)
        self.quest_channel = ch
        self.quest_meta = report.pop('meta')
        ids = {q['guid']: q['id'] for s_ in quests.load_catalog() for q in s_['quests']}
        self.quest_by_id = {ids.get(qg): g for g, (sid, qg) in self.quest_meta.items() if ids.get(qg)}
        self.quest_blocks = {b.guid: b for b in entry.parsed.blocks if b.cls_name == 'Quest'}
        pos = next((k for k, e in enumerate(self.entries) if e.t > t), len(self.entries))
        self.entries.insert(pos, entry)
        print('QUESTS staged %s at %d ms (guids %d..%d)' % (report, t, start + 2, counter[0]), flush=True)

    # -------------------------------------------------------------------------------- client -> server
    def on_client_bunch(self, b):
        """Decode one bunch the client sent on an actor channel. -> list of (channel, class, block-description, [(field name, kind, payload)])."""
        out = []
        f = self._rx.setdefault(b.ch, Reassembler()).feed(b)
        if not f:
            return out
        for root in f.exports:
            for i in flatten(root):
                if i.name is not None:
                    self.ctx._names[i.guid] = (i.outer, i.name)
        try:
            p, _ = model.parse(f, self.ctx, self.chan_cls.get(b.ch), from_client=True)
        except Exception as e:                       # undecodable: report raw size (and dump the bits for diagnosis)
            if getattr(self, '_dbg_unparsed', 0) < 60:
                self._dbg_unparsed = getattr(self, '_dbg_unparsed', 0) + 1
                import traceback
                tb = traceback.extract_tb(e.__traceback__)[-1]
                print('UNPARSED ch%d cls=%s %s(%s) at %s:%d nbits=%d bits(LSB-first)=%s' % (
                    b.ch, self.chan_cls.get(b.ch), type(e).__name__, e, tb.name, tb.lineno, f.nbits,
                    bin(f.bits)[2:].zfill(f.nbits)[::-1][:600]), flush=True)
            return [(b.ch, self.chan_cls.get(b.ch), 'unparsed %s' % type(e).__name__, [])]
        pending_recovery = None
        for blk in p.blocks:
            who = 'actor' if blk.actor else self.ctx._names.get(blk.guid, (0, 'guid%s' % blk.guid))[1]
            if not blk.actor and blk.cls_name is None and getattr(self, '_dbg_unres', 0) < 80:       # diagnosis: client RPC on a component we cannot name
                self._dbg_unres = getattr(self, '_dbg_unres', 0) + 1
                sm = [(g, n) for g, (o, n) in self.ctx._names.items() if n and ('state_machine' in n or 'AbilitySystem' in n)]
                bits_ = bin(f.bits)[2:].zfill(f.nbits)[::-1]
                print('UNRES ch%d guid=%s stable=%s cls=%s outer=%s raw=%s bunch=%d bits LSB-first: %s' % (b.ch, blk.guid, blk.stable, blk.cls, blk.outer,
                      None if blk.raw is None else blk.raw[1], f.nbits, bits_[:200]), flush=True)
            if blk.cls_name == 'player_state_machine_bpc_C' and getattr(self, '_dbg_psm', 0) < 40:     # diagnosis: owner's state-machine RPCs
                self._dbg_psm = getattr(self, '_dbg_psm', 0) + 1
                if blk.raw is not None:
                    print('PSM raw nbits=%d bits(LSB-first)=%s' % (blk.raw[1], bin(blk.raw[0])[2:].zfill(blk.raw[1])[::-1]), flush=True)
                else:
                    print('PSM fields=%s values=%s' % ([(x[0], x[1], x[3]) for x in (blk.fields or [])], None if blk.values is None else list(blk.values)), flush=True)
            out.append((b.ch, blk.cls_name, who, [(x[1], x[2], x[3]) for x in (blk.fields or [])]))
            if os.environ.get('MPGS_INTERACTION_REPLY', '1') == '1':
                for x in blk.fields or []:
                    if x[2] != 'rpc':
                        continue
                    if blk.cls_name == 'base_interactionusercode_component_C' and x[1] == 'ServerStartInteraction':
                        self._reply_interaction(b.ch, blk, x[3])
                    elif blk.cls_name == 'player_journey_bpc_C' and x[1] == 'ServerUnlockNode':
                        self._reply_journey_unlock(b.ch, blk, x[3])
                    elif blk.cls_name == 'QuestSystemComponent' and x[1] in ('ServerRedeemQuest', 'ServerStartQuest'):
                        self._set_quest_status(x[3].get('Quest'), 3 if x[1] == 'ServerRedeemQuest' else 1, x[1])
                    elif blk.cls_name == 'progression_bpc_C' and x[1] == 'Request Pending Awards from Server':
                        self._reply_pending_progression_awards(b.ch, blk)
                    elif blk.cls_name == 'HuntPassComponent' and x[1] == 'ServerClaimSelectedHuntPassRewards':
                        self._reply_huntpass_claim(b.ch, blk)
                    elif blk.cls_name == 'player_state_machine_bpc_C' and x[1] == 'Server_UpdateDefaultPlayerState':
                        cur, prev = x[3].get('DefaultPlayerState'), x[3].get('PreviousDefaultPlayerState')
                        if cur is not None and cur != getattr(self, '_psm_default', None):
                            self._psm_prev_default = prev if prev is not None else getattr(self, '_psm_default', 0)
                            self._psm_default = cur
                            if getattr(self, '_recovering', None) == b.ch:      # recovery answered before the state change arrived
                                self._recovering = None
                                self._restore_state(b.ch)
                    elif blk.actor and x[1] in ('ServerCraftItemFromCatalogData', 'ServerPurchaseItemFromCatalog'):
                        self._craft('craft' if x[1].startswith('ServerCraft') else 'purchase', x[3])
                    elif blk.actor and x[1] == 'ServerAddItemProgressionXPFromItems':
                        self._infuse(b.ch, blk.cls_name, x[3])
                    elif blk.actor and x[1] == 'ServerInternalAssignItem':
                        self._assigns = getattr(self, '_assigns', []) + [
                            (x[3].get('SlotIndex', 0), x[3].get('SlotType'), x[3].get('InstanceId', ''))]
                    elif blk.actor and x[1] == 'ServerCommitLoadout':
                        self._commit_loadout()
                    elif blk.actor and x[1] == 'ServerRecoverFromFall':
                        pending_recovery = (b.ch, blk.cls_name)     # after the whole bunch: it also carries the state change
                    elif blk.cls_name == 'scheduler_bpc_C' and x[1] == 'UpdateSchedules':
                        self._reply_schedule(b.ch, blk)
        if pending_recovery:
            self._edge_recovery(*pending_recovery)
        return out

    def _reply_pending_progression_awards(self, ch, request_block):
        """Complete the 2.1.1 progression-award request without inventing rewards.

        The SDK marks this RPC as NetServer and pairs it with Show Awards on Client.
        The backend does not yet persist unclaimed award objects, so an explicit
        empty array is the safe state; it avoids leaving the claim UI waiting on an
        absent reply and does not grant or consume inventory.
        """
        self._queue_rpc(ch, request_block, 'Show Awards on Client', {'Awards': []})
        print('PROGRESSION Request Pending Awards from Server -> Show Awards on Client (0 saved awards)', flush=True)

    def _reply_huntpass_claim(self, ch, request_block):
        """ServerClaimSelectedHuntPassRewards: grant the free rewards of every rank above the confirmed one (one
        backend transaction), raise the confirmed rank, then V2 ops + the updated pass record + the completion."""
        import threading
        from . import huntpass, rewards
        from .initialization import _backend_headers
        ids = getattr(self, 'live_ids', {}) or {}
        pass_id = getattr(self, 'huntpass_id', None) or 'season19'
        path = huntpass.load_path(pass_id)
        if path is None or not (ids.get('acct') and ids.get('char')):
            self._main_queue.append(('huntpass', (ch, request_block, False, None, None)))
            print('HUNTPASS claim refused: %s' % ('no config for ' + pass_id if path is None else 'no player ids'), flush=True)
            return
        hdrs = _backend_headers(ids.get('auth_token'))

        def work():
            try:
                row = huntpass.load_rows(ids['acct'], hdrs).get(pass_id, {})
                xp, confirmed = int(row.get('progress') or 0), int(row.get('confirmed_fremium_rank') or 0)
                current, _ = huntpass.rank(path, xp)
                items, skipped = huntpass.claim_plan(path, confirmed, current)
                body = None
                if items:
                    body = rewards.build_transaction(ids['acct'], ids['char'],
                                                     huntpass.transaction_seed(ids['acct'], pass_id, confirmed, current), items)
                    ok, detail = rewards.post_transaction(body, hdrs)
                    if not ok:
                        print('HUNTPASS claim grant failed -> %s' % detail, flush=True)
                        self._main_queue.append(('huntpass', (ch, request_block, False, None, None)))
                        return
                if current > confirmed:
                    huntpass.confirm(ids['acct'], pass_id, current, hdrs)
                rec = huntpass.record(pass_id, path, xp, max(confirmed, current), int(row.get('confirmed_premium_rank') or 0))
                print('HUNTPASS claim %s ranks %d..%d granted %s%s' % (pass_id, confirmed + 1, current, items,
                      ' (not granted: %s)' % ', '.join(skipped) if skipped else ''), flush=True)
                self._main_queue.append(('huntpass', (ch, request_block, True, body, rec)))
            except Exception as exc:
                print('HUNTPASS claim failed (%s: %s)' % (type(exc).__name__, str(exc)[:120]), flush=True)
                self._main_queue.append(('huntpass', (ch, request_block, False, None, None)))
        threading.Thread(target=work, daemon=True).start()

    def _finish_huntpass_claim(self, ch, request_block, ok, body, rec):
        from . import rewards
        # inserted at the send position: queue in reverse (V2 ops, pass record, then the completion)
        self._queue_rpc(ch, request_block, 'ClientClaimSelectedHuntPassRewardsCompleted', {'bSuccess': ok, 'DynamicRewards': []})
        sch, hdr = self._server_header('HuntPassComponent')
        if ok and rec is not None and hdr is not None:
            b = model.Block()
            b.actor, b.rep, b.guid, b.stable, b.destroy = False, True, hdr.guid, hdr.stable, False
            b.cls, b.outer, b.ref, b.ref_name = hdr.cls, hdr.outer, None, None
            b.cls_name = 'HuntPassComponent'
            v = OrderedFields()
            v['ActiveHuntPassData'] = {'count': 1, 'elems': {0: rec}}
            b.values, b.tail, b.raw, b.fields = v, None, None, None
            b.partial_properties = False
            e = Entry()
            e.t, e.ch, e.cls, e.exports = 0, sch, self.chan_cls.get(sch), []
            e.open = e.close = e.must_map = False
            e.reliable = True
            e.patch_pairs = ()
            e.parsed = model.Parsed()
            e.parsed.blocks = [b]
            self.entries.insert(self.pos, e)
        if ok and body:
            if not hasattr(self, 'v2_state'):
                self.v2_state = rewards.state_from_updates(getattr(self, 'inventory_v2_updates', []))
            inv = next(((e.ch, e.cls) for e in self.entries for b in e.parsed.blocks
                        if b.actor and b.cls_name == 'bp_archon_inventory_C'), None)
            if inv is not None:
                self._queue_v2_ops(inv, rewards.v2_operations(self.v2_state, body))
        print('HUNTPASS claim reply success=%s' % ok, flush=True)

    def _server_header(self, cls_name):
        """This connection's own header (guid, stable, cls, outer) for a replicated component, from the sent world."""
        for e in self.entries:
            for blk in e.parsed.blocks:
                if blk.cls_name == cls_name and not blk.actor:
                    return e.ch, blk
        return None, None

    def _queue_rpc(self, ch, header, func, params):
        """Queue one reliable server->client RPC on `header`'s sub-object (path reference or replicated guid)."""
        cls = header.cls_name
        fi = self.world.schema.field_index(cls)
        idx = fi.index(('func', func))
        e = Entry()
        e.t, e.ch, e.cls, e.exports = 0, ch, self.chan_cls.get(ch), []
        e.open = e.close = e.must_map = False
        e.reliable = True
        e.patch_pairs = ()
        e.parsed = model.Parsed()
        blk = model.Block()
        blk.actor, blk.rep, blk.guid, blk.destroy = False, False, header.guid, False
        path = getattr(header, 'ref', None) is not None and header.guid == 1
        blk.stable = True if path else header.stable
        blk.cls, blk.outer = (None, None) if path else (header.cls, header.outer)
        blk.ref, blk.ref_name = (header.ref, header.ref_name) if path else (None, None)
        blk.cls_name = cls
        blk.values = blk.tail = blk.raw = None
        blk.partial_properties = False
        blk.fields = [(idx, func, 'rpc', params)]
        e.parsed.blocks = [blk]
        self.entries.insert(self.pos, e)

    def _set_quest_status(self, ref, status, why):
        """Quest property update (Status) on its dynamic sub-object; session-only until backend write-back."""
        g = ref[1] if isinstance(ref, tuple) and ref[0] == 'obj' else None
        src = getattr(self, 'quest_blocks', {}).get(g)
        if src is None:
            print('QUEST %s for unknown quest %s' % (why, ref), flush=True)
            return
        allowed = {3: (2,), 1: (4, 0)}.get(status, ())      # redeem needs Redeemable; start needs Available (Unavailable kept for quests the baseline never offered)
        cur = src.values.get('Status')
        if os.environ.get('MPGS_QUEST_RULES', '1') == '1' and cur not in allowed:
            print('QUEST %s quest=%d refused: status %s, needs %s' % (why, g, cur, allowed), flush=True)
            return
        src.values['Status'] = status
        b = model.Block()
        b.actor, b.rep, b.guid, b.stable, b.destroy = False, True, g, False, False
        b.cls, b.outer, b.ref, b.ref_name = src.cls, src.outer, None, None
        b.cls_name = 'Quest'
        v = OrderedFields()
        v['Status'] = status
        b.values, b.tail, b.raw, b.fields = v, None, None, None
        b.partial_properties = False
        e = Entry()
        e.t, e.ch, e.cls, e.exports = 0, self.quest_channel, self.chan_cls.get(self.quest_channel), []
        e.open = e.close = e.must_map = False
        e.reliable = True
        e.patch_pairs = ()
        e.parsed = model.Parsed()
        e.parsed.blocks = [b]
        self.entries.insert(self.pos, e)
        print('QUEST %s quest=%d -> Status %d' % (why, g, status), flush=True)
        meta = getattr(self, 'quest_meta', {}).get(g)
        ids = getattr(self, 'live_ids', {}) or {}
        if meta and ids.get('acct') and ids.get('char') and os.environ.get('MPGS_QUEST_REWARDS', '1') == '1':
            self._grant_quest_rewards(ids, meta, 'rewards' if status == 3 else 'on_accept')
        if meta and ids.get('acct') and ids.get('char') and os.environ.get('MPGS_QUEST_PERSIST', '1') == '1':
            import threading
            from . import quests
            from .initialization import _backend_headers

            def save():
                try:
                    code = quests.persist_status(ids['acct'], ids['char'], meta[0], meta[1], status,
                                                 _backend_headers(ids.get('auth_token')))
                    print('QUEST saved %s %s -> %d (HTTP %s)' % (meta[0], meta[1], status, code), flush=True)
                except Exception as exc:
                    print('QUEST save failed (%s: %s)' % (type(exc).__name__, str(exc)[:120]), flush=True)
            threading.Thread(target=save, daemon=True).start()

    def _quest_status(self, quest_id):
        # test only: MPGS_DEV_QUESTS=Q1,Q2 (or *) counts these quests as redeemed for unlock gates (crafting, Slayer's
        # Path); the quests themselves and the saved quest data are not changed
        dev = os.environ.get('MPGS_DEV_QUESTS', '')
        if dev and (dev.strip() == '*' or quest_id in {q.strip() for q in dev.split(',')}):
            return 3
        g = getattr(self, 'quest_by_id', {}).get(quest_id)
        b = getattr(self, 'quest_blocks', {}).get(g)
        return None if b is None else b.values.get('Status')

    def _grant_quest_rewards(self, ids, meta, kind):
        """Grant a quest's rewards (redeem) or accept rewards (start) through the backend, then show them with
        V2 inventory operations (queued from the worker thread, sent from the main loop)."""
        import threading
        from . import quests, rewards
        from .initialization import _backend_headers
        q = next((q for s_ in quests.load_catalog() if s_['id'] == meta[0] for q in s_['quests'] if q['guid'] == meta[1]), None)
        items, skipped = rewards.grant_items((q or {}).get(kind))
        if skipped:
            print('QUEST %s %s rewards not granted yet: %s' % (meta[1], kind, ','.join(sorted(set(skipped)))), flush=True)
        if not items:
            return
        body = rewards.build_transaction(ids['acct'], ids['char'], '%s:%s:%s' % (ids['acct'], meta[1], kind), items)

        def work():
            ok, detail = rewards.post_transaction(body, _backend_headers(ids.get('auth_token')))
            print('QUEST %s %s grant %s -> %s' % (q.get('id') if q else meta[1], kind, items, detail), flush=True)
            if ok:
                self._main_queue.append(('v2', body))
        threading.Thread(target=work, daemon=True).start()

    def _infuse(self, ch, cls, params):
        """Aetherite infusion (ServerAddItemProgressionXPFromItems): check the stacks, spend them and add the
        weapon-track XP in the backend, then V2 updates + ClientAddItemProgressionXP + refreshed weapon details."""
        import threading
        from . import progression, rewards
        from .initialization import _backend_headers
        item = params.get('ItemId') or ''
        names = [next(iter(e.values())) for e in params.get('ConsumeItems') or []]
        amounts = [int(next(iter(e.values()))) for e in params.get('ConsumeAmounts') or []]
        spends = [(n, a) for n, a in zip(names, amounts) if a > 0]
        track = progression.weapon_track(item)
        ids = self.live_ids or {}
        curves = getattr(self, 'prog_curves', None) or {}
        if not hasattr(self, 'v2_state'):
            self.v2_state = rewards.state_from_updates(getattr(self, 'inventory_v2_updates', []))
        owned = {r['instanceId']: r['quantity'] for r in self.v2_state}
        bad = [n for n, a in spends if n not in progression.AETHERITE_XP or owned.get(n, 0) < a]
        if track is None or track[0] not in curves or not spends or bad or not ids.get('acct'):
            print('INFUSE refused item=%s spends=%s (bad=%s track=%s)' % (item, spends, bad, track), flush=True)
            return
        curve = curves[track[0]]
        before = self.prog_progress.get(track[0], 0)
        xp = min(sum(progression.AETHERITE_XP[n] * a for n, a in spends), max(0, sum(curve) - before))
        hdrs = _backend_headers(ids.get('auth_token'))

        def work():
            body = {'accountId': ids['acct'], 'characterId': ids['char'],
                    'transactionId': __import__('uuid').uuid4().hex.upper(),
                    'removeStackedItems': [{'catalogId': n, 'quantity': a} for n, a in spends]}
            ok, detail = rewards.post_transaction(body, hdrs)
            if not ok:
                print('INFUSE spend refused %s -> %s' % (spends, detail), flush=True)
                return
            after = before
            if xp > 0:
                after = progression.grant_xp(ids['acct'], track[0], xp, hdrs) or before + xp
            self._main_queue.append(('infuse', (ch, cls, item, track, before, after, xp, spends)))
            print('INFUSE %s spent %s -> %s +%d XP (%d -> %d)' % (item, spends, track[0], xp, before, after), flush=True)
        threading.Thread(target=work, daemon=True).start()

    def _finish_infuse(self, ch, cls, item, track, before, after, xp, spends):
        from . import progression, rewards
        self.prog_progress[track[0]] = after
        curve = self.prog_curves[track[0]]
        grant = progression.experience_grant(curve, before, after, track[1], item, xp)
        inv = next(((e.ch, e.cls) for e in self.entries for b in e.parsed.blocks
                    if b.actor and b.cls_name == 'bp_archon_inventory_C'), None)
        # inserted at the send position: queue in reverse (V2 spends, then the XP reply, then weapon details)
        weapon = (((self.player_records or {}).get('active_loadout') or {}).get('weapon') or {}).get('item_id')
        wd = progression.weapon_details(self.prog_curves, self.prog_progress, weapon)
        if wd:
            ps = next(((e.ch, e.cls, b) for e in self.entries for b in e.parsed.blocks
                       if b.cls_name == 'player_state_bp_C' and b.actor and b.values is not None), None)
            if ps is not None:
                e = Entry()
                e.t, e.ch, e.cls, e.exports = 0, ps[0], ps[1], []
                e.open = e.close = e.must_map = False
                e.reliable = True
                e.patch_pairs = ()
                e.parsed = model.Parsed()
                blk = model.Block()
                blk.actor, blk.rep, blk.guid, blk.stable, blk.destroy = True, True, 0, False, False
                blk.cls = blk.outer = None
                blk.ref = blk.ref_name = None
                blk.cls_name = 'player_state_bp_C'
                v = OrderedFields()
                for k, val in wd.items():
                    v[k] = val
                blk.values, blk.tail, blk.raw, blk.fields = v, None, None, None
                blk.partial_properties = False
                e.parsed.blocks = [blk]
                self.entries.insert(self.pos, e)
        self._queue_actor_rpc(ch, cls, 'ClientAddItemProgressionXP', grant)
        if inv is not None:
            for op in reversed(rewards.v2_spend_operations(self.v2_state, spends)):
                field = encode_client_item_state_updates_field([op])
                self._queue_actor_rpc_field(inv[0], 'bp_archon_inventory_C', field)
        print('INFUSE reply level %s -> %s' % (grant['ExperienceGrant.PreviousLevel'], grant['ExperienceGrant.Level']), flush=True)

    def _commit_loadout(self):
        """ServerCommitLoadout: save the queued ServerInternalAssignItem changes to the backend loadout slot,
        then update the matching ArchonLoadoutItemSlot sub-objects (worker thread -> main queue)."""
        assigns, self._assigns = getattr(self, '_assigns', []), []
        ids = getattr(self, 'live_ids', {}) or {}
        if not assigns or not ids.get('acct') or not ids.get('char'):
            print('LOADOUT commit with nothing to save (%d assigns)' % len(assigns), flush=True)
            return
        import threading
        from . import loadout
        from .initialization import _backend_headers
        index = assigns[0][0] or 0

        def save():
            try:
                applied, rejected, code = loadout.commit(ids['acct'], ids['char'], index,
                                                         [(t, i) for _, t, i in assigns],
                                                         _backend_headers(ids.get('auth_token')))
                print('LOADOUT commit slot %d applied=%s rejected=%s (HTTP %s)' % (index, applied, rejected, code), flush=True)
                if code == 200:
                    by_slot = dict(applied)
                    cats = getattr(self, '_loadout_catalogs', {})
                    for _, t, i in assigns:
                        if by_slot.get(loadout.SLOT_NAMES.get(t)):
                            cats[i] = by_slot[loadout.SLOT_NAMES.get(t)]
                    self._loadout_catalogs = cats
                    self._main_queue.append(('loadout', [(t, i) for _, t, i in assigns
                                                         if loadout.SLOT_NAMES.get(t) in dict(applied)]))
            except Exception as exc:
                print('LOADOUT commit failed (%s: %s)' % (type(exc).__name__, str(exc)[:120]), flush=True)
        threading.Thread(target=save, daemon=True).start()

    def _finish_loadout(self, assigns):
        """Point each committed slot's ArchonLoadoutItemSlot.ItemInstanceID at the new item (property update)."""
        from .inventory_wire import item_identity_crc
        from . import loadout
        for slot_type, instance in assigns:
            src = next(((e.ch, b) for e in self.entries for b in e.parsed.blocks
                        if b.cls_name == 'ArchonLoadoutItemSlot' and b.values is not None
                        and b.values.get('ItemSlotType') == slot_type and b.rep), None)
            if src is None:
                print('LOADOUT no slot object for type %s' % slot_type, flush=True)
                continue
            ch, sb = src
            crc = item_identity_crc(instance) if instance else 0
            sb.values['ItemInstanceID.ItemInstanceIdCrc'] = crc
            b = model.Block()
            b.actor, b.rep, b.guid, b.stable, b.destroy = False, True, sb.guid, sb.stable, False
            b.cls, b.outer, b.ref, b.ref_name = sb.cls, sb.outer, None, None
            b.cls_name = 'ArchonLoadoutItemSlot'
            v = OrderedFields()
            v['ItemInstanceID.ItemInstanceIdCrc'] = crc
            b.values, b.tail, b.raw, b.fields = v, None, None, None
            b.partial_properties = False
            e = Entry()
            e.t, e.ch, e.cls, e.exports = 0, ch, self.chan_cls.get(ch), []
            e.open = e.close = e.must_map = False
            e.reliable = True
            e.patch_pairs = ()
            e.parsed = model.Parsed()
            e.parsed.blocks = [b]
            self.entries.insert(self.pos, e)
            print('LOADOUT %s slot -> instance crc %08X' % (loadout.SLOT_NAMES.get(slot_type), crc), flush=True)
            if instance and os.environ.get('MPGS_EQUIP_LIVE', '1') == '1':
                self._respawn_equipment(slot_type, instance)

    def _respawn_equipment(self, slot, instance):
        """Live equipment change: close the slot actor's channel and open a new actor of the equipped item's class."""
        from . import loadout
        name = loadout.SLOT_NAMES.get(slot)
        catalog = self._loadout_catalogs.get(instance) if hasattr(self, '_loadout_catalogs') else None
        if catalog is None:
            print('LOADOUT %s respawn skipped: catalog for %s unknown' % (name, instance), flush=True)
            return
        self._equip_cur = getattr(self, '_equip_cur', {})
        cur = self._equip_cur.get(slot) or loadout.equipment_entry(self.entries, self.world.schema, slot)
        if cur is None:
            print('LOADOUT %s respawn skipped: no actor for the slot' % name, flush=True)
            return
        self._live_guid = getattr(self, '_live_guid', self.guid_base * 32 + 16000)
        self._live_ch = getattr(self, '_live_ch', 900)

        def next_guid():
            self._live_guid += 2
            return self._live_guid
        res = loadout.respawn_equipment([cur], self.world.schema, slot, catalog, self._live_ch, next_guid)
        if res is None:
            print('LOADOUT %s respawn: nothing to change for %s' % (name, catalog), flush=True)
            return
        old_ch, ne, (old_cls, new_cls) = res
        self._live_ch += 1
        close = Entry()
        close.t, close.ch, close.cls, close.exports = 0, old_ch, cur.cls, []
        close.open, close.close, close.must_map, close.reliable = False, True, False, True
        close.patch_pairs = ()
        close.parsed = model.Parsed()
        close.parsed.blocks = []
        ne.open, ne.close, ne.reliable = True, False, True
        self.chan_cls[ne.ch] = ne.cls
        self.entries.insert(self.pos, ne)        # inserted at the send position: open goes after the close
        self.entries.insert(self.pos, close)
        self._equip_cur[slot] = ne
        if self.player_records and isinstance(self.player_records.get('active_loadout'), dict):
            self.player_records['active_loadout'][name] = {'item_id': catalog, 'instance_id': instance}
        print('LOADOUT %s respawn ch%d %s -> ch%d %s (%s)' % (name, old_ch, old_cls, ne.ch, new_cls, catalog), flush=True)

    def _queue_actor_rpc_field(self, ch, cls, field):
        e = Entry()
        e.t, e.ch, e.cls, e.exports = 0, ch, cls, []
        e.open = e.close = e.must_map = False
        e.reliable = True
        e.patch_pairs = ()
        e.parsed = model.Parsed()
        blk = model.Block()
        blk.actor, blk.rep, blk.guid, blk.stable, blk.destroy = True, False, 0, False, False
        blk.cls = blk.outer = None
        blk.ref = blk.ref_name = None
        blk.cls_name = cls
        blk.values = blk.tail = blk.raw = None
        blk.partial_properties = False
        blk.fields = [field]
        e.parsed.blocks = [blk]
        self.entries.insert(self.pos, e)

    def _queue_v2_ops(self, inv, ops):
        """V2 item-state RPCs on the inventory actor, in order (inserted at the send position: reverse)."""
        for op in reversed(ops):
            field = encode_client_item_state_updates_field([op])
            rpc = Entry()
            rpc.t, rpc.ch, rpc.cls, rpc.exports = 0, inv[0], 'bp_archon_inventory_C', []
            rpc.open = rpc.close = rpc.must_map = False
            rpc.reliable = True
            rpc.patch_pairs = ()
            rpc.parsed = model.Parsed()
            blk = model.Block()
            blk.actor, blk.rep, blk.guid, blk.stable, blk.destroy = True, False, 0, False, False
            blk.cls = blk.outer = None
            blk.ref = blk.ref_name = None
            blk.cls_name = 'bp_archon_inventory_C'
            blk.values = blk.tail = blk.raw = None
            blk.partial_properties = False
            blk.fields = [field]
            rpc.parsed.blocks = [blk]
            self.entries.insert(self.pos, rpc)

    def _craft(self, kind, params):
        """ServerCraftItemFromCatalogData / ServerPurchaseItemFromCatalog: check the recipe, gate and stacks,
        then one backend transaction (spend + add); V2 ops from the main loop once it is applied."""
        import threading
        from . import crafting, rewards
        from .initialization import _backend_headers
        item = params.get('ItemId') or ''
        payload = params.get('Payload') or {}
        qty = next((v for k, v in (payload.items() if isinstance(payload, dict) else ()) if k.endswith('Qty')), 1)
        ids = getattr(self, 'live_ids', {}) or {}
        if not hasattr(self, 'v2_state'):
            self.v2_state = rewards.state_from_updates(getattr(self, 'inventory_v2_updates', []))
        owned = {r['instanceId']: r['quantity'] for r in self.v2_state if not rewards.is_instanced(r['instanceId'])}
        recipes = getattr(self, '_recipes', None)
        if recipes is None:
            recipes = self._recipes = crafting.load_recipes() or {}
        ok, why, spends, grants = crafting.plan(recipes, item, kind, qty, owned, self._quest_status)
        if not ok or not (ids.get('acct') and ids.get('char')):
            print('CRAFT %s %s refused: %s' % (kind, item, why if not ok else 'no player ids'), flush=True)
            return
        body = crafting.build_transaction(ids['acct'], ids['char'], spends, grants)

        def work():
            done, detail = rewards.post_transaction(body, _backend_headers(ids.get('auth_token')))
            print('CRAFT %s %s spent %s granted %s -> %s' % (kind, item, spends, grants, detail), flush=True)
            if done:
                self._main_queue.append(('craft', body))
        threading.Thread(target=work, daemon=True).start()

    def _drain_main_queue(self):
        from . import rewards
        while self._main_queue:
            kind, body = self._main_queue.pop(0)
            if kind == 'infuse':
                self._finish_infuse(*body)
                continue
            if kind == 'loadout':
                self._finish_loadout(body)
                continue
            if kind == 'huntpass':
                self._finish_huntpass_claim(*body)
                continue
            if kind == 'craft':
                from . import crafting
                inv = next(((e.ch, e.cls) for e in self.entries for b in e.parsed.blocks
                            if b.actor and b.cls_name == 'bp_archon_inventory_C'), None)
                if inv is not None:
                    self._queue_v2_ops(inv, crafting.v2_operations(self.v2_state, body))
                    print('CRAFT V2 ops queued (%d items in client state)' % len(self.v2_state), flush=True)
                continue
            if kind != 'v2':
                continue
            if not hasattr(self, 'v2_state'):
                self.v2_state = rewards.state_from_updates(getattr(self, 'inventory_v2_updates', []))
            inv = next(((e.ch, e.cls) for e in self.entries for b in e.parsed.blocks
                        if b.actor and b.cls_name == 'bp_archon_inventory_C'), None)
            if inv is None:
                continue
            self._queue_v2_ops(inv, rewards.v2_operations(self.v2_state, body))
            print('INVENTORY V2 reward ops queued (%d items in client state)' % len(self.v2_state), flush=True)

    def _reply_journey_unlock(self, ch, req, params):
        """ServerUnlockNode(NodeID, RuntimeMapId): check parent / quests / currency, then
        ClientHandlePlayerJourneyUpdates([node -> Unlocked]). Refused requests get no reply."""
        from . import requirements
        node, rmap = params.get('NodeID'), params.get('RuntimeMapId')
        name = node[0] if isinstance(node, tuple) else node
        if not hasattr(self, 'journey_unlocked'):
            self.journey_unlocked = {'Slayer_00'}          # the template's journey state: root node unlocked
        rules = requirements.load_rules()
        if rules is not None and os.environ.get('MPGS_JOURNEY_RULES', '1') == '1':
            ok, why = requirements.check_node(rules, name, self.journey_unlocked, self._quest_status)
            if not ok and why != 'already unlocked':
                print('JOURNEY unlock node=%s refused: %s' % (name, why), flush=True)
                return
            costs = [] if why == 'already unlocked' else rules[name]['costs']
            ids = getattr(self, 'live_ids', {}) or {}
            if costs:
                if not (ids.get('acct') and ids.get('char')):
                    print('JOURNEY unlock node=%s refused: no player ids for the spend' % name, flush=True)
                    return
                from .initialization import _backend_headers
                paid, detail = requirements.spend(ids['acct'], ids['char'], costs, _backend_headers(ids.get('auth_token')))
                if not paid:
                    print('JOURNEY unlock node=%s refused: spend %s -> %s' % (name, costs, detail), flush=True)
                    return
                print('JOURNEY unlock node=%s paid %s (%s)' % (name, costs, detail), flush=True)
                from . import rewards
                if not hasattr(self, 'v2_state'):
                    self.v2_state = rewards.state_from_updates(getattr(self, 'inventory_v2_updates', []))
                inv = next(((e.ch, e.cls) for e in self.entries for b in e.parsed.blocks
                            if b.actor and b.cls_name == 'bp_archon_inventory_C'), None)
                if inv is not None:
                    self._queue_v2_ops(inv, rewards.v2_spend_operations(
                        self.v2_state, [(c['currency'], int(c['amount'])) for c in costs if int(c['amount']) > 0]))
        self.journey_unlocked.add(name)
        ids = getattr(self, 'live_ids', {}) or {}
        if ids.get('acct') and os.environ.get('MPGS_JOURNEY_PERSIST', '1') == '1':
            import threading
            from . import journey
            from .initialization import _backend_headers

            def save():
                try:
                    print('JOURNEY saved %s (HTTP %s)' % (name, journey.save_unlocked(
                        ids['acct'], name, _backend_headers(ids.get('auth_token')))), flush=True)
                except Exception as exc:
                    print('JOURNEY save failed (%s: %s)' % (type(exc).__name__, str(exc)[:120]), flush=True)
            threading.Thread(target=save, daemon=True).start()
        sch, hdr = self._server_header('player_journey_bpc_C')
        if hdr is None:
            return
        up = OrderedFields()
        for k, v in (('RuntimeMapId', rmap), ('NodeID', node), ('OperationType', 0), ('NodeStatus', 2),
                     ('ObjectiveOnlineId', ''), ('ObjectiveAmount', 0), ('ObjectiveStatus', 0)):
            up['PlayerJourneyUpdates[].' + k] = v
        self._queue_rpc(sch, hdr, 'ClientHandlePlayerJourneyUpdates', {'PlayerJourneyUpdates': [up]})
        print('JOURNEY unlock node=%s map=%s -> Unlocked' % (node, rmap), flush=True)

    def _reply_schedule(self, ch, req):
        """UpdateSchedules -> resend this session's (clock-rolled) ClientReceiveCurrentSchedule."""
        sch, hdr = self._server_header('scheduler_bpc_C')
        last = None
        for e in self.entries:
            for blk in e.parsed.blocks:
                for f in blk.fields or []:
                    if f[1] == 'ClientReceiveCurrentSchedule' and f[2] == 'rpc':
                        last = f[3]
        if hdr is None or last is None:
            return
        self._queue_rpc(sch, hdr, 'ClientReceiveCurrentSchedule', copy.deepcopy(last))
        print('SCHEDULE resent %d items' % len(last.get('CurrentScheduleItems', [])), flush=True)

    def _reply_interaction(self, ch, req, params):
        """Accept a ServerStartInteraction at once: ClientInteractionCompleted(target) on the same component."""
        cls = req.cls_name
        fi = self.world.schema.field_index(cls)
        idx = next(i for i, f in enumerate(fi) if f == ('func', 'ClientInteractionCompleted'))
        target = next(iter(params.values())) if isinstance(params, dict) else None
        e = Entry()
        e.t, e.ch, e.cls, e.exports = 0, ch, self.chan_cls.get(ch), []
        e.open = e.close = e.must_map = False
        e.reliable = True
        e.patch_pairs = ()
        e.parsed = model.Parsed()
        blk = model.Block()
        blk.actor, blk.rep, blk.guid, blk.stable, blk.destroy = False, False, req.guid, True, False
        blk.cls = blk.outer = None
        blk.ref, blk.ref_name = req.ref, req.ref_name
        blk.cls_name = cls
        blk.values = blk.tail = blk.raw = None
        blk.partial_properties = False
        blk.fields = [(idx, 'ClientInteractionCompleted', 'rpc', dict(params))]
        e.parsed.blocks = [blk]
        self.entries.insert(self.pos, e)
        name = target[3] if isinstance(target, tuple) and target[0] == 'objpath' else target
        print('INTERACTION start target=%s -> ClientInteractionCompleted on ch%d' % (name, ch), flush=True)

    # -------------------------------------------------------------------------------- movement relay
    def on_move(self, payload):
        """payload = raw (bits, nbits) of a ServerMovePacked RPC from this player's client."""
        mv = moves.parse_server_move_packed(*payload)
        if not mv:
            return None
        prev = self._prev_move
        vel = [0.0, 0.0, 0.0]
        if prev is not None:
            dt = mv['time'] - prev['time']
            if 0.001 < dt < 0.5:
                vel = [(mv['loc'][i] - prev['loc'][i]) / dt for i in range(3)]
        mv['vel'] = vel
        if mv['mode'] == 1 and abs(vel[2]) > 60.0:       # the client's move does not carry a falling mode; airborne = vertical speed (drives the remote anim's in-air state)
            mv['mode'] = 3
        self._prev_move = mv
        self.pawn_state = mv
        self._run_pending_edge()
        self._fall_guard(mv)
        return mv

    def _queue_actor_rpc(self, ch, cls, func, params):
        fi = self.world.schema.field_index(cls)
        e = Entry()
        e.t, e.ch, e.cls, e.exports = 0, ch, cls, []
        e.open = e.close = e.must_map = False
        e.reliable = True
        e.patch_pairs = ()
        e.parsed = model.Parsed()
        blk = model.Block()
        blk.actor, blk.rep, blk.guid, blk.stable, blk.destroy = True, False, 0, False, False
        blk.cls = blk.outer = None
        blk.ref = blk.ref_name = None
        blk.cls_name = cls
        blk.values = blk.tail = blk.raw = None
        blk.partial_properties = False
        blk.fields = [(fi.index(('func', func)), func, 'rpc', params)]
        e.parsed.blocks = [blk]
        self.entries.insert(self.pos, e)

    RECOVERY_STATE = 15        # EPlayerStatesDefault value the owner enters on an edge fall

    def _restore_state(self, ch):
        """Leave the recovery state with the server's multicast (the owner entered it locally on the fall)."""
        psm = next((b for e in self.entries if e.ch == ch for b in e.parsed.blocks
                    if b.cls_name == 'player_state_machine_bpc_C' and not b.actor), None)
        restore = getattr(self, '_psm_prev_default', None)
        if psm is None or restore is None:
            return
        self._queue_rpc(ch, psm, 'Multi_UpdateDefaultPlayerState',
                        {'DefaultPlayerState': restore, 'PreviousDefaultPlayerState': self._psm_default})
        print('EDGE state %s -> %s' % (self._psm_default, restore), flush=True)

    EDGE_TELEPORT_DELAY = 4.0  # client: EdgeRecoveryTeleportDelay (3.0 s, live CDO) then a ~1 s fade-out; move once the screen is dark

    def _edge_recovery(self, ch, cls):
        """Hold the request for the teleport delay; the next client move after it runs the recovery (the
        position correction must carry a current move timestamp)."""
        if getattr(self, '_pending_edge', None) is None and getattr(self, '_safe_loc', None) is not None:
            delay = float(os.environ.get('MPGS_EDGE_TELEPORT_DELAY', self.EDGE_TELEPORT_DELAY))
            self._pending_edge = (ch, cls, time.monotonic() + delay)

    def _run_pending_edge(self):
        pe = getattr(self, '_pending_edge', None)
        if pe is not None and time.monotonic() >= pe[2]:
            self._pending_edge = None
            self._last_rescue = 0.0
            self._do_edge_recovery(pe[0], pe[1])

    def _do_edge_recovery(self, ch, cls):
        """The game's own Edge Recovery: the client detects the fall (fade, camera) and asks
        ServerRecoverFromFall; the server teleports it back (TeleportOnOwningClient to the last grounded
        spot) and confirms with OnRecoverFromEdgeFall. The client repeats the request until answered."""
        safe = getattr(self, '_safe_loc', None)
        now = time.monotonic()
        if safe is None or now - getattr(self, '_last_rescue', 0.0) < 2.0:
            return
        self._last_rescue = now
        dest = [safe[0][0], safe[0][1], safe[0][2] + 50.0]
        # The client's scripted recovery fall waits for an authoritative position correction on its own
        # latest move (UE: server teleport -> ClientAdjustPosition), then OnRecoverFromEdgeFall fades back in.
        ts = (self._prev_move or {}).get('time', 0.0)
        # each queued RPC goes in at the send position, so queue in reverse: adjust, teleport, confirm, state
        # end the recovery like the original server: IsInFallRecovery (native, replicated) and the BP IsFalling
        # flag cleared on the pawn once the fade has played
        e = Entry()
        e.t, e.ch, e.cls, e.exports = getattr(self, '_elapsed', 0) + int(os.environ.get('MPGS_EDGE_CLEAR_MS', '1000')), ch, cls, []
        e.open = e.close = e.must_map = False
        e.reliable = True
        e.patch_pairs = ()
        e.parsed = model.Parsed()
        blk = model.Block()
        blk.actor, blk.rep, blk.guid, blk.stable, blk.destroy = True, True, 0, False, False
        blk.cls = blk.outer = None
        blk.ref = blk.ref_name = None
        blk.cls_name = cls
        v = OrderedFields()
        v['IsInFallRecovery'] = 0
        v['IsFalling'] = 0
        blk.values, blk.tail, blk.raw, blk.fields = v, None, None, None
        blk.partial_properties = False
        e.parsed.blocks = [blk]
        at = next((k for k in range(self.pos, len(self.entries)) if self.entries[k].t > e.t), len(self.entries))
        self.entries.insert(at, e)
        if getattr(self, '_psm_default', None) == self.RECOVERY_STATE:
            self._restore_state(ch)
        else:
            self._recovering = ch
        self._queue_actor_rpc(ch, cls, 'OnRecoverFromEdgeFall', {})
        self._queue_actor_rpc(ch, cls, 'TeleportOnOwningClient', {'DestLocation': dest, 'DestRotation': [0.0, safe[1][1], 0.0]})
        self._queue_actor_rpc(ch, cls, 'ClientAdjustPosition', {
            'Timestamp': ts, 'NewLoc': dest, 'NewVel': [0.0, 0.0, 0.0], 'NewBase': ('obj', 0),
            'NewBaseBoneName': ('ename', 0), 'bHasBase': 0, 'bBaseRelativePosition': 0, 'ServerMovementMode': 1})
        print('EDGE recovery %s -> %s (adjust ts=%.3f)' % (self.name, [round(x) for x in dest], ts), flush=True)

    def _fall_guard(self, mv):
        """Server-side out-of-world check (UE's CheckStillInWorld/KillZ only run on the server; Ramsgate's
        KillZ is disabled): a player falling more than MPGS_FALL_RESCUE_Z below their last grounded spot is
        put back there with ClientSetLocation."""
        drop = float(os.environ.get('MPGS_FALL_RESCUE_Z', '8000'))      # fallback only: Edge Recovery normally answers first
        if drop <= 0:
            return
        loc = mv['loc']
        if abs(mv['vel'][2]) < 60.0 and mv['mode'] == 1:
            self._safe_loc = (list(loc), list(mv['ctrl_rot']))
            return
        safe = getattr(self, '_safe_loc', None)
        if safe is None or loc[2] > safe[0][2] - drop or getattr(self, '_pending_edge', None) is not None:
            return
        now = time.monotonic()
        if now - getattr(self, '_last_rescue', 0.0) < 3.0:
            return
        self._last_rescue = now
        ch = next((e.ch for e in self.entries if e.cls == 'player_controller_city_bp_C' and e.open), None)
        if ch is None:
            return
        fi = self.world.schema.field_index('player_controller_city_bp_C')
        idx = fi.index(('func', 'ClientSetLocation'))
        target = [safe[0][0], safe[0][1], safe[0][2] + 50.0]
        e = Entry()
        e.t, e.ch, e.cls, e.exports = 0, ch, 'player_controller_city_bp_C', []
        e.open = e.close = e.must_map = False
        e.reliable = True
        e.patch_pairs = ()
        e.parsed = model.Parsed()
        blk = model.Block()
        blk.actor, blk.rep, blk.guid, blk.stable, blk.destroy = True, False, 0, False, False
        blk.cls = blk.outer = None
        blk.ref = blk.ref_name = None
        blk.cls_name = 'player_controller_city_bp_C'
        blk.values = blk.tail = blk.raw = None
        blk.partial_properties = False
        blk.fields = [(idx, 'ClientSetLocation', 'rpc', {'NewLocation': target, 'NewRotation': [0.0, safe[1][1], 0.0]})]
        e.parsed.blocks = [blk]
        self.entries.insert(self.pos, e)
        print('FALL rescue %s z=%.0f -> %s' % (self.name, loc[2], [round(x) for x in target]), flush=True)

    def remote_update(self, src):
        """Unreliable property update of src's pawn for THIS connection (ReplicatedMovement + movement mode + view pitch)."""
        ch = self.remote_pawn_ch.get(id(src))
        st = src.pawn_state
        if ch is None or st is None:
            return []
        if any(e.ch == ch for e in self.entries[self.pos:]):      # the pawn's reliable spawn/initial bunches are not all sent yet
            return []
        rot = st['ctrl_rot']
        yaw = self._facing.get(id(src), rot[1])
        if math.hypot(st['vel'][0], st['vel'][1]) > 40.0:           # the pawn faces its direction of travel (no strafe/backup states in the city); camera yaw only when idle at first
            yaw = math.degrees(math.atan2(st['vel'][1], st['vel'][0])) % 360.0
        self._facing[id(src)] = yaw
        values = {
            'ReplicatedMovement': {'loc': st['loc'], 'rot': [0.0, yaw, 0.0], 'vel': st['vel'], 'sleep': 0, 'phys': 0},
            'ReplicatedMovementMode': st['mode'],
            'RemoteViewPitch': int(((rot[0] % 360.0) / 360.0) * 256) & 0xFF,
        }
        b = model.Block()
        b.actor, b.rep, b.guid, b.stable, b.destroy, b.cls, b.outer = 1, 1, None, None, False, None, None
        b.cls_name, b.values, b.tail, b.raw, b.fields = 'BP_PlayerCharacter_C', values, None, None, None
        p = model.Parsed()
        p.blocks = [b]
        w = model.encode(p, self.world.ctx, 'BP_PlayerCharacter_C')
        return compose.compose(ch, w.n, w.nbits, [], open=False, close=False, reliable=False)

    # EArchonLocomotionState (0 Idle, 1 Walk, 2 Run, 3 Sprint; max enumerator 5 -> 3 bits) and EPlayerStatesDefault (max enumerator 16 -> 5 bits).
    LOCO_BITS, DEFAULT_BITS = 3, 5
    FN_MULTI_CURRENT, FN_MULTI_DEFAULT, FN_MULTI_DESIRED = 6, 7, 8        # field indices in player_state_machine_bpc_C

    # Locomotion classes measured on the client: idle < ~20, class 1 (<~380), class 2 (~400-600), class 3 sprint (>~620, up to 850).
    @staticmethod
    def loco_class(speed, cur):
        up = (20.0, 380.0, 620.0)
        down = (10.0, 340.0, 590.0)
        c = cur
        while c < 3 and speed >= up[c]:
            c += 1
        while c > 0 and speed < down[c - 1]:
            c -= 1
        return c

    # EPlayerStatesDefault values seen on the real client: 0..3 = idle / loco classes, 4 = airborne (jump), 6 = landing.
    DEF_JUMP, DEF_LAND = 4, 6

    def remote_state(self, src):
        """Server -> client NetMulticast of the player-state-machine component (what the real server's copy of the component derives from the
        owner's movement and relays): the remote pawn's anim blueprint selects its locomotion/jump branch from these, not from velocity alone."""
        ch = self.remote_pawn_ch.get(id(src))
        comp = self.remote_comp.get(id(src))
        st = src.pawn_state
        if ch is None or comp is None or st is None or any(e.ch == ch for e in self.entries[self.pos:]):
            return []
        now = time.time()
        sent = self._loco_sent.setdefault(id(src), {'cur': 0, 'def': 0, 'des': 0, 't': 0.0, 'air': False, 'land': 0.0})
        speed = math.hypot(st['vel'][0], st['vel'][1])
        cur = self.loco_class(speed, sent['cur'])
        air = st['mode'] == 3
        if sent['air'] and not air:
            sent['land'] = now + 0.45
        sent['air'] = air
        if air:
            dfl = self.DEF_JUMP
            cur_out = cur                                  # a running jump keeps its locomotion class, a standing one stays 0
        elif now < sent['land']:
            dfl = self.DEF_LAND
            cur_out = 0 if cur == 0 else cur
        else:
            dfl, cur_out = cur, cur
        des = cur                                          # owner's key-input intent: moving classes while moving
        if cur_out == sent['cur'] and dfl == sent['def'] and des == sent['des'] and now - sent['t'] < 1.0:
            return []
        prev_cur, prev_def, prev_des = sent['cur'], sent['def'], sent['des']
        sent.update(cur=cur_out, **{'def': dfl}, des=des, t=now)

        def params(nbits, *vals):                       # FRepLayout::SendPropertiesForRPC: per parameter [send bit = value != default][value]
            bits = n = 0
            for v in vals:
                if v:
                    bits |= (1 | (v << 1)) << n
                    n += 1 + nbits
                else:
                    n += 1
            return bits, n
        fields = [(self.FN_MULTI_DESIRED, 'Multi_UpdateDesiredLocoState', 'raw', params(self.LOCO_BITS, des, prev_des)),
                  (self.FN_MULTI_CURRENT, 'Multi_UpdateCurrentLocoState', 'raw', params(self.LOCO_BITS, cur_out, prev_cur)),
                  (self.FN_MULTI_DEFAULT, 'Multi_UpdateDefaultPlayerState', 'raw', params(self.DEFAULT_BITS, dfl, prev_def))]
        b = model.Block()
        b.actor, b.rep, b.guid, b.stable, b.destroy, b.cls, b.outer = 0, 0, comp[0], comp[1], False, comp[2], comp[3]
        b.cls_name, b.values, b.tail, b.raw, b.fields = 'player_state_machine_bpc_C', None, None, None, fields
        p = model.Parsed()
        p.blocks = [b]
        w = model.encode(p, self.world.ctx, 'BP_PlayerCharacter_C')
        return compose.compose(ch, w.n, w.nbits, [], open=False, close=False, reliable=False)

    # -------------------------------------------------------------------------------- other players
    def add_remote(self, other, slot, elapsed_ms):
        """Show `other`'s actors to this connection (simulated-proxy view, own channel range, own spawn offset)."""
        ents = views.remote_entries(self.world.schema, other.entries, ch_base=20 + 20 * slot, loc_offset=(150.0 * (slot + 1), 0.0, 0.0))
        if not ents:
            return
        # The remote bunches reference static GUIDs (packages, classes, CDOs) that this connection only learns from its OWN owner stream
        # (later exports reference their outers by number only). A remote bunch that arrives first leaves those GUIDs "broken" in the client's cache
        # and poisons the owner channels too (seen live: ch 4 failed with "Guid is broken", then the client closed the connection). So remote
        # actors are only shown once the connection's own initial world has been delivered.
        own_end = max((e.t for e in self.entries if e.ch < 20), default=0)
        start = max(elapsed_ms, own_end + 500)
        t0 = ents[0].t
        for e in ents:
            e.t = start + (e.t - t0)
        self.entries.extend(ents)
        self.entries.sort(key=lambda e: e.t)          # stable; everything already sent has t <= elapsed_ms <= the new entries
        self.remote[id(other)] = sorted({e.ch for e in ents})
        pawn = [e.ch for e in ents if e.cls == 'BP_PlayerCharacter_C']
        if pawn:
            self.remote_pawn_ch[id(other)] = pawn[0]
            for e in ents:
                for b in e.parsed.blocks:
                    if e.ch == pawn[0] and not b.actor and b.cls_name == 'player_state_machine_bpc_C':
                        self.remote_comp[id(other)] = (b.guid, b.stable, b.cls, b.outer)

    def close_remote(self, other):
        """Close bunches for the channels that showed `other` (player left)."""
        chs = self.remote.pop(id(other), [])
        return [Bunch(ch, 0, 0, reliable=True, close=True, chname=CHNAME_ACTOR) for ch in chs]

    def due(self, elapsed_ms):
        self._elapsed = elapsed_ms
        if self._main_queue:
            self._drain_main_queue()
        out = []
        ev = self.entries
        while self.pos < len(ev) and ev[self.pos].t <= elapsed_ms:
            e = ev[self.pos]
            w = model.encode(e.parsed, self.world.ctx, e.cls)
            bits, n = w.n, w.nbits
            if e.patch_pairs and any(b.raw and b.raw[1] for b in e.parsed.blocks):
                bits, hits = patch_bits(bits, n, e.patch_pairs)
                self.patch_hits += hits
            out += compose.compose(e.ch, bits, n, e.exports, open=e.open, close=e.close, reliable=e.reliable, must_map=e.must_map)
            self.pos += 1
        return out

    @property
    def finished(self):
        # work queued by worker threads (rewards, infusion) is drained in due(), so it keeps the session active
        return self.pos >= len(self.entries) and not getattr(self, '_main_queue', None)


def _ramsgate_atmosphere_entry(world, entries):
    """Replicate the Ramsgate day atmosphere state on the placed atmosphere spawner.

    The actor identity and values are taken from the stock 2.1.1 cooked map/Blueprint exports.
    Keep this opt-in until a fresh stock-client join confirms the RepNotify path visually.
    """
    actor_guid = 30001                         # static/odd; outside the recorded export range
    level = None
    highest_time = max((entry.t for entry in entries), default=0)
    for source in world.entries:
        for root in source.exports:
            for info in flatten(root):
                if info.guid == 5 and info.name == 'PersistentLevel':
                    level = info
                    break
            if level is not None:
                break
        if level is not None:
            break
    if level is None:
        raise ValueError('recording has no exported Ramsgate PersistentLevel path')
    if actor_guid in world.ctx._names:
        raise ValueError('static atmosphere actor NetGUID collides with the world')

    actor = GuidInfo()
    actor.guid, actor.flags, actor.outer_info = actor_guid, 1, level
    actor.name, actor.checksum = 'atmosphere_spawner_bp_3', None

    block = model.Block()
    block.actor, block.rep = True, True
    block.guid, block.stable, block.destroy, block.cls, block.outer = None, None, False, None, None
    block.cls_name = 'atmosphere_spawner_bp_C'
    # ServerAtmosphereSettings::ChangeValues is built from the Ramsgate day atmosphere defaults:
    # the day Blueprint supplies the sun/skylight/fog targets, while the spawner CDO supplies the
    # transition timing and affect flags. Mode=1 is Archon::EAtmosphereDrivenValueMode::Target.
    # ChangeAtmosphereCount starts at 1 so the initial replication differs from the zeroed CDO.
    values = {
        'ServerAtmosphereSettings.ChangeAtmosphereCount': 1,
        'ServerAtmosphereSettings.ChangeValues.Duration': 1.0,
        'ServerAtmosphereSettings.ChangeValues.FadeInTime': 0.25,
        'ServerAtmosphereSettings.ChangeValues.FadeOutTime': 0.25,
        'ServerAtmosphereSettings.ChangeValues.InterpolationCurve': ('obj', 0),
        'ServerAtmosphereSettings.ChangeValues.AffectLighting': True,
        'ServerAtmosphereSettings.ChangeValues.SunBrightness.Mode': 1,
        'ServerAtmosphereSettings.ChangeValues.SunBrightness.Value': 5.0,
        'ServerAtmosphereSettings.ChangeValues.DirectionalLightColor.Mode': 1,
        'ServerAtmosphereSettings.ChangeValues.DirectionalLightColor.Color.R': 1.0,
        'ServerAtmosphereSettings.ChangeValues.DirectionalLightColor.Color.G': 0.613884,
        'ServerAtmosphereSettings.ChangeValues.DirectionalLightColor.Color.B': 0.379006,
        'ServerAtmosphereSettings.ChangeValues.DirectionalLightColor.Color.A': 0.0,
        'ServerAtmosphereSettings.ChangeValues.SkylightBrightness.Mode': 1,
        'ServerAtmosphereSettings.ChangeValues.SkylightBrightness.Value': 6.0,
        'ServerAtmosphereSettings.ChangeValues.SkylightColor.Mode': 1,
        'ServerAtmosphereSettings.ChangeValues.SkylightColor.Color.R': 0.364583,
        'ServerAtmosphereSettings.ChangeValues.SkylightColor.Color.G': 0.480169,
        'ServerAtmosphereSettings.ChangeValues.SkylightColor.Color.B': 1.0,
        'ServerAtmosphereSettings.ChangeValues.SkylightColor.Color.A': 1.0,
        'ServerAtmosphereSettings.ChangeValues.AffectFog': True,
        'ServerAtmosphereSettings.ChangeValues.HeightFogDensity.Mode': 1,
        'ServerAtmosphereSettings.ChangeValues.HeightFogDensity.Value': 0.05,
        'ServerAtmosphereSettings.ChangeValues.HeightFogOpacity.Mode': 1,
        'ServerAtmosphereSettings.ChangeValues.HeightFogOpacity.Value': 0.7,
        'ServerAtmosphereSettings.ChangeValues.HeightFogColor.Mode': 1,
        'ServerAtmosphereSettings.ChangeValues.HeightFogColor.Color.R': 0.0,
        'ServerAtmosphereSettings.ChangeValues.HeightFogColor.Color.G': 0.145911,
        'ServerAtmosphereSettings.ChangeValues.HeightFogColor.Color.B': 0.307292,
        'ServerAtmosphereSettings.ChangeValues.HeightFogColor.Color.A': 1.0,
        'ServerAtmosphereSettings.ChangeValues.HeightFogSunGlowColor.Mode': 1,
        'ServerAtmosphereSettings.ChangeValues.HeightFogSunGlowColor.Color.R': 0.317708,
        'ServerAtmosphereSettings.ChangeValues.HeightFogSunGlowColor.Color.G': 0.175489,
        'ServerAtmosphereSettings.ChangeValues.HeightFogSunGlowColor.Color.B': 0.067565,
        'ServerAtmosphereSettings.ChangeValues.HeightFogSunGlowColor.Color.A': 1.0,
        'ServerAtmosphereSettings.ChangeValues.HeightFogFalloff.Mode': 1,
        'ServerAtmosphereSettings.ChangeValues.HeightFogFalloff.Value': 1.0,
        'RequestedAtmosphereIndex': 0,
    }
    block.values, block.tail, block.raw, block.fields = values, None, None, None

    parsed = model.Parsed()
    parsed.open = {'guid': actor_guid}
    parsed.blocks = [block]
    entry = Entry()
    entry.t, entry.ch, entry.parsed, entry.cls, entry.exports = highest_time + 500, 17, parsed, block.cls_name, [actor]
    entry.open, entry.close, entry.reliable, entry.must_map = True, False, True, False
    entry.patch_pairs = ()
    return entry
