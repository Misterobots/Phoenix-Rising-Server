"""Dev-only live reload (MPGS_DEV_RELOAD=1): a saved change to an mpgs .py file takes effect in the running server.

Polled from the server loop (main thread, between packets). A changed module is re-imported, then every class it defines
gets the new functions copied onto the OLD class object, so objects that already exist (the connected player's world,
sessions) run the new code without reconnecting. Module-level state is re-run like a fresh import. Not covered: code that
only runs while joining (needs a rejoin), new __init__ fields on old objects, and a change that fails to import (logged,
old code keeps running).
"""
import importlib
import os
import sys
import time
import traceback

_ROOT = os.path.dirname(os.path.abspath(__file__))
_mtimes = {}
_last = 0.0


def enabled():
    return os.environ.get('MPGS_DEV_RELOAD') == '1'


def _modules():
    for name, mod in list(sys.modules.items()):
        f = getattr(mod, '__file__', None)
        if (name == 'mpgs' or name.startswith('mpgs.')) and f and f.endswith('.py') \
                and os.path.abspath(f).startswith(_ROOT) and name != __name__:
            yield name, mod, f


def _patch_classes(old_ns, mod):
    for key, new in vars(mod).items():
        old = old_ns.get(key)
        if not isinstance(new, type) or not isinstance(old, type) or old is new or new.__module__ != mod.__name__:
            continue
        for attr, val in vars(new).items():
            # __slots__ fields are descriptors bound to the class that made them: copying the new class's onto the old one
            # breaks every existing object (and copy.copy of it)
            if attr in ('__dict__', '__weakref__', '__slots__') or type(val).__name__ in ('member_descriptor',
                                                                                          'getset_descriptor'):
                continue
            try:
                setattr(old, attr, val)
            except (AttributeError, TypeError):
                pass
        setattr(mod, key, old)          # keep one class identity: isinstance checks on existing objects still hold


def poll(log=print, interval=1.0):
    """Reload changed modules; cheap to call every loop pass (checks the disk once per `interval`)."""
    global _last
    now = time.monotonic()
    if now - _last < interval:
        return []
    _last = now
    done = []
    for name, mod, f in _modules():
        try:
            m = os.stat(f).st_mtime
        except OSError:
            continue
        prev = _mtimes.setdefault(name, m)
        if m == prev:
            continue
        _mtimes[name] = m
        old_ns = dict(vars(mod))
        try:
            importlib.reload(mod)
            _patch_classes(old_ns, mod)
            done.append(name)
            log('DEV RELOAD %s' % name)
        except Exception:
            mod.__dict__.update(old_ns)     # failed import: put the old code back
            log('DEV RELOAD %s FAILED, old code kept:\n%s' % (name, traceback.format_exc()))
    return done
