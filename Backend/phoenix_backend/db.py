"""SQLite storage: one file, no database server."""
import json
import os
import sqlite3
import threading
import time

from . import ids

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    account_id   TEXT PRIMARY KEY,          -- game account id (base32)
    epic_id      TEXT UNIQUE NOT NULL,      -- Epic account id (hex32)
    puid         TEXT UNIQUE NOT NULL,      -- product user id (hex32)
    display_name TEXT UNIQUE NOT NULL,
    created      REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS exchange_codes (
    code       TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    expires    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS characters (
    character_id   TEXT PRIMARY KEY,
    account_id     TEXT NOT NULL,
    data           TEXT NOT NULL,           -- the client's character blob (a JSON string), stored as sent
    created        REAL NOT NULL,
    update_version INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS items (               -- instanced items (gear): one row per item
    instance_id    TEXT PRIMARY KEY,
    character_id   TEXT NOT NULL,
    catalog_id     TEXT NOT NULL,
    item_data      TEXT,                          -- the item's data JSON string, or NULL
    update_version INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS stacks (              -- stacked items (currencies, materials, tokens ...)
    character_id TEXT NOT NULL,
    catalog_id   TEXT NOT NULL,
    quantity     INTEGER NOT NULL,
    PRIMARY KEY (character_id, catalog_id)
);
CREATE TABLE IF NOT EXISTS loadouts (
    character_id TEXT NOT NULL,
    slot_index   INTEGER NOT NULL,
    data         TEXT NOT NULL,                   -- the loadout slot object as JSON
    PRIMARY KEY (character_id, slot_index)
);
CREATE TABLE IF NOT EXISTS progression (
    account_id     TEXT NOT NULL,
    progression_id TEXT NOT NULL,
    progress       INTEGER NOT NULL,
    PRIMARY KEY (account_id, progression_id)
);
CREATE TABLE IF NOT EXISTS transactions (        -- applied inventory transactions (idempotent retries)
    transaction_id TEXT PRIMARY KEY,
    applied        REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (
    scope TEXT NOT NULL,                    -- e.g. account id
    key   TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (scope, key)
);
"""


class DB:
    def __init__(self, path):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        self.conn.executescript(SCHEMA)

    def q(self, sql, args=()):
        with self.lock:
            return self.conn.execute(sql, args).fetchall()

    def one(self, sql, args=()):
        rows = self.q(sql, args)
        return rows[0] if rows else None

    # accounts --------------------------------------------------------------------------------------------------
    def account_by_name(self, name):
        return self.one('SELECT * FROM accounts WHERE display_name = ?', (name,))

    def account(self, account_id):
        return self.one('SELECT * FROM accounts WHERE account_id = ?', (account_id,))

    def account_by_epic(self, epic_id):
        return self.one('SELECT * FROM accounts WHERE epic_id = ?', (epic_id,))

    def create_account(self, name):
        acc = (ids.base32_id(), ids.hex32(), ids.hex32(), name, time.time())
        self.q('INSERT INTO accounts VALUES (?, ?, ?, ?, ?)', acc)
        return self.account(acc[0])

    # exchange codes (one-time, short-lived) ---------------------------------------------------------------------
    def new_exchange_code(self, account_id, ttl=300):
        code = ids.hex32()
        self.q('INSERT INTO exchange_codes VALUES (?, ?, ?)', (code, account_id, time.time() + ttl))
        return code

    def take_exchange_code(self, code):
        row = self.one('SELECT * FROM exchange_codes WHERE code = ?', (code,))
        if row is None:
            return None
        self.q('DELETE FROM exchange_codes WHERE code = ?', (code,))
        return row['account_id'] if row['expires'] >= time.time() else None

    # characters -------------------------------------------------------------------------------------------------
    def characters(self, account_id):
        return self.q('SELECT * FROM characters WHERE account_id = ? ORDER BY created', (account_id,))

    def character(self, character_id):
        return self.one('SELECT * FROM characters WHERE character_id = ?', (character_id,))

    def save_character(self, account_id, character_id, data):
        cur = self.character(character_id)
        if cur is None:
            self.q('INSERT INTO characters VALUES (?, ?, ?, ?, 0)', (character_id, account_id, data, time.time()))
        else:
            self.q('UPDATE characters SET data = ?, update_version = update_version + 1 WHERE character_id = ?',
                   (data, character_id))
        return self.character(character_id)

    # inventory --------------------------------------------------------------------------------------------------
    def items(self, character_id):
        return self.q('SELECT * FROM items WHERE character_id = ? ORDER BY rowid', (character_id,))

    def stacks(self, character_id):
        return self.q('SELECT * FROM stacks WHERE character_id = ? ORDER BY rowid', (character_id,))

    def stack(self, character_id, catalog_id):
        row = self.one('SELECT quantity FROM stacks WHERE character_id = ? AND catalog_id = ?', (character_id, catalog_id))
        return row['quantity'] if row else 0

    def apply_transaction(self, character_id, tx_id, add_instanced=(), add_stacked=(), remove_stacked=(), remove_instanced=()):
        """All-or-nothing inventory change. -> 'applied' | 'duplicate' | 'insufficient:<catalog>'."""
        with self.lock:
            c = self.conn
            if tx_id and c.execute('SELECT 1 FROM transactions WHERE transaction_id = ?', (tx_id,)).fetchone():
                return 'duplicate'
            for it in remove_stacked:
                row = c.execute('SELECT quantity FROM stacks WHERE character_id = ? AND catalog_id = ?',
                                (character_id, it['catalogId'])).fetchone()
                if (row['quantity'] if row else 0) < int(it['quantity']):
                    return 'insufficient:' + it['catalogId']
            c.execute('BEGIN')
            try:
                for it in add_instanced:
                    data = it.get('itemData')
                    c.execute('INSERT OR IGNORE INTO items VALUES (?, ?, ?, ?, ?)',
                              (it['instanceId'], character_id, it['catalogId'],
                               data if data is None or isinstance(data, str) else json.dumps(data), int(it.get('updateVersion') or 0)))
                for it in remove_instanced:
                    c.execute('DELETE FROM items WHERE character_id = ? AND instance_id = ?', (character_id, it['instanceId']))
                for it in add_stacked:
                    c.execute('INSERT INTO stacks VALUES (?, ?, ?) ON CONFLICT(character_id, catalog_id) '
                              'DO UPDATE SET quantity = quantity + excluded.quantity',
                              (character_id, it['catalogId'], int(it['quantity'])))
                for it in remove_stacked:
                    c.execute('UPDATE stacks SET quantity = quantity - ? WHERE character_id = ? AND catalog_id = ?',
                              (int(it['quantity']), character_id, it['catalogId']))
                c.execute('DELETE FROM stacks WHERE character_id = ? AND quantity <= 0', (character_id,))
                if tx_id:
                    c.execute('INSERT INTO transactions VALUES (?, ?)', (tx_id, time.time()))
                c.execute('COMMIT')
            except Exception:
                c.execute('ROLLBACK')
                raise
        return 'applied'

    # loadouts ---------------------------------------------------------------------------------------------------
    def loadouts(self, character_id):
        return [json.loads(r['data']) for r in self.q('SELECT data FROM loadouts WHERE character_id = ? ORDER BY slot_index',
                                                      (character_id,))]

    def save_loadout(self, character_id, index, slot):
        self.q('INSERT OR REPLACE INTO loadouts VALUES (?, ?, ?)', (character_id, int(index), json.dumps(slot)))

    # progression ------------------------------------------------------------------------------------------------
    def progression(self, account_id):
        return {r['progression_id']: r['progress'] for r in
                self.q('SELECT * FROM progression WHERE account_id = ?', (account_id,))}

    def add_progress(self, account_id, track, amount):
        self.q('INSERT INTO progression VALUES (?, ?, ?) ON CONFLICT(account_id, progression_id) '
               'DO UPDATE SET progress = progress + excluded.progress', (account_id, track, int(amount)))
        return self.progression(account_id).get(track, 0)

    # small per-account values ---------------------------------------------------------------------------------
    def get(self, scope, key, default=None):
        row = self.one('SELECT value FROM kv WHERE scope = ? AND key = ?', (scope, key))
        return json.loads(row['value']) if row else default

    def put(self, scope, key, value):
        self.q('INSERT OR REPLACE INTO kv VALUES (?, ?, ?)', (scope, key, json.dumps(value)))
