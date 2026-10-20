"""Backend access for the game server: session-key consume."""
import json
import os
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEY_FILES = (os.path.join(HERE, '..', 'Backend', 'data', 'gameserver.key'),)


def gameserver_api_key():
    """The backend's shared game-server key: MPGS_GAMESERVER_KEY, else the backend's data/gameserver.key."""
    if os.environ.get('MPGS_GAMESERVER_KEY'):
        return os.environ['MPGS_GAMESERVER_KEY']
    for path in KEY_FILES:
        if os.path.exists(path):
            return open(path).read().strip()
    raise RuntimeError('no game-server key: start the backend once (it creates data/gameserver.key) or set MPGS_GAMESERVER_KEY')


def consume_key(token: str, base='http://127.0.0.1:3001') -> bytes:
    req = urllib.request.Request(
        base + '/key/consume', data=json.dumps({'token': token}).encode(),
        headers={'Content-Type': 'application/json', 'x-phoenixrising-gameserver-apikey': gameserver_api_key()})
    return bytes.fromhex(json.load(urllib.request.urlopen(req, timeout=5))['key'])


def register_instance(info: dict, base='http://127.0.0.1:3001') -> dict:
    req = urllib.request.Request(
        base + '/instances/register', data=json.dumps(info).encode(),
        headers={'Content-Type': 'application/json', 'x-phoenixrising-gameserver-apikey': gameserver_api_key()})
    return json.load(urllib.request.urlopen(req, timeout=5))


def unregister_instance(info: dict, base='http://127.0.0.1:3001') -> dict:
    req = urllib.request.Request(
        base + '/instances/unregister', data=json.dumps(info).encode(),
        headers={'Content-Type': 'application/json', 'x-phoenixrising-gameserver-apikey': gameserver_api_key()})
    return json.load(urllib.request.urlopen(req, timeout=5))
