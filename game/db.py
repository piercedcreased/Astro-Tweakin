"""Tiny SQLite layer. One file (astro_tweakin.db) next to the bot."""
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    name TEXT,
    coins INTEGER NOT NULL DEFAULT 0,
    pickaxe TEXT NOT NULL DEFAULT 'wood',
    layer INTEGER NOT NULL DEFAULT 1,
    max_layer INTEGER NOT NULL DEFAULT 1,
    rebirths INTEGER NOT NULL DEFAULT 0,
    sluglings INTEGER NOT NULL DEFAULT 0,
    last_collect REAL NOT NULL DEFAULT 0,
    last_roll REAL NOT NULL DEFAULT 0,
    total_mined INTEGER NOT NULL DEFAULT 0,
    total_earned INTEGER NOT NULL DEFAULT 0,
    best_ore TEXT
);
CREATE TABLE IF NOT EXISTS items (
    user_id INTEGER NOT NULL,
    item TEXT NOT NULL,
    qty INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, item)
);
CREATE TABLE IF NOT EXISTS discovered (
    user_id INTEGER NOT NULL,
    ore TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    first_at REAL NOT NULL,
    PRIMARY KEY (user_id, ore)
);
CREATE TABLE IF NOT EXISTS buffs (
    user_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    amount REAL NOT NULL,
    expires REAL NOT NULL,
    PRIMARY KEY (user_id, kind)
);
CREATE TABLE IF NOT EXISTS guilds (
    id INTEGER PRIMARY KEY,
    announce_channel INTEGER
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    luck REAL NOT NULL,
    expires REAL NOT NULL,
    label TEXT
);
CREATE TABLE IF NOT EXISTS finds (
    user_id INTEGER NOT NULL,
    ore TEXT NOT NULL,
    at REAL NOT NULL
);
"""


class DB:
    def __init__(self, path="astro_tweakin.db"):
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)

    def q(self, sql, *args):
        return self.conn.execute(sql, args).fetchall()

    def one(self, sql, *args):
        return self.conn.execute(sql, args).fetchone()

    def run(self, sql, *args):
        self.conn.execute(sql, args)

    # users
    def user(self, uid, name=None):
        row = self.one("SELECT * FROM users WHERE id=?", uid)
        if row is None:
            self.run("INSERT INTO users (id, name, last_collect) VALUES (?, ?, ?)", uid, name, time.time())
            row = self.one("SELECT * FROM users WHERE id=?", uid)
        elif name and row["name"] != name:
            self.run("UPDATE users SET name=? WHERE id=?", name, uid)
        return row

    def set(self, uid, **fields):
        cols = ", ".join(f"{k}=?" for k in fields)
        self.run(f"UPDATE users SET {cols} WHERE id=?", *fields.values(), uid)

    def add_coins(self, uid, amount):
        self.run("UPDATE users SET coins = coins + ? WHERE id=?", amount, uid)

    # items: "ore:iron", "bar:gold"
    def items(self, uid, prefix=""):
        rows = self.q("SELECT item, qty FROM items WHERE user_id=? AND qty>0 AND item LIKE ?", uid, prefix + "%")
        return {r["item"]: r["qty"] for r in rows}

    def qty(self, uid, item):
        row = self.one("SELECT qty FROM items WHERE user_id=? AND item=?", uid, item)
        return row["qty"] if row else 0

    def add_item(self, uid, item, qty):
        self.run(
            "INSERT INTO items (user_id, item, qty) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, item) DO UPDATE SET qty = qty + excluded.qty",
            uid, item, qty,
        )

    def discover(self, uid, ore, count):
        self.run(
            "INSERT INTO discovered (user_id, ore, count, first_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id, ore) DO UPDATE SET count = count + excluded.count",
            uid, ore, count, time.time(),
        )

    def discovered(self, uid):
        return {r["ore"]: r["count"] for r in self.q("SELECT ore, count FROM discovered WHERE user_id=?", uid)}

    # buffs
    def buffs(self, uid):
        now = time.time()
        self.run("DELETE FROM buffs WHERE expires < ?", now)
        return {r["kind"]: (r["amount"], r["expires"]) for r in self.q("SELECT * FROM buffs WHERE user_id=?", uid)}

    def add_buff(self, uid, kind, amount, seconds):
        cur = self.buffs(uid).get(kind)
        now = time.time()
        if cur and cur[0] == amount:
            expires = cur[1] + seconds          # same buff stacks its time
        elif cur and cur[0] > amount and kind != "haste":
            expires = cur[1]                    # keep the stronger one
            amount = cur[0]
        else:
            expires = now + seconds
        self.run(
            "INSERT INTO buffs (user_id, kind, amount, expires) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id, kind) DO UPDATE SET amount=excluded.amount, expires=excluded.expires",
            uid, kind, amount, expires,
        )

    # server-wide luck event
    def event(self):
        row = self.one("SELECT * FROM events WHERE id=1 AND expires > ?", time.time())
        return row

    def set_event(self, luck, seconds, label):
        self.run(
            "INSERT INTO events (id, luck, expires, label) VALUES (1, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET luck=excluded.luck, expires=excluded.expires, label=excluded.label",
            luck, time.time() + seconds, label,
        )

    def announce_channel(self, gid):
        row = self.one("SELECT announce_channel FROM guilds WHERE id=?", gid)
        return row["announce_channel"] if row else None

    def set_announce_channel(self, gid, cid):
        self.run(
            "INSERT INTO guilds (id, announce_channel) VALUES (?, ?) "
            "ON CONFLICT(id) DO UPDATE SET announce_channel=excluded.announce_channel", gid, cid,
        )
