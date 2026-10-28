"""Test helper: grant items to a character through the backend, as a quest reward would.

python -m tools.grant_item <account display name> <catalog id> [amount]
The account's first character gets the items. Takes effect at the client's next inventory fetch (rejoin).
"""
import json
import os
import sqlite3
import sys
from urllib.request import Request, urlopen

from mpgs import rewards
from mpgs.initialization import _backend_headers

BASE = 'http://127.0.0.1:3001'
DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'Backend', 'data', 'phoenix.sqlite')


def main(argv):
    name, catalog = argv[0], argv[1]
    amount = int(argv[2]) if len(argv) > 2 else 1
    req = Request(BASE + '/launcher/v1/local-profiles', data=json.dumps({'displayName': name, 'reuse': True}).encode(),
                  headers={'Content-Type': 'application/json'}, method='POST')
    account = json.loads(urlopen(req, timeout=10).read().decode())['accountId']
    db = sqlite3.connect('file:%s?mode=ro' % DB, uri=True)
    character = db.execute('select character_id from characters where account_id=? order by rowid',
                           (account,)).fetchone()[0]
    body = rewards.build_transaction(account, character, 'grant-tool:%s:%s:%d' % (account, catalog, amount),
                                     [(catalog, amount)])
    print(rewards.post_transaction(body, _backend_headers(None), BASE))


if __name__ == '__main__':
    main(sys.argv[1:])
