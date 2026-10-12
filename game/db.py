"""SQLite layer. One file (astro_tweakin.db) next to the bot.

Data-safety rules:
- Every game action runs inside one transaction (db.tx()), so a crash or error halfway
  through rolls the whole action back instead of leaving half a purchase.
- Coins and item counts can never go below zero: a write that would do that raises
  NotEnough and the action is rolled back.
- WAL + a background "flusher": every save is written to disk (fsync) by a separate thread the moment
  it happens, so a power cut can't lose a finished action, but buttons never wait for the disk.
  (Syncing inside every click made buttons slow on Windows.) The save can't be corrupted either way.
- Backups: see game/backup.py.
"""
import contextlib
import logging
import sqlite3
import threading
import time

log = logging.getLogger("astro-tweakin.db")


class NotEnough(Exception):
    """A write would have made coins or an item go negative."""


class CorruptDatabase(Exception):
    pass

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
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS stats (
    user_id INTEGER NOT NULL,
    key TEXT NOT NULL,
    value INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, key)
);
CREATE TABLE IF NOT EXISTS bounties (
    user_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    idx INTEGER NOT NULL,
    kind TEXT NOT NULL,
    need INTEGER NOT NULL,
    base INTEGER NOT NULL,
    reward INTEGER NOT NULL,
    claimed INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, day, idx)
);
CREATE TABLE IF NOT EXISTS achievements (
    user_id INTEGER NOT NULL,
    key TEXT NOT NULL,
    at REAL NOT NULL,
    PRIMARY KEY (user_id, key)
);
CREATE TABLE IF NOT EXISTS raids (
    attacker INTEGER NOT NULL,
    target INTEGER NOT NULL,
    at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS locks (
    user_id INTEGER NOT NULL,
    item TEXT NOT NULL,
    PRIMARY KEY (user_id, item)
);
CREATE TABLE IF NOT EXISTS enchants (
    user_id INTEGER NOT NULL,
    slot INTEGER NOT NULL,
    ench TEXT NOT NULL,
    level INTEGER NOT NULL,
    rerolled INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, slot)
);
CREATE TABLE IF NOT EXISTS usage (
    at REAL NOT NULL,
    guild_id INTEGER,
    user_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    action TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS usage_at ON usage (at);
CREATE TABLE IF NOT EXISTS market (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    seller INTEGER NOT NULL,
    item TEXT NOT NULL,
    qty INTEGER NOT NULL,
    price INTEGER NOT NULL,
    listed REAL NOT NULL,
    expires REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    buyer INTEGER,
    done_at REAL,
    seen INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS market_open ON market (status, expires);
CREATE TABLE IF NOT EXISTS claims (
    kind TEXT NOT NULL,
    event INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    at REAL NOT NULL,
    PRIMARY KEY (kind, event, user_id)
);
"""

# Columns added in later versions get listed here, so old save files are upgraded in place
# (never wiped) when the bot starts. Format: (table, column, definition)
MIGRATIONS = [
    ("users", "autosell", "INTEGER NOT NULL DEFAULT 0"),          # v0.2.0
    ("users", "daily_last", "REAL NOT NULL DEFAULT 0"),           # v0.2.0
    ("users", "daily_streak", "INTEGER NOT NULL DEFAULT 0"),      # v0.2.0
    ("users", "title", "TEXT"),                                   # v0.3.0
    ("users", "ship_stash", "INTEGER NOT NULL DEFAULT 0"),        # v0.3.0
    ("users", "ship_hull", "INTEGER NOT NULL DEFAULT 0"),         # v0.3.0
    ("users", "ship_engine", "INTEGER NOT NULL DEFAULT 0"),       # v0.3.0
    ("users", "ship_paint", "INTEGER NOT NULL DEFAULT 0"),        # v0.3.0
    ("users", "last_check", "REAL NOT NULL DEFAULT 0"),           # v0.3.0
    ("users", "last_raid", "REAL NOT NULL DEFAULT 0"),            # v0.3.0
    ("users", "best_find", "TEXT"),                               # v0.6.0 rarest find incl. mutations
    ("users", "best_score", "REAL NOT NULL DEFAULT 0"),           # v0.6.0
    ("users", "cal_day", "INTEGER NOT NULL DEFAULT 0"),           # v0.7.0 daily calendar position (1-7)
    ("users", "slug_rank", "INTEGER NOT NULL DEFAULT 0"),         # v0.7.0 slugling prestige
    ("users", "slug_pack", "INTEGER NOT NULL DEFAULT 0"),         # v0.7.0 Bigger Backpacks level
    ("users", "mg_count", "INTEGER NOT NULL DEFAULT 0"),          # v0.7.0 presses since the last minigame
    ("users", "pick_name", "TEXT"),                               # v0.8.0 Pickaxe Rename Tag
]


class DB:
    def __init__(self, path="astro_tweakin.db"):
        self.path = path
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._depth = 0
        self._dirty = threading.Event()
        self._stop = False
        self._flusher = None
        self.check_integrity()
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        self._migrate()

    def check_integrity(self):
        try:
            result = self.conn.execute("PRAGMA integrity_check").fetchone()[0]
        except sqlite3.DatabaseError as e:
            result = str(e)
        if result != "ok":
            raise CorruptDatabase(
                f"The save file {self.path} is damaged ({result}). The bot will not start so nothing gets "
                f"overwritten. Run restore_backup.bat to restore the latest backup.")

    def _migrate(self):
        for table, column, definition in MIGRATIONS:
            cols = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                log.info("Upgrading save file: adding %s.%s", table, column)
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    @contextlib.contextmanager
    def tx(self):
        """All-or-nothing block. Nested calls join the outer transaction."""
        with self._lock:
            outer = self._depth == 0
            if outer:
                self.conn.execute("BEGIN IMMEDIATE")
            self._depth += 1
            try:
                yield self
            except BaseException:
                self._depth -= 1
                if outer:
                    self.conn.execute("ROLLBACK")
                raise
            else:
                self._depth -= 1
                if outer:
                    self.conn.execute("COMMIT")
                    self._dirty.set()

    def start_flusher(self, idle=5.0):
        """Starts the background thread that puts every save on disk right after it happens."""
        if self._flusher is None:
            self._flusher = threading.Thread(target=self._flush_loop, args=(idle,), name="save-flusher", daemon=True)
            self._flusher.start()

    def _flush_loop(self, idle):
        # Its own connection, so the disk sync never holds up the bot. A checkpoint fsyncs the WAL file
        # (which makes every finished save permanent) and copies it into the main save file.
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None, check_same_thread=False)
        conn.execute("PRAGMA synchronous=NORMAL")
        last_clean = 0.0
        try:
            while not self._stop:
                self._dirty.wait(idle)
                self._dirty.clear()
                try:
                    if time.time() - last_clean > 300:
                        conn.execute("DELETE FROM buffs WHERE expires < ?", (time.time(),))
                        last_clean = time.time()
                    conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
                except sqlite3.Error:
                    log.exception("Background save flush failed (will retry)")
        finally:
            conn.close()

    def stop_flusher(self):
        if self._flusher is not None:
            self._stop = True
            self._dirty.set()
            self._flusher.join(timeout=10)
            self._flusher = None

    def close(self):
        self.stop_flusher()
        with self._lock:
            with contextlib.suppress(Exception):
                self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self.conn.close()

    def q(self, sql, *args):
        with self._lock:
            return self.conn.execute(sql, args).fetchall()

    def one(self, sql, *args):
        with self._lock:
            return self.conn.execute(sql, args).fetchone()

    def run(self, sql, *args):
        with self._lock:
            cur = self.conn.execute(sql, args)
            if self._depth == 0:
                self._dirty.set()       # a write outside a transaction is saved right away too
            return cur

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
        cur = self.run("UPDATE users SET coins = coins + ? WHERE id=? AND coins + ? >= 0", amount, uid, amount)
        if cur.rowcount != 1:
            raise NotEnough(f"user {uid} can't afford {-amount} coins")

    # items: "ore:iron", "bar:gold"
    def items(self, uid, prefix=""):
        rows = self.q("SELECT item, qty FROM items WHERE user_id=? AND qty>0 AND item LIKE ?", uid, prefix + "%")
        return {r["item"]: r["qty"] for r in rows}

    def qty(self, uid, item):
        row = self.one("SELECT qty FROM items WHERE user_id=? AND item=?", uid, item)
        return row["qty"] if row else 0

    def add_item(self, uid, item, qty):
        if qty < 0:
            cur = self.run("UPDATE items SET qty = qty + ? WHERE user_id=? AND item=? AND qty + ? >= 0",
                           qty, uid, item, qty)
            if cur.rowcount != 1:
                raise NotEnough(f"user {uid} doesn't have {-qty}x {item}")
            return
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
        # expired buffs are just ignored here (no write = no disk sync); checkpoint() deletes them later
        rows = self.q("SELECT * FROM buffs WHERE user_id=? AND expires > ?", uid, time.time())
        return {r["kind"]: (r["amount"], r["expires"]) for r in rows}

    def add_buff(self, uid, kind, amount, seconds):
        cur = self.buffs(uid).get(kind)
        now = time.time()
        if cur and cur[0] == amount:
            expires = cur[1] + seconds          # same buff stacks its time
        elif cur and cur[0] > amount:
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

    # locked items are never sold, smelted by "Smelt everything" or fenced
    def locked(self, uid):
        return {r["item"] for r in self.q("SELECT item FROM locks WHERE user_id=?", uid)}

    def set_lock(self, uid, item, on):
        if on:
            self.run("INSERT OR IGNORE INTO locks (user_id, item) VALUES (?, ?)", uid, item)
        else:
            self.run("DELETE FROM locks WHERE user_id=? AND item=?", uid, item)

    # counters for bounties and achievements ("mined", "raids", ...)
    def bump(self, uid, key, n=1):
        if n:
            self.run(
                "INSERT INTO stats (user_id, key, value) VALUES (?, ?, ?) "
                "ON CONFLICT(user_id, key) DO UPDATE SET value = value + excluded.value", uid, key, n)

    def stat(self, uid, key):
        row = self.one("SELECT value FROM stats WHERE user_id=? AND key=?", uid, key)
        return row["value"] if row else 0

    def stats(self, uid):
        return {r["key"]: r["value"] for r in self.q("SELECT key, value FROM stats WHERE user_id=?", uid)}

    def meta(self, key, default=None):
        row = self.one("SELECT value FROM meta WHERE key=?", key)
        return row["value"] if row else default

    def set_meta(self, key, value):
        self.run("INSERT INTO meta (key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", key, str(value))

    def claim(self, kind, event, uid):
        """Records a one-time claim. Returns False if this player already claimed it."""
        cur = self.run("INSERT OR IGNORE INTO claims (kind, event, user_id, at) VALUES (?, ?, ?, ?)",
                       kind, event, uid, time.time())
        return cur.rowcount == 1

    def claims(self, kind, event):
        return [r["user_id"] for r in self.q("SELECT user_id FROM claims WHERE kind=? AND event=? ORDER BY at", kind, event)]

    def log_usage(self, guild_id, user_id, kind, action):
        """One row per command or button press, for /alcad. Rows older than 90 days get pruned."""
        self.run("INSERT INTO usage (at, guild_id, user_id, kind, action) VALUES (?, ?, ?, ?, ?)",
                 time.time(), guild_id, user_id, kind, action[:60])

    def prune_usage(self, days=90):
        self.run("DELETE FROM usage WHERE at < ?", time.time() - days * 86400)

    def bot_channels(self, gid):
        """Channels the game is limited to in this server (empty = anywhere)."""
        raw = self.meta(f"botchannels:{gid}", "")
        return [int(x) for x in raw.split(",") if x]

    def set_bot_channels(self, gid, ids):
        self.set_meta(f"botchannels:{gid}", ",".join(str(i) for i in dict.fromkeys(ids)))

    def announce_channel(self, gid):
        row = self.one("SELECT announce_channel FROM guilds WHERE id=?", gid)
        return row["announce_channel"] if row else None

    def set_announce_channel(self, gid, cid):
        self.run(
            "INSERT INTO guilds (id, announce_channel) VALUES (?, ?) "
            "ON CONFLICT(id) DO UPDATE SET announce_channel=excluded.announce_channel", gid, cid,
        )
