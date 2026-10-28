import os
import sys
import time

from mpgs import devreload


def test_reload_patches_existing_objects(tmp_path, monkeypatch):
    pkg = os.path.dirname(devreload.__file__)
    path = os.path.join(pkg, '_devreload_probe.py')
    open(path, 'w').write('class C:\n    def f(self):\n        return 1\n')
    try:
        from mpgs import _devreload_probe as probe
        obj = probe.C()
        devreload._last = 0
        devreload.poll(log=lambda *_: None)          # records mtimes
        time.sleep(0.05)
        open(path, 'w').write('class C:\n    def f(self):\n        return 2\n')
        os.utime(path, (time.time() + 5, time.time() + 5))
        devreload._last = 0
        assert 'mpgs._devreload_probe' in devreload.poll(log=lambda *_: None)
        assert obj.f() == 2 and isinstance(obj, probe.C)
        open(path, 'w').write('class C:\n    def f(self)\n')   # syntax error: old code kept
        os.utime(path, (time.time() + 10, time.time() + 10))
        devreload._last = 0
        devreload.poll(log=lambda *_: None)
        assert obj.f() == 2
    finally:
        os.remove(path)
        sys.modules.pop('mpgs._devreload_probe', None)
