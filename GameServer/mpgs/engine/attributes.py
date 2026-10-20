"""Engine layer E3.3: attribute replication. A server that owns gameplay attributes (health, stamina, the weapon meter...) replicates
them as ordinary properties of the attribute-set sub-object (Dauntless declares them as plain float properties, e.g.
ArchonDamageAttributeSet.Determination is handle 19). This module writes such updates on an object the client already has."""
from ..repl import model


def find_block(session, ch, cls_name):
    """The block that created/updated an object of class `cls_name` on channel `ch` in this session's sent world (its header)."""
    for e in session.entries:
        if e.ch != ch:
            continue
        for blk in e.parsed.blocks:
            if blk.cls_name == cls_name and not blk.actor:
                return blk
    return None


def property_update(session, ch, header, values, reliable=True):
    """One entry: a property block for the object `header` identifies (same guid / stable / class / outer header as when it was
    created). values: {property name: value}, e.g. {'Determination': 12.0}."""
    from ..genworld import Entry
    b = model.Block()
    b.actor, b.rep, b.guid, b.stable, b.destroy = False, True, header.guid, header.stable, False
    b.cls, b.outer = header.cls, header.outer
    b.ref, b.ref_name = getattr(header, 'ref', None), getattr(header, 'ref_name', None)
    b.cls_name = header.cls_name
    b.values = dict(values)
    b.tail = b.raw = b.fields = None
    b.partial_properties = False
    e = Entry()
    e.t, e.ch, e.cls, e.exports = 0, ch, session.chan_cls.get(ch), []
    e.open = e.close = e.must_map = False
    e.reliable = reliable
    e.patch_pairs = ()
    e.parsed = model.Parsed()
    e.parsed.blocks = [b]
    return e
