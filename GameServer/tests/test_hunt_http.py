"""Exercise the client/server hunt handoff over separate HTTP listeners."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent / 'Backend'))
sys.path.insert(0, str(ROOT))
from aiohttp.test_utils import TestClient, TestServer
from phoenix_backend.__main__ import build_app
from phoenix_backend.db import DB
from phoenix_backend.gameplay import Gameplay
from phoenix_backend.routes import Routes
from phoenix_backend.tokens import Tokens
from mpgs import hunts
from mpgs.server import GameServer


class HuntHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def test_join_register_cross_listener_key_and_unregister(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ', {'PHOENIX_HUNT_ID': ''}):
            db = DB(str(Path(folder) / 'test.sqlite'))
            tokens = Tokens(str(Path(folder) / 'secret'))
            routes = Routes(db, tokens)
            gameplay = Gameplay(db, tokens, routes, folder)
            clients = [TestClient(TestServer(build_app(db, tokens, xmpp, folder, routes=routes, gameplay=gameplay)))
                       for xmpp in (False, True)]
            selected = hunts.select('Adventure_IslandA_D24')
            game = GameServer(0, None, selected['map'], selected['gameMode'], selected['huntId'])
            try:
                for client in clients:
                    await client.start_server()
                api, launcher = clients
                account = db.create_account('HTTPTest')
                token = tokens.mint('session_token', 60, sub=account['account_id'], acc=account['account_id'])
                headers = {'Authorization': 'Bearer ' + token}
                server_headers = {'x-phoenixrising-gameserver-apikey': gameplay.gs_key}
                # A client cannot advertise itself as an available game server.
                response = await api.post('/instances/register', json=game.instance_info(), headers=headers)
                self.assertEqual(response.status, 403)
                response = await launcher.post('/candidate/join', json={
                    'gameMode': 'ISLAND', 'gameType': 'HUNTING_GROUND', 'hunts': [selected['huntId']]}, headers=headers)
                self.assertEqual(response.status, 200, await response.text())
                response = await api.get('/candidate/status', headers=headers)
                pending = await response.json()
                self.assertEqual(pending['status'], 'MATCHING')
                response = await api.post('/instances/register', json=game.instance_info(), headers=server_headers)
                self.assertEqual(response.status, 200, await response.text())
                response = await launcher.get('/candidate/status', headers=headers)
                ready = await response.json()
                self.assertEqual(ready['serverInfo']['port'], game.port)
                self.assertEqual(ready['serverInfo']['map'], game.map_path + '?bUseIPSockets')
                response = await launcher.post('/key/generate', json={}, headers=headers)
                issued = await response.json()
                response = await api.post('/key/consume', json={'token': issued['token']}, headers=server_headers)
                self.assertEqual((await response.json())['key'], issued['key'])
                response = await api.post('/key/consume', json={'token': issued['token']}, headers=server_headers)
                self.assertEqual(response.status, 404)
                response = await api.post('/instances/unregister', json=game.instance_info(), headers=server_headers)
                self.assertEqual(response.status, 200)
                response = await launcher.get('/candidate/status', headers=headers)
                self.assertEqual((await response.json())['status'], 'MATCHING')
            finally:
                game.sock.close()
                for client in clients:
                    await client.close()
                db.conn.close()


if __name__ == '__main__':
    unittest.main()
